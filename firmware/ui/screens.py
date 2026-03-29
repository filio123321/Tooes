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
# Keep the tower layer hidden when the map gets broad, but let it show up
# before the user has to zoom into an almost street-level view.
CATALOG_TOWER_MIN_ZOOM = 14

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
            "Hold: open menu",
        ],
    },
    {
        "title": "READY TO WALK",
        "lines": [
            "Start point loads from",
            ".env.local as INITIAL_L.",
            "",
            "Walk normally to build",
            "your local trace.",
            "",
            "After 25m the SDR fix",
            "nudges the anchor.",
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
        "Press to start nav >>"
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
    show_catalog_towers: bool,
    show_trace: bool,
    trace_points: list[tuple[float, float]],
    menu_open: bool,
    menu_index: int,
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

    visible_catalog_towers = []
    if show_catalog_towers and zoom >= CATALOG_TOWER_MIN_ZOOM:
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
                visible_catalog_towers.append(
                    (
                        haversine_km(user_lat, user_lon, tower.lat, tower.lon),
                        tower,
                        tower_x,
                        tower_y,
                    )
                )

        # Draw farthest first so the nearest towers stay visible on top when
        # several icons land close together on the e-paper display.
        for _, tower, tower_x, tower_y in sorted(
            visible_catalog_towers,
            key=lambda item: item[0],
            reverse=True,
        ):
            draw_catalog_tower_icon(draw, tower_x, tower_y, tower.radio)

    csv_label = "CSV:OFF" if not show_catalog_towers else f"CSV:{len(visible_catalog_towers)}"
    draw.text((112, 2), csv_label, font=_font, fill=0)

    if show_trace and len(trace_points) >= 2:
        _draw_trace_overlay(draw, trace_points, user_lat, user_lon, zoom, w, h)

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

    if menu_open:
        _draw_map_menu(draw, menu_index, show_overlay, show_catalog_towers, show_trace)

    return img


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _center_text(draw, width, y, text, font=None):
    f = font or _font
    tw = draw.textlength(text, font=f)
    draw.text(((width - tw) / 2, y), text, font=f, fill=0)


def _draw_map_menu(
    draw,
    menu_index: int,
    show_overlay: bool,
    show_catalog_towers: bool,
    show_trace: bool,
):
    items = [
        "EXIT",
        f"SIG {'ON' if show_overlay else 'OFF'}",
        f"TWR {'ON' if show_catalog_towers else 'OFF'}",
        f"TRC {'ON' if show_trace else 'OFF'}",
    ]

    origin_x = 6
    origin_y = 18
    cell_w = 64
    cell_h = 22
    gap_x = 6
    gap_y = 6
    cols = 2

    rows = 2
    panel_w = cols * cell_w + gap_x * (cols - 1) + 8
    panel_h = rows * cell_h + gap_y * (rows - 1) + 8
    draw.rounded_rectangle(
        (origin_x, origin_y, origin_x + panel_w, origin_y + panel_h),
        radius=4,
        fill=255,
        outline=0,
        width=1,
    )

    for idx, label in enumerate(items):
        row = idx // cols
        col = idx % cols
        x0 = origin_x + 4 + col * (cell_w + gap_x)
        y0 = origin_y + 4 + row * (cell_h + gap_y)
        x1 = x0 + cell_w
        y1 = y0 + cell_h
        selected = idx == menu_index

        draw.rounded_rectangle(
            (x0, y0, x1, y1),
            radius=3,
            fill=0 if selected else 255,
            outline=0,
            width=1,
        )
        text_fill = 255 if selected else 0
        tw = draw.textlength(label, font=_font)
        tx = x0 + (cell_w - tw) / 2
        ty = y0 + 7
        draw.text((tx, ty), label, font=_font, fill=text_fill)


def _draw_trace_overlay(
    draw,
    trace_points: list[tuple[float, float]],
    center_lat: float,
    center_lon: float,
    zoom: int,
    width: int,
    height: int,
):
    """Draw the historical path as a simple polyline on top of the map."""
    points = []
    for lat, lon in trace_points:
        px, py = latlon_to_screen(lat, lon, center_lat, center_lon, zoom, width, height)
        points.append((px, py))

    if len(points) < 2:
        return

    draw.line(points, fill=0, width=2)
    for index, (px, py) in enumerate(points):
        if index % 4 == 0 or index == len(points) - 1:
            draw.ellipse((px - 1, py - 1, px + 1, py + 1), fill=0)
