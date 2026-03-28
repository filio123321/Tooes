#!/usr/bin/env python3
import math
import logging
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from gpiozero import RotaryEncoder, Button

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
WAVESHARE_LIB = (
    REPO_ROOT
    / "external"
    / "waveshare-epd"
    / "RaspberryPi_JetsonNano"
    / "python"
    / "lib"
)
TILE_ROOT = REPO_ROOT / "firmware" / "offline_tiles"

if not WAVESHARE_LIB.exists():
    raise FileNotFoundError(
        f"Waveshare lib not found: {WAVESHARE_LIB}\n"
        "From repo root run:\n"
        "  git submodule update --init --recursive"
    )

if str(WAVESHARE_LIB) not in sys.path:
    sys.path.insert(0, str(WAVESHARE_LIB))

from waveshare_epd import epd2in9_V2

logging.basicConfig(level=logging.INFO)

# =========================================================
# Display settings - LANDSCAPE
# =========================================================
EPD_WIDTH = 296
EPD_HEIGHT = 128

# =========================================================
# Map / location settings
# USER is always the map center
# =========================================================
USER_LAT = 42.0202
USER_LON = 23.0918

TOWER_LAT = 42.0187
TOWER_LON = 23.0998

MIN_ZOOM = 0
MAX_ZOOM = 19
DEFAULT_ZOOM = 16

TILE_SIZE = 256

# =========================================================
# HW-040 pins (BCM)
# board pin 29 = GPIO5
# board pin 31 = GPIO6
# board pin 33 = GPIO13
# =========================================================
ENCODER_CLK_PIN = 5
ENCODER_DT_PIN = 6
ENCODER_SW_PIN = 13

# =========================================================
# Drawing settings
# =========================================================
USER_RADIUS = 5
USER_STROKE = 2

TOWER_STROKE = 3
TOWER_BODY_W = 10
TOWER_BODY_H = 16

SIGNAL_STROKE = 3
SIGNAL_RADII = [12, 18, 24]

LINK_STROKE = 2
EDGE_ARROW_STROKE = 3
EDGE_ARROW_LEN = 14
EDGE_ARROW_WING = 6

font = ImageFont.load_default()

# =========================================================
# Globals
# =========================================================
current_zoom = DEFAULT_ZOOM
show_overlay = False
needs_redraw = True
encoder = None


def clamp(value, min_value, max_value):
    return max(min_value, min(value, max_value))


# =========================================================
# Coordinate helpers
# =========================================================
def latlon_to_world_pixels(lat, lon, zoom):
    lat = clamp(lat, -85.05112878, 85.05112878)
    scale = TILE_SIZE * (2 ** zoom)

    x = (lon + 180.0) / 360.0 * scale
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * scale
    return x, y


def latlon_to_screen(lat, lon, center_lat, center_lon, zoom, width, height):
    center_x_world, center_y_world = latlon_to_world_pixels(center_lat, center_lon, zoom)
    point_x_world, point_y_world = latlon_to_world_pixels(lat, lon, zoom)

    sx = (point_x_world - center_x_world) + (width / 2)
    sy = (point_y_world - center_y_world) + (height / 2)
    return sx, sy


def bearing_degrees(lat1, lon1, lat2, lon2):
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)

    y = math.sin(dlon) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)

    angle = math.degrees(math.atan2(y, x))
    return (angle + 360) % 360


def bearing_to_unit_vector(deg):
    rad = math.radians(deg)
    dx = math.sin(rad)
    dy = -math.cos(rad)
    return dx, dy


def bearing_to_text(deg):
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int((deg + 22.5) // 45) % 8
    return directions[idx]


# =========================================================
# Tile loading - OFFLINE ONLY
# =========================================================
def make_missing_tile(z, x, y):
    img = Image.new("L", (TILE_SIZE, TILE_SIZE), 255)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, TILE_SIZE - 1, TILE_SIZE - 1), outline=0)
    draw.text((8, 8), f"{z}/{x}/{y}", font=font, fill=0)
    draw.text((8, 22), "no tile", font=font, fill=0)
    return img


def fetch_tile(z, x, y):
    n = 2 ** z
    x = x % n

    if y < 0 or y >= n:
        return Image.new("L", (TILE_SIZE, TILE_SIZE), 255)

    tile_path = TILE_ROOT / str(z) / str(x) / f"{y}.png"

    if tile_path.exists():
        try:
            return Image.open(tile_path).convert("L")
        except Exception as e:
            logging.warning(f"Failed reading tile {tile_path}: {e}")

    return make_missing_tile(z, x, y)


def render_map(center_lat, center_lon, zoom, width, height):
    zoom = clamp(zoom, MIN_ZOOM, MAX_ZOOM)

    center_world_x, center_world_y = latlon_to_world_pixels(center_lat, center_lon, zoom)

    left_world = center_world_x - width / 2
    top_world = center_world_y - height / 2
    right_world = center_world_x + width / 2
    bottom_world = center_world_y + height / 2

    left_tile = int(math.floor(left_world / TILE_SIZE))
    right_tile = int(math.floor(right_world / TILE_SIZE))
    top_tile = int(math.floor(top_world / TILE_SIZE))
    bottom_tile = int(math.floor(bottom_world / TILE_SIZE))

    canvas = Image.new("L", (width, height), 255)

    for ty in range(top_tile, bottom_tile + 1):
        for tx in range(left_tile, right_tile + 1):
            tile = fetch_tile(zoom, tx, ty)
            paste_x = int(tx * TILE_SIZE - left_world)
            paste_y = int(ty * TILE_SIZE - top_world)
            canvas.paste(tile, (paste_x, paste_y))

    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, width - 1, height - 1), outline=0)
    draw.rectangle((0, 0, width - 1, 12), fill=255)
    draw.text((4, 2), f"Offline Z:{zoom}", font=font, fill=0)

    if show_overlay:
        draw.text((214, 2), "SIG ON", font=font, fill=0)
    else:
        draw.text((210, 2), "SIG OFF", font=font, fill=0)

    return canvas


# =========================================================
# Drawing helpers
# =========================================================
def point_visible(x, y, width, height, margin_top=12, margin=0):
    return (margin <= x < width - margin) and (margin_top <= y < height - margin)


def draw_user_marker(draw, x, y):
    r = USER_RADIUS
    draw.ellipse((x - r, y - r, x + r, y + r), outline=0, width=USER_STROKE, fill=255)
    draw.line((x - 8, y, x + 8, y), fill=0, width=USER_STROKE)
    draw.line((x, y - 8, x, y + 8), fill=0, width=USER_STROKE)
    draw.text((x + 8, y + 6), "YOU", font=font, fill=0)


def draw_tower_icon(draw, x, y):
    body_w = TOWER_BODY_W
    body_h = TOWER_BODY_H
    top_y = y - body_h // 2
    bottom_y = y + body_h // 2

    draw.line((x - body_w // 2, bottom_y, x, top_y), fill=0, width=TOWER_STROKE)
    draw.line((x + body_w // 2, bottom_y, x, top_y), fill=0, width=TOWER_STROKE)
    draw.line((x - body_w // 2 - 2, bottom_y, x + body_w // 2 + 2, bottom_y), fill=0, width=TOWER_STROKE)
    draw.line((x - body_w // 3, y + 3, x + body_w // 3, y + 3), fill=0, width=2)
    draw.line((x - body_w // 4, y - 2, x + body_w // 4, y - 2), fill=0, width=2)
    draw.line((x, top_y - 4, x, top_y + 2), fill=0, width=TOWER_STROKE)

    arc_r1 = 7
    arc_r2 = 11
    cx = x
    cy = top_y - 4
    draw.arc((cx - arc_r1, cy - arc_r1, cx + arc_r1, cy + arc_r1), 300, 60, fill=0, width=2)
    draw.arc((cx - arc_r2, cy - arc_r2, cx + arc_r2, cy + arc_r2), 300, 60, fill=0, width=2)

    draw.text((x + 8, y + 6), "TWR", font=font, fill=0)


def draw_signal_from_user(draw, x, y, bearing_deg):
    pil_angle = (bearing_deg - 90) % 360

    spread = 35
    start = pil_angle - spread
    end = pil_angle + spread

    draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=0)

    for radius in SIGNAL_RADII:
        draw.arc(
            (x - radius, y - radius, x + radius, y + radius),
            start=start,
            end=end,
            fill=0,
            width=SIGNAL_STROKE
        )


def draw_link_line(draw, x1, y1, x2, y2):
    draw.line((x1, y1, x2, y2), fill=0, width=LINK_STROKE)


def draw_edge_arrow(draw, width, height, bearing_deg, label):
    dx, dy = bearing_to_unit_vector(bearing_deg)

    margin = 12
    top_margin = 18
    cx = width / 2
    cy = height / 2
    candidates = []

    if dx != 0:
        t_left = (margin - cx) / dx
        t_right = ((width - margin) - cx) / dx

        if t_left > 0:
            y = cy + t_left * dy
            if top_margin <= y <= height - margin:
                candidates.append((t_left, margin, y))

        if t_right > 0:
            y = cy + t_right * dy
            if top_margin <= y <= height - margin:
                candidates.append((t_right, width - margin, y))

    if dy != 0:
        t_top = (top_margin - cy) / dy
        t_bottom = ((height - margin) - cy) / dy

        if t_top > 0:
            x = cx + t_top * dx
            if margin <= x <= width - margin:
                candidates.append((t_top, x, top_margin))

        if t_bottom > 0:
            x = cx + t_bottom * dx
            if margin <= x <= width - margin:
                candidates.append((t_bottom, x, height - margin))

    if not candidates:
        return

    _, px, py = min(candidates, key=lambda item: item[0])

    back_x = px - dx * EDGE_ARROW_LEN
    back_y = py - dy * EDGE_ARROW_LEN

    perp_x = -dy
    perp_y = dx

    left_x = back_x + perp_x * EDGE_ARROW_WING
    left_y = back_y + perp_y * EDGE_ARROW_WING
    right_x = back_x - perp_x * EDGE_ARROW_WING
    right_y = back_y - perp_y * EDGE_ARROW_WING

    draw.line((back_x, back_y, px, py), fill=0, width=EDGE_ARROW_STROKE)
    draw.line((left_x, left_y, px, py), fill=0, width=EDGE_ARROW_STROKE)
    draw.line((right_x, right_y, px, py), fill=0, width=EDGE_ARROW_STROKE)

    tx = max(2, min(width - 28, px - 8))
    ty = max(14, min(height - 10, py - 10))
    draw.text((tx, ty), label, font=font, fill=0)


# =========================================================
# Overlay drawing
# =========================================================
def draw_overlay(img, center_lat, center_lon, zoom):
    draw = ImageDraw.Draw(img)
    w, h = img.size

    user_x = w / 2
    user_y = h / 2

    tower_x, tower_y = latlon_to_screen(TOWER_LAT, TOWER_LON, center_lat, center_lon, zoom, w, h)

    tower_visible = point_visible(tower_x, tower_y, w, h, margin_top=12)
    tower_bearing_from_user = bearing_degrees(USER_LAT, USER_LON, TOWER_LAT, TOWER_LON)
    tower_bearing_from_center = bearing_degrees(center_lat, center_lon, TOWER_LAT, TOWER_LON)

    draw_user_marker(draw, user_x, user_y)
    draw_signal_from_user(draw, user_x, user_y, tower_bearing_from_user)

    if tower_visible:
        draw_tower_icon(draw, tower_x, tower_y)
        draw_link_line(draw, user_x, user_y, tower_x, tower_y)
    else:
        draw_edge_arrow(draw, w, h, tower_bearing_from_center, "TWR")

    draw.rectangle((100, 0, 208, 12), fill=255)
    draw.text((102, 2), f"DIR:{bearing_to_text(tower_bearing_from_user)}", font=font, fill=0)

    return img


# =========================================================
# Controls
# =========================================================
def zoom_from_steps():
    return clamp(DEFAULT_ZOOM + encoder.steps, MIN_ZOOM, MAX_ZOOM)


def on_button_press():
    global show_overlay, needs_redraw
    show_overlay = not show_overlay
    logging.info(f"show_overlay={show_overlay}")
    needs_redraw = True


# =========================================================
# Main
# =========================================================
def main():
    global encoder, current_zoom, needs_redraw

    if not TILE_ROOT.exists():
        logging.warning(f"Offline tile folder not found: {TILE_ROOT}")

    encoder = RotaryEncoder(
        a=ENCODER_CLK_PIN,
        b=ENCODER_DT_PIN,
        wrap=False
    )
    encoder.steps = 0

    button = Button(ENCODER_SW_PIN, pull_up=True, bounce_time=0.2)
    button.when_pressed = on_button_press

    epd = epd2in9_V2.EPD()
    logging.info("Initializing display...")
    epd.init()
    epd.Clear(0xFF)

    current_zoom = DEFAULT_ZOOM
    needs_redraw = True

    try:
        while True:
            new_zoom = zoom_from_steps()

            if new_zoom != current_zoom:
                current_zoom = new_zoom
                needs_redraw = True

                if current_zoom == MIN_ZOOM and encoder.steps < (MIN_ZOOM - DEFAULT_ZOOM):
                    encoder.steps = MIN_ZOOM - DEFAULT_ZOOM
                elif current_zoom == MAX_ZOOM and encoder.steps > (MAX_ZOOM - DEFAULT_ZOOM):
                    encoder.steps = MAX_ZOOM - DEFAULT_ZOOM

            if needs_redraw:
                logging.info(f"Rendering zoom={current_zoom}, show_overlay={show_overlay}")

                img = render_map(
                    USER_LAT,
                    USER_LON,
                    current_zoom,
                    EPD_WIDTH,
                    EPD_HEIGHT
                )

                if show_overlay:
                    img = draw_overlay(
                        img,
                        USER_LAT,
                        USER_LON,
                        current_zoom
                    )

                epd.display(epd.getbuffer(img))
                needs_redraw = False

            time.sleep(0.05)

    except KeyboardInterrupt:
        logging.info("Exiting...")

    finally:
        try:
            epd.sleep()
        except Exception as e:
            logging.warning(f"epd.sleep() failed: {e}")


if __name__ == "__main__":
    main()
