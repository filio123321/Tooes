"""Screen renderers for each UI state: boot, tutorial, scanning, map."""

import dataclasses
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFont

from firmware.hal.types import CellKey
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
    draw_tower_icon,
    draw_signal_arcs,
    draw_link_line,
    draw_edge_arrow,
    point_visible,
)

_font = ImageFont.load_default()

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
            "/|\\ = Cell tower",
            " >> = Nearest tower",
            "",
            "Zoom: rotate dial",
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
    heading_deg: float,
    towers: List[DiscoveredTower],
) -> Image.Image:
    """Full map render: tiles + towers + user marker + nearest-tower bar."""
    zoom = clamp(zoom, MIN_ZOOM, MAX_ZOOM)

    # Tile canvas
    img = render_map_canvas(user_lat, user_lon, zoom, w, h)
    draw = ImageDraw.Draw(img)

    # Border
    draw.rectangle((0, 0, w - 1, h - 1), outline=0)

    user_x, user_y = w / 2, h / 2

    # Resolved towers with known coordinates
    resolved = [t for t in towers if t.lat is not None]

    # Find nearest tower
    nearest = None
    nearest_dist = float("inf")
    for t in resolved:
        d = haversine_km(user_lat, user_lon, t.lat, t.lon)
        if d < nearest_dist:
            nearest_dist = d
            nearest = t

    # Draw towers
    for t in resolved:
        tx, ty = latlon_to_screen(
            t.lat, t.lon, user_lat, user_lon, zoom, w, h
        )
        tower_brg = bearing_deg(user_lat, user_lon, t.lat, t.lon)

        if point_visible(tx, ty, w, h):
            draw_tower_icon(draw, tx, ty, t.label)
            draw_link_line(draw, user_x, user_y, tx, ty)
        else:
            draw_edge_arrow(draw, w, h, tower_brg, t.label)

    # Signal arcs toward nearest tower
    if nearest:
        nb = bearing_deg(user_lat, user_lon, nearest.lat, nearest.lon)
        draw_signal_arcs(draw, user_x, user_y, nb)

    # User marker (always on top)
    draw_user_marker(draw, user_x, user_y, heading_deg)

    # ---- Top bar ----
    draw.rectangle((0, 0, w - 1, 12), fill=255)
    draw.line((0, 12, w - 1, 12), fill=0)

    lat_hemi = "N" if user_lat >= 0 else "S"
    lon_hemi = "E" if user_lon >= 0 else "W"
    top_left = f"Z:{zoom} {abs(user_lat):.4f}{lat_hemi} {abs(user_lon):.4f}{lon_hemi}"
    draw.text((4, 2), top_left, font=_font, fill=0)

    hdg_str = f"HDG:{int(heading_deg) % 360:03d}"
    hw = draw.textlength(hdg_str, font=_font)
    draw.text((w - hw - 4, 2), hdg_str, font=_font, fill=0)

    # ---- Bottom bar: nearest tower ----
    if nearest:
        draw.rectangle((0, h - 14, w - 1, h - 1), fill=255)
        draw.line((0, h - 14, w - 1, h - 14), fill=0)

        nb = bearing_deg(user_lat, user_lon, nearest.lat, nearest.lon)
        dir_txt = bearing_to_text(nb)
        dist_str = (
            f"{nearest_dist * 1000:.0f}m"
            if nearest_dist < 1
            else f"{nearest_dist:.1f}km"
        )
        bar = (
            f">> {nearest.label}: {dist_str} {dir_txt}  "
            f"{nearest.best_rssi:.0f}dBm"
        )
        draw.text((4, h - 12), bar, font=_font, fill=0)

    return img


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _center_text(draw, width, y, text, font=None):
    f = font or _font
    tw = draw.textlength(text, font=f)
    draw.text(((width - tw) / 2, y), text, font=f, fill=0)
