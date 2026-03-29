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
from firmware.hal import (
    CellKey,
    get_accel_reader,
    get_rotation_reader,
    get_sweep_source,
)
from firmware.navigation import (
    ImuSampleProcessor,
    NavigationEngine,
    NavigationSnapshot,
    SdrFixProvider,
    load_navigation_config,
)
from firmware.navigation.geo import haversine_m
from firmware.opencellid import lookup_tower
from firmware.tower_data import CatalogTower, load_catalog_towers

_log = logging.getLogger(__name__)

# ---- Display ----
EPD_WIDTH = 296
EPD_HEIGHT = 128
DEFAULT_ZOOM = 16
MAP_MENU_ITEM_COUNT = 4

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
        self._repo_root = Path(__file__).resolve().parent.parent.parent
        self._nav_config = load_navigation_config(self._repo_root)
        self.screen: Screen = Screen.BOOT
        self.tutorial_page: int = 0
        self.zoom: int = DEFAULT_ZOOM
        self.heading_deg: float = 0.0
        self.user_lat: float = self._nav_config.initial_lat
        self.user_lon: float = self._nav_config.initial_lon
        self.trace_points: List[tuple[float, float]] = []
        self.towers: List[DiscoveredTower] = []
        self.catalog_towers: List[CatalogTower] = load_catalog_towers()
        self.scan_done: bool = False
        self.show_overlay: bool = False
        self.show_catalog_towers: bool = True
        self.show_trace: bool = False
        self.menu_open: bool = False
        self.menu_index: int = 0

        self._needs_redraw: bool = True
        self._tower_counter: int = 0
        self._scan_done_at: Optional[float] = None
        self._button_hold_triggered: bool = False
        self._menu_encoder_steps: int = 0
        self._navigation: NavigationEngine | None = None
        self._last_nav_snapshot: NavigationSnapshot | None = None

        # Hardware handles (assigned in run())
        self._epd = None
        self._encoder = None
        self._button = None
        self._compass = None

        _log.info("Loaded %s catalog towers for map rendering", len(self.catalog_towers))
        self.set_user_position(self.user_lat, self.user_lon)

    # ----- hardware init -----

    def _init_display(self):
        waveshare_lib = (
            self._repo_root
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
        self._button = Button(
            ENCODER_SW,
            pull_up=True,
            bounce_time=0.2,
            hold_time=0.7,
        )
        self._button.hold_repeat = False
        self._button.when_released = self._on_button_release
        self._button.when_held = self._on_button_hold
        _log.info("Controls initialized")

    def _init_compass(self):
        try:
            self._compass = get_rotation_reader()
            _log.info(
                "Rotation reader initialized (%s)",
                type(self._compass).__name__,
            )
        except Exception as e:
            _log.warning("Rotation reader unavailable: %s — heading will stay at 0", e)
            self._compass = None

    def _init_navigation(self):
        if not self._compass:
            _log.warning("Navigation disabled: no rotation reader available")
            return

        try:
            accel = get_accel_reader()
        except Exception as e:
            _log.warning("Navigation disabled: no accelerometer available (%s)", e)
            return

        sdr_provider = None
        if self._nav_config.sdr_enabled:
            sdr_provider = SdrFixProvider(
                driver=self._nav_config.sdr_driver,
                serial=self._nav_config.sdr_serial,
                catalogue_path=self._nav_config.sdr_catalogue,
                signal_types=self._nav_config.sdr_types,
            )

        processor = ImuSampleProcessor(
            accel=accel,
            rotation=self._compass,
            gravity_time_constant_s=self._nav_config.gravity_time_constant_s,
            linear_smoothing_window=self._nav_config.linear_smoothing_window,
            stationary_linear_threshold_g=self._nav_config.stationary_linear_threshold_g,
            stationary_magnitude_threshold_g=self._nav_config.stationary_magnitude_threshold_g,
        )
        self._navigation = NavigationEngine(
            config=self._nav_config,
            sample_processor=processor,
            sdr_provider=sdr_provider,
        )
        self._apply_navigation_snapshot(self._navigation.snapshot())
        _log.info(
            "Navigation initialized from INITIAL_L=%.6f,%.6f",
            self.user_lat,
            self.user_lon,
        )

    # ----- button handler -----

    def _on_button_release(self):
        if self._button_hold_triggered:
            self._button_hold_triggered = False
            return

        if self.screen == Screen.TUTORIAL:
            self.tutorial_page += 1
            if self.tutorial_page >= 3:
                self.screen = Screen.MAP
            self._needs_redraw = True

        elif self.screen == Screen.SCANNING and self.scan_done:
            # Skip the auto-transition timer — jump to map now
            self._finish_scan()
            self.screen = Screen.MAP
            self._needs_redraw = True

        elif self.screen == Screen.MAP and self.menu_open:
            self._activate_menu_item()

    def _on_button_hold(self):
        if self.screen != Screen.MAP:
            return

        self._button_hold_triggered = True
        if self.menu_open:
            self._close_menu()
        else:
            self._open_menu()
        self._needs_redraw = True

    def _open_menu(self):
        self.menu_open = True
        self.menu_index = 0
        if self._encoder:
            self._menu_encoder_steps = int(self._encoder.steps)
        _log.info("Map menu opened")

    def _close_menu(self):
        self.menu_open = False
        self._sync_encoder_to_zoom()
        _log.info("Map menu closed")

    def _activate_menu_item(self):
        if self.menu_index == 0:
            self._close_menu()
        elif self.menu_index == 1:
            self.show_overlay = not self.show_overlay
            _log.info("Signal marker toggled: %s", self.show_overlay)
        elif self.menu_index == 2:
            self.show_catalog_towers = not self.show_catalog_towers
            _log.info("Catalog towers toggled: %s", self.show_catalog_towers)
        elif self.menu_index == 3:
            self.show_trace = not self.show_trace
            _log.info("Trace toggled: %s", self.show_trace)
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
            self.set_user_position(wlat / total_w, wlon / total_w)
            _log.info(
                "Estimated position: %.5f, %.5f", self.user_lat, self.user_lon
            )

    def set_user_position(self, lat: float, lon: float, record_trace: bool = True):
        self.user_lat = lat
        self.user_lon = lon
        if record_trace:
            self._record_trace_point(lat, lon)

    def _record_trace_point(self, lat: float, lon: float):
        if self.trace_points:
            last_lat, last_lon = self.trace_points[-1]
            if abs(last_lat - lat) < 1e-7 and abs(last_lon - lon) < 1e-7:
                return
        self.trace_points.append((lat, lon))
        if len(self.trace_points) > self._nav_config.trace_max_points:
            self.trace_points = self.trace_points[-self._nav_config.trace_max_points :]

    def _apply_navigation_snapshot(self, snapshot: NavigationSnapshot):
        self.heading_deg = snapshot.heading_deg
        self.user_lat = snapshot.lat
        self.user_lon = snapshot.lon
        self.trace_points = list(snapshot.trace_points)
        self._last_nav_snapshot = snapshot

    def _update_navigation(self, dt_s: float, now_s: float):
        if self._navigation is None:
            return

        snapshot = self._navigation.update(dt_s=dt_s, now_s=now_s)
        previous = self._last_nav_snapshot
        self._apply_navigation_snapshot(snapshot)

        if previous is None:
            self._needs_redraw = True
            return

        moved_m = haversine_m(previous.lat, previous.lon, snapshot.lat, snapshot.lon)
        heading_delta = abs(snapshot.heading_deg - previous.heading_deg)
        if heading_delta > 180:
            heading_delta = 360 - heading_delta

        if (
            moved_m >= self._nav_config.redraw_distance_m
            or heading_delta > 15
            or len(snapshot.trace_points) != len(previous.trace_points)
            or snapshot.fix_source != previous.fix_source
            or snapshot.sdr_pending != previous.sdr_pending
        ):
            self._needs_redraw = True

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

        if self.menu_open:
            self._read_menu_selection()
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

    def _read_menu_selection(self):
        if not self._encoder:
            return

        current_steps = int(self._encoder.steps)
        delta = current_steps - self._menu_encoder_steps
        if delta == 0:
            return

        self.menu_index = (self.menu_index + delta) % MAP_MENU_ITEM_COUNT
        self._menu_encoder_steps = current_steps
        self._needs_redraw = True

    def _sync_encoder_to_zoom(self):
        if not self._encoder:
            return

        target_steps = self.zoom - DEFAULT_ZOOM
        self._encoder.steps = target_steps
        self._menu_encoder_steps = int(self._encoder.steps)

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
                self.show_catalog_towers,
                self.show_trace,
                self.trace_points,
                self.menu_open,
                self.menu_index,
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
        self._init_navigation()

        # Boot splash
        self.screen = Screen.BOOT
        self._show(self._render())
        time.sleep(2)

        # Tutorial
        self.screen = Screen.TUTORIAL
        self.tutorial_page = 0
        self._needs_redraw = True
        prev_time = time.monotonic()

        try:
            while True:
                now = time.monotonic()
                dt_s = max(now - prev_time, 1e-3)
                prev_time = now

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
                    self._update_navigation(dt_s, now)

                # Redraw when state changed
                if self._needs_redraw:
                    self._show(self._render())
                    self._needs_redraw = False

                time.sleep(0.05)

        except KeyboardInterrupt:
            _log.info("Shutting down...")
        finally:
            if self._navigation:
                self._navigation.close()
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
