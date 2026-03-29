"""Display shell for Tooes.

This module owns the e-paper display and user input only. Sensor work and
position tracking live in the background runtime service.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from firmware.ui.geo import MAX_ZOOM, MIN_ZOOM, clamp
from firmware.ui.runtime import UiRuntime
from firmware.ui.screens import render_boot, render_map, render_scanning, render_tutorial
from firmware.ui.state import RenderState, Screen, UiState

_log = logging.getLogger(__name__)

EPD_WIDTH = 296
EPD_HEIGHT = 128
DEFAULT_ZOOM = 16
MAP_MENU_ITEM_COUNT = 4

ENCODER_CLK = 5
ENCODER_DT = 6
ENCODER_SW = 13


class App:
    """Top-level UI shell that renders snapshots from the runtime service."""

    def __init__(self) -> None:
        self._repo_root = Path(__file__).resolve().parent.parent.parent
        self._ui = UiState(zoom=DEFAULT_ZOOM)
        self._runtime = UiRuntime(self._repo_root)
        self._needs_redraw = True
        self._scan_done_at: float | None = None
        self._button_hold_triggered = False
        self._menu_encoder_steps = 0
        self._last_runtime_snapshot = self._runtime.snapshot()

        self._epd = None
        self._encoder = None
        self._button = None

        _log.info("Loaded %s catalog towers for map rendering", len(self._runtime.catalog_towers))

    # ----- hardware init -----

    def _init_display(self) -> None:
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

    def _init_controls(self) -> None:
        from gpiozero import Button, RotaryEncoder  # type: ignore[import]

        self._encoder = RotaryEncoder(a=ENCODER_CLK, b=ENCODER_DT, wrap=False)
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

    # ----- button handler -----

    def _on_button_release(self) -> None:
        if self._button_hold_triggered:
            self._button_hold_triggered = False
            return

        if self._ui.screen == Screen.TUTORIAL:
            self._ui.tutorial_page += 1
            if self._ui.tutorial_page >= 3:
                self._ui.screen = Screen.SCANNING
                self._runtime.request_scan()
                self._scan_done_at = None
            self._needs_redraw = True
            return

        if self._ui.screen == Screen.SCANNING and self._runtime.snapshot().scan_done:
            self._ui.screen = Screen.MAP
            self._needs_redraw = True
            return

        if self._ui.screen == Screen.MAP and self._ui.menu_open:
            self._activate_menu_item()

    def _on_button_hold(self) -> None:
        if self._ui.screen != Screen.MAP:
            return

        self._button_hold_triggered = True
        if self._ui.menu_open:
            self._close_menu()
        else:
            self._open_menu()
        self._needs_redraw = True

    def _open_menu(self) -> None:
        self._ui.menu_open = True
        self._ui.menu_index = 0
        if self._encoder:
            self._menu_encoder_steps = int(self._encoder.steps)
        _log.info("Map menu opened")

    def _close_menu(self) -> None:
        self._ui.menu_open = False
        self._sync_encoder_to_zoom()
        _log.info("Map menu closed")

    def _activate_menu_item(self) -> None:
        if self._ui.menu_index == 0:
            self._close_menu()
        elif self._ui.menu_index == 1:
            self._ui.show_overlay = not self._ui.show_overlay
            _log.info("Signal marker toggled: %s", self._ui.show_overlay)
        elif self._ui.menu_index == 2:
            self._ui.show_catalog_towers = not self._ui.show_catalog_towers
            _log.info("Catalog towers toggled: %s", self._ui.show_catalog_towers)
        elif self._ui.menu_index == 3:
            self._ui.show_trace = not self._ui.show_trace
            _log.info("Trace toggled: %s", self._ui.show_trace)
        self._needs_redraw = True

    # ----- controls -----

    def _read_zoom(self) -> None:
        if not self._encoder:
            return

        if self._ui.menu_open:
            self._read_menu_selection()
            return

        new_zoom = clamp(DEFAULT_ZOOM + self._encoder.steps, MIN_ZOOM, MAX_ZOOM)
        if new_zoom != self._ui.zoom:
            self._ui.zoom = new_zoom
            self._needs_redraw = True

        min_steps = MIN_ZOOM - DEFAULT_ZOOM
        max_steps = MAX_ZOOM - DEFAULT_ZOOM
        if self._encoder.steps < min_steps:
            self._encoder.steps = min_steps
        elif self._encoder.steps > max_steps:
            self._encoder.steps = max_steps

    def _read_menu_selection(self) -> None:
        if not self._encoder:
            return

        current_steps = int(self._encoder.steps)
        delta = current_steps - self._menu_encoder_steps
        if delta == 0:
            return

        self._ui.menu_index = (self._ui.menu_index + delta) % MAP_MENU_ITEM_COUNT
        self._menu_encoder_steps = current_steps
        self._needs_redraw = True

    def _sync_encoder_to_zoom(self) -> None:
        if not self._encoder:
            return

        target_steps = self._ui.zoom - DEFAULT_ZOOM
        self._encoder.steps = target_steps
        self._menu_encoder_steps = int(self._encoder.steps)

    # ----- rendering -----

    def _render(self):
        w, h = EPD_WIDTH, EPD_HEIGHT
        runtime = self._runtime.snapshot()
        frame = RenderState(
            screen=self._ui.screen,
            tutorial_page=self._ui.tutorial_page,
            zoom=self._ui.zoom,
            heading_deg=runtime.heading_deg,
            user_lat=runtime.user_lat,
            user_lon=runtime.user_lon,
            trace_points=runtime.trace_points,
            towers=runtime.towers,
            catalog_towers=self._runtime.catalog_towers,
            scan_done=runtime.scan_done,
            scan_active=runtime.scan_active,
            show_overlay=self._ui.show_overlay,
            show_catalog_towers=self._ui.show_catalog_towers,
            show_trace=self._ui.show_trace,
            menu_open=self._ui.menu_open,
            menu_index=self._ui.menu_index,
        )

        if frame.screen == Screen.BOOT:
            return render_boot(w, h)
        if frame.screen == Screen.TUTORIAL:
            return render_tutorial(w, h, frame.tutorial_page)
        if frame.screen == Screen.SCANNING:
            return render_scanning(w, h, frame)
        if frame.screen == Screen.MAP:
            return render_map(w, h, frame)

    def _show(self, img) -> None:
        if self._epd and img:
            self._epd.display(self._epd.getbuffer(img))

    # ----- main loop -----

    def run(self) -> None:
        _log.info("Starting Tooes...")
        self._runtime.start()
        self._init_display()
        self._init_controls()

        self._ui.screen = Screen.BOOT
        self._show(self._render())
        time.sleep(2)

        self._ui.screen = Screen.TUTORIAL
        self._ui.tutorial_page = 0
        self._needs_redraw = True

        try:
            while True:
                now = time.monotonic()

                runtime = self._runtime.snapshot()
                if runtime != self._last_runtime_snapshot:
                    self._needs_redraw = True
                    self._last_runtime_snapshot = runtime

                if self._ui.screen == Screen.SCANNING and runtime.scan_done:
                    if self._scan_done_at is None:
                        self._scan_done_at = now
                    if now - self._scan_done_at > 2.0:
                        self._ui.screen = Screen.MAP
                        self._needs_redraw = True

                if self._ui.screen == Screen.MAP:
                    self._read_zoom()

                if self._needs_redraw:
                    self._show(self._render())
                    self._needs_redraw = False

                time.sleep(0.05)

        except KeyboardInterrupt:
            _log.info("Shutting down...")
        finally:
            self._runtime.close()
            if self._epd:
                try:
                    self._epd.sleep()
                except Exception:
                    pass


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    App().run()


if __name__ == "__main__":
    main()
