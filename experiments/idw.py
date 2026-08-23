import pandas as pd
import numpy as np

# ═══════════════════════════════════════════════
# LOAD — keep ALL stations, no auto-removal
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
# IDW METHOD
# ═══════════════════════════════════════════════
def idw_predict(coords, values, t_lon, t_lat, power=2):
    d = np.sqrt((coords[:,0]-t_lon)**2 + (coords[:,1]-t_lat)**2)
    if np.any(d == 0):
        return values[np.argmin(d)]
    w = 1 / (d ** power)
    return float(np.sum(w*values) / np.sum(w))

# ═══════════════════════════════════════════════
# TEST JUST THE FIRST STATION
# ═══════════════════════════════════════════════
station = station_order[0]                                    # first station in the list
other_ids = [s for s in station_order if s != station]

print("=" * 60)
print(f"Station Removed         : {station}")
print(f"Predicted Using Stations: {other_ids}")
print("=" * 60)

rows = []
for date in daily['date'].unique():
    day_data = daily[daily['date'] == date]
    if len(day_data) < len(station_order):
        continue

    others = day_data[day_data['StationID'] != station]
    if len(others) < 3:
        continue

    actual_row = day_data[day_data['StationID'] == station].iloc[0]
    coords = np.vstack((others['lon'], others['lat'])).T
    t_lon, t_lat = actual_row['lon'], actual_row['lat']

    predicted = idw_predict(coords, others['avgtemp'].values, t_lon, t_lat)
    actual = float(actual_row['avgtemp'])

    rows.append({'date': date, 'actual': round(actual, 2), 'predicted': round(predicted, 2)})

result_df = pd.DataFrame(rows)

print("\n--- avgtemp — Actual vs Predicted (first 5 rows) ---")
print(result_df.head(5).to_string(index=False))