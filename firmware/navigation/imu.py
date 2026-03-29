"""Streaming IMU preprocessing and relative path tracking."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from firmware.hal.protocols import AccelerationReader, RotationReader


def _vector_norm(vector: tuple[float, float, float]) -> float:
    x, y, z = vector
    return math.sqrt((x * x) + (y * y) + (z * z))


def _vector_subtract(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(a - b for a, b in zip(left, right))


class LowPassVectorFilter:
    """Time-constant low-pass filter adapted from the MPU logger prototype."""

    def __init__(
        self,
        time_constant_s: float,
        initial: tuple[float, float, float] | None = None,
    ) -> None:
        self._time_constant_s = max(time_constant_s, 1e-6)
        self._value = initial

    def update(
        self,
        sample: tuple[float, float, float],
        dt_s: float,
    ) -> tuple[float, float, float]:
        if self._value is None:
            self._value = sample
            return sample

        alpha = math.exp(-max(dt_s, 1e-6) / self._time_constant_s)
        self._value = tuple(
            (alpha * previous) + ((1.0 - alpha) * current)
            for previous, current in zip(self._value, sample)
        )
        return self._value


class MovingAverageVector:
    """Small moving average for the linear acceleration signal."""

    def __init__(self, window_size: int) -> None:
        self._samples: deque[tuple[float, float, float]] = deque(
            maxlen=max(window_size, 1)
        )

    def update(self, sample: tuple[float, float, float]) -> tuple[float, float, float]:
        self._samples.append(sample)
        count = len(self._samples)
        sx = sum(item[0] for item in self._samples)
        sy = sum(item[1] for item in self._samples)
        sz = sum(item[2] for item in self._samples)
        return (sx / count, sy / count, sz / count)


@dataclass(frozen=True)
class ProcessedImuSample:
    timestamp_s: float
    dt_s: float
    heading_deg: float
    accel_g: tuple[float, float, float]
    gravity_g: tuple[float, float, float]
    linear_g: tuple[float, float, float]
    linear_avg_g: tuple[float, float, float]
    linear_avg_mag_g: float
    stationary: bool


class ImuSampleProcessor:
    """Produces filtered per-sample IMU data for online path estimation."""

    def __init__(
        self,
        accel: AccelerationReader,
        rotation: RotationReader,
        gravity_time_constant_s: float = 0.75,
        linear_smoothing_window: int = 5,
        stationary_linear_threshold_g: float = 0.08,
        stationary_magnitude_threshold_g: float = 0.12,
    ) -> None:
        self._accel = accel
        self._rotation = rotation
        self._gravity = LowPassVectorFilter(gravity_time_constant_s)
        self._linear_avg = MovingAverageVector(linear_smoothing_window)
        self._stationary_linear_threshold_g = max(stationary_linear_threshold_g, 0.0)
        self._stationary_magnitude_threshold_g = max(
            stationary_magnitude_threshold_g, 0.0
        )

    def sample(self, dt_s: float, timestamp_s: float) -> ProcessedImuSample:
        accel_g = self._accel.read_accel_g()
        heading_deg = self._rotation.read_azimuth()
        gravity_g = self._gravity.update(accel_g, dt_s)
        linear_g = _vector_subtract(accel_g, gravity_g)
        linear_avg_g = self._linear_avg.update(linear_g)
        accel_mag_g = _vector_norm(accel_g)
        linear_avg_mag_g = _vector_norm(linear_avg_g)
        stationary = (
            abs(accel_mag_g - 1.0) <= self._stationary_magnitude_threshold_g
            and linear_avg_mag_g <= self._stationary_linear_threshold_g
        )
        return ProcessedImuSample(
            timestamp_s=timestamp_s,
            dt_s=dt_s,
            heading_deg=heading_deg,
            accel_g=accel_g,
            gravity_g=gravity_g,
            linear_g=linear_g,
            linear_avg_g=linear_avg_g,
            linear_avg_mag_g=linear_avg_mag_g,
            stationary=stationary,
        )


class RelativePathTracker:
    """Streaming step-based path tracker adapted from the plotting prototype."""

    def __init__(
        self,
        step_length_m: float = 0.70,
        peak_threshold_g: float = 0.12,
        min_step_seconds: float = 0.35,
    ) -> None:
        self._step_length_m = max(step_length_m, 0.0)
        self._peak_threshold_g = max(peak_threshold_g, 0.0)
        self._min_step_seconds = max(min_step_seconds, 0.0)
        self._window: deque[ProcessedImuSample] = deque(maxlen=3)
        self._last_step_time = float("-inf")
        self._x_m = 0.0
        self._y_m = 0.0

    def update(self, sample: ProcessedImuSample) -> tuple[float, float]:
        self._window.append(sample)
        if len(self._window) < 3:
            return self._x_m, self._y_m

        previous, middle, current = self._window
        self._maybe_apply_step(previous, middle, current)
        return self._x_m, self._y_m

    def _maybe_apply_step(
        self,
        previous: ProcessedImuSample,
        middle: ProcessedImuSample,
        current: ProcessedImuSample,
    ) -> None:
        if middle.stationary:
            return
        if middle.linear_avg_mag_g < self._peak_threshold_g:
            return
        if middle.linear_avg_mag_g < previous.linear_avg_mag_g:
            return
        if middle.linear_avg_mag_g <= current.linear_avg_mag_g:
            return
        if middle.timestamp_s - self._last_step_time < self._min_step_seconds:
            return

        heading_rad = math.radians(middle.heading_deg)
        self._x_m += self._step_length_m * math.sin(heading_rad)
        self._y_m += self._step_length_m * math.cos(heading_rad)
        self._last_step_time = middle.timestamp_s

    def get_position(self) -> tuple[float, float]:
        return self._x_m, self._y_m

    def distance_from(self, x0: float, y0: float) -> float:
        dx = self._x_m - x0
        dy = self._y_m - y0
        return math.sqrt((dx * dx) + (dy * dy))
