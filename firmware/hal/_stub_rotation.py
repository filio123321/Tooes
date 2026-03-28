"""Placeholder rotation and tilt readers for tests without hardware."""

from __future__ import annotations


class StubRotationReader:
    """Returns monotonically increasing azimuth, wrapping at 360."""

    def __init__(self, step_deg: float = 10.0) -> None:
        self._azimuth = 0.0
        self._step = step_deg

    def read_azimuth(self) -> float:
        az = self._azimuth
        self._azimuth = (self._azimuth + self._step) % 360.0
        return az


class StubTiltReader:
    """Always reports perfectly level (pitch=0, roll=0)."""

    def read_pitch_roll(self) -> tuple[float, float]:
        return (0.0, 0.0)


class StubAccelerationReader:
    """Reports stationary (gravity pointing down, no lateral acceleration)."""

    def read_accel_g(self) -> tuple[float, float, float]:
        return (0.0, 0.0, 1.0)
