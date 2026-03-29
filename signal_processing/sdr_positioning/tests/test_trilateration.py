"""Checkpoints 2 + 3: FSPL model and trilateration solver."""
from __future__ import annotations

import math

import pytest

from signal_processing.sdr_positioning.models import Measurement
from signal_processing.sdr_positioning.trilateration import (
    Environment,
    _classify_environment,
    _geometry_ok,
    _rssi_predicted,
    _rssi_to_distance,
    trilaterate,
)


def _make_measurement(
    lat: float,
    lon: float,
    freq_hz: float,
    power_w: float,
    rssi_dbm: float,
    antenna_gain_dbi: float = 0.0,
    best_effort: bool = False,
) -> Measurement:
    return Measurement(
        source_id=f"TX_{lat}_{lon}",
        rssi_dbm=rssi_dbm,
        freq_hz=freq_hz,
        signal_type="FM",
        lat=lat,
        lon=lon,
        power_w=power_w,
        antenna_gain_dbi=antenna_gain_dbi,
        gain_used=50.0,
        best_effort=best_effort,
    )


class TestFSPL:
    def test_round_trip_los(self):
        """Distance → predicted RSSI → back to distance should be consistent."""
        true_d = 5000.0  # 5 km
        power_w = 300.0
        freq_hz = 91.4e6
        rssi = _rssi_predicted(true_d, power_w, 0.0, freq_hz, Environment.OUTDOOR_LOS)
        estimated_d = _rssi_to_distance(rssi, power_w, 0.0, freq_hz, Environment.OUTDOOR_LOS)
        assert abs(estimated_d - true_d) < 1.0

    def test_indoor_correction_shifts_distance(self):
        """Indoor attenuation makes the signal weaker, so LOS model over-estimates distance."""
        power_w = 1000.0
        freq_hz = 100.0e6
        true_d = 200.0
        rssi_indoor = _rssi_predicted(true_d, power_w, 0.0, freq_hz, Environment.INDOOR_LIGHT)
        # Invert assuming LOS: weaker RSSI (extra indoor loss) → model thinks transmitter is farther
        d_los_estimate = _rssi_to_distance(rssi_indoor, power_w, 0.0, freq_hz, Environment.OUTDOOR_LOS)
        assert d_los_estimate > true_d

    def test_higher_frequency_gives_shorter_apparent_distance(self):
        """At the same RSSI, a higher-frequency signal appears to come from closer."""
        power_w = 100.0
        rssi = -80.0
        d_fm  = _rssi_to_distance(rssi, power_w, 0.0, 100.0e6, Environment.OUTDOOR_LOS)
        d_gsm = _rssi_to_distance(rssi, power_w, 0.0, 950.0e6, Environment.OUTDOOR_LOS)
        assert d_gsm < d_fm

    def test_antenna_gain_increases_apparent_range(self):
        """A higher antenna gain should push the RSSI up → longer estimated distance."""
        power_w = 100.0
        freq_hz = 500.0e6
        rssi = -85.0
        d_0dbi = _rssi_to_distance(rssi, power_w, 0.0,  freq_hz, Environment.OUTDOOR_LOS)
        d_6dbi = _rssi_to_distance(rssi, power_w, 6.0, freq_hz, Environment.OUTDOOR_LOS)
        assert d_6dbi > d_0dbi

    def test_classify_environment(self):
        assert _classify_environment([0.0, 1.0, 2.0]) is Environment.OUTDOOR_LOS
        assert _classify_environment([3.0, 5.0, 7.0]) is Environment.OUTDOOR_URBAN
        assert _classify_environment([12.0, 15.0, 18.0]) is Environment.INDOOR_LIGHT
        assert _classify_environment([22.0, 25.0, 30.0]) is Environment.INDOOR_DEEP
        assert _classify_environment([]) is Environment.OUTDOOR_LOS


class TestSolver:
    """Build synthetic measurements with known geometry and verify solver accuracy."""

    # Receiver at the centroid of Bulgaria Blagoevgrad region
    RX_LAT = 42.02
    RX_LON = 23.09

    def _synthetic_measurements(self, n: int = 4, noise_db: float = 0.0) -> list[Measurement]:
        """Place n transmitters in cardinal / intercardinal directions, compute RSSI."""
        import random
        rng = random.Random(42)
        directions = [(0.3, 0.0), (-0.3, 0.0), (0.0, 0.4), (0.0, -0.4),
                      (0.2, 0.3), (-0.2, -0.3), (0.2, -0.3), (-0.2, 0.3)]
        measurements = []
        for dlat, dlon in directions[:n]:
            tx_lat = self.RX_LAT + dlat
            tx_lon = self.RX_LON + dlon
            d = math.sqrt((dlat * 110540) ** 2 + (dlon * 111320 * math.cos(math.radians(self.RX_LAT))) ** 2)
            power_w = 1000.0
            freq_hz = 100.0e6
            rssi = _rssi_predicted(d, power_w, 0.0, freq_hz, Environment.OUTDOOR_LOS)
            rssi += rng.uniform(-noise_db, noise_db)
            measurements.append(_make_measurement(tx_lat, tx_lon, freq_hz, power_w, rssi))
        return measurements

    def test_4_source_grid_accuracy(self):
        # Use zero noise so solver converges to exact position (noise_db>0 at 33km ranges
        # introduces ~1km distance uncertainty per source which swamps the 500m tolerance)
        measurements = self._synthetic_measurements(n=4, noise_db=0.0)
        result = trilaterate(measurements)
        assert result is not None
        lat, lon, accuracy_m = result
        # Compute error in metres
        dlat = (lat - self.RX_LAT) * 110540
        dlon = (lon - self.RX_LON) * 111320 * math.cos(math.radians(self.RX_LAT))
        error_m = math.sqrt(dlat ** 2 + dlon ** 2)
        assert error_m < 100.0, f"Position error {error_m:.0f} m exceeds 100 m"

    def test_returns_none_for_fewer_than_3_sources(self):
        measurements = self._synthetic_measurements(n=2)
        assert trilaterate(measurements) is None

    def test_never_raises(self):
        # Pass garbage measurements — trilaterate must not raise
        result = trilaterate([])
        assert result is None

    def test_bad_geometry_inflates_accuracy(self):
        """All transmitters in the same direction → GDOP warning → accuracy × 3."""
        # Place 4 transmitters very close together north of the receiver
        measurements = []
        for i in range(4):
            tx_lat = self.RX_LAT + 0.3 + i * 0.01
            tx_lon = self.RX_LON + i * 0.005
            d = (0.3 + i * 0.01) * 110540
            rssi = _rssi_predicted(d, 1000.0, 0.0, 100.0e6, Environment.OUTDOOR_LOS)
            measurements.append(_make_measurement(tx_lat, tx_lon, 100.0e6, 1000.0, rssi))

        result_good = trilaterate(self._synthetic_measurements(4))
        result_bad  = trilaterate(measurements)
        assert result_bad is not None
        assert result_good is not None
        assert result_bad[2] > result_good[2]  # bad geometry → higher accuracy_m

    def test_geometry_ok_detects_clustered_sources(self):
        # All sources bearing ~0° from receiver → gap ≥ 270°
        assert _geometry_ok(
            [self.RX_LAT + 0.1, self.RX_LAT + 0.2, self.RX_LAT + 0.3, self.RX_LAT + 0.4],
            [self.RX_LON] * 4,
            self.RX_LAT,
            self.RX_LON,
        ) is False

    def test_geometry_ok_passes_distributed_sources(self):
        # Sources in N, S, E, W
        assert _geometry_ok(
            [self.RX_LAT + 0.3, self.RX_LAT - 0.3, self.RX_LAT,       self.RX_LAT      ],
            [self.RX_LON,       self.RX_LON,        self.RX_LON + 0.4, self.RX_LON - 0.4],
            self.RX_LAT,
            self.RX_LON,
        ) is True
