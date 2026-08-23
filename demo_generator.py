import os
# ═══════════════════════════════════════════════════════
# mongo.py — FINAL CONSOLIDATED VERSION
#
# Combines every fix agreed on so far:
# 1. SEPARATE COLLECTION PER SOURCE — weather_db now has one
#    collection per source (ECM_Global_HR, GFS_Main, etc.), not
#    everything dumped into one forecast_data collection. This is
#    what sir asked for — open MongoDB Compass and you'll see 10
#    separate collections plus pipeline_errors.
# 2. HOURLY DATA — 8 time slots per day (00:00, 03:00, ... 21:00)
#    per source, each with realistic diurnal variation (temp peaks
#    mid-afternoon, humidity peaks at dawn, wind peaks afternoon).
#    Every document has a forecast_hour field.
# 3. SPATIALLY SMOOTH DATA — this is the fix for the "doesn't look
#    like Windy" complaint. The old generator gave every one of the
#    50,000 points a fully independent random number, which produces
#    speckled noise no amount of blurring can fix. This version
#    generates a coarse random field and smoothly samples it at each
#    point, so nearby points vary together — producing the organic
#    "warm patch here, cool patch there" look real weather (and
#    Windy) has.
# 4. NO CSVs — removed entirely. interpolate.py reads straight from
#    each source's MongoDB collection. Simpler, always in sync,
#    nothing to go stale.
# ═══════════════════════════════════════════════════════
import pymongo
import numpy as np
from scipy.interpolate import RegularGridInterpolator
import threading
import time
from datetime import datetime
from error_logger import log_error, log_success

# ═══════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════
MONGO_URL   = os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME     = "weather_db"

# Render.com auto-sets RENDER=true on its containers. Free-tier hosting
# has limited CPU/RAM, so we auto-shrink the demo dataset there — full
# 50,000-point / 10-source dataset is unnecessary for a live portfolio demo
# and would be slow to (re)generate on every deploy/restart.
IS_RENDER   = os.getenv("RENDER") == "true"
NUM_POINTS  = int(os.getenv("DEMO_NUM_POINTS", "1200" if IS_RENDER else "50000"))
NUM_THREADS = int(os.getenv("DEMO_NUM_THREADS", "1" if IS_RENDER else "10"))

LAT_MIN, LAT_MAX = 23.0, 37.0
LON_MIN, LON_MAX = 60.0, 77.0       

# 8 hourly slots per day — matches the time slider in api.py.
# Shrunk to 1 slot on Render's free tier (limited RAM) to keep
# memory use low — this is a demo dataset, not the full local run.
HOURS_PER_DAY = [0, 3, 6, 9, 12, 15, 18, 21] if not IS_RENDER else [12]

SOURCES = [
    {"source_id": 1,  "name": "ECM_Global_HR",  "model": "ECM",  "temp_offset":  0.0},
    {"source_id": 2,  "name": "ECM_Regional",    "model": "ECM",  "temp_offset":  0.3},
    {"source_id": 3,  "name": "ECM_Ensemble",    "model": "ECM",  "temp_offset": -0.2},
    {"source_id": 4,  "name": "ICON_Global",     "model": "ICON", "temp_offset":  0.5},
    {"source_id": 5,  "name": "ICON_EU",         "model": "ICON", "temp_offset": -0.4},
    {"source_id": 6,  "name": "GFS_Main",        "model": "GFS",  "temp_offset":  0.8},
    {"source_id": 7,  "name": "GFS_High_Res",    "model": "GFS",  "temp_offset": -0.1},
    {"source_id": 8,  "name": "WRF_Pakistan",    "model": "WRF",  "temp_offset":  0.2},
    {"source_id": 9,  "name": "NCEP_Analysis",   "model": "NCEP", "temp_offset": -0.3},
    {"source_id": 10, "name": "CFS_Seasonal",    "model": "CFS",  "temp_offset":  0.6},
]

# Fewer sources on Render's free tier — 1 is enough to demo the
# architecture without running out of the 512MB RAM limit.
if IS_RENDER:
    SOURCES = SOURCES[:1]


# ═══════════════════════════════════════════════════════
# CONNECT TO MONGODB
# ═══════════════════════════════════════════════════════
def connect_mongodb():
    try:
        print("Connecting to MongoDB...")
        client = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        client.server_info()
        db = client[DB_NAME]
        print(f"Connected -> database: {DB_NAME}")
        return client, db
    except Exception as e:
        log_error("MongoDB", "connection", "ConnectionError", str(e))
        print(f"MongoDB connection failed: {e}")
        raise


# ═══════════════════════════════════════════════════════
# GENERATE GRID POINTS — same 50,000 points every run
# ═══════════════════════════════════════════════════════
def generate_grid_points():
    np.random.seed(42)
    lats = np.random.uniform(LAT_MIN, LAT_MAX, NUM_POINTS)
    lons = np.random.uniform(LON_MIN, LON_MAX, NUM_POINTS)
    return lats, lons


# ═══════════════════════════════════════════════════════
# SMOOTH SPATIAL NOISE FIELD
# Generates a coarse random grid over the Pakistan box, then
# smoothly samples it at each point's exact lat/lon. Nearby points
# end up close in value because they sample nearby parts of the
# same coarse field — this is what produces organic-looking weather
# patterns instead of speckled random dots.
# ═══════════════════════════════════════════════════════
def make_smooth_field(lats, lons, coarse_n, seed):
    rng = np.random.RandomState(seed)
    coarse_lat = np.linspace(LAT_MIN, LAT_MAX, coarse_n)
    coarse_lon = np.linspace(LON_MIN, LON_MAX, coarse_n)
    coarse_vals = rng.normal(0, 1, size=(coarse_n, coarse_n))

    interpolator = RegularGridInterpolator(
        (coarse_lat, coarse_lon), coarse_vals,
        bounds_error=False, fill_value=None
    )
    pts = np.column_stack([lats, lons])
    field = interpolator(pts)
    field = (field - field.mean()) / (field.std() + 1e-9)
    return field


# ═══════════════════════════════════════════════════════
# GENERATE WEATHER DATA — ONE SOURCE, ONE HOUR
# Diurnal cycle: temp peaks mid-afternoon, humidity peaks at dawn,
# wind peaks in the afternoon — layered on top of the smooth spatial
# field so each hour still has organic-looking patches, not just a
# uniform shift.
# ═══════════════════════════════════════════════════════
def generate_hour_data(lats, lons, source, forecast_date, hour):
    n           = NUM_POINTS
    temp_offset = source["temp_offset"]
    seed_base   = source["source_id"] * 1000 + hour   # unique pattern per source AND hour

    temp_field = make_smooth_field(lats, lons, coarse_n=12, seed=seed_base + 1)
    hum_field  = make_smooth_field(lats, lons, coarse_n=10, seed=seed_base + 2)
    wind_field = make_smooth_field(lats, lons, coarse_n=14, seed=seed_base + 3)
    pres_field = make_smooth_field(lats, lons, coarse_n=10, seed=seed_base + 4)

    # Diurnal temperature cycle — peaks ~15:00, lowest ~03:00-06:00
    hour_temp_mod = 4 * np.sin(np.pi * (hour - 3) / 12)
    base_temp = 45 - (lats * 0.8)
    avgtemp = base_temp + temp_offset + hour_temp_mod + (temp_field * 3.0)
    mintemp = avgtemp - np.random.uniform(2, 5, n)
    maxtemp = avgtemp + np.random.uniform(2, 5, n)

    # Diurnal humidity cycle — inverse of temperature
    hour_hum_mod = -8 * np.sin(np.pi * (hour - 3) / 12)
    avghum = 55 + (70 - lats) * 0.5 + hour_hum_mod + (hum_field * 10.0)
    avghum = np.clip(avghum, 10, 95)

    # Wind — stronger in the afternoon
    hour_wind_mod = 1.5 * np.sin(np.pi * hour / 18)
    avgwind = np.abs(3 + abs(temp_offset) + hour_wind_mod + (wind_field * 2.0))

    pressure = 1013 - (lats * 0.5) + (pres_field * 2.5)

    documents = []
    for i in range(n):
        documents.append({
            "source_id"    : source["source_id"],
            "source_name"  : source["name"],
            "model"        : source["model"],
            "forecast_date": forecast_date,
            "forecast_hour": hour,
            "lat"          : round(float(lats[i]), 4),
            "lon"          : round(float(lons[i]), 4),
            "avgtemp"      : round(float(avgtemp[i]), 2),
            "mintemp"      : round(float(mintemp[i]), 2),
            "maxtemp"      : round(float(maxtemp[i]), 2),
            "avghum"       : round(float(avghum[i]),  2),
            "avgwind"      : round(float(avgwind[i]), 2),
            "pressure"     : round(float(pressure[i]),2),
            "created_at"   : datetime.now().isoformat()
        })
    return documents


# ═══════════════════════════════════════════════════════
# ONE THREAD'S JOB — handles one source, all 8 hours,
# saves into that source's own MongoDB collection
# ═══════════════════════════════════════════════════════
def fetch_and_save(source, lats, lons, forecast_date, db, results, lock):
    name        = source["name"]
    thread_name = f"Thread-{source['source_id']}-{name}"
    print(f"{thread_name} - started")
    start = time.time()

    try:
        collection = db[name]
        collection.delete_many({})

        total_docs = 0
        for hour in HOURS_PER_DAY:
            try:
                documents = generate_hour_data(lats, lons, source, forecast_date, hour)
                collection.insert_many(documents, ordered=False)
                total_docs += len(documents)
                print(f"  {thread_name} - hour {hour:02d}:00 -> {len(documents):,} docs saved")
            except Exception as hour_err:
                log_error(name, "hour_save", type(hour_err).__name__, str(hour_err), extra={"hour": hour})
                print(f"  {thread_name} - hour {hour:02d}:00 FAILED: {hour_err}")

        elapsed = round(time.time() - start, 2)
        log_success(name, "fetch_and_save", records=total_docs,
                    extra={"hours": len(HOURS_PER_DAY), "elapsed": elapsed})
        print(f"{thread_name} - DONE in {elapsed}s - {total_docs:,} total docs in '{name}' collection")

        with lock:
            results.append({
                "source_id": source["source_id"], "source_name": name, "model": source["model"],
                "records": total_docs, "hours": len(HOURS_PER_DAY), "time_sec": elapsed, "status": "success"
            })

    except Exception as e:
        print(f"{thread_name} - ERROR: {e}")
        log_error(name, "fetch_and_save", type(e).__name__, str(e))
        with lock:
            results.append({"source_id": source["source_id"], "source_name": name, "status": "failed", "error": str(e)})


# ═══════════════════════════════════════════════════════
# RUN ALL 10 THREADS SIMULTANEOUSLY
# ═══════════════════════════════════════════════════════
def run_multithreaded(db, lats, lons, forecast_date):
    threads = []
    results = []
    lock    = threading.Lock()

    print(f"\nStarting {NUM_THREADS} threads simultaneously...")
    overall_start = time.time()

    for source in SOURCES:
        t = threading.Thread(target=fetch_and_save, args=(source, lats, lons, forecast_date, db, results, lock))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    elapsed = round(time.time() - overall_start, 2)
    print(f"\nAll {NUM_THREADS} threads done in {elapsed}s")
    return results, elapsed


# ═══════════════════════════════════════════════════════
# PRINT SUMMARY
# ═══════════════════════════════════════════════════════
def print_summary(results, total_time):
    print("\n" + "="*65)
    print("SUMMARY")
    print("="*65)

    success = [r for r in results if r["status"] == "success"]
    failed  = [r for r in results if r["status"] == "failed"]
    total_docs = sum(r.get("records", 0) for r in success)

    print(f"Sources OK      : {len(success)}/{len(results)}")
    print(f"Total docs saved: {total_docs:,}  ({NUM_POINTS:,} points x {len(HOURS_PER_DAY)} hours x {len(success)} sources)")
    print(f"Time taken      : {total_time}s (multithreaded)")

    print(f"\n{'Collection':<25} {'Model':<8} {'Docs':<12} {'Hours':<8} {'Time(s)':<10} Status")
    print("-"*70)
    for r in sorted(results, key=lambda x: x["source_id"]):
        if r["status"] == "success":
            print(f"{r['source_name']:<25} {r['model']:<8} {r['records']:<12,} {r['hours']:<8} {r['time_sec']:<10} OK")
        else:
            print(f"{r['source_name']:<25} {'?':<8} {'0':<12} {'?':<8} {'?':<10} FAILED: {r.get('error','')}")

    if failed:
        print(f"\n{len(failed)} source(s) failed - check MongoDB pipeline_errors collection")
    print("="*65)


# ═══════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════
def main():
    print("="*65)
    print("WEATHER PIPELINE - FINAL VERSION")
    print(f"{len(SOURCES)} sources x {len(HOURS_PER_DAY)} hours x {NUM_POINTS:,} points")
    print("Each source -> own MongoDB collection. Spatially-smooth data.")
    print("="*65)

    client, db = connect_mongodb()

    print(f"\nGenerating {NUM_POINTS:,} grid points over Pakistan...")
    lats, lons = generate_grid_points()

    forecast_date = datetime.now().strftime("%Y-%m-%d")
    print(f"Forecast date: {forecast_date}")
    print(f"Hours: {HOURS_PER_DAY}")

    results, total_time = run_multithreaded(db, lats, lons, forecast_date)
    print_summary(results, total_time)

    print("\nVerifying MongoDB collections:")
    for name in sorted(db.list_collection_names()):
        count = db[name].count_documents({})
        print(f"  {name}: {count:,} documents")

    client.close()
    print("\nPipeline complete. Run interpolate.py next (one time only).")


if __name__ == "__main__":
    main()