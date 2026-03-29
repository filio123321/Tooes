"""Small geographic helpers shared by the navigation runtime."""

from __future__ import annotations

import math


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(value, hi))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6_371_000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2.0) ** 2
    )
    return 2.0 * radius_m * math.asin(math.sqrt(a))


def latlon_to_enu(
    lat: float,
    lon: float,
    origin_lat: float,
    origin_lon: float,
) -> tuple[float, float]:
    east_m = (lon - origin_lon) * 111_320.0 * math.cos(math.radians(origin_lat))
    north_m = (lat - origin_lat) * 110_540.0
    return east_m, north_m


def enu_to_latlon(
    east_m: float,
    north_m: float,
    origin_lat: float,
    origin_lon: float,
) -> tuple[float, float]:
    lon = origin_lon + east_m / (111_320.0 * math.cos(math.radians(origin_lat)))
    lat = origin_lat + north_m / 110_540.0
    return lat, lon
