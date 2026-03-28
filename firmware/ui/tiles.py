"""Offline OpenStreetMap tile loading and map-canvas composition."""

import math
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from firmware.ui.geo import clamp, latlon_to_world_px, TILE_SIZE, MIN_ZOOM, MAX_ZOOM

_log = logging.getLogger(__name__)

_FIRMWARE_DIR = Path(__file__).resolve().parent.parent
TILE_ROOT = _FIRMWARE_DIR / "offline_tiles"

_font = ImageFont.load_default()


def _make_missing_tile(z, x, y):
    """Placeholder tile when the real one is not on disk."""
    img = Image.new("L", (TILE_SIZE, TILE_SIZE), 255)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, TILE_SIZE - 1, TILE_SIZE - 1), outline=0)
    draw.text((8, 8), f"{z}/{x}/{y}", font=_font, fill=0)
    draw.text((8, 22), "no tile", font=_font, fill=0)
    return img


def fetch_tile(z, x, y):
    """Load a single 256x256 tile from disk, or return a placeholder."""
    n = 2 ** z
    x = x % n
    if y < 0 or y >= n:
        return Image.new("L", (TILE_SIZE, TILE_SIZE), 255)

    tile_path = TILE_ROOT / str(z) / str(x) / f"{y}.png"
    if tile_path.exists():
        try:
            return Image.open(tile_path).convert("L")
        except Exception as e:
            _log.warning("Failed reading tile %s: %s", tile_path, e)

    return _make_missing_tile(z, x, y)


def render_map_canvas(center_lat, center_lon, zoom, width, height):
    """Compose tile mosaic centred on *center_lat/lon*. Returns greyscale Image."""
    zoom = clamp(zoom, MIN_ZOOM, MAX_ZOOM)
    cx, cy = latlon_to_world_px(center_lat, center_lon, zoom)

    left = cx - width / 2
    top = cy - height / 2
    right = cx + width / 2
    bottom = cy + height / 2

    lt = int(math.floor(left / TILE_SIZE))
    rt = int(math.floor(right / TILE_SIZE))
    tt = int(math.floor(top / TILE_SIZE))
    bt = int(math.floor(bottom / TILE_SIZE))

    canvas = Image.new("L", (width, height), 255)
    for ty in range(tt, bt + 1):
        for tx in range(lt, rt + 1):
            tile = fetch_tile(zoom, tx, ty)
            paste_x = int(tx * TILE_SIZE - left)
            paste_y = int(ty * TILE_SIZE - top)
            canvas.paste(tile, (paste_x, paste_y))

    return canvas
