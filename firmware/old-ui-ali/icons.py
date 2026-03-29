"""Drawing helpers for map markers, towers, signals, and navigation arrows."""

import math

from PIL import ImageFont

from firmware.ui.geo import bearing_to_unit

_font = ImageFont.load_default()

# Sizing constants
USER_RADIUS = 5
TOWER_BODY_W = 10
TOWER_BODY_H = 16
SIGNAL_RADII = [12, 18, 24]
EDGE_ARROW_LEN = 14
EDGE_ARROW_WING = 6


def draw_user_marker(draw, x, y, heading_deg=None):
    """Draw the user-position marker with optional compass-heading arrow."""
    r = USER_RADIUS
    # Outer circle
    draw.ellipse((x - r, y - r, x + r, y + r), outline=0, width=2, fill=255)
    # Crosshair
    draw.line((x - 4, y, x + 4, y), fill=0, width=1)
    draw.line((x, y - 4, x, y + 4), fill=0, width=1)

    if heading_deg is not None:
        # Small triangle on the circle rim pointing in the heading direction
        rad = math.radians(heading_deg)
        sin_h, cos_h = math.sin(rad), math.cos(rad)
        # Tip beyond the circle
        tip_x = x + sin_h * (r + 7)
        tip_y = y - cos_h * (r + 7)
        # Base just inside the circle
        base_x = x + sin_h * (r - 1)
        base_y = y - cos_h * (r - 1)
        # Perpendicular wing offsets
        px, py = cos_h, sin_h
        w = 3
        draw.polygon(
            [
                (tip_x, tip_y),
                (base_x - px * w, base_y - py * w),
                (base_x + px * w, base_y + py * w),
            ],
            fill=0,
        )


def draw_tower_icon(draw, x, y, label="TWR"):
    """Draw a cell-tower icon at (x, y) with a text label."""
    bw, bh = TOWER_BODY_W, TOWER_BODY_H
    top_y = y - bh // 2
    bot_y = y + bh // 2

    # Legs
    draw.line((x - bw // 2, bot_y, x, top_y), fill=0, width=3)
    draw.line((x + bw // 2, bot_y, x, top_y), fill=0, width=3)
    # Crossbars
    draw.line(
        (x - bw // 2 - 2, bot_y, x + bw // 2 + 2, bot_y), fill=0, width=3
    )
    draw.line((x - bw // 3, y + 3, x + bw // 3, y + 3), fill=0, width=2)
    draw.line((x - bw // 4, y - 2, x + bw // 4, y - 2), fill=0, width=2)
    # Antenna mast
    draw.line((x, top_y - 4, x, top_y + 2), fill=0, width=3)
    # Signal arcs at the tip
    cx, cy = x, top_y - 4
    draw.arc((cx - 7, cy - 7, cx + 7, cy + 7), 300, 60, fill=0, width=2)
    draw.arc((cx - 11, cy - 11, cx + 11, cy + 11), 300, 60, fill=0, width=2)
    # Label
    draw.text((x + 8, y + 6), label, font=_font, fill=0)


def draw_signal_arcs(draw, x, y, bearing_deg):
    """Draw signal-strength arcs emanating from (x, y) toward *bearing_deg*."""
    pil_angle = (bearing_deg - 90) % 360
    spread = 35
    start = pil_angle - spread
    end = pil_angle + spread
    draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=0)
    for r in SIGNAL_RADII:
        draw.arc(
            (x - r, y - r, x + r, y + r),
            start=start,
            end=end,
            fill=0,
            width=3,
        )


def draw_link_line(draw, x1, y1, x2, y2):
    """Thin line connecting two on-screen points."""
    draw.line((x1, y1, x2, y2), fill=0, width=2)


def draw_edge_arrow(draw, width, height, bearing_deg, label):
    """Arrow at the screen edge pointing toward an off-screen tower."""
    dx, dy = bearing_to_unit(bearing_deg)
    margin, top_margin = 12, 18
    cx, cy = width / 2, height / 2
    candidates = []

    if dx != 0:
        for t_val, xval in [
            ((margin - cx) / dx, margin),
            (((width - margin) - cx) / dx, width - margin),
        ]:
            if t_val > 0:
                yval = cy + t_val * dy
                if top_margin <= yval <= height - margin:
                    candidates.append((t_val, xval, yval))

    if dy != 0:
        for t_val, yval in [
            ((top_margin - cy) / dy, top_margin),
            (((height - margin) - cy) / dy, height - margin),
        ]:
            if t_val > 0:
                xval = cx + t_val * dx
                if margin <= xval <= width - margin:
                    candidates.append((t_val, xval, yval))

    if not candidates:
        return

    _, px, py = min(candidates, key=lambda c: c[0])

    bx = px - dx * EDGE_ARROW_LEN
    by = py - dy * EDGE_ARROW_LEN
    pw = EDGE_ARROW_WING

    draw.line((bx, by, px, py), fill=0, width=3)
    draw.line((bx + dy * pw, by - dx * pw, px, py), fill=0, width=3)
    draw.line((bx - dy * pw, by + dx * pw, px, py), fill=0, width=3)

    tx = max(2, min(width - 28, px - 8))
    ty = max(14, min(height - 10, py - 10))
    draw.text((tx, ty), label, font=_font, fill=0)


def point_visible(x, y, width, height, margin_top=12):
    """True when (x, y) falls inside the visible map area."""
    return 0 <= x < width and margin_top <= y < height
