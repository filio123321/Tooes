#!/usr/bin/env python3
import math
import time
from pathlib import Path
from io import BytesIO

import requests
from PIL import Image

# =========================================================
# CONFIG
# =========================================================

# Blagoevgrad
CENTER_LAT = 42.0209
CENTER_LON = 23.0943

# Radius around Blagoevgrad
RADIUS_KM = 100

# Zoom levels to preload
MIN_ZOOM = 10
MAX_ZOOM = 16

SCRIPT_DIR = Path(__file__).resolve().parent
FIRMWARE_DIR = SCRIPT_DIR.parent
OUTPUT_ROOT = FIRMWARE_DIR / "offline_tiles"

# IMPORTANT:
# Use a tile source/provider that explicitly allows offline/bulk download.
# Example format:
TILE_URL_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

USER_AGENT = "OfflineTilePreloader/1.0"
REQUEST_DELAY_SEC = 0.1
REQUEST_TIMEOUT_SEC = 20


# =========================================================
# HELPERS
# =========================================================
def latlon_to_tile(lat: float, lon: float, zoom: int):
    lat = max(min(lat, 85.05112878), -85.05112878)
    n = 2 ** zoom
    xtile = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    ytile = (1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return xtile, ytile


def km_to_lat_deg(km: float) -> float:
    return km / 111.32


def km_to_lon_deg(km: float, lat_deg: float) -> float:
    return km / (111.32 * math.cos(math.radians(lat_deg)))


def bbox_from_center_radius(lat: float, lon: float, radius_km: float):
    dlat = km_to_lat_deg(radius_km)
    dlon = km_to_lon_deg(radius_km, lat)

    min_lat = lat - dlat
    max_lat = lat + dlat
    min_lon = lon - dlon
    max_lon = lon + dlon

    return min_lat, min_lon, max_lat, max_lon


def tile_range_for_bbox(min_lat, min_lon, max_lat, max_lon, zoom):
    x1, y1 = latlon_to_tile(max_lat, min_lon, zoom)  # top-left
    x2, y2 = latlon_to_tile(min_lat, max_lon, zoom)  # bottom-right

    n = 2 ** zoom

    min_x = max(0, int(math.floor(min(x1, x2))))
    max_x = min(n - 1, int(math.floor(max(x1, x2))))
    min_y = max(0, int(math.floor(min(y1, y2))))
    max_y = min(n - 1, int(math.floor(max(y1, y2))))

    return min_x, max_x, min_y, max_y


def ensure_png(content: bytes):
    img = Image.open(BytesIO(content))
    return img.convert("RGBA")


def download_tile(session: requests.Session, z: int, x: int, y: int, output_root: Path) -> bool:
    url = TILE_URL_TEMPLATE.format(z=z, x=x, y=y)
    out_path = output_root / str(z) / str(x) / f"{y}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists():
        return True

    try:
        r = session.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SEC,
        )
        r.raise_for_status()

        img = ensure_png(r.content)
        img.save(out_path, format="PNG")
        return True

    except Exception as e:
        print(f"FAILED {z}/{x}/{y}: {e}")
        return False


# =========================================================
# MAIN
# =========================================================
def main():
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    min_lat, min_lon, max_lat, max_lon = bbox_from_center_radius(
        CENTER_LAT, CENTER_LON, RADIUS_KM
    )

    print(f"Output folder: {OUTPUT_ROOT}")
    print(f"Center: ({CENTER_LAT}, {CENTER_LON})")
    print(f"Radius: {RADIUS_KM} km")
    print(f"BBox: lat {min_lat:.5f}..{max_lat:.5f}, lon {min_lon:.5f}..{max_lon:.5f}")
    print(f"Zooms: {MIN_ZOOM}..{MAX_ZOOM}")
    print()

    total_tiles = 0
    ranges = {}

    for z in range(MIN_ZOOM, MAX_ZOOM + 1):
        min_x, max_x, min_y, max_y = tile_range_for_bbox(min_lat, min_lon, max_lat, max_lon, z)
        count = (max_x - min_x + 1) * (max_y - min_y + 1)
        ranges[z] = (min_x, max_x, min_y, max_y, count)
        total_tiles += count
        print(f"z={z}: x={min_x}..{max_x}, y={min_y}..{max_y} -> {count} tiles")

    print(f"\nTotal tiles to download: {total_tiles}\n")

    session = requests.Session()

    done = 0
    ok = 0
    failed = 0

    for z in range(MIN_ZOOM, MAX_ZOOM + 1):
        min_x, max_x, min_y, max_y, _ = ranges[z]

        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                done += 1
                print(f"[{done}/{total_tiles}] downloading {z}/{x}/{y}")

                if download_tile(session, z, x, y, OUTPUT_ROOT):
                    ok += 1
                else:
                    failed += 1

                time.sleep(REQUEST_DELAY_SEC)

    print("\nDone.")
    print(f"Saved:  {ok}")
    print(f"Failed: {failed}")
    print(f"Folder: {OUTPUT_ROOT}")


if __name__ == "__main__":
    main()
