from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Measurement:
    """A single normalised RF power reading from one catalogued transmitter."""
    source_id: str
    rssi_dbm: float           # true received power (gain removed)
    freq_hz: float
    signal_type: str
    lat: float                # transmitter latitude
    lon: float                # transmitter longitude
    power_w: float
    antenna_gain_dbi: float
    gain_used: float          # diagnostic only — never used in trilateration
    best_effort: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class PositionEstimate:
    """Fused position output from the Kalman filter."""
    lat: float
    lon: float
    accuracy_m: float
    speed_ms: float
    heading_deg: float
    source: str               # "RF_UPDATE" or "IMU"
    n_rf_sources: int
    last_rf_age: float        # seconds since last RF fix was accepted
