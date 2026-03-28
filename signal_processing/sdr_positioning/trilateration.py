from __future__ import annotations

import logging
import math
from enum import Enum

import numpy as np
from scipy.optimize import minimize

from sdr_positioning.models import Measurement

_log = logging.getLogger(__name__)

_MIN_SOURCES = 3


class Environment(Enum):
    OUTDOOR_LOS   = (0.0,  1.0)
    OUTDOOR_URBAN = (5.0,  1.3)
    INDOOR_LIGHT  = (15.0, 2.0)
    INDOOR_DEEP   = (27.0, 3.0)

    def __init__(self, extra_loss_db: float, accuracy_mult: float) -> None:
        self.extra_loss_db  = extra_loss_db
        self.accuracy_mult  = accuracy_mult


# ---------------------------------------------------------------------------
# FSPL primitives — each formula appears exactly once in this module
# ---------------------------------------------------------------------------

def _eirp_dbm(power_w: float, antenna_gain_dbi: float) -> float:
    return 10.0 * math.log10(power_w) + 30.0 + antenna_gain_dbi


def _fspl_db(d_m: float, freq_hz: float) -> float:
    return 20.0 * math.log10(max(d_m, 1.0)) + 20.0 * math.log10(freq_hz) - 147.55


def _rssi_predicted(
    d_m: float,
    power_w: float,
    antenna_gain_dbi: float,
    freq_hz: float,
    env: Environment,
) -> float:
    """Predict received RSSI at distance d_m. FSPL formula — defined here only."""
    return _eirp_dbm(power_w, antenna_gain_dbi) - _fspl_db(d_m, freq_hz) - env.extra_loss_db


def _rssi_to_distance(
    rssi_dbm: float,
    power_w: float,
    antenna_gain_dbi: float,
    freq_hz: float,
    env: Environment,
) -> float:
    """Invert FSPL model: measured RSSI → estimated distance in metres."""
    eirp = _eirp_dbm(power_w, antenna_gain_dbi)
    # Solve: rssi = eirp - (20·log10(d) + 20·log10(f) - 147.55) - env.extra_loss_db
    log_d = (eirp - rssi_dbm - env.extra_loss_db - 20.0 * math.log10(freq_hz) + 147.55) / 20.0
    return max(10.0 ** log_d, 1.0)


# ---------------------------------------------------------------------------
# Environment classification
# ---------------------------------------------------------------------------

def _classify_environment(excess_losses: list[float]) -> Environment:
    """Map median excess loss (dB) to an Environment category."""
    if not excess_losses:
        return Environment.OUTDOOR_LOS
    median_excess = float(np.median(excess_losses))
    if median_excess < 3.0:
        return Environment.OUTDOOR_LOS
    elif median_excess < 12.0:
        return Environment.OUTDOOR_URBAN
    elif median_excess < 22.0:
        return Environment.INDOOR_LIGHT
    else:
        return Environment.INDOOR_DEEP


# ---------------------------------------------------------------------------
# Geometry quality check (GDOP proxy)
# ---------------------------------------------------------------------------

def _geometry_ok(
    tx_lats: list[float],
    tx_lons: list[float],
    rx_lat: float,
    rx_lon: float,
) -> bool:
    """Return False if all transmitters fall within a 90-degree arc (poor geometry)."""
    bearings = []
    for lat, lon in zip(tx_lats, tx_lons):
        dy = lat - rx_lat
        dx = (lon - rx_lon) * math.cos(math.radians(rx_lat))
        bearings.append(math.degrees(math.atan2(dx, dy)) % 360.0)
    bearings.sort()
    gaps = [bearings[i + 1] - bearings[i] for i in range(len(bearings) - 1)]
    gaps.append(360.0 - bearings[-1] + bearings[0])
    return max(gaps) < 270.0


# ---------------------------------------------------------------------------
# Coordinate helpers (ENU projection local to a reference lat/lon)
# ---------------------------------------------------------------------------

def _to_enu(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    px = (lon - lon0) * 111320.0 * math.cos(math.radians(lat0))
    py = (lat - lat0) * 110540.0
    return px, py


def _from_enu(px: float, py: float, lat0: float, lon0: float) -> tuple[float, float]:
    lon = lon0 + px / (111320.0 * math.cos(math.radians(lat0)))
    lat = lat0 + py / 110540.0
    return lat, lon


# ---------------------------------------------------------------------------
# Internal solver
# ---------------------------------------------------------------------------

def _solve(
    measurements: list[Measurement],
    env: Environment,
) -> tuple[float, float, float]:
    """Run Nelder-Mead weighted least-squares solver for a given environment.

    Returns (lat, lon, accuracy_m).
    """
    distances = [
        _rssi_to_distance(m.rssi_dbm, m.power_w, m.antenna_gain_dbi, m.freq_hz, env)
        for m in measurements
    ]
    weights = [
        (0.5 if m.best_effort else 1.0) / max((d * 0.3) ** 2, 1.0)
        for m, d in zip(measurements, distances)
    ]

    lat0 = float(np.mean([m.lat for m in measurements]))
    lon0 = float(np.mean([m.lon for m in measurements]))

    tx_enu = [_to_enu(m.lat, m.lon, lat0, lon0) for m in measurements]

    def cost(xy: np.ndarray) -> float:
        rx_px, rx_py = float(xy[0]), float(xy[1])
        total = 0.0
        for (tx_px, tx_py), d_est, w in zip(tx_enu, distances, weights):
            d_pred = math.sqrt((rx_px - tx_px) ** 2 + (rx_py - tx_py) ** 2)
            total += w * (d_pred - d_est) ** 2
        return total

    w_sum = sum(weights)
    x0 = np.array([
        sum(w * px for (px, _), w in zip(tx_enu, weights)) / w_sum,
        sum(w * py for (_, py), w in zip(tx_enu, weights)) / w_sum,
    ])

    result = minimize(cost, x0, method="Nelder-Mead",
                      options={"maxiter": 2000, "xatol": 1.0, "fatol": 1.0})
    rx_px, rx_py = float(result.x[0]), float(result.x[1])
    lat, lon = _from_enu(rx_px, rx_py, lat0, lon0)

    n = len(measurements)
    residuals = [
        (math.sqrt((rx_px - tx_px) ** 2 + (rx_py - tx_py) ** 2) - d_est) ** 2
        for (tx_px, tx_py), d_est in zip(tx_enu, distances)
    ]
    accuracy_m = math.sqrt(sum(residuals) / n) * env.accuracy_mult

    if not _geometry_ok([m.lat for m in measurements], [m.lon for m in measurements], lat, lon):
        accuracy_m *= 3.0

    return lat, lon, accuracy_m


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def trilaterate(
    measurements: list[Measurement],
    origin: tuple[float, float] | None = None,  # unused currently; reserved for future use
) -> tuple[float, float, float] | None:
    """Estimate receiver position from a list of RF measurements.

    Returns (lat, lon, accuracy_m) or None if fewer than 3 sources are available.
    Never raises.
    """
    try:
        return _trilaterate(measurements)
    except Exception:
        _log.warning("trilaterate() failed", exc_info=True)
        return None


def _best_per_site(measurements: list[Measurement]) -> list[Measurement]:
    """Keep the strongest measurement from each unique transmitter site.

    Multiple transmitters sharing the same physical tower add no new geometric
    information and bias the solver toward that location.  Grouping by site
    (lat/lon rounded to ~100 m) and retaining the highest RSSI measurement
    gives each distinct location exactly one constraint.
    """
    best: dict[tuple[float, float], Measurement] = {}
    for m in measurements:
        key = (round(m.lat, 3), round(m.lon, 3))
        if key not in best or m.rssi_dbm > best[key].rssi_dbm:
            best[key] = m
    return list(best.values())


def _trilaterate(measurements: list[Measurement]) -> tuple[float, float, float] | None:
    measurements = _best_per_site(measurements)
    if len(measurements) < _MIN_SOURCES:
        return None

    # Pass 1: solve under OUTDOOR_LOS assumption for a preliminary position
    lat_pre, lon_pre, acc_pre = _solve(measurements, Environment.OUTDOOR_LOS)

    # Compute excess loss for each source using geometric distances from the preliminary fix.
    # rssi_pred at the true geometric distance minus actual rssi gives the extra attenuation
    # not accounted for by free-space propagation alone.
    excess_losses: list[float] = []
    for m in measurements:
        d_geom = _haversine_m(lat_pre, lon_pre, m.lat, m.lon)
        rssi_pred = _rssi_predicted(d_geom, m.power_w, m.antenna_gain_dbi, m.freq_hz, Environment.OUTDOOR_LOS)
        excess_losses.append(rssi_pred - m.rssi_dbm)

    env = _classify_environment(excess_losses)

    if env is Environment.OUTDOOR_LOS:
        return lat_pre, lon_pre, acc_pre  # already solved with the correct environment

    # Pass 2: re-solve with the detected environment
    return _solve(measurements, env)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 2.0 * R * math.asin(math.sqrt(a))
