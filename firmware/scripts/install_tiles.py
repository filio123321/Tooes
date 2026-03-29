#!/usr/bin/env python3
"""Download and verify offline tiles for the e-paper UI."""

from __future__ import annotations

import argparse
import math
import time
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import requests
    from PIL import Image


# Match the UI defaults exactly.
DEFAULT_CENTER_LAT = 42.012280
DEFAULT_CENTER_LON = 23.095261
DEFAULT_RADIUS_KM = 10.0
DEFAULT_MIN_ZOOM = 10
DEFAULT_MAX_ZOOM = 16

DISPLAY_W = 296
DISPLAY_H = 128
TILE_SIZE = 256

SCRIPT_DIR = Path(__file__).resolve().parent
FIRMWARE_DIR = SCRIPT_DIR.parent
DEFAULT_OUTPUT_ROOT = FIRMWARE_DIR / "offline_tiles"

# IMPORTANT:
# Use a provider that permits your planned use. The default endpoint works
# well for light, development-scale fetches, but do not point this at huge
# offline batches.
DEFAULT_TILE_URL_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

USER_AGENT = "TooesOfflineTileInstaller/1.0"
REQUEST_DELAY_SEC = 0.1
REQUEST_TIMEOUT_SEC = 20
MAX_RETRIES = 3
VIEWPORT_PADDING_TILES = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download offline tiles for the Tooes e-paper UI.")
    parser.add_argument("--center-lat", type=float, default=DEFAULT_CENTER_LAT)
    parser.add_argument("--center-lon", type=float, default=DEFAULT_CENTER_LON)
    parser.add_argument("--radius-km", type=float, default=DEFAULT_RADIUS_KM)
    parser.add_argument("--min-zoom", type=int, default=DEFAULT_MIN_ZOOM)
    parser.add_argument("--max-zoom", type=int, default=DEFAULT_MAX_ZOOM)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--tile-url-template", default=DEFAULT_TILE_URL_TEMPLATE)
    parser.add_argument("--delay-sec", type=float, default=REQUEST_DELAY_SEC)
    parser.add_argument("--timeout-sec", type=float, default=REQUEST_TIMEOUT_SEC)
    parser.add_argument("--retries", type=int, default=MAX_RETRIES)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def latlon_to_tile(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    lat = max(min(lat, 85.05112878), -85.05112878)
    n = 2**zoom
    xtile = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    ytile = (1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return xtile, ytile


def km_to_lat_deg(km: float) -> float:
    return km / 111.32


def km_to_lon_deg(km: float, lat_deg: float) -> float:
    return km / (111.32 * math.cos(math.radians(lat_deg)))


def bbox_from_center_radius(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    dlat = km_to_lat_deg(radius_km)
    dlon = km_to_lon_deg(radius_km, lat)
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


def tile_range_for_bbox(min_lat: float, min_lon: float, max_lat: float, max_lon: float, zoom: int) -> tuple[int, int, int, int]:
    x1, y1 = latlon_to_tile(max_lat, min_lon, zoom)
    x2, y2 = latlon_to_tile(min_lat, max_lon, zoom)
    n = 2**zoom
    min_x = max(0, int(math.floor(min(x1, x2))))
    max_x = min(n - 1, int(math.floor(max(x1, x2))))
    min_y = max(0, int(math.floor(min(y1, y2))))
    max_y = min(n - 1, int(math.floor(max(y1, y2))))
    return min_x, max_x, min_y, max_y


def viewport_tile_range(lat: float, lon: float, zoom: int) -> tuple[int, int, int, int]:
    n = 2**zoom
    cx, cy = latlon_to_tile(lat, lon, zoom)
    half_w = math.ceil(DISPLAY_W / 2 / TILE_SIZE) + VIEWPORT_PADDING_TILES
    half_h = math.ceil(DISPLAY_H / 2 / TILE_SIZE) + VIEWPORT_PADDING_TILES
    min_x = max(0, int(math.floor(cx)) - half_w)
    max_x = min(n - 1, int(math.floor(cx)) + half_w)
    min_y = max(0, int(math.floor(cy)) - half_h)
    max_y = min(n - 1, int(math.floor(cy)) + half_h)
    return min_x, max_x, min_y, max_y


def merge_ranges(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    return (
        min(a[0], b[0]),
        max(a[1], b[1]),
        min(a[2], b[2]),
        max(a[3], b[3]),
    )


def plan_ranges(center_lat: float, center_lon: float, radius_km: float, min_zoom: int, max_zoom: int) -> dict[int, tuple[int, int, int, int, int]]:
    min_lat, min_lon, max_lat, max_lon = bbox_from_center_radius(center_lat, center_lon, radius_km)
    ranges: dict[int, tuple[int, int, int, int, int]] = {}

    for zoom in range(min_zoom, max_zoom + 1):
        bbox_range = tile_range_for_bbox(min_lat, min_lon, max_lat, max_lon, zoom)
        viewport_range = viewport_tile_range(center_lat, center_lon, zoom)
        min_x, max_x, min_y, max_y = merge_ranges(bbox_range, viewport_range)
        count = (max_x - min_x + 1) * (max_y - min_y + 1)
        ranges[zoom] = (min_x, max_x, min_y, max_y, count)

    return ranges


def ensure_png(content: bytes) -> "Image.Image":
    from PIL import Image

    img = Image.open(BytesIO(content))
    return img.convert("RGBA")


def tile_path(output_root: Path, zoom: int, x: int, y: int) -> Path:
    return output_root / str(zoom) / str(x) / f"{y}.png"


def download_tile(
    session: requests.Session,
    zoom: int,
    x: int,
    y: int,
    output_root: Path,
    tile_url_template: str,
    timeout_sec: float,
    retries: int,
) -> bool:
    out_path = tile_path(output_root, zoom, x, y)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists():
        return True

    url = tile_url_template.format(z=zoom, x=x, y=y)
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            response = session.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=timeout_sec,
            )
            response.raise_for_status()
            img = ensure_png(response.content)
            img.save(out_path, format="PNG")
            return True
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.5 * attempt)

    print(f"FAILED {zoom}/{x}/{y}: {last_error}")
    return False


def verify_ranges(output_root: Path, ranges: dict[int, tuple[int, int, int, int, int]]) -> tuple[bool, list[str]]:
    missing: list[str] = []

    for zoom, (min_x, max_x, min_y, max_y, _) in ranges.items():
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                if not tile_path(output_root, zoom, x, y).exists():
                    missing.append(f"{zoom}/{x}/{y}")

    return not missing, missing


def main() -> None:
    args = parse_args()

    if args.min_zoom > args.max_zoom:
        raise SystemExit("--min-zoom must be <= --max-zoom")
    if args.radius_km < 0:
        raise SystemExit("--radius-km must be >= 0")

    output_root: Path = args.output_root
    output_root.mkdir(parents=True, exist_ok=True)

    ranges = plan_ranges(
        center_lat=args.center_lat,
        center_lon=args.center_lon,
        radius_km=args.radius_km,
        min_zoom=args.min_zoom,
        max_zoom=args.max_zoom,
    )

    total_tiles = sum(item[4] for item in ranges.values())
    existing_tiles = 0
    for zoom, (min_x, max_x, min_y, max_y, _) in ranges.items():
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                if tile_path(output_root, zoom, x, y).exists():
                    existing_tiles += 1

    print(f"Output folder: {output_root}")
    print(f"Center: ({args.center_lat}, {args.center_lon})")
    print(f"Radius: {args.radius_km} km")
    print(f"Zooms: {args.min_zoom}..{args.max_zoom}")
    print(f"Tile URL: {args.tile_url_template}")
    print()

    for zoom in range(args.min_zoom, args.max_zoom + 1):
        min_x, max_x, min_y, max_y, count = ranges[zoom]
        print(f"z={zoom}: x={min_x}..{max_x}, y={min_y}..{max_y} -> {count} tiles")

    print(f"\nPlanned tiles: {total_tiles}")
    print(f"Already present: {existing_tiles}")
    print(f"Missing: {total_tiles - existing_tiles}\n")

    if args.dry_run:
        print("Dry run only. No tiles downloaded.")
        return

    import requests

    session = requests.Session()
    done = 0
    ok = existing_tiles
    failed = 0

    for zoom in range(args.min_zoom, args.max_zoom + 1):
        min_x, max_x, min_y, max_y, _ = ranges[zoom]
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                done += 1
                print(f"[{done}/{total_tiles}] {zoom}/{x}/{y}")
                if download_tile(
                    session=session,
                    zoom=zoom,
                    x=x,
                    y=y,
                    output_root=output_root,
                    tile_url_template=args.tile_url_template,
                    timeout_sec=args.timeout_sec,
                    retries=args.retries,
                ):
                    ok += 1
                else:
                    failed += 1
                time.sleep(args.delay_sec)

    verified, missing = verify_ranges(output_root, ranges)

    print("\nDone.")
    print(f"Saved or present: {ok}")
    print(f"Failed: {failed}")
    if verified:
        print("Verification: PASS")
    else:
        print("Verification: FAIL")
        print(f"Missing tiles: {len(missing)}")
        preview = ", ".join(missing[:10])
        if preview:
            print(f"Examples: {preview}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
