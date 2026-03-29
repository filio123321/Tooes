"""Navigation engine combining IMU relative motion with SDR anchor corrections."""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass

from firmware.navigation.config import NavigationConfig
from firmware.navigation.geo import clamp, enu_to_latlon, latlon_to_enu
from firmware.navigation.imu import ImuSampleProcessor, ProcessedImuSample, RelativePathTracker
from firmware.navigation.path_logger import PathLogger
from firmware.navigation.sdr import SdrFix, SdrFixProvider
from firmware.navigation.trace import TraceHistory


_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NavigationSnapshot:
    lat: float
    lon: float
    heading_deg: float
    relative_x_m: float
    relative_y_m: float
    distance_since_anchor_m: float
    trace_points: tuple[tuple[float, float], ...]
    fix_source: str
    sdr_pending: bool
    sdr_accuracy_m: float | None


class NavigationEngine:
    def __init__(
        self,
        config: NavigationConfig,
        sample_processor: ImuSampleProcessor | None = None,
        sdr_provider: SdrFixProvider | None = None,
        path_logger: PathLogger | None = None,
    ) -> None:
        self._config = config
        self._sample_processor = sample_processor
        self._path_logger = path_logger
        self._path = RelativePathTracker(
            step_length_m=config.step_length_m,
            peak_threshold_g=config.peak_threshold_g,
            min_step_seconds=config.min_step_seconds,
        )
        self._trace = TraceHistory(max_points=config.trace_max_points)
        self._trace.append(config.initial_lat, config.initial_lon, "INITIAL")

        self._anchor_lat = config.initial_lat
        self._anchor_lon = config.initial_lon
        self._anchor_rel_x_m = 0.0
        self._anchor_rel_y_m = 0.0
        self._lat = config.initial_lat
        self._lon = config.initial_lon
        self._heading_deg = 0.0
        self._fix_source = "INITIAL"
        self._sdr_accuracy_m: float | None = None

        self._sdr_provider = sdr_provider
        self._sdr_pending = False
        self._last_sdr_request_at = float("-inf")
        self._pending_sdr_fix: SdrFix | None = None
        self._sdr_lock = threading.Lock()
        self._closed = False
        self._log_path_point(
            lat=self._lat,
            lon=self._lon,
            relative_x_m=0.0,
            relative_y_m=0.0,
            source="INITIAL",
            distance_since_anchor_m=0.0,
            sdr_accuracy_m=None,
        )

    def update(self, dt_s: float, now_s: float | None = None) -> NavigationSnapshot:
        if self._sample_processor is None:
            raise RuntimeError("NavigationEngine.update() requires a sample processor")
        now_s = time.monotonic() if now_s is None else now_s
        sample = self._sample_processor.sample(dt_s=dt_s, timestamp_s=now_s)
        return self.update_with_sample(sample, now_s=now_s)

    def update_with_sample(
        self,
        sample: ProcessedImuSample,
        now_s: float | None = None,
    ) -> NavigationSnapshot:
        now_s = sample.timestamp_s if now_s is None else now_s
        previous_rel_x_m, previous_rel_y_m = self._path.get_position()
        rel_x_m, rel_y_m = self._path.update(sample)
        self._heading_deg = sample.heading_deg

        east_m = rel_x_m - self._anchor_rel_x_m
        north_m = rel_y_m - self._anchor_rel_y_m
        self._lat, self._lon = enu_to_latlon(
            east_m,
            north_m,
            self._anchor_lat,
            self._anchor_lon,
        )
        position_changed = (
            not math.isclose(rel_x_m, previous_rel_x_m, abs_tol=1e-9)
            or not math.isclose(rel_y_m, previous_rel_y_m, abs_tol=1e-9)
        )
        if position_changed:
            self._log_path_point(
                lat=self._lat,
                lon=self._lon,
                relative_x_m=rel_x_m,
                relative_y_m=rel_y_m,
                source="IMU",
                distance_since_anchor_m=self.distance_since_anchor_m,
                sdr_accuracy_m=self._sdr_accuracy_m,
            )

        self._trace.append_if_far_enough(
            self._lat,
            self._lon,
            self._config.trace_point_distance_m,
            "IMU",
        )

        self._consume_pending_sdr_fix()
        self._maybe_request_sdr(now_s)
        return self.snapshot()

    def snapshot(self) -> NavigationSnapshot:
        rel_x_m, rel_y_m = self._path.get_position()
        return NavigationSnapshot(
            lat=self._lat,
            lon=self._lon,
            heading_deg=self._heading_deg,
            relative_x_m=rel_x_m,
            relative_y_m=rel_y_m,
            distance_since_anchor_m=self.distance_since_anchor_m,
            trace_points=tuple(self._trace.as_tuples()),
            fix_source=self._fix_source,
            sdr_pending=self._sdr_pending,
            sdr_accuracy_m=self._sdr_accuracy_m,
        )

    @property
    def distance_since_anchor_m(self) -> float:
        return self._path.distance_from(self._anchor_rel_x_m, self._anchor_rel_y_m)

    def needs_sdr_scan(self, now_s: float | None = None) -> bool:
        if self._sdr_provider is None or self._closed:
            return False
        now_s = time.monotonic() if now_s is None else now_s
        if self._sdr_pending:
            return False
        if self.distance_since_anchor_m < self._config.trigger_distance_m:
            return False
        return (now_s - self._last_sdr_request_at) >= self._config.sdr_min_interval_s

    def _maybe_request_sdr(self, now_s: float) -> None:
        if not self.needs_sdr_scan(now_s):
            return

        self._sdr_pending = True
        self._last_sdr_request_at = now_s
        worker = threading.Thread(target=self._run_sdr_scan, daemon=True)
        worker.start()

    def _run_sdr_scan(self) -> None:
        fix: SdrFix | None = None
        try:
            if self._sdr_provider is not None:
                fix = self._sdr_provider.scan_once()
        except Exception:
            _log.warning("SDR scan failed", exc_info=True)
        finally:
            with self._sdr_lock:
                self._pending_sdr_fix = fix
                self._sdr_pending = False

    def _consume_pending_sdr_fix(self) -> None:
        with self._sdr_lock:
            fix = self._pending_sdr_fix
            self._pending_sdr_fix = None

        if fix is None:
            return
        self.apply_sdr_fix(fix)

    def apply_sdr_fix(self, fix: SdrFix) -> None:
        rel_x_m, rel_y_m = self._path.get_position()
        effective_accuracy_m = max(
            fix.accuracy_m,
            self._config.sdr_confidence_radius_m,
        )

        predicted_lat = self._lat
        predicted_lon = self._lon
        delta_east_m, delta_north_m = latlon_to_enu(
            fix.lat,
            fix.lon,
            predicted_lat,
            predicted_lon,
        )
        weight = clamp(
            self.distance_since_anchor_m / max(effective_accuracy_m, 1.0),
            self._config.sdr_blend_floor,
            self._config.sdr_blend_cap,
        )
        fused_lat, fused_lon = enu_to_latlon(
            delta_east_m * weight,
            delta_north_m * weight,
            predicted_lat,
            predicted_lon,
        )

        self._anchor_lat = fused_lat
        self._anchor_lon = fused_lon
        self._anchor_rel_x_m = rel_x_m
        self._anchor_rel_y_m = rel_y_m
        self._lat = fused_lat
        self._lon = fused_lon
        self._fix_source = "RF_BLEND"
        self._sdr_accuracy_m = effective_accuracy_m
        self._trace.append(fused_lat, fused_lon, "RF_BLEND")
        self._log_path_point(
            lat=fused_lat,
            lon=fused_lon,
            relative_x_m=rel_x_m,
            relative_y_m=rel_y_m,
            source="RF_BLEND",
            distance_since_anchor_m=0.0,
            sdr_accuracy_m=effective_accuracy_m,
        )

    def _log_path_point(
        self,
        *,
        lat: float,
        lon: float,
        relative_x_m: float,
        relative_y_m: float,
        source: str,
        distance_since_anchor_m: float,
        sdr_accuracy_m: float | None,
    ) -> None:
        if self._path_logger is None:
            return
        self._path_logger.log_point(
            lat=lat,
            lon=lon,
            relative_x_m=relative_x_m,
            relative_y_m=relative_y_m,
            source=source,
            distance_since_anchor_m=distance_since_anchor_m,
            sdr_accuracy_m=sdr_accuracy_m,
        )

    def close(self) -> None:
        self._closed = True
        if self._sdr_provider is not None:
            self._sdr_provider.close()
        if self._path_logger is not None:
            self._path_logger.close()
