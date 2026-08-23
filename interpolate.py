# ═══════════════════════════════════════════════════════
# interpolate.py — FINAL CONSOLIDATED VERSION
#
# Reads directly from each source's own MongoDB collection
# (matches mongo.py's per-source, per-hour structure). Generates
# one seamless image per zoom level, per hour, per variable, per
# source. Turbo colormap + gaussian smoothing + edge feathering
# (kept from the visual-style fixes already agreed on).
# ═══════════════════════════════════════════════════════
import pymongo
import pandas as pd
import numpy as np
from scipy.interpolate import griddata
from scipy.ndimage import gaussian_filter
import matplotlib.pyplot as plt
from PIL import Image
import os
import time
from error_logger import log_error, log_success

MONGO_URL   = os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME     = "weather_db"
MAPS_DIR    = "maps"
TILES_DIR   = "tiles"

# Same free-tier auto-detect as demo_generator.py — smaller grid/render
# size means less CPU/RAM per image, so startup stays fast on Render.
IS_RENDER   = os.getenv("RENDER") == "true"
GRID_SIZE   = int(os.getenv("INTERP_GRID_SIZE", "96" if IS_RENDER else "512"))
RENDER_SIZE = int(os.getenv("INTERP_RENDER_SIZE", "192" if IS_RENDER else "1024"))
TILE_SIZE   = 256

LAT_MIN, LAT_MAX = 23.0, 37.0
LON_MIN, LON_MAX = 60.0, 77.0

VARIABLES = ['avgtemp', 'mintemp', 'maxtemp', 'avghum', 'avgwind']
NON_SOURCE_COLLECTIONS = {'pipeline_errors'}

COLORMAPS = {v: 'turbo' for v in VARIABLES}

SMOOTH_SIGMA      = 4.5
EDGE_FEATHER_FRAC = 0.05

NORMALIZATION_RANGES = {
    'avgtemp': (12, 34),
    'mintemp': (5,  28),
    'maxtemp': (14, 40),
    'avghum' : (40, 92),
    'avgwind': (0,  9),
}

ZOOM_LEVELS = {4: 1} if IS_RENDER else {4: 1, 6: 2, 8: 4}


def get_source_collections(db):
    names = db.list_collection_names()
    return sorted([n for n in names if n not in NON_SOURCE_COLLECTIONS])


def get_hours_for_source(collection):
    return sorted(collection.distinct("forecast_hour"))


def interpolate_variable(df, variable, source_name):
    try:
        lons, lats, values = df['lon'].values, df['lat'].values, df[variable].values
        lon_grid = np.linspace(LON_MIN, LON_MAX, GRID_SIZE)
        lat_grid = np.linspace(LAT_MAX, LAT_MIN, GRID_SIZE)
        LON_MESH, LAT_MESH = np.meshgrid(lon_grid, lat_grid)

        # 'cubic' is more memory/CPU intensive; use the lighter 'linear'
        # method on Render's free tier to stay within the 512MB limit.
        interp_method = 'linear' if IS_RENDER else 'cubic'
        predicted = griddata((lons, lats), values, (LON_MESH, LAT_MESH), method=interp_method)

        if np.any(np.isnan(predicted)):
            nearest = griddata((lons, lats), values, (LON_MESH, LAT_MESH), method='nearest')
            predicted[np.isnan(predicted)] = nearest[np.isnan(predicted)]

        predicted = np.clip(predicted, float(values.min()), float(values.max()))
        predicted = gaussian_filter(predicted, sigma=SMOOTH_SIGMA)
        return predicted

    except Exception as e:
        log_error(source_name, "interpolation", type(e).__name__, str(e), variable)
        return None


def apply_edge_feather(rgba_array, feather_frac=EDGE_FEATHER_FRAC):
    h, w = rgba_array.shape[:2]
    fx, fy = max(1, int(w * feather_frac)), max(1, int(h * feather_frac))
    ramp_x = np.ones(w, dtype=np.float32); ramp_x[:fx] = np.linspace(0,1,fx); ramp_x[-fx:] = np.linspace(1,0,fx)
    ramp_y = np.ones(h, dtype=np.float32); ramp_y[:fy] = np.linspace(0,1,fy); ramp_y[-fy:] = np.linspace(1,0,fy)
    mask = np.outer(ramp_y, ramp_x)
    alpha = rgba_array[:, :, 3].astype(np.float32)
    rgba_array[:, :, 3] = (alpha * mask).astype(np.uint8)
    return rgba_array


def grid_to_image(grid, variable, source_name):
    try:
        fixed_min, fixed_max = NORMALIZATION_RANGES.get(variable, (float(grid.min()), float(grid.max())))
        normalized = np.clip((grid - fixed_min) / (fixed_max - fixed_min), 0, 1)
        colormap = plt.get_cmap(COLORMAPS[variable])
        colored = (colormap(normalized) * 255).astype(np.uint8)
        colored = apply_edge_feather(colored)
        img = Image.fromarray(colored, mode='RGBA').resize((RENDER_SIZE, RENDER_SIZE), Image.LANCZOS)
        return img
    except Exception as e:
        log_error(source_name, "image_generation", type(e).__name__, str(e), variable)
        return None


def create_zoom_images(img, source_name, variable, hour):
    zoom_dir = os.path.join(TILES_DIR, source_name, variable, str(hour))
    os.makedirs(zoom_dir, exist_ok=True)
    saved = {}
    for zoom, tiles_per_side in ZOOM_LEVELS.items():
        try:
            zoom_px = tiles_per_side * TILE_SIZE
            zoom_img = img.resize((zoom_px, zoom_px), Image.LANCZOS)
            zoom_path = os.path.join(zoom_dir, f"z{zoom}.png")
            zoom_img.save(zoom_path)
            saved[zoom] = zoom_px
        except Exception as e:
            log_error(source_name, "zoom_image_generation", type(e).__name__, str(e), variable, {"zoom": zoom, "hour": hour})
    return saved


def process_source_hour(collection, source_name, hour, stats, min_hour):
    docs = list(collection.find({"forecast_hour": hour}, {"_id": 0}))
    if len(docs) < 10:
        log_error(source_name, "data_check", "InsufficientDataError", f"Only {len(docs)} docs for hour {hour}", extra={"hour": hour})
        print(f"    hour {hour:02d}:00 - SKIPPED (only {len(docs)} points)")
        return

    df = pd.DataFrame(docs)
    required = ['lat', 'lon'] + VARIABLES
    missing = [c for c in required if c not in df.columns]
    if missing:
        log_error(source_name, "data_validation", "MissingColumns", f"Missing: {missing}", extra={"hour": hour})
        print(f"    hour {hour:02d}:00 - SKIPPED (missing columns: {missing})")
        return

    for variable in VARIABLES:
        grid = interpolate_variable(df, variable, source_name)
        if grid is None:
            stats['failed'] += 1
            continue
        img = grid_to_image(grid, variable, source_name)
        if img is None:
            stats['failed'] += 1
            continue

        if hour == min_hour:
            try:
                map_path = os.path.join(MAPS_DIR, f"{source_name}_{variable}.png")
                img.resize((TILE_SIZE, TILE_SIZE), Image.LANCZOS).save(map_path)
            except Exception as e:
                log_error(source_name, "map_save", type(e).__name__, str(e), variable)

        zoom_results = create_zoom_images(img, source_name, variable, hour)
        stats['maps'] += 1
        stats['tiles'] += len(zoom_results)

    log_success(source_name, "process_source_hour", extra={"hour": hour, "points": len(docs)})


def process_all():
    os.makedirs(MAPS_DIR, exist_ok=True)
    os.makedirs(TILES_DIR, exist_ok=True)

    try:
        client = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        client.server_info()
    except Exception as e:
        log_error("interpolate", "startup", "MongoConnectionError", str(e))
        print(f"ERROR - could not connect to MongoDB: {e}")
        return

    db = client[DB_NAME]
    sources = get_source_collections(db)

    if not sources:
        log_error("interpolate", "startup", "NoSourcesError", "No source collections found in weather_db.")
        print("ERROR - no source collections found. Run mongo.py first.")
        client.close()
        return

    print(f"Found {len(sources)} source collections: {sources}")
    print(f"Grid size: {GRID_SIZE}x{GRID_SIZE} | Colormap: turbo | Smoothing sigma={SMOOTH_SIGMA}")
    print(f"Zoom levels: {list(ZOOM_LEVELS.keys())}")
    print("Normalization ranges:")
    for v, (lo, hi) in NORMALIZATION_RANGES.items():
        print(f"  {v}: {lo} to {hi}")
    print("="*60)

    total_start = time.time()
    stats = {'maps': 0, 'tiles': 0, 'failed': 0}

    for source_name in sources:
        collection = db[source_name]
        hours = get_hours_for_source(collection)

        if not hours:
            log_error(source_name, "data_check", "NoHoursError", "No forecast_hour values found.")
            print(f"\n{source_name} - SKIPPED (no data)")
            continue

        print(f"\nProcessing: {source_name}  ({len(hours)} hour-slots: {hours})")
        print("-"*40)
        source_start = time.time()

        for hour in hours:
            hour_start = time.time()
            process_source_hour(collection, source_name, hour, stats, min(hours))
            print(f"    hour {hour:02d}:00 done in {round(time.time()-hour_start,2)}s")

        print(f"  Source done in {round(time.time()-source_start,2)}s")

    total_elapsed = round(time.time() - total_start, 2)
    client.close()

    print("\n" + "="*60)
    print("ALL DONE!")
    print(f"Total time  : {total_elapsed}s")
    print(f"Maps saved  : {stats['maps']}")
    print(f"Tile sets   : {stats['tiles']}")
    if stats['failed']:
        print(f"Failed items: {stats['failed']} (check /errors)")
    print(f"Tiles organized as: tiles/{{source}}/{{variable}}/{{hour}}/z{{zoom}}.png")
    print("="*60)


if __name__ == "__main__":
    process_all()