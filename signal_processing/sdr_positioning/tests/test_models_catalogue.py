"""Checkpoint 1: Models and catalogue loading."""
from __future__ import annotations

import time
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from signal_processing.sdr_positioning.models import Measurement, PositionEstimate
from signal_processing.sdr_positioning.sdr_module.catalogue import CatalogueEntry, CatalogueLoader, TYPE_DEFAULTS

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestCatalogueEntry:
    def test_frozen(self, fm_entry):
        with pytest.raises((FrozenInstanceError, AttributeError)):
            fm_entry.lat = 0.0  # type: ignore[misc]

    def test_freq_hz_conversion(self, fm_entry):
        # 91.4 MHz → 91_400_000 Hz
        assert abs(fm_entry.freq_hz - 91_400_000.0) < 1.0

    def test_freq_hz_co_channel_key(self):
        # "538_bor" key → 538 MHz
        entries = CatalogueLoader().load(FIXTURES.parent.parent / "stations.json")
        bor_entries = [e for e in entries if e.station == "ТВРС Бор"]
        assert len(bor_entries) == 3
        freqs = sorted(e.freq_mhz for e in bor_entries)
        assert freqs == [538.0, 554.0, 570.0]

    def test_type_default_antenna_gain(self, fm_entry):
        # FM type default: 0.0 dBi; not overridden in fixture
        assert fm_entry.antenna_gain_dbi == pytest.approx(TYPE_DEFAULTS["FM"].antenna_gain_dbi)

    def test_json_override_antenna_gain(self, dvbt_entry):
        # DVB-T entry in fixture has explicit antenna_gain_dbi = 6.0
        assert dvbt_entry.antenna_gain_dbi == pytest.approx(6.0)

    def test_min_rssi_from_type_defaults(self, fm_entry):
        assert fm_entry.min_rssi_dbm == pytest.approx(TYPE_DEFAULTS["FM"].min_rssi_dbm)

    def test_source_id_format(self, fm_entry):
        # source_id should be stable and contain signal type, freq and station
        assert "FM" in fm_entry.source_id
        assert "91.4" in fm_entry.source_id
        assert "TX_North" in fm_entry.source_id

    def test_source_id_stability(self, catalogue_path):
        a = CatalogueLoader().load(catalogue_path)
        b = CatalogueLoader().load(catalogue_path)
        assert [e.source_id for e in a] == [e.source_id for e in b]

    def test_all_five_types_loaded(self, catalogue_entries):
        types = {e.signal_type for e in catalogue_entries}
        assert types == {"FM", "VOR", "DAB", "DVB-T", "GSM"}

    def test_unknown_type_skipped(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text('{"100.0": {"lat": 0, "lon": 0, "type": "UNKNOWN", "power_w": 1}}')
        entries = CatalogueLoader().load(bad)
        assert entries == []

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            CatalogueLoader().load(tmp_path / "nonexistent.json")


class TestMeasurementDataclass:
    def test_defaults(self):
        m = Measurement(
            source_id="FM_91.4_TX",
            rssi_dbm=-65.0,
            freq_hz=91_400_000.0,
            signal_type="FM",
            lat=42.0,
            lon=23.0,
            power_w=300.0,
            antenna_gain_dbi=0.0,
            gain_used=50.0,
        )
        assert m.best_effort is False

    def test_timestamp_auto_set(self):
        before = time.time()
        m = Measurement(
            source_id="x", rssi_dbm=0.0, freq_hz=0.0, signal_type="FM",
            lat=0.0, lon=0.0, power_w=1.0, antenna_gain_dbi=0.0, gain_used=0.0,
        )
        after = time.time()
        assert before <= m.timestamp <= after

    def test_mutable(self):
        m = Measurement(
            source_id="x", rssi_dbm=0.0, freq_hz=0.0, signal_type="FM",
            lat=0.0, lon=0.0, power_w=1.0, antenna_gain_dbi=0.0, gain_used=0.0,
        )
        m.best_effort = True
        assert m.best_effort is True
