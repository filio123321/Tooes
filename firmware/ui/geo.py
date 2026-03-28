"""Coordinate math, bearing, and distance calculations for map rendering."""

import math

TILE_SIZE = 256
MIN_ZOOM = 0
MAX_ZOOM = 19


def clamp(value, lo, hi):
    return max(lo, min(value, hi))


def latlon_to_world_px(lat, lon, zoom):
    """Convert lat/lon to absolute world-pixel coordinates at *zoom*."""
    lat = clamp(lat, -85.05112878, 85.05112878)
    scale = TILE_SIZE * (2 ** zoom)
    x = (lon + 180.0) / 360.0 * scale
    lat_rad = math.radians(lat)
    y = (
        (1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi)
        / 2.0
        * scale
    )
    return x, y


def latlon_to_screen(lat, lon, center_lat, center_lon, zoom, width, height):
    """Convert lat/lon to screen-pixel coordinates given a map centre."""
    cx, cy = latlon_to_world_px(center_lat, center_lon, zoom)
    px, py = latlon_to_world_px(lat, lon, zoom)
    return px - cx + width / 2, py - cy + height / 2


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial bearing from point 1 to point 2 in degrees (0=N, 90=E)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(phi2)
    x = (
        math.cos(phi1) * math.sin(phi2)
        - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    )
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def bearing_to_unit(deg):
    """Bearing to (dx, dy) unit vector in screen coords (x right, y down)."""
    rad = math.radians(deg)
    return math.sin(rad), -math.cos(rad)


def bearing_to_text(deg):
    """Bearing to compass-rose label (N, NE, E, ...)."""
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int((deg + 22.5) // 45) % 8]


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in kilometres between two lat/lon points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
