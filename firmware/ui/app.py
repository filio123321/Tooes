"""Main application: screen manager, hardware init, and event loop."""

import logging
import sys
import time
import threading
from enum import Enum, auto
from pathlib import Path
from typing import List, Optional

from firmware.ui.geo import MIN_ZOOM, MAX_ZOOM, clamp
from firmware.ui.screens import (
    DiscoveredTower,
    render_boot,
    render_tutorial,
    render_scanning,
    render_map,
)
from firmware.hal import get_sweep_source, CellKey
from firmware.opencellid import lookup_tower
from firmware.tower_data import CatalogTower, load_catalog_towers

_log = logging.getLogger(__name__)

# ---- Display ----
EPD_WIDTH = 296
EPD_HEIGHT = 128
DEFAULT_ZOOM = 16

# ---- GPIO pins (BCM) for HW-040 rotary encoder ----
ENCODER_CLK = 5
ENCODER_DT = 6
ENCODER_SW = 13


class Screen(Enum):
    BOOT = auto()
    TUTORIAL = auto()
    SCANNING = auto()
    MAP = auto()


class App:
    """Top-level application that drives the e-paper UI."""

    def __init__(self):
        self.screen: Screen = Screen.BOOT
        self.tutorial_page: int = 0
        self.zoom: int = DEFAULT_ZOOM
        self.heading_deg: float = 0.0
        self.user_lat: float = 42.012280  # fallback until trilateration runs
        self.user_lon: float = 23.095261
        self.towers: List[DiscoveredTower] = []
        self.catalog_towers: List[CatalogTower] = load_catalog_towers()
        self.scan_done: bool = False
        self.show_overlay: bool = False

        self._needs_redraw: bool = True
        self._tower_counter: int = 0
        self._scan_done_at: Optional[float] = None

        # Hardware handles (assigned in run())
        self._epd = None
        self._encoder = None
        self._button = None
        self._compass = None

        _log.info("Loaded %s catalog towers for map rendering", len(self.catalog_towers))

    # ----- hardware init -----

    def _init_display(self):
        repo_root = Path(__file__).resolve().parent.parent.parent
        waveshare_lib = (
            repo_root
            / "external"
            / "waveshare-epd"
            / "RaspberryPi_JetsonNano"
            / "python"
            / "lib"
        )
        if str(waveshare_lib) not in sys.path:
            sys.path.insert(0, str(waveshare_lib))

        from waveshare_epd import epd2in9_V2  # type: ignore[import]

        self._epd = epd2in9_V2.EPD()
        self._epd.init()
        self._epd.Clear(0xFF)
        _log.info("Display initialized")

    def _init_controls(self):
        from gpiozero import RotaryEncoder, Button  # type: ignore[import]

        self._encoder = RotaryEncoder(
            a=ENCODER_CLK, b=ENCODER_DT, wrap=False
        )
        self._encoder.steps = 0
        self._button = Button(ENCODER_SW, pull_up=True, bounce_time=0.2)
        self._button.when_pressed = self._on_button
        _log.info("Controls initialized")

    def _init_compass(self):
        try:
            from firmware.hal.qmc5883l import QMC5883LRotationReader

            self._compass = QMC5883LRotationReader()
            _log.info("Compass initialized (QMC5883L)")
        except Exception as e:
            _log.warning("Compass unavailable: %s — heading will stay at 0", e)
            self._compass = None

    # ----- button handler -----

    def _on_button(self):
        if self.screen == Screen.TUTORIAL:
            self.tutorial_page += 1
            if self.tutorial_page >= 3:
                self.screen = Screen.SCANNING
                self._start_scan()
            self._needs_redraw = True

        elif self.screen == Screen.SCANNING and self.scan_done:
            # Skip the auto-transition timer — jump to map now
            self._finish_scan()
            self.screen = Screen.MAP
            self._needs_redraw = True

        elif self.screen == Screen.MAP:
            self.show_overlay = not self.show_overlay
            _log.info("Map overlay toggled: %s", self.show_overlay)
            self._needs_redraw = True

    # ----- scanning -----

    def _start_scan(self):
        self.towers = []
        self.scan_done = False
        self._tower_counter = 0
        self._scan_done_at = None
        thread = threading.Thread(target=self._scan_worker, daemon=True)
        thread.start()

    def _scan_worker(self):
        """Background thread: consume sweep samples, discover towers."""
        try:
            source = get_sweep_source()
            seen: dict[CellKey, DiscoveredTower] = {}

            for sample in source:
                for cell_key, rssi in sample.cells.items():
                    if (
                        cell_key not in seen
                        or rssi > seen[cell_key].best_rssi
                    ):
                        # Lookup tower coordinates
                        coords = lookup_tower(
                            cell_key.mcc,
                            cell_key.mnc,
                            cell_key.lac,
                            cell_key.ci,
                        )
                        if cell_key not in seen:
                            self._tower_counter += 1
                            label = f"T{self._tower_counter}"
                        else:
                            label = seen[cell_key].label

                        seen[cell_key] = DiscoveredTower(
                            key=cell_key,
                            lat=coords.lat if coords else None,
                            lon=coords.lon if coords else None,
                            best_rssi=rssi,
                            label=label,
                        )
                        # Atomic replacement — safe to read from render thread
                        self.towers = list(seen.values())
                        self._needs_redraw = True
        except Exception as e:
            _log.error("Scan error: %s", e)
        finally:
            self.scan_done = True
            self._needs_redraw = True

    def _finish_scan(self):
        """Estimate user position from discovered towers (RSSI-weighted centroid)."""
        resolved = [t for t in self.towers if t.lat is not None]
        if not resolved:
            return
        # Weight: stronger signal (less negative RSSI) → heavier weight
        total_w = 0.0
        wlat = 0.0
        wlon = 0.0
        for t in resolved:
            w = 10 ** (t.best_rssi / -20.0)
            wlat += t.lat * w
            wlon += t.lon * w
            total_w += w
        if total_w > 0:
            self.user_lat = wlat / total_w
            self.user_lon = wlon / total_w
            _log.info(
                "Estimated position: %.5f, %.5f", self.user_lat, self.user_lon
            )

    # ----- sensor reads -----

    def _read_heading(self) -> bool:
        """Read compass heading. Returns True if changed significantly (>15 deg)."""
        if not self._compass:
            return False
        try:
            new_hdg = self._compass.read_azimuth()
        except Exception:
            return False
        delta = abs(new_hdg - self.heading_deg)
        if delta > 180:
            delta = 360 - delta
        if delta > 15:
            self.heading_deg = new_hdg
            return True
        return False

    def _read_zoom(self):
        if not self._encoder:
            return
        new_zoom = clamp(
            DEFAULT_ZOOM + self._encoder.steps,
            MIN_ZOOM,
            MAX_ZOOM,
        )
        if new_zoom != self.zoom:
            self.zoom = new_zoom
            self._needs_redraw = True
        # Hard-clamp encoder steps so it stops at the limits
        min_steps = MIN_ZOOM - DEFAULT_ZOOM
        max_steps = MAX_ZOOM - DEFAULT_ZOOM
        if self._encoder.steps < min_steps:
            self._encoder.steps = min_steps
        elif self._encoder.steps > max_steps:
            self._encoder.steps = max_steps

    # ----- rendering -----

    def _render(self):
        w, h = EPD_WIDTH, EPD_HEIGHT

        if self.screen == Screen.BOOT:
            return render_boot(w, h)
        if self.screen == Screen.TUTORIAL:
            return render_tutorial(w, h, self.tutorial_page)
        if self.screen == Screen.SCANNING:
            return render_scanning(w, h, self.towers, self.scan_done)
        if self.screen == Screen.MAP:
            return render_map(
                w,
                h,
                self.user_lat,
                self.user_lon,
                self.zoom,
                self.towers,
                self.catalog_towers,
                self.show_overlay,
            )

    def _show(self, img):
        if self._epd and img:
            self._epd.display(self._epd.getbuffer(img))

    # ----- main loop -----

    def run(self):
        _log.info("Starting Tooes...")

        self._init_display()
        self._init_controls()
        self._init_compass()

        # Boot splash
        self.screen = Screen.BOOT
        self._show(self._render())
        time.sleep(2)

        # Tutorial
        self.screen = Screen.TUTORIAL
        self.tutorial_page = 0
        self._needs_redraw = True

        try:
            while True:
                now = time.time()

                # Auto-transition: scanning → map (2 s after scan finishes)
                if self.screen == Screen.SCANNING:
                    if self.scan_done and self._scan_done_at is None:
                        self._scan_done_at = now
                    if (
                        self._scan_done_at
                        and now - self._scan_done_at > 2.0
                    ):
                        self._finish_scan()
                        self.screen = Screen.MAP
                        self._needs_redraw = True

                # Map-screen controls
                if self.screen == Screen.MAP:
                    self._read_zoom()

                # Redraw when state changed
                if self._needs_redraw:
                    self._show(self._render())
                    self._needs_redraw = False

                time.sleep(0.05)

        except KeyboardInterrupt:
            _log.info("Shutting down...")
        finally:
            if self._epd:
                try:
                    self._epd.sleep()
                except Exception:
                    pass


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    App().run()


if __name__ == "__main__":
    main()
