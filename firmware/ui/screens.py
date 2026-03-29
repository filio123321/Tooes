"""Screen renderers for each UI state: boot, tutorial, scanning, map."""

import dataclasses
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFont

from firmware.hal.types import CellKey
from firmware.tower_data import CatalogTower
from firmware.ui.geo import (
    bearing_deg,
    bearing_to_text,
    haversine_km,
    latlon_to_screen,
    clamp,
    MIN_ZOOM,
    MAX_ZOOM,
)
from firmware.ui.tiles import render_map_canvas
from firmware.ui.icons import (
    draw_user_marker,
    draw_catalog_tower_icon,
    draw_tower_icon,
    draw_signal_arcs,
    draw_link_line,
    draw_edge_arrow,
    point_visible,
)

_font = ImageFont.load_default()
CATALOG_TOWER_MIN_ZOOM = 15

# Larger font for tutorial/boot screens (Pillow 10.1+, fallback to default)
try:
    _font_lg = ImageFont.load_default(size=16)
    _font_md = ImageFont.load_default(size=12)
except TypeError:
    # Older Pillow without size param — try system truetype
    try:
        _font_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        _font_md = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    except OSError:
        _font_lg = _font
        _font_md = _font


# -------------------------------------------------------------------
# Shared data model
# -------------------------------------------------------------------

@dataclasses.dataclass
class DiscoveredTower:
    """A cell tower found during scanning."""

    key: CellKey
    lat: Optional[float]
    lon: Optional[float]
    best_rssi: float
    label: str  # "T1", "T2", ...


# -------------------------------------------------------------------
# Screen 1 — Boot splash
# -------------------------------------------------------------------

def render_boot(w: int, h: int) -> Image.Image:
    img = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, w - 1, h - 1), outline=0, width=2)

    _center_text(draw, w, 20, "T O O E S", font=_font_lg)
    _center_text(draw, w, 45, "Passive RF Navigator", font=_font_md)
    _center_text(draw, w, 75, "Initializing hardware...", font=_font_md)
    return img


# -------------------------------------------------------------------
# Screen 2 — Tutorial (3 pages, button to advance)
# -------------------------------------------------------------------

_TUTORIAL_PAGES = [
    {
        "title": "HOW THIS WORKS",
        "lines": [
            "Passively listens to cell",
            "tower broadcasts to find",
            "your location.",
            "",
            "No GPS, SIM, or network.",
            "Completely invisible.",
        ],
    },
    {
        "title": "READING THE MAP",
        "lines": [
            "(+) = Your position",
            "Shapes = GSM/UMTS/LTE",
            "Arcs = signal direction",
            "",
            "Zoom: rotate dial",
            "Press: signal overlay",
        ],
    },
    {
        "title": "READY TO SCAN",
        "lines": [
            "Scanning for cell towers.",
            "",
            "Slowly rotate 360 degrees",
            "while holding device level.",
            "",
            "More towers = better fix.",
        ],
    },
]


def render_tutorial(w: int, h: int, page: int) -> Image.Image:
    page = clamp(page, 0, len(_TUTORIAL_PAGES) - 1)
    data = _TUTORIAL_PAGES[page]

    img = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, w - 1, h - 1), outline=0)

    # Header bar (inverted)
    draw.rectangle((0, 0, w - 1, 18), fill=0)
    draw.text((4, 1), data["title"], font=_font_lg, fill=255)
    page_str = f"{page + 1}/{len(_TUTORIAL_PAGES)}"
    pw = draw.textlength(page_str, font=_font_md)
    draw.text((w - pw - 4, 3), page_str, font=_font_md, fill=255)

    # Body
    y = 24
    for line in data["lines"]:
        draw.text((8, y), line, font=_font_md, fill=0)
        y += 14

    # Footer
    footer = (
        "Press to start scan >>"
        if page >= len(_TUTORIAL_PAGES) - 1
        else "Press to continue >>"
    )
    fw = draw.textlength(footer, font=_font_md)
    draw.text((w - fw - 8, h - 16), footer, font=_font_md, fill=0)
    return img


# -------------------------------------------------------------------
# Screen 3 — Scanning / tower discovery
# -------------------------------------------------------------------

def render_scanning(
    w: int,
    h: int,
    towers: List[DiscoveredTower],
    is_done: bool,
) -> Image.Image:
    img = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, w - 1, h - 1), outline=0)

    # Header
    draw.rectangle((0, 0, w - 1, 14), fill=0)
    header = "SCAN COMPLETE" if is_done else "SCANNING..."
    draw.text((4, 2), header, font=_font, fill=255)
    count_str = f"{len(towers)} towers"
    cw = draw.textlength(count_str, font=_font)
    draw.text((w - cw - 4, 2), count_str, font=_font, fill=255)

    # Tower list (max 7 rows)
    resolved = [t for t in towers if t.lat is not None]
    unresolved = [t for t in towers if t.lat is None]
    y = 20
    max_rows = 7

    for t in resolved[:max_rows]:
        cell = f"{t.key.mcc}/{t.key.mnc}/{t.key.lac}/{t.key.ci}"
        rssi = f"{t.best_rssi:.0f}dBm"
        coord = f"{t.lat:.3f},{t.lon:.3f}"
        draw.text((4, y), cell, font=_font, fill=0)
        draw.text((155, y), rssi, font=_font, fill=0)
        draw.text((210, y), coord, font=_font, fill=0)
        y += 12
        max_rows -= 1

    for t in unresolved[:max_rows]:
        cell = f"{t.key.mcc}/{t.key.mnc}/{t.key.lac}/{t.key.ci}"
        rssi = f"{t.best_rssi:.0f}dBm"
        draw.text((4, y), cell, font=_font, fill=0)
        draw.text((155, y), rssi, font=_font, fill=0)
        draw.text((210, y), "(unknown)", font=_font, fill=0)
        y += 12

    # Footer
    if is_done:
        n = len(resolved)
        if n >= 3:
            footer = "Triangulating position..."
        elif n > 0:
            footer = f"Only {n} tower(s) resolved."
        else:
            footer = "No towers resolved."
        draw.text((4, h - 14), footer, font=_font, fill=0)
    else:
        draw.text((4, h - 14), "Listening for broadcasts...", font=_font, fill=0)

    return img


# -------------------------------------------------------------------
# Screen 4 — Main map view
# -------------------------------------------------------------------

def render_map(
    w: int,
    h: int,
    user_lat: float,
    user_lon: float,
    zoom: int,
    towers: List[DiscoveredTower],
    catalog_towers: List[CatalogTower],
    show_overlay: bool,
) -> Image.Image:
    """PoC-style map render with optional signal overlay."""
    zoom = clamp(zoom, MIN_ZOOM, MAX_ZOOM)

    img = render_map_canvas(user_lat, user_lon, zoom, w, h)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, w - 1, h - 1), outline=0)
    draw.rectangle((0, 0, w - 1, 12), fill=255)
    draw.text((4, 2), f"Offline Z:{zoom}", font=_font, fill=0)
    draw.text((210, 2), "SIG ON" if show_overlay else "SIG OFF", font=_font, fill=0)

    user_x = w / 2
    user_y = h / 2
    resolved = [t for t in towers if t.lat is not None]

    nearest = None
    nearest_dist = float("inf")
    for t in resolved:
        d = haversine_km(user_lat, user_lon, t.lat, t.lon)
        if d < nearest_dist:
            nearest_dist = d
            nearest = t

    nearest_catalog = None
    nearest_catalog_dist = float("inf")
    for tower in catalog_towers:
        d = haversine_km(user_lat, user_lon, tower.lat, tower.lon)
        if d < nearest_catalog_dist:
            nearest_catalog_dist = d
            nearest_catalog = tower

    if zoom >= CATALOG_TOWER_MIN_ZOOM:
        for tower in catalog_towers:
            tower_x, tower_y = latlon_to_screen(
                tower.lat,
                tower.lon,
                user_lat,
                user_lon,
                zoom,
                w,
                h,
            )
            if point_visible(tower_x, tower_y, w, h):
                draw_catalog_tower_icon(draw, tower_x, tower_y, tower.radio)

    overlay_lat = None
    overlay_lon = None
    overlay_label = "TWR"
    if nearest:
        overlay_lat = nearest.lat
        overlay_lon = nearest.lon
        overlay_label = nearest.label
    elif nearest_catalog:
        overlay_lat = nearest_catalog.lat
        overlay_lon = nearest_catalog.lon

    if show_overlay and overlay_lat is not None and overlay_lon is not None:
        tower_x, tower_y = latlon_to_screen(
            overlay_lat,
            overlay_lon,
            user_lat,
            user_lon,
            zoom,
            w,
            h,
        )
        tower_bearing = bearing_deg(user_lat, user_lon, overlay_lat, overlay_lon)

        draw_signal_arcs(draw, user_x, user_y, tower_bearing)
        if point_visible(tower_x, tower_y, w, h):
            draw_tower_icon(draw, tower_x, tower_y, overlay_label)
            draw_link_line(draw, user_x, user_y, tower_x, tower_y)
        else:
            draw_edge_arrow(draw, w, h, tower_bearing, overlay_label)

        draw.rectangle((100, 0, 208, 12), fill=255)
        draw.text((102, 2), f"DIR:{bearing_to_text(tower_bearing)}", font=_font, fill=0)

    draw_user_marker(draw, user_x, user_y, None)

    return img


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _center_text(draw, width, y, text, font=None):
    f = font or _font
    tw = draw.textlength(text, font=f)
    draw.text(((width - tw) / 2, y), text, font=f, fill=0)
