import pandas as pd
import numpy as np

# ═══════════════════════════════════════════════
# NORMALIZATION FUNCTION
# ═══════════════════════════════════════════════
def normalize_mae(mae, var_min, var_max):
    range_val = var_max - var_min
    if range_val == 0:
        return 0.0
    return round(mae / range_val, 4)

# ═══════════════════════════════════════════════
# IDW METHOD — used here just to generate real MAE values
# ═══════════════════════════════════════════════
def idw_predict(coords, values, t_lon, t_lat, power=2):
    d = np.sqrt((coords[:,0]-t_lon)**2 + (coords[:,1]-t_lat)**2)
    if np.any(d == 0):
        return values[np.argmin(d)]
    w = 1 / (d ** power)
    return float(np.sum(w*values) / np.sum(w))

# ═══════════════════════════════════════════════
# LOAD AND CLEAN — your real data
# ═══════════════════════════════════════════════
df = pd.read_csv("data/isl.csv")
clean = df[['StationID', 'lat', 'lon', 'date',
            'avgtemp', 'mintemp', 'maxtemp', 'avghum', 'avgwind']].copy()
clean = clean.dropna(subset=['avgtemp', 'mintemp', 'maxtemp', 'avghum', 'avgwind'])

daily = clean.groupby(['StationID', 'date']).agg(
    lat=('lat', 'first'), lon=('lon', 'first'),
    avgtemp=('avgtemp', 'mean'), mintemp=('mintemp', 'mean'), maxtemp=('maxtemp', 'mean'),
    avghum=('avghum', 'mean'), avgwind=('avgwind', 'mean')
).reset_index()

variables = ['avgtemp', 'mintemp', 'maxtemp', 'avghum', 'avgwind']
station_order = [int(s) for s in sorted(daily['StationID'].unique())]

# ═══════════════════════════════════════════════
# CALCULATE REAL VARIABLE RANGES FROM YOUR DATA
# ═══════════════════════════════════════════════
var_ranges = {var: {'min': daily[var].min(), 'max': daily[var].max()} for var in variables}

print("=" * 55)
print("YOUR DATA — VARIABLE RANGES")
print("=" * 55)
for var, r in var_ranges.items():
    print(f"{var:10s}: {r['min']:.2f} to {r['max']:.2f}  (range = {r['max']-r['min']:.2f})")

# ═══════════════════════════════════════════════
# RUN LEAVE-ONE-OUT (IDW) TO GET REAL MAE VALUES
# ═══════════════════════════════════════════════
results = []
for date in daily['date'].unique():
    day_data = daily[daily['date'] == date]
    if len(day_data) < len(station_order):
        continue

    for station in station_order:
        others = day_data[day_data['StationID'] != station]
        if len(others) < 3:
            continue

        actual_row = day_data[day_data['StationID'] == station].iloc[0]
        coords = np.vstack((others['lon'], others['lat'])).T
        t_lon, t_lat = actual_row['lon'], actual_row['lat']

        for var in variables:
            predicted = idw_predict(coords, others[var].values, t_lon, t_lat)
            actual = float(actual_row[var])
            mae = abs(actual - predicted)

            # ★ NORMALIZATION applied to each real MAE value
            norm_mae = normalize_mae(mae, var_ranges[var]['min'], var_ranges[var]['max'])

            results.append({
                'station': station, 'date': date, 'variable': var,
                'mae': round(mae, 4), 'norm_mae': norm_mae
            })

results_df = pd.DataFrame(results)

# ═══════════════════════════════════════════════
# SHOW REAL RAW MAE vs NORMALIZED MAE — per variable
# ═══════════════════════════════════════════════
summary = results_df.groupby('variable').agg(
    avg_raw_mae=('mae', 'mean'),
    avg_norm_mae=('norm_mae', 'mean')
).round(4)

summary['range'] = [var_ranges[v]['max'] - var_ranges[v]['min'] for v in summary.index]
summary['percent_error'] = (summary['avg_norm_mae'] * 100).round(2)

print("\n" + "=" * 55)
print("NORMALIZATION RESULTS — YOUR ACTUAL DATA")
print("=" * 55)
print(summary.to_string())

print("\n" + "=" * 55)
print("INTERPRETATION")
print("=" * 55)
easiest = summary['avg_norm_mae'].idxmin()
hardest = summary['avg_norm_mae'].idxmax()
print(f"Easiest to interpolate (lowest % error): {easiest} ({summary.loc[easiest, 'percent_error']}%)")
print(f"Hardest to interpolate (highest % error): {hardest} ({summary.loc[hardest, 'percent_error']}%)")
