import os
# ═══════════════════════════════════════════════════════
# fetch_real_weather.py
# Pulls REAL weather data from Open-Meteo (free, no API key)
# for a grid of points across Pakistan, and stores it in
# MongoDB — replacing your old fake/random data generator.
#
# Run this on a schedule (every 1 hour) to keep data fresh.
# See bottom of file for how to schedule it.
# ═══════════════════════════════════════════════════════
import requests
import pymongo
import numpy as np
import time
from datetime import datetime, timezone

# ═══════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════
MONGO_URL       = os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME         = "weather_db"
COLLECTION_NAME = "live_weather"          # NEW collection — separate from old fake data

# Pakistan bounding box — same as your other scripts
LAT_MIN, LAT_MAX = 23.0, 37.0
LON_MIN, LON_MAX = 60.0, 77.0

# How many grid points to fetch. Open-Meteo allows up to ~100
# coordinates per single API call, so we batch requests.
# NOTE: 8000 individual points would mean 80 API calls — fine for
# an hourly job, but if you hit rate limits, reduce GRID_POINTS.
GRID_POINTS_LAT = 40                       # 40 x 40 = 1600 points (good balance of detail vs. speed)
GRID_POINTS_LON = 40

BATCH_SIZE = 100                           # points per API call (Open-Meteo limit-friendly)
API_URL = "https://api.open-meteo.com/v1/forecast"

VARIABLES = "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m"


# ═══════════════════════════════════════════════════════
# BUILD THE GRID OF POINTS TO FETCH
# ═══════════════════════════════════════════════════════
def build_grid():
    """Even grid across Pakistan — more organized than random scatter,
    which also makes interpolation cleaner later."""
    lats = np.linspace(LAT_MIN, LAT_MAX, GRID_POINTS_LAT)
    lons = np.linspace(LON_MIN, LON_MAX, GRID_POINTS_LON)
    lat_grid, lon_grid = np.meshgrid(lats, lons)
    return lat_grid.flatten(), lon_grid.flatten()


# ═══════════════════════════════════════════════════════
# FETCH ONE BATCH FROM OPEN-METEO
# Open-Meteo supports comma-separated lat/lon lists in ONE
# request, returning a LIST of results (same order as input).
# ═══════════════════════════════════════════════════════
def fetch_batch(lats, lons, retries=3):
    lat_str = ",".join(str(round(l, 4)) for l in lats)
    lon_str = ",".join(str(round(l, 4)) for l in lons)

    params = {
        "latitude": lat_str,
        "longitude": lon_str,
        "current": VARIABLES,
        "timezone": "auto",
    }

    for attempt in range(retries):
        try:
            resp = requests.get(API_URL, params=params, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                # Single point returns a dict, multiple points return a list —
                # normalize to a list either way.
                if isinstance(data, dict):
                    data = [data]
                return data
            else:
                print(f"    API returned {resp.status_code}, retrying ({attempt+1}/{retries})...")
        except requests.exceptions.RequestException as e:
            print(f"    Request failed: {e}, retrying ({attempt+1}/{retries})...")
        time.sleep(2)                      # wait before retry — avoids hammering the API

    return None                            # all retries failed


# ═══════════════════════════════════════════════════════
# MAIN — fetch everything and store in MongoDB
# ═══════════════════════════════════════════════════════
def main():
    print("Fetching real-time weather data from Open-Meteo...")

    lats, lons = build_grid()
    total_points = len(lats)
    print(f"Grid built: {total_points} points across Pakistan")

    try:
        client = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        client.server_info()               # forces a connection check now, not later
    except Exception as e:
        print(f"ERROR — could not connect to MongoDB: {e}")
        print("Fix: make sure MongoDB is running (mongod), then re-run this script.")
        return                             # stop here — no point continuing without a DB

    db = client[DB_NAME]
    collection = db[COLLECTION_NAME]

    fetched_at = datetime.now(timezone.utc)
    all_documents = []
    failed_batches = 0

    num_batches = (total_points + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, total_points, BATCH_SIZE):
        batch_num = i // BATCH_SIZE + 1
        batch_lats = lats[i:i + BATCH_SIZE]
        batch_lons = lons[i:i + BATCH_SIZE]

        print(f"  Batch {batch_num}/{num_batches} ({len(batch_lats)} points)...", end=" ")

        results = fetch_batch(batch_lats, batch_lons)

        if results is None:
            print("FAILED — skipping this batch")
            failed_batches += 1
            continue

        for j, point_data in enumerate(results):
            current = point_data.get("current", {})
            if not current:
                continue                   # skip points with no data instead of crashing

            all_documents.append({
                "source"        : "open_meteo",
                "lat"           : round(float(batch_lats[j]), 4),
                "lon"           : round(float(batch_lons[j]), 4),
                "temperature"   : current.get("temperature_2m"),
                "humidity"      : current.get("relative_humidity_2m"),
                "wind_speed"    : current.get("wind_speed_10m"),
                "wind_dir_deg"  : current.get("wind_direction_10m"),
                "fetched_at"    : fetched_at,
            })

        print(f"ok ({len(results)} points)")
        time.sleep(0.5)                    # be polite to the free API — avoid rate-limit bans

    if not all_documents:
        print("\nERROR — no data was fetched. Nothing saved. Check your internet connection.")
        client.close()
        return

    # ── Save to MongoDB ───────────────────────────────
    # Strategy: keep only the LATEST snapshot live for the map to use,
    # but don't delete history — mark old docs instead, so you can
    # later build a "weather history" feature if needed.
    collection.insert_many(all_documents, ordered=False)

    print(f"\nSaved {len(all_documents)} points to MongoDB ({DB_NAME}.{COLLECTION_NAME})")
    if failed_batches:
        print(f"WARNING — {failed_batches}/{num_batches} batches failed and were skipped.")
        print("The map will still work using the successful batches, but coverage may have small gaps.")

    # ── Clean up old data so the collection doesn't grow forever ──
    # Keep only the last 24 hours of snapshots.
    cutoff = fetched_at.timestamp() - (24 * 60 * 60)
    cutoff_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc)
    deleted = collection.delete_many({"fetched_at": {"$lt": cutoff_dt}})
    if deleted.deleted_count:
        print(f"Cleaned up {deleted.deleted_count} old records (older than 24h)")

    client.close()
    print("Done.")


if __name__ == "__main__":
    main()

# ═══════════════════════════════════════════════════════
# HOW TO SCHEDULE THIS (run automatically every hour)
# ═══════════════════════════════════════════════════════
#
# OPTION A — Windows Task Scheduler (recommended, no extra code):
#   1. Open Task Scheduler -> Create Basic Task
#   2. Trigger: Daily, repeat every 1 hour
#   3. Action: Start a program
#        Program: C:\Path\To\python.exe
#        Arguments: fetch_real_weather.py
#        Start in: the project directory
#
# OPTION B — simple Python loop (run this in its own terminal,
# leave it running in the background):
#
#   import time
#   while True:
#       main()
#       time.sleep(3600)   # 3600 seconds = 1 hour
#