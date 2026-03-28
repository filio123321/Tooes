from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def catalogue_path() -> Path:
    return FIXTURES / "catalogue.json"


@pytest.fixture
def catalogue_entries(catalogue_path):
    from sdr_positioning.sdr_module.catalogue import CatalogueLoader
    return CatalogueLoader().load(catalogue_path)


@pytest.fixture
def fm_entry(catalogue_entries):
    return next(e for e in catalogue_entries if e.signal_type == "FM")


@pytest.fixture
def dvbt_entry(catalogue_entries):
    return next(e for e in catalogue_entries if e.signal_type == "DVB-T")


class MockReceiver:
    """Deterministic mock: read_power_dbm() returns TRUE_POWER + current gain.

    Setting TRUE_POWER = -60 ensures all signal types settle within their AGC
    target windows (FM, DVB-T settle on step 2; VOR, DAB settle on step 2;
    GSM settles on step 1).
    """
    TRUE_POWER: float = -60.0

    def __init__(self) -> None:
        self.gain: float = 0.0
        self._freq: float = 0.0
        self._rate: float = 0.0

    def set_gain(self, gain_db: float) -> None:
        self.gain = gain_db

    def set_freq(self, freq_hz: float) -> None:
        self._freq = freq_hz

    def set_sample_rate(self, rate_hz: float) -> None:
        self._rate = rate_hz

    def read_power_dbm(self, n_samples: int = 4096) -> float:
        return self.TRUE_POWER + self.gain

    def close(self) -> None:
        pass


class NeverSettlingReceiver(MockReceiver):
    """Simulates a fluctuating signal that keeps the AGC oscillating.

    Alternates between returning a reading 20 dB above and 20 dB below the
    linear model prediction, preventing the gain loop from settling in any
    signal type's target window while keeping true_power in a valid range.
    """
    TRUE_POWER: float = -65.0

    def __init__(self) -> None:
        super().__init__()
        self._call_count = 0

    def read_power_dbm(self, n_samples: int = 4096) -> float:
        self._call_count += 1
        offset = 20.0 if (self._call_count % 2 == 1) else -20.0
        return self.TRUE_POWER + self.gain + offset


@pytest.fixture
def mock_receiver() -> MockReceiver:
    return MockReceiver()
