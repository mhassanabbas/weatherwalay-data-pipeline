import pandas as pd                                                # tables/DataFrames
import numpy as np                                                  # arrays and math
import matplotlib.pyplot as plt                                     # charts
from scipy.interpolate import RBFInterpolator                       # RBF method
from scipy.interpolate import CloughTocher2DInterpolator, NearestNDInterpolator  # CT method + fallback
from pykrige.ok import OrdinaryKriging                               # Kriging method

# ═══════════════════════════════════════════════════════════════
# AI AUTOMATION — AUTO DETECT OUTLIER STATIONS
# Instead of manually deciding which station is "too far away",
# the program calculates this automatically using basic statistics
# ═══════════════════════════════════════════════════════════════
def auto_detect_outliers(df):
    stations = df.groupby('StationID').agg(
        lat=('lat', 'first'),
        lon=('lon', 'first')
    ).reset_index()

    mean_lon, mean_lat = stations['lon'].mean(), stations['lat'].mean()   # average location of all stations
    std_lon,  std_lat  = stations['lon'].std(),  stations['lat'].std()     # how spread out stations normally are

    # keep stations within 2 standard deviations of the center — flag anything beyond that
    mask = (
        (abs(stations['lon'] - mean_lon) < 2 * std_lon) &
        (abs(stations['lat'] - mean_lat) < 2 * std_lat)
    )

    valid_ids   = stations[mask]['StationID'].tolist()
    outlier_ids = stations[~mask]['StationID'].tolist()
    return valid_ids, outlier_ids


# ═══════════════════════════════════════════════════════════════
# NORMALIZATION — scales MAE to a fair 0-1 range per variable
# formula: normalized = MAE / (max_value - min_value)
# ═══════════════════════════════════════════════════════════════
def normalize_mae(mae, var_min, var_max):
    range_val = var_max - var_min
    if range_val == 0:
        return 0.0
    return round(mae / range_val, 4)


# ═══════════════════════════════════════════════════════════════
# LOAD, CLEAN, AUTO-FILTER
# ═══════════════════════════════════════════════════════════════
df = pd.read_csv("data/isl.csv")

valid_ids, outlier_ids = auto_detect_outliers(df)                   # ★ AI automation step
print(f"Auto-detected outlier stations: {outlier_ids}")
df = df[df['StationID'].isin(valid_ids)]                             # keep only the valid ones

clean = df[['StationID', 'lat', 'lon', 'date',
            'avgtemp', 'mintemp', 'maxtemp',
            'avghum', 'avgwind']].copy()
clean = clean.dropna(subset=['avgtemp', 'mintemp', 'maxtemp', 'avghum', 'avgwind'])

daily = clean.groupby(['StationID', 'date']).agg(
    lat     = ('lat',     'first'),
    lon     = ('lon',     'first'),
    avgtemp = ('avgtemp', 'mean'),
    mintemp = ('mintemp', 'mean'),
    maxtemp = ('maxtemp', 'mean'),
    avghum  = ('avghum',  'mean'),
    avgwind = ('avgwind', 'mean')
).reset_index()

variables     = ['avgtemp', 'mintemp', 'maxtemp', 'avghum', 'avgwind']
station_order = [int(s) for s in sorted(daily['StationID'].unique())]

# ── calculate each variable's min/max ONCE, upfront, for normalization ──
var_ranges = {var: {'min': daily[var].min(), 'max': daily[var].max()} for var in variables}
print("\nVariable ranges (used for normalization):")
for var, r in var_ranges.items():
    print(f"  {var}: {r['min']:.2f} to {r['max']:.2f} (range = {r['max']-r['min']:.2f})")


# ═══════════════════════════════════════════════════════════════
# 4 INTERPOLATION METHOD FUNCTIONS
# ═══════════════════════════════════════════════════════════════
def idw_predict(coords, values, t_lon, t_lat, power=2):
    distances = np.sqrt((coords[:, 0] - t_lon)**2 + (coords[:, 1] - t_lat)**2)
    if np.any(distances == 0):
        return values[np.argmin(distances)]
    weights = 1 / (distances ** power)
    return float(np.sum(weights * values) / np.sum(weights))

def rbf_predict(coords, values, t_lon, t_lat):
    machine = RBFInterpolator(coords, values)
    return float(machine([[t_lon, t_lat]])[0])

def kriging_predict(coords, values, t_lon, t_lat):
    ok = OrdinaryKriging(coords[:, 0], coords[:, 1], values,
                          variogram_model='linear', verbose=False, enable_plotting=False)
    predicted, variance = ok.execute('points', [t_lon], [t_lat])
    return float(predicted[0])

def ct_predict(coords, values, t_lon, t_lat):
    machine   = CloughTocher2DInterpolator(coords, values)
    predicted = float(machine(t_lon, t_lat))
    if np.isnan(predicted):                                          # fallback if outside convex hull
        nearest   = NearestNDInterpolator(coords, values)
        predicted = float(nearest([[t_lon, t_lat]]))
    return predicted

methods = {'IDW': idw_predict, 'RBF': rbf_predict, 'Kriging': kriging_predict, 'CloughTocher': ct_predict}


# ═══════════════════════════════════════════════════════════════
# LEAVE-ONE-OUT — run for every method, no chain, WITH normalization
# ═══════════════════════════════════════════════════════════════
all_results = {name: [] for name in methods}

for name, func in methods.items():
    print(f"\nRunning {name}...")

    for date in daily['date'].unique():
        day_data = daily[daily['date'] == date].copy()
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
                predicted = func(coords, others[var].values, t_lon, t_lat)
                actual    = float(actual_row[var])
                mae       = abs(actual - predicted)

                norm_mae = normalize_mae(mae, var_ranges[var]['min'], var_ranges[var]['max'])  # ★ normalization

                all_results[name].append({
                    'station'  : station, 'date': date, 'variable': var,
                    'actual'   : round(actual, 2), 'predicted': round(predicted, 2),
                    'mae'      : round(mae, 2), 'norm_mae': norm_mae
                })

    print(f"{name} done!")


# ═══════════════════════════════════════════════════════════════
# COMPARISON TABLE — normalized MAE per method per variable
# ═══════════════════════════════════════════════════════════════
comparison_rows = []
for name, rows in all_results.items():
    df_method = pd.DataFrame(rows)
    for var in variables:
        avg_norm_mae = df_method[df_method['variable'] == var]['norm_mae'].mean()
        comparison_rows.append({'Method': name, 'Variable': var, 'Normalized MAE': round(avg_norm_mae, 4)})

comparison_df = pd.DataFrame(comparison_rows)
pivot = comparison_df.pivot(index='Method', columns='Variable', values='Normalized MAE')
pivot = pivot[variables]
pivot['Overall Normalized MAE'] = pivot.mean(axis=1).round(4)
pivot = pivot.sort_values('Overall Normalized MAE')

print("\n" + "=" * 60)
print("FINAL COMPARISON — ALL METHODS (Normalized MAE)")
print("=" * 60)
print(pivot.to_string())


# ═══════════════════════════════════════════════════════════════
# AI AUTOMATION — AUTO SELECT BEST METHOD, STATION, VARIABLE
# ═══════════════════════════════════════════════════════════════
best_method = pivot['Overall Normalized MAE'].idxmin()                # ★ lowest overall error = winner

best_df = pd.DataFrame(all_results[best_method])                       # use the WINNING method's results for station/variable insights
station_summary = best_df.groupby('station')['norm_mae'].mean()
variable_summary = best_df.groupby('variable')['norm_mae'].mean()

best_station    = station_summary.idxmin()
worst_station   = station_summary.idxmax()
best_variable   = variable_summary.idxmin()
worst_variable  = variable_summary.idxmax()

print("\n" + "=" * 60)
print("AI AUTOMATION — AUTO GENERATED INSIGHTS")
print("=" * 60)
print(f"Outlier stations removed     : {outlier_ids}")
print(f"Best performing method       : {best_method} (Overall Normalized MAE = {pivot.loc[best_method, 'Overall Normalized MAE']})")
print(f"Most predictable station     : {best_station} (Normalized MAE = {round(station_summary[best_station],4)})")
print(f"Least predictable station    : {worst_station} (Normalized MAE = {round(station_summary[worst_station],4)})")
print(f"Easiest variable to predict  : {best_variable} (Normalized MAE = {round(variable_summary[best_variable],4)})")
print(f"Hardest variable to predict  : {worst_variable} (Normalized MAE = {round(variable_summary[worst_variable],4)})")
print("=" * 60)


# ═══════════════════════════════════════════════════════════════
# VISUALIZATION — overall + per-variable comparison
# ═══════════════════════════════════════════════════════════════
fig, axes = plt.subplots(1, 2, figsize=(16, 6))

pivot['Overall Normalized MAE'].plot(
    kind='bar', ax=axes[0],
    color=['green' if m == best_method else 'steelblue' for m in pivot.index],
    edgecolor='black'
)
axes[0].set_title('Overall Normalized MAE — All Methods\n(Green = Auto-Selected Best)')
axes[0].set_ylabel('Normalized MAE (0-1 scale)')
axes[0].tick_params(axis='x', rotation=0)
axes[0].grid(True, alpha=0.3)

x, width = np.arange(len(variables)), 0.2
colors = ['steelblue', 'tomato', 'green', 'purple']
for i, (name, rows) in enumerate(all_results.items()):
    df_method = pd.DataFrame(rows)
    norm_maes = [df_method[df_method['variable'] == var]['norm_mae'].mean() for var in variables]
    axes[1].bar(x + i*width, norm_maes, width, label=name, color=colors[i], alpha=0.8)

axes[1].set_title('Normalized MAE Per Variable — All Methods')
axes[1].set_xticks(x + width*1.5)
axes[1].set_xticklabels(variables, rotation=15)
axes[1].legend()
axes[1].grid(True, alpha=0.3)

plt.tight_layout()
plt.show()