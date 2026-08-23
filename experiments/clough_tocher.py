import pandas as pd
import numpy as np
from scipy.interpolate import CloughTocher2DInterpolator, NearestNDInterpolator

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
# CLOUGH-TOCHER METHOD (with Nearest Neighbor fallback)
# ═══════════════════════════════════════════════
def ct_predict(coords, values, t_lon, t_lat):
    machine = CloughTocher2DInterpolator(coords, values)
    predicted = float(machine(t_lon, t_lat))

    if np.isnan(predicted):                            # target is outside the triangulated boundary
        nearest = NearestNDInterpolator(coords, values)
        predicted = float(nearest([[t_lon, t_lat]]))
        used_fallback = True
    else:
        used_fallback = False

    return predicted, used_fallback

# ═══════════════════════════════════════════════
# LOOP THROUGH EACH STATION — CLOUGH-TOCHER ONLY
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

        predicted, used_fallback = ct_predict(coords, others['avgtemp'].values, t_lon, t_lat)
        actual = float(actual_row['avgtemp'])

        rows.append({
            'date': date,
            'actual': round(actual, 2),
            'predicted': round(predicted, 2),
            'fallback_used': 'YES' if used_fallback else 'no'
        })

    result_df = pd.DataFrame(rows)

    print(f"\n--- avgtemp — Actual vs Predicted (Clough-Tocher, first 5 rows) ---")
    print(result_df.head(5).to_string(index=False))

    input("\nPress Enter to continue to next station...")

print("\nClough-Tocher finished for all stations.")