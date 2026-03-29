from __future__ import annotations

import logging
import math
import time

from .kalman import KalmanFilter, enu_to_latlon, latlon_to_enu
from .models import PositionEstimate
from .sdr_module import SDRModule
from .trilateration import trilaterate

_log = logging.getLogger(__name__)


class FusionEngine:
    def __init__(
        self,
        sdr: SDRModule,
        sigma_a: float = 0.1,
        origin: tuple[float, float] | None = None,
    ) -> None:
        self._sdr = sdr
        self._kf  = KalmanFilter(sigma_a)
        self._origin = origin
        self._last_rf_time = 0.0

    def feed_imu(
        self,
        ax_body: float,
        ay_body: float,
        heading_deg: float,
        dt: float,
    ) -> None:
        """Propagate Kalman filter with IMU acceleration.

        Rotates acceleration from body frame to world (ENU) frame using the provided heading,
        then calls KalmanFilter.predict(). Safe to call before the first RF fix is available.

        Args:
            ax_body:     Forward acceleration in body frame (m/s²).
            ay_body:     Lateral acceleration in body frame (m/s²).
            heading_deg: Current heading in degrees (0 = North, 90 = East).
            dt:          Time since last call in seconds.
        """
        psi = math.radians(heading_deg)
        ax_world = ax_body * math.sin(psi) + ay_body * math.cos(psi)
        ay_world = ax_body * math.cos(psi) - ay_body * math.sin(psi)
        self._kf.predict(ax_world, ay_world, dt)

    def step(self) -> PositionEstimate | None:
        """Run one SDR scan cycle and return a fused position estimate.

        Returns None until the first RF fix has been accepted by the Kalman filter.
        Never raises.
        """
        measurements = self._sdr.scan()
        rf_applied = False

        rf_result = trilaterate(measurements, origin=self._origin)
        if rf_result is not None:
            lat_rf, lon_rf, acc_rf = rf_result

            if self._origin is None:
                # First fix — anchor the ENU coordinate system
                self._origin = (lat_rf, lon_rf)
                self._kf.x[0] = 0.0
                self._kf.x[1] = 0.0

            px, py = latlon_to_enu(lat_rf, lon_rf, *self._origin)
            if self._kf.update(px, py, acc_rf):
                self._last_rf_time = time.time()
                rf_applied = True

        if not self._kf.initialized:
            return None

        lat, lon = enu_to_latlon(self._kf.x[0], self._kf.x[1], *self._origin)  # type: ignore[misc]
        speed_ms = math.sqrt(self._kf.x[2] ** 2 + self._kf.x[3] ** 2)
        # atan2(East, North) → heading from North; East = vx, North = vy
        heading_deg = math.degrees(math.atan2(self._kf.x[2], self._kf.x[3])) % 360.0

        return PositionEstimate(
            lat=lat,
            lon=lon,
            accuracy_m=self._kf.accuracy_m,
            speed_ms=speed_ms,
            heading_deg=heading_deg,
            source="RF_UPDATE" if rf_applied else "IMU",
            n_rf_sources=len(measurements),
            last_rf_age=time.time() - self._last_rf_time,
        )
