#!/usr/bin/env python3
"""Walking-sweep POC — dead-reckoning position tracker + mock (or real) cell reader.

Run on the Raspberry Pi desktop or locally with stubs:

    # All stubs (desktop testing):
    python3 -m firmware.scripts.sweep_poc

    # Real sensors + mock radio on the Pi:
    HAL_ROTATION=qmc5883l HAL_TILT=mpu6050 HAL_ACCEL=mpu6050 \
    HAL_CELLS=mock HAL_TRIGGER_DISTANCE=2.0 \
        python3 -m firmware.scripts.sweep_poc

Requires: pygame
"""

from __future__ import annotations

import math
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import pygame

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))

from firmware.hal.types import CellKey
from firmware.hal.factory import (
    get_accel_reader,
    get_cell_reader,
    get_rotation_reader,
)
from firmware.hal.dead_reckoning import DeadReckoningTracker
from firmware.hal.mock_cells import TOWER_POSITIONS

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WIN_W, WIN_H = 1024, 600
MAP_W, MAP_H = 600, 600
BAR_W = WIN_W - MAP_W  # 424

TRIGGER_DISTANCE_M = float(os.environ.get("HAL_TRIGGER_DISTANCE", "2.0"))
FPS = 30
MIN_VIEW_RANGE = 20.0  # metres — minimum visible range so the view isn't tiny

CELL_COLORS: Dict[CellKey, Tuple[int, int, int]] = {
    CellKey(284, 1, 1000, 101): (230, 80, 80),
    CellKey(284, 1, 1000, 102): (80, 200, 80),
    CellKey(284, 3, 3400, 201): (80, 140, 230),
}

DEFAULT_CELL_COLOR = (180, 180, 180)

BG_MAP = (25, 25, 30)
BG_BAR = (35, 35, 40)
GRID_COLOR = (55, 55, 60)
PATH_COLOR = (200, 200, 210)
TEXT_COLOR = (220, 220, 220)
HEADING_COLOR = (255, 220, 60)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class Measurement:
    x: float
    y: float
    cells: Dict[CellKey, float]


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def _cell_color(cell: CellKey) -> Tuple[int, int, int]:
    return CELL_COLORS.get(cell, DEFAULT_CELL_COLOR)


def _cell_label(cell: CellKey) -> str:
    return f"{cell.mcc}/{cell.mnc}/{cell.lac}/{cell.ci}"


def _strongest_cell(cells: Dict[CellKey, float]) -> CellKey | None:
    if not cells:
        return None
    return max(cells, key=cells.get)  # type: ignore[arg-type]


class ViewTransform:
    """Maps world (x, y) in metres to pixel coordinates inside the map panel."""

    def __init__(self, path: List[Tuple[float, float]], towers: Dict[CellKey, Tuple[float, float]]) -> None:
        all_pts = list(path) + list(towers.values())
        if not all_pts:
            all_pts = [(0.0, 0.0)]

        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]

        cx = (min(xs) + max(xs)) / 2
        cy = (min(ys) + max(ys)) / 2
        span_x = max(max(xs) - min(xs), MIN_VIEW_RANGE)
        span_y = max(max(ys) - min(ys), MIN_VIEW_RANGE)
        span = max(span_x, span_y) * 1.3  # 30 % padding

        self._cx = cx
        self._cy = cy
        self._scale = (MAP_W - 40) / span  # px per metre
        self._ox = MAP_W // 2
        self._oy = MAP_H // 2

    def to_px(self, x: float, y: float) -> Tuple[int, int]:
        px = int(self._ox + (x - self._cx) * self._scale)
        py = int(self._oy - (y - self._cy) * self._scale)  # y-up → screen y-down
        return px, py

    @property
    def metres_per_grid(self) -> float:
        """Rounded grid spacing in metres that gives ~60-120 px per line."""
        raw = 80.0 / self._scale
        magnitude = 10 ** math.floor(math.log10(max(raw, 0.01)))
        for nice in (1, 2, 5, 10):
            if magnitude * nice >= raw:
                return magnitude * nice
        return magnitude * 10


def draw_grid(surface: pygame.Surface, vt: ViewTransform) -> None:
    step = vt.metres_per_grid
    x_lo = vt._cx - (MAP_W / 2) / vt._scale
    x_hi = vt._cx + (MAP_W / 2) / vt._scale
    y_lo = vt._cy - (MAP_H / 2) / vt._scale
    y_hi = vt._cy + (MAP_H / 2) / vt._scale

    x = math.floor(x_lo / step) * step
    while x <= x_hi:
        px, _ = vt.to_px(x, 0)
        pygame.draw.line(surface, GRID_COLOR, (px, 0), (px, MAP_H))
        x += step

    y = math.floor(y_lo / step) * step
    while y <= y_hi:
        _, py = vt.to_px(0, y)
        pygame.draw.line(surface, GRID_COLOR, (0, py), (MAP_W, py))
        y += step


def draw_towers(surface: pygame.Surface, vt: ViewTransform, towers: Dict[CellKey, Tuple[float, float]]) -> None:
    font = pygame.font.SysFont("monospace", 11)
    for cell, (tx, ty) in towers.items():
        px, py = vt.to_px(tx, ty)
        color = _cell_color(cell)
        pts = [(px, py - 8), (px - 6, py + 5), (px + 6, py + 5)]
        pygame.draw.polygon(surface, color, pts)
        pygame.draw.polygon(surface, (0, 0, 0), pts, 1)
        label = font.render(f"ci={cell.ci}", True, color)
        surface.blit(label, (px + 8, py - 6))


def draw_path(surface: pygame.Surface, vt: ViewTransform, path: List[Tuple[float, float]]) -> None:
    if len(path) < 2:
        return
    points = [vt.to_px(x, y) for x, y in path]
    pygame.draw.lines(surface, PATH_COLOR, False, points, 1)


def draw_measurements(
    surface: pygame.Surface,
    vt: ViewTransform,
    measurements: List[Measurement],
) -> None:
    for m in measurements:
        px, py = vt.to_px(m.x, m.y)
        strongest = _strongest_cell(m.cells)
        color = _cell_color(strongest) if strongest else DEFAULT_CELL_COLOR
        pygame.draw.circle(surface, color, (px, py), 5)
        pygame.draw.circle(surface, (0, 0, 0), (px, py), 5, 1)


def draw_current_pos(
    surface: pygame.Surface,
    vt: ViewTransform,
    x: float,
    y: float,
    heading_deg: float,
) -> None:
    px, py = vt.to_px(x, y)
    pygame.draw.circle(surface, (255, 255, 255), (px, py), 6)
    pygame.draw.circle(surface, (0, 0, 0), (px, py), 6, 1)

    rad = math.radians(heading_deg)
    dx = math.sin(rad) * 18
    dy = -math.cos(rad) * 18  # screen y is inverted
    end_x = px + int(dx)
    end_y = py + int(dy)
    pygame.draw.line(surface, HEADING_COLOR, (px, py), (end_x, end_y), 2)


def draw_bar_panel(
    surface: pygame.Surface,
    measurements: List[Measurement],
    all_cells: List[CellKey],
    font: pygame.font.Font,
    big_font: pygame.font.Font,
) -> None:
    surface.fill(BG_BAR)

    title = big_font.render("RSSI (dBm)", True, TEXT_COLOR)
    surface.blit(title, (BAR_W // 2 - title.get_width() // 2, 12))

    if not measurements:
        hint = font.render("Walk to trigger first measurement", True, (120, 120, 120))
        surface.blit(hint, (BAR_W // 2 - hint.get_width() // 2, MAP_H // 2))
        return

    latest = measurements[-1].cells

    bar_area_top = 50
    bar_height = 28
    bar_gap = 12
    label_w = 160
    max_bar_w = BAR_W - label_w - 30

    rssi_min = -100.0
    rssi_max = -20.0

    for i, cell in enumerate(all_cells):
        y = bar_area_top + i * (bar_height + bar_gap)
        color = _cell_color(cell)

        lbl = font.render(_cell_label(cell), True, color)
        surface.blit(lbl, (8, y + (bar_height - lbl.get_height()) // 2))

        rssi = latest.get(cell, rssi_min)
        frac = max(0.0, min(1.0, (rssi - rssi_min) / (rssi_max - rssi_min)))
        bw = int(frac * max_bar_w)
        bar_rect = pygame.Rect(label_w, y, bw, bar_height)
        pygame.draw.rect(surface, color, bar_rect)
        pygame.draw.rect(surface, (0, 0, 0), bar_rect, 1)

        val = font.render(f"{rssi:.0f}", True, TEXT_COLOR)
        surface.blit(val, (label_w + bw + 6, y + (bar_height - val.get_height()) // 2))

    # Measurement count + distance
    info_y = bar_area_top + len(all_cells) * (bar_height + bar_gap) + 20
    info = font.render(f"Measurements: {len(measurements)}", True, TEXT_COLOR)
    surface.blit(info, (8, info_y))

    m = measurements[-1]
    pos = font.render(f"Pos: ({m.x:.1f}, {m.y:.1f}) m", True, TEXT_COLOR)
    surface.blit(pos, (8, info_y + 22))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    rotation = get_rotation_reader()
    accel = get_accel_reader()

    tracker = DeadReckoningTracker(accel=accel, rotation=rotation)

    cell_reader = get_cell_reader(position_fn=tracker.get_position)

    # Determine which towers to draw (use mock layout when available).
    try:
        towers = getattr(cell_reader, "towers", None) or dict(TOWER_POSITIONS)
    except Exception:
        towers = {}
    all_cells = sorted(towers.keys(), key=lambda c: c.to_tuple())

    pygame.init()
    screen = pygame.display.set_mode((WIN_W, WIN_H))
    pygame.display.set_caption("Sweep POC — walk to collect measurements")

    map_surface = pygame.Surface((MAP_W, MAP_H))
    bar_surface = pygame.Surface((BAR_W, MAP_H))

    font = pygame.font.SysFont("monospace", 14)
    big_font = pygame.font.SysFont("monospace", 18, bold=True)

    clock = pygame.time.Clock()

    path: List[Tuple[float, float]] = [(0.0, 0.0)]
    measurements: List[Measurement] = []
    last_meas_x, last_meas_y = 0.0, 0.0
    prev_time = time.monotonic()

    print(f"Sweep POC running  |  trigger every {TRIGGER_DISTANCE_M:.1f} m  |  close window or Esc to quit")

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_SPACE:
                    # Manual measurement trigger
                    x, y = tracker.get_position()
                    cells = cell_reader.read_cells()
                    measurements.append(Measurement(x, y, cells))
                    last_meas_x, last_meas_y = x, y
                    print(f"  [manual] measurement #{len(measurements)} at ({x:.1f}, {y:.1f})")

        now = time.monotonic()
        dt = now - prev_time
        prev_time = now

        x, y = tracker.update(dt)
        path.append((x, y))

        # Distance-triggered measurement
        dist = math.sqrt((x - last_meas_x) ** 2 + (y - last_meas_y) ** 2)
        if dist >= TRIGGER_DISTANCE_M:
            cells = cell_reader.read_cells()
            measurements.append(Measurement(x, y, cells))
            last_meas_x, last_meas_y = x, y
            print(f"  measurement #{len(measurements)} at ({x:.1f}, {y:.1f})")

        # --- Draw map panel --------------------------------------------------
        map_surface.fill(BG_MAP)
        vt = ViewTransform(path, towers)
        draw_grid(map_surface, vt)
        draw_towers(map_surface, vt, towers)
        draw_path(map_surface, vt, path)
        draw_measurements(map_surface, vt, measurements)
        draw_current_pos(map_surface, vt, x, y, tracker.get_heading())

        # Scale legend
        grid_m = vt.metres_per_grid
        legend = font.render(f"grid = {grid_m:.0f} m", True, (100, 100, 100))
        map_surface.blit(legend, (6, MAP_H - 20))

        # --- Draw bar panel --------------------------------------------------
        draw_bar_panel(bar_surface, measurements, all_cells, font, big_font)

        # --- Compose ---------------------------------------------------------
        screen.blit(map_surface, (0, 0))
        screen.blit(bar_surface, (MAP_W, 0))

        heading = tracker.get_heading()
        pygame.display.set_caption(
            f"Sweep POC  |  ({x:.1f}, {y:.1f}) m  |  hdg {heading:.0f}°  |  {len(measurements)} meas"
        )
        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()
    print(f"Done — {len(measurements)} measurements collected.")


if __name__ == "__main__":
    main()
