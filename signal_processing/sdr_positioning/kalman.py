from __future__ import annotations

import math
import time

import numpy as np


# ---------------------------------------------------------------------------
# Coordinate helpers (module-level so fusion.py can import them directly)
# ---------------------------------------------------------------------------

def latlon_to_enu(
    lat: float, lon: float, origin_lat: float, origin_lon: float
) -> tuple[float, float]:
    """Convert lat/lon to ENU (East px, North py) offset in metres from origin."""
    px = (lon - origin_lon) * 111320.0 * math.cos(math.radians(origin_lat))
    py = (lat - origin_lat) * 110540.0
    return px, py


def enu_to_latlon(
    px: float, py: float, origin_lat: float, origin_lon: float
) -> tuple[float, float]:
    """Convert ENU offset in metres back to lat/lon."""
    lon = origin_lon + px / (111320.0 * math.cos(math.radians(origin_lat)))
    lat = origin_lat + py / 110540.0
    return lat, lon


# ---------------------------------------------------------------------------
# 4D Kalman filter  [px, py, vx, vy] in local ENU metres
# ---------------------------------------------------------------------------

class KalmanFilter:
    # χ²(2DOF, p=0.95) thresholds for Mahalanobis outlier gating
    _GATE_NORMAL = 5.991
    _GATE_WIDE   = 13.816  # used during warm-up to accept first RF fixes
    _WARM_UP_SEC = 10.0

    def __init__(self, sigma_a: float = 0.1) -> None:
        """
        Args:
            sigma_a: Process noise standard deviation (m/s²). Default 0.1 m/s².
        """
        self.x = np.zeros(4)                          # [px, py, vx, vy]
        self.P = np.diag([1e6, 1e6, 1e4, 1e4])       # large initial uncertainty
        self._sigma_a  = sigma_a
        self._start_t  = time.time()
        self.initialized = False                       # True after first accepted update()

    @property
    def accuracy_m(self) -> float:
        """RMS positional standard deviation from diagonal of P."""
        return math.sqrt((self.P[0, 0] + self.P[1, 1]) / 2.0)

    def predict(self, ax_world: float, ay_world: float, dt: float) -> None:
        """Propagate state with constant-acceleration model.

        Args:
            ax_world: Acceleration in world East direction (m/s²).
            ay_world: Acceleration in world North direction (m/s²).
            dt:       Time step in seconds.

        Note: body→world frame rotation is performed by FusionEngine, not here.
        """
        F = np.array([
            [1.0, 0.0,  dt, 0.0],
            [0.0, 1.0, 0.0,  dt],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ])
        B = np.array([
            [0.5 * dt ** 2, 0.0           ],
            [0.0,           0.5 * dt ** 2 ],
            [dt,            0.0           ],
            [0.0,           dt            ],
        ])
        q = self._sigma_a ** 2
        Q = B @ np.diag([q, q]) @ B.T

        self.x = F @ self.x + B @ np.array([ax_world, ay_world])
        self.P = F @ self.P @ F.T + Q

    def update(self, px_rf: float, py_rf: float, accuracy_m: float) -> bool:
        """Apply an RF position measurement.

        Returns True if the measurement was accepted, False if rejected by the outlier gate.
        """
        H = np.array([[1.0, 0.0, 0.0, 0.0],
                      [0.0, 1.0, 0.0, 0.0]])
        R = np.diag([accuracy_m ** 2, accuracy_m ** 2])

        z = np.array([px_rf, py_rf])
        innovation = z - H @ self.x
        S = H @ self.P @ H.T + R
        S_inv = np.linalg.inv(S)
        d_sq = float(innovation @ S_inv @ innovation)

        gate = (self._GATE_WIDE
                if (time.time() - self._start_t) < self._WARM_UP_SEC
                else self._GATE_NORMAL)
        if d_sq > gate:
            return False  # outlier — reject

        K = self.P @ H.T @ S_inv
        self.x = self.x + K @ innovation
        self.P = (np.eye(4) - K @ H) @ self.P
        self.initialized = True
        return True
