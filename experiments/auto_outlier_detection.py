import pandas as pd
import numpy as np


# Auto-select best variogram model for Kriging — instead of always using 'linear', automatically test 'linear', 'spherical', 'gaussian', 'exponential' and pick whichever gives the lowest MAE for each variable.
# Auto-flag missing/incomplete days — automatically detect and report which dates had incomplete station data (instead of silently skipping them), so you know how much data was actually excluded.
# Auto-recommend best method per variable — after running all methods, automatically state "use Kriging for avgtemp, use IDW for avgwind" instead of one method for everything.
# Auto-detect suspicious/impossible values — flag readings like negative wind speed or humidity >100%, before they even enter interpolation.
# ═══════════════════════════════════════════════
# AI AUTOMATION — automatically detects outlier stations
# using basic statistics (distance from the group's center)
# ═══════════════════════════════════════════════
def auto_detect_outliers(df):
    stations = df.groupby('StationID').agg(lat=('lat', 'first'), lon=('lon', 'first')).reset_index()

    mean_lon, mean_lat = stations['lon'].mean(), stations['lat'].mean()   # average location of all stations
    std_lon, std_lat   = stations['lon'].std(), stations['lat'].std()      # how spread out they normally are

    # flag anything beyond 2 standard deviations from the center
    mask = (
        (abs(stations['lon'] - mean_lon) < 2 * std_lon) &
        (abs(stations['lat'] - mean_lat) < 2 * std_lat)
    )

    valid_ids   = stations[mask]['StationID'].tolist()
    outlier_ids = stations[~mask]['StationID'].tolist()
    return valid_ids, outlier_ids


# ═══════════════════════════════════════════════
# LOAD DATA
# ═══════════════════════════════════════════════
df = pd.read_csv("data/isl.csv")

# ═══════════════════════════════════════════════
# RUN AUTOMATION
# ═══════════════════════════════════════════════
valid_ids, outlier_ids = auto_detect_outliers(df)

print("=" * 50)
print("AI AUTOMATION — OUTLIER DETECTION")
print("=" * 50)
print(f"Total stations checked : {df['StationID'].nunique()}")
print(f"Valid stations         : {valid_ids}")
print(f"Outlier stations found : {outlier_ids}")

if outlier_ids:
    print(f"\nThese station(s) sit unusually far from the group's center")
    print(f"(beyond 2 standard deviations of average location) and were")
    print(f"automatically flagged for review.")
else:
    print(f"\nNo outliers detected — all stations are within a normal spread.")