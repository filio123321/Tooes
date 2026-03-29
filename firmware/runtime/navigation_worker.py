"""Background navigation worker that keeps sensors and SDR off the UI thread."""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from pathlib import Path

from firmware.hal import get_accel_reader, get_rotation_reader
from firmware.navigation import (
    ImuSampleProcessor,
    NavigationEngine,
    NavigationSnapshot,
    PathLogger,
    SdrFixProvider,
    load_navigation_config,
)


_log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class NavigationRuntimeSnapshot:
    navigation: NavigationSnapshot | None
    enabled: bool
    accel_backend: str | None
    rotation_backend: str | None
    path_log_path: str | None
    last_error: str | None


class NavigationWorker:
    """Owns navigation hardware and updates it on a background thread."""

    def __init__(self, repo_root: Path | None = None) -> None:
        self._repo_root = repo_root or Path(__file__).resolve().parents[2]
        self._config = load_navigation_config(self._repo_root)
        self._engine: NavigationEngine | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._snapshot = NavigationRuntimeSnapshot(
            navigation=None,
            enabled=False,
            accel_backend=None,
            rotation_backend=None,
            path_log_path=(
                str(self._config.path_log_path)
                if self._config.path_log_enabled and self._config.path_log_path
                else None
            ),
            last_error=None,
        )

    @property
    def config(self):
        return self._config

    def start(self) -> None:
        if self._thread is not None:
            return

        try:
            rotation = get_rotation_reader()
            accel = get_accel_reader()
            rotation_backend = type(rotation).__name__
            accel_backend = type(accel).__name__
            if "Stub" in rotation_backend or "Stub" in accel_backend:
                _log.warning(
                    "Navigation using stub hardware: rotation=%s accel=%s",
                    rotation_backend,
                    accel_backend,
                )
            else:
                _log.info(
                    "Navigation backends: rotation=%s accel=%s",
                    rotation_backend,
                    accel_backend,
                )

            sdr_provider = None
            if self._config.sdr_enabled:
                sdr_provider = SdrFixProvider(
                    driver=self._config.sdr_driver,
                    serial=self._config.sdr_serial,
                    catalogue_path=self._config.sdr_catalogue,
                    signal_types=self._config.sdr_types,
                )

            path_logger = None
            if self._config.path_log_enabled and self._config.path_log_path:
                path_logger = PathLogger(self._config.path_log_path)
                _log.info("Path logging to %s", self._config.path_log_path)

            processor = ImuSampleProcessor(
                accel=accel,
                rotation=rotation,
                gravity_time_constant_s=self._config.gravity_time_constant_s,
                linear_smoothing_window=self._config.linear_smoothing_window,
                stationary_linear_threshold_g=self._config.stationary_linear_threshold_g,
                stationary_magnitude_threshold_g=self._config.stationary_magnitude_threshold_g,
            )
            self._engine = NavigationEngine(
                config=self._config,
                sample_processor=processor,
                sdr_provider=sdr_provider,
                path_logger=path_logger,
            )
            self._store_snapshot(
                navigation=self._engine.snapshot(),
                enabled=True,
                accel_backend=accel_backend,
                rotation_backend=rotation_backend,
                last_error=None,
            )
        except Exception as exc:
            _log.exception("Failed to start navigation worker")
            self._store_snapshot(
                navigation=None,
                enabled=False,
                accel_backend=None,
                rotation_backend=None,
                last_error=str(exc),
            )
            return

        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        assert self._engine is not None

        prev_time = time.monotonic()
        interval_s = 1.0 / max(self._config.update_hz, 1.0)
        while not self._stop_event.is_set():
            now = time.monotonic()
            dt_s = max(now - prev_time, 1e-3)
            prev_time = now
            try:
                snapshot = self._engine.update(dt_s=dt_s, now_s=now)
                current = self.get_snapshot()
                self._store_snapshot(
                    navigation=snapshot,
                    enabled=True,
                    accel_backend=current.accel_backend,
                    rotation_backend=current.rotation_backend,
                    last_error=None,
                )
            except Exception as exc:
                _log.warning("Navigation worker update failed", exc_info=True)
                current = self.get_snapshot()
                self._store_snapshot(
                    navigation=current.navigation,
                    enabled=False,
                    accel_backend=current.accel_backend,
                    rotation_backend=current.rotation_backend,
                    last_error=str(exc),
                )
                time.sleep(0.25)
                continue

            elapsed = time.monotonic() - now
            remaining = interval_s - elapsed
            if remaining > 0:
                self._stop_event.wait(remaining)

    def get_snapshot(self) -> NavigationRuntimeSnapshot:
        with self._lock:
            return self._snapshot

    def _store_snapshot(
        self,
        *,
        navigation: NavigationSnapshot | None,
        enabled: bool,
        accel_backend: str | None,
        rotation_backend: str | None,
        last_error: str | None,
    ) -> None:
        with self._lock:
            self._snapshot = NavigationRuntimeSnapshot(
                navigation=navigation,
                enabled=enabled,
                accel_backend=accel_backend,
                rotation_backend=rotation_backend,
                path_log_path=(
                    str(self._config.path_log_path)
                    if self._config.path_log_enabled and self._config.path_log_path
                    else None
                ),
                last_error=last_error,
            )

    def close(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._engine is not None:
            self._engine.close()
