"""Queue-backed background runtime for navigation and RF scanning."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from pathlib import Path
from queue import Empty, Full, Queue

from firmware.hal import CellKey, get_accel_reader, get_rotation_reader, get_sweep_source
from firmware.navigation.config import load_navigation_config
from firmware.navigation.imu import ImuSampleProcessor
from firmware.navigation.path_logger import PathLogger
from firmware.navigation.sdr import SdrFixProvider
from firmware.navigation.service import NavigationEngine, NavigationSnapshot
from firmware.tower_data import CatalogTower, load_catalog_towers
from firmware.opencellid import lookup_tower
from firmware.ui.state import RuntimeSnapshot
from firmware.ui.state import DiscoveredTower

_log = logging.getLogger(__name__)


class FirmwareOrchestrator:
    """Owns sensor threads and publishes a single, non-blocking runtime snapshot."""

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root
        self._nav_config = load_navigation_config(repo_root)
        self.catalog_towers: tuple[CatalogTower, ...] = tuple(load_catalog_towers())

        self._lock = threading.Lock()
        self._update_queue: Queue[RuntimeSnapshot] = Queue(maxsize=1)
        self._stop_event = threading.Event()
        self._scan_request = threading.Event()
        self._nav_thread: threading.Thread | None = None
        self._scan_thread: threading.Thread | None = None
        self._navigation: NavigationEngine | None = None
        self._snapshot = RuntimeSnapshot(
            user_lat=self._nav_config.initial_lat,
            user_lon=self._nav_config.initial_lon,
            heading_deg=0.0,
            trace_points=((self._nav_config.initial_lat, self._nav_config.initial_lon),),
            towers=tuple(),
            scan_done=False,
            scan_active=False,
            nav_ready=False,
            sdr_pending=False,
            sdr_accuracy_m=None,
        )

    def start(self) -> None:
        if self._nav_thread is not None:
            return
        self._nav_thread = threading.Thread(target=self._nav_loop, daemon=True)
        self._nav_thread.start()

    def request_scan(self) -> None:
        if self._scan_thread is not None and self._scan_thread.is_alive():
            return
        self._scan_request.set()
        self._scan_thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._scan_thread.start()

    def snapshot(self) -> RuntimeSnapshot:
        self._drain_updates()
        with self._lock:
            return self._snapshot

    @property
    def config(self):
        return self._nav_config

    def close(self) -> None:
        self._stop_event.set()
        if self._navigation is not None:
            self._navigation.close()
        for thread in (self._nav_thread, self._scan_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=1.0)

    def _build_navigation(self) -> NavigationEngine | None:
        try:
            rotation = get_rotation_reader()
            accel = get_accel_reader()
        except Exception as exc:
            _log.warning("Navigation unavailable: %s", exc)
            return None

        rotation_backend = type(rotation).__name__
        accel_backend = type(accel).__name__
        if "Stub" in rotation_backend or "Stub" in accel_backend:
            _log.warning(
                "Navigation backends using stub hardware: rotation=%s accel=%s",
                rotation_backend,
                accel_backend,
            )
        else:
            _log.info(
                "Navigation backends ready: rotation=%s accel=%s",
                rotation_backend,
                accel_backend,
            )

        sdr_provider = None
        if self._nav_config.sdr_enabled:
            sdr_provider = SdrFixProvider(
                driver=self._nav_config.sdr_driver,
                serial=self._nav_config.sdr_serial,
                catalogue_path=self._nav_config.sdr_catalogue,
                signal_types=self._nav_config.sdr_types,
            )

        path_logger = None
        if self._nav_config.path_log_enabled and self._nav_config.path_log_path:
            path_logger = PathLogger(self._nav_config.path_log_path)
            _log.info("Path logging to %s", self._nav_config.path_log_path)

        processor = ImuSampleProcessor(
            accel=accel,
            rotation=rotation,
            gravity_time_constant_s=self._nav_config.gravity_time_constant_s,
            linear_smoothing_window=self._nav_config.linear_smoothing_window,
            stationary_linear_threshold_g=self._nav_config.stationary_linear_threshold_g,
            stationary_magnitude_threshold_g=self._nav_config.stationary_magnitude_threshold_g,
        )
        return NavigationEngine(
            config=self._nav_config,
            sample_processor=processor,
            sdr_provider=sdr_provider,
            path_logger=path_logger,
        )

    def _nav_loop(self) -> None:
        navigation = self._build_navigation()
        if navigation is None:
            return

        self._navigation = navigation
        _log.info("Navigation loop running at %.1f Hz", self._nav_config.update_hz)
        prev_time = time.monotonic()
        while not self._stop_event.is_set():
            now = time.monotonic()
            dt_s = max(now - prev_time, 1e-3)
            prev_time = now

            try:
                nav_snapshot = navigation.update(dt_s=dt_s, now_s=now)
            except Exception:
                _log.warning("Navigation update failed", exc_info=True)
                if self._stop_event.wait(0.1):
                    break
                continue

            self._publish_navigation(nav_snapshot)
            interval_s = max(1.0 / max(self._nav_config.update_hz, 1.0), 0.01)
            elapsed = time.monotonic() - now
            remaining = interval_s - elapsed
            if remaining > 0 and self._stop_event.wait(remaining):
                break

    def _publish_navigation(self, nav_snapshot: NavigationSnapshot) -> None:
        with self._lock:
            self._snapshot = replace(
                self._snapshot,
                user_lat=nav_snapshot.lat,
                user_lon=nav_snapshot.lon,
                heading_deg=nav_snapshot.heading_deg,
                trace_points=nav_snapshot.trace_points,
                nav_ready=True,
                sdr_pending=nav_snapshot.sdr_pending,
                sdr_accuracy_m=nav_snapshot.sdr_accuracy_m,
            )
            self._push_snapshot(self._snapshot)

    def _scan_loop(self) -> None:
        if not self._scan_request.is_set():
            return

        seen: dict[CellKey, DiscoveredTower] = {}
        tower_counter = 0
        try:
            source = get_sweep_source()
            with self._lock:
                self._snapshot = replace(self._snapshot, scan_active=True, scan_done=False)
                self._push_snapshot(self._snapshot)

            for sample in source:
                if self._stop_event.is_set():
                    break
                for cell_key, rssi in sample.cells.items():
                    if cell_key not in seen or rssi > seen[cell_key].best_rssi:
                        coords = lookup_tower(
                            cell_key.mcc,
                            cell_key.mnc,
                            cell_key.lac,
                            cell_key.ci,
                        )
                        if cell_key not in seen:
                            tower_counter += 1
                            label = f"T{tower_counter}"
                        else:
                            label = seen[cell_key].label

                        seen[cell_key] = DiscoveredTower(
                            key=cell_key,
                            lat=coords.lat if coords else None,
                            lon=coords.lon if coords else None,
                            best_rssi=rssi,
                            label=label,
                        )
                        with self._lock:
                            self._snapshot = replace(self._snapshot, towers=tuple(seen.values()))
                            self._push_snapshot(self._snapshot)
        except Exception:
            _log.error("Scan error", exc_info=True)
        finally:
            self._scan_request.clear()
            with self._lock:
                self._snapshot = replace(
                    self._snapshot,
                    towers=tuple(seen.values()),
                    scan_done=True,
                    scan_active=False,
                )
                self._push_snapshot(self._snapshot)

    def _push_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        try:
            self._update_queue.put_nowait(snapshot)
        except Full:
            try:
                self._update_queue.get_nowait()
            except Empty:
                pass
            try:
                self._update_queue.put_nowait(snapshot)
            except Full:
                pass

    def _drain_updates(self) -> None:
        latest = None
        while True:
            try:
                latest = self._update_queue.get_nowait()
            except Empty:
                break
        if latest is None:
            return
        with self._lock:
            self._snapshot = latest
