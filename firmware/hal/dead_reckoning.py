"""Dead-reckoning position tracker using accelerometer + magnetometer.

Uses ZUPT (Zero-Velocity Update) to limit drift: when the measured
acceleration magnitude is close to 1 g the device is assumed stationary
and velocity is reset to zero.
"""

from __future__ import annotations

import math
from typing import Protocol, runtime_checkable

from firmware.hal.protocols import AccelerationReader, RotationReader

_G = 9.81  # m/s²
_ZUPT_THRESHOLD_G = 0.06  # |magnitude - 1g| below this → stationary


class DeadReckoningTracker:
    """Tracks (x, y) position in metres relative to the start point.

    Call :meth:`update` once per frame with the elapsed *dt*.  Internally
    the tracker reads the accelerometer and heading, removes gravity,
    integrates twice, and applies ZUPT when the device is still.
    """

    def __init__(
        self,
        accel: AccelerationReader,
        rotation: RotationReader,
        zupt_threshold_g: float = _ZUPT_THRESHOLD_G,
    ) -> None:
        self._accel = accel
        self._rotation = rotation
        self._zupt_thresh = zupt_threshold_g

        self._x = 0.0
        self._y = 0.0
        self._vx = 0.0
        self._vy = 0.0
        self._heading_deg = 0.0

    # -- public API -----------------------------------------------------------

    def update(self, dt: float) -> tuple[float, float]:
        """Advance the tracker by *dt* seconds and return (x, y)."""
        if dt <= 0:
            return self._x, self._y

        ax_g, ay_g, az_g = self._accel.read_accel_g()
        self._heading_deg = self._rotation.read_azimuth()

        mag = math.sqrt(ax_g * ax_g + ay_g * ay_g + az_g * az_g)

        if abs(mag - 1.0) < self._zupt_thresh:
            self._vx = 0.0
            self._vy = 0.0
            return self._x, self._y

        # Remove gravity (assume sensor Z is roughly "up").
        lin_x = ax_g * _G
        lin_y = ay_g * _G

        # Rotate sensor-frame horizontal acceleration into world frame
        # using the magnetometer heading.
        heading_rad = math.radians(self._heading_deg)
        cos_h = math.cos(heading_rad)
        sin_h = math.sin(heading_rad)

        world_ax = lin_x * cos_h - lin_y * sin_h
        world_ay = lin_x * sin_h + lin_y * cos_h

        self._vx += world_ax * dt
        self._vy += world_ay * dt

        self._x += self._vx * dt
        self._y += self._vy * dt

        return self._x, self._y

    def get_position(self) -> tuple[float, float]:
        return self._x, self._y

    def get_heading(self) -> float:
        return self._heading_deg

    def distance_from(self, x0: float, y0: float) -> float:
        dx = self._x - x0
        dy = self._y - y0
        return math.sqrt(dx * dx + dy * dy)
