"""
Unit tests for the core interpolation and validation math used across
experiments/ and interpolate.py. These test pure functions only — no
MongoDB, no CSV file, no network — so they run anywhere, including CI.
"""
import numpy as np
import pytest


# ── IDW (Inverse Distance Weighting) ──────────────────────────────
def idw_predict(coords, values, t_lon, t_lat, power=2):
    d = np.sqrt((coords[:, 0] - t_lon) ** 2 + (coords[:, 1] - t_lat) ** 2)
    if np.any(d == 0):
        return values[np.argmin(d)]
    w = 1 / (d ** power)
    return float(np.sum(w * values) / np.sum(w))


def test_idw_exact_match_at_known_point():
    """If the target point exactly matches a known station, IDW should
    return that station's value exactly (not an average)."""
    coords = np.array([[0.0, 0.0], [1.0, 1.0], [2.0, 0.0]])
    values = np.array([10.0, 20.0, 30.0])
    result = idw_predict(coords, values, 0.0, 0.0)
    assert result == 10.0


def test_idw_equidistant_points_average_equally():
    """Two points equidistant from the target should be weighted equally."""
    coords = np.array([[-1.0, 0.0], [1.0, 0.0]])
    values = np.array([10.0, 20.0])
    result = idw_predict(coords, values, 0.0, 0.0)
    assert result == pytest.approx(15.0)


def test_idw_closer_point_dominates():
    """A much closer station should pull the prediction toward its value."""
    coords = np.array([[0.1, 0.0], [10.0, 0.0]])
    values = np.array([10.0, 100.0])
    result = idw_predict(coords, values, 0.0, 0.0)
    assert result < 20.0  # dominated by the near station (10.0), not the average (55.0)


# ── Normalization ──────────────────────────────────────────────────
def normalize_mae(mae, var_min, var_max):
    range_val = var_max - var_min
    if range_val == 0:
        return 0.0
    return round(mae / range_val, 4)


def test_normalize_mae_basic():
    # error of 2 within a range of 10 (0 to 10) = 0.2
    assert normalize_mae(2, 0, 10) == 0.2


def test_normalize_mae_zero_range_returns_zero():
    """Guards against division by zero when a variable has no spread."""
    assert normalize_mae(5, 10, 10) == 0.0


def test_normalize_mae_zero_error():
    assert normalize_mae(0, 0, 10) == 0.0


def test_normalize_mae_full_range_error():
    assert normalize_mae(10, 0, 10) == 1.0


# ── Outlier detection ────────────────────────────────────────────
def detect_outliers(lons, lats, std_threshold=2):
    lons, lats = np.array(lons), np.array(lats)
    mean_lon, mean_lat = lons.mean(), lats.mean()
    std_lon, std_lat = lons.std(), lats.std()
    mask = (
        (np.abs(lons - mean_lon) < std_threshold * std_lon) &
        (np.abs(lats - mean_lat) < std_threshold * std_lat)
    )
    return mask


def test_outlier_detection_flags_distant_station():
    # 5 clustered stations + 1 far outlier
    lons = [73.0, 73.1, 72.9, 73.05, 72.95, 90.0]
    lats = [33.6, 33.7, 33.5, 33.65, 33.55, 10.0]
    mask = detect_outliers(lons, lats)
    assert mask[-1] == False  # the far point is flagged as outlier
    assert mask[:5].all()      # all clustered points are kept


def test_outlier_detection_no_outliers_in_tight_cluster():
    lons = [73.0, 73.01, 72.99, 73.02, 72.98]
    lats = [33.6, 33.61, 33.59, 33.62, 33.58]
    mask = detect_outliers(lons, lats)
    assert mask.all()
