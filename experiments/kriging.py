import pandas as pd
import numpy as np
from pykrige.ok import OrdinaryKriging

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
# KRIGING METHOD — now returns variance too
# ═══════════════════════════════════════════════
def kriging_predict(coords, values, t_lon, t_lat):
    ok = OrdinaryKriging(coords[:,0], coords[:,1], values,
                          variogram_model='linear', verbose=False, enable_plotting=False)
    predicted, variance = ok.execute('points', [t_lon], [t_lat])
    return float(predicted[0]), float(variance[0])          # ★ return both now

# ═══════════════════════════════════════════════
# LOOP THROUGH EACH STATION — KRIGING ONLY
# ═══════════════════════════════════════════════
for station in station_order:

    other_ids = [s for s in station_order if s != station]

    print("\n" + "=" * 60)
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

        predicted, variance = kriging_predict(coords, others['avgtemp'].values, t_lon, t_lat)
        actual = float(actual_row['avgtemp'])
        mae = abs(actual - predicted)

        rows.append({
            'date': date,
            'actual': round(actual, 2),
            'predicted': round(predicted, 2),
            'mae': round(mae, 2),
            'variance': round(variance, 3),                  # ★ uncertainty shown here
            'std_dev': round(np.sqrt(variance), 3)            # ★ easier-to-read version (± range)
        })

    result_df = pd.DataFrame(rows)

    print(f"\n--- avgtemp — Actual vs Predicted with Uncertainty (Kriging, first 5 rows) ---")
    print(result_df.head(5).to_string(index=False))

    input("\nPress Enter to continue to next station...")

print("\nKriging finished for all stations.")