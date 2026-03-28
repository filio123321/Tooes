"""Placeholder rotation reader that increments azimuth on each call.

Replace with a real IMU / magnetometer reader on the Pi.
"""

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
