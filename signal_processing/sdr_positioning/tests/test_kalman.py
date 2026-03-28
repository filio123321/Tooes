"""Checkpoint 5: Kalman filter predict / update / outlier rejection."""
from __future__ import annotations

import math
import time
from unittest.mock import patch

import numpy as np
import pytest

from sdr_positioning.kalman import KalmanFilter, enu_to_latlon, latlon_to_enu


class TestKalmanFilter:
    def test_initial_state_is_zero(self):
        kf = KalmanFilter()
        assert np.allclose(kf.x, 0.0)

    def test_not_initialized_before_update(self):
        kf = KalmanFilter()
        assert kf.initialized is False

    def test_predict_grows_position_uncertainty(self):
        kf = KalmanFilter()
        p00_before = kf.P[0, 0]
        for _ in range(10):
            kf.predict(0.0, 0.0, dt=0.1)
        assert kf.P[0, 0] > p00_before

    def test_predict_with_zero_accel_keeps_position_stable(self):
        kf = KalmanFilter()
        for _ in range(100):
            kf.predict(0.0, 0.0, dt=0.1)
        assert np.allclose(kf.x[:2], 0.0, atol=1e-9)

    def test_update_shrinks_uncertainty(self):
        kf = KalmanFilter()
        p00_before = kf.P[0, 0]
        accepted = kf.update(100.0, 200.0, accuracy_m=50.0)
        assert accepted is True
        assert kf.P[0, 0] < p00_before

    def test_update_moves_state_toward_measurement(self):
        kf = KalmanFilter()
        kf.update(500.0, 300.0, accuracy_m=30.0)
        assert kf.x[0] > 0.0  # pulled toward 500
        assert kf.x[1] > 0.0  # pulled toward 300

    def test_update_sets_initialized(self):
        kf = KalmanFilter()
        kf.update(10.0, 10.0, accuracy_m=20.0)
        assert kf.initialized is True

    def test_outlier_rejected_with_tight_covariance(self):
        kf = KalmanFilter()
        # Initialise with a close measurement to tighten P
        kf.update(0.0, 0.0, accuracy_m=10.0)
        # After P is tightened, wait out warm-up: large jump is rejected by normal gate
        with patch("sdr_positioning.kalman.time") as mock_time:
            mock_time.time.return_value = kf._start_t + 20.0  # past warm-up
            rejected = kf.update(50_000.0, 50_000.0, accuracy_m=10.0)
        assert rejected is False

    def test_wide_gate_accepts_outlier_during_warmup(self):
        # After tightening P (P[0,0]≈25), a measurement at z=(18,0) gives
        # Mahalanobis d²≈6.48: above normal gate (5.991) but below wide gate (13.816).
        kf = KalmanFilter()
        kf.update(0.0, 0.0, accuracy_m=5.0)  # tighten P to ~25 m²
        with patch("sdr_positioning.kalman.time") as mock_time:
            mock_time.time.return_value = kf._start_t + 1.0  # within 10s warm-up → wide gate
            accepted = kf.update(18.0, 0.0, accuracy_m=5.0)
        assert accepted is True

    def test_outlier_rejected_outside_warmup(self):
        # Same measurement at z=(18,0) should be rejected when past warm-up
        kf = KalmanFilter()
        kf.update(0.0, 0.0, accuracy_m=5.0)
        with patch("sdr_positioning.kalman.time") as mock_time:
            mock_time.time.return_value = kf._start_t + 20.0  # past warm-up → normal gate
            rejected = kf.update(18.0, 0.0, accuracy_m=5.0)
        assert rejected is False

    def test_accuracy_property(self):
        kf = KalmanFilter()
        assert kf.accuracy_m == pytest.approx(math.sqrt((1e6 + 1e6) / 2.0))
        kf.update(0.0, 0.0, accuracy_m=50.0)
        assert kf.accuracy_m < math.sqrt((1e6 + 1e6) / 2.0)


class TestCoordinateHelpers:
    def test_round_trip(self):
        origin_lat, origin_lon = 42.02, 23.09
        lat, lon = 42.15, 23.35
        px, py = latlon_to_enu(lat, lon, origin_lat, origin_lon)
        lat2, lon2 = enu_to_latlon(px, py, origin_lat, origin_lon)
        assert abs(lat2 - lat) < 1e-6
        assert abs(lon2 - lon) < 1e-6

    def test_origin_maps_to_zero(self):
        px, py = latlon_to_enu(42.02, 23.09, 42.02, 23.09)
        assert abs(px) < 1e-6
        assert abs(py) < 1e-6
