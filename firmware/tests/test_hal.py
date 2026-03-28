"""Tests for the HAL: mock source, JSONL replay, grgsm_scanner parser, and contracts."""

from __future__ import annotations

import math
import os
from pathlib import Path

import pytest

from firmware.hal.types import CellKey, SweepSample, SCHEMA_VERSION
from firmware.hal.protocols import SweepSampleSource
from firmware.hal.mock import MockSweepSource, DEFAULT_CELLS
from firmware.hal.replay import JsonlReplaySource
from firmware.hal.grgsm_scanner import parse_scanner_line, parse_scanner_output
from firmware.hal.factory import get_sweep_source

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDEN_PATH = FIXTURES / "golden_sweep.jsonl"


# ---------------------------------------------------------------------------
# types round-trip
# ---------------------------------------------------------------------------

class TestSweepSampleSerialization:
    def test_round_trip(self):
        original = SweepSample(
            t=1.5,
            azimuth_deg=90.0,
            cells={CellKey(284, 1, 1000, 101): -65.0},
        )
        restored = SweepSample.from_json(original.to_json())
        assert restored.t == original.t
        assert restored.azimuth_deg == original.azimuth_deg
        assert restored.cells == original.cells

    def test_schema_version_present(self):
        s = SweepSample(t=0, azimuth_deg=0, cells={})
        d = s.to_dict()
        assert d["schema_version"] == SCHEMA_VERSION

    def test_bad_schema_version_rejected(self):
        d = {"schema_version": 999, "t": 0, "azimuth_deg": 0, "cells": []}
        with pytest.raises(ValueError, match="Unsupported schema_version"):
            SweepSample.from_dict(d)


# ---------------------------------------------------------------------------
# MockSweepSource
# ---------------------------------------------------------------------------

class TestMockSweepSource:
    def test_yields_expected_count(self):
        samples = list(MockSweepSource(n_samples=10))
        assert len(samples) == 10

    def test_monotonic_time(self):
        samples = list(MockSweepSource(n_samples=20))
        times = [s.t for s in samples]
        assert times == sorted(times)
        assert times[0] == 0.0

    def test_azimuth_range(self):
        for s in MockSweepSource(n_samples=36):
            assert 0.0 <= s.azimuth_deg < 360.0

    def test_contains_default_cells(self):
        for s in MockSweepSource(n_samples=5):
            assert set(s.cells.keys()) == set(DEFAULT_CELLS)

    def test_rssi_finite(self):
        for s in MockSweepSource(n_samples=36):
            for rssi in s.cells.values():
                assert math.isfinite(rssi)

    def test_peak_rssi_near_peak_azimuth(self):
        samples = list(MockSweepSource(n_samples=360))
        cell = DEFAULT_CELLS[0]
        best = max(samples, key=lambda s: s.cells[cell])
        assert abs(best.azimuth_deg - 45.0) < 5.0


# ---------------------------------------------------------------------------
# jsonl_replay_source
# ---------------------------------------------------------------------------

class TestJsonlReplaySource:
    def test_loads_golden_fixture(self):
        samples = list(JsonlReplaySource(GOLDEN_PATH))
        assert len(samples) == 20

    def test_first_and_last_cells_match(self):
        samples = list(JsonlReplaySource(GOLDEN_PATH))
        assert set(samples[0].cells.keys()) == set(DEFAULT_CELLS)
        assert set(samples[-1].cells.keys()) == set(DEFAULT_CELLS)

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            # Generator is lazy — the check fires at first next() or inside
            # the function before the first yield, so just calling it is enough
            # since the path check is eager.
            list(JsonlReplaySource("/tmp/nonexistent_hal_test.jsonl"))


# ---------------------------------------------------------------------------
# grgsm_scanner parser
# ---------------------------------------------------------------------------

class TestGrgsmScannerParser:
    SAMPLE_LINE = (
        "ARFCN:  692, Freq: 1966.2M, CID:  3492, LAC: 32451, "
        "MCC: 310, MNC:  26, Pwr: -57"
    )

    def test_parse_valid_line(self):
        result = parse_scanner_line(self.SAMPLE_LINE)
        assert result is not None
        key, rssi = result
        assert key == CellKey(mcc=310, mnc=26, lac=32451, ci=3492)
        assert rssi == -57.0

    def test_parse_blank_line(self):
        assert parse_scanner_line("") is None
        assert parse_scanner_line("   ") is None

    def test_parse_garbage(self):
        assert parse_scanner_line("some random log line") is None

    def test_parse_multi_line(self):
        text = (
            "ARFCN:  512, Freq: 1930.2M, CID:  1001, LAC: 10454, MCC: 310, MNC:  41, Pwr: -58\n"
            "ARFCN:  535, Freq: 1934.8M, CID:  1002, LAC: 10454, MCC: 310, MNC:  41, Pwr: -45\n"
            "ARFCN:  692, Freq: 1966.2M, CID:  3492, LAC: 32451, MCC: 310, MNC:  26, Pwr: -57\n"
        )
        cells = parse_scanner_output(text)
        assert len(cells) == 3
        assert CellKey(310, 26, 32451, 3492) in cells


# ---------------------------------------------------------------------------
# Protocol contract
# ---------------------------------------------------------------------------

class TestProtocolContract:
    def test_mock_satisfies_protocol(self):
        assert isinstance(MockSweepSource(), SweepSampleSource)

    def test_replay_satisfies_protocol(self):
        src = JsonlReplaySource(GOLDEN_PATH)
        assert hasattr(src, "__iter__") and hasattr(src, "__next__")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class TestFactory:
    def test_default_is_mock(self, monkeypatch):
        monkeypatch.delenv("HAL_BACKEND", raising=False)
        src = get_sweep_source()
        assert isinstance(src, MockSweepSource)

    def test_replay_backend(self, monkeypatch):
        monkeypatch.setenv("HAL_BACKEND", "replay")
        monkeypatch.setenv("HAL_REPLAY_PATH", str(GOLDEN_PATH))
        src = get_sweep_source()
        samples = list(src)
        assert len(samples) == 20

    def test_replay_missing_path(self, monkeypatch):
        monkeypatch.setenv("HAL_BACKEND", "replay")
        monkeypatch.delenv("HAL_REPLAY_PATH", raising=False)
        with pytest.raises(RuntimeError, match="HAL_REPLAY_PATH"):
            get_sweep_source()

    def test_unknown_backend(self, monkeypatch):
        monkeypatch.setenv("HAL_BACKEND", "quantum_sdr")
        with pytest.raises(ValueError, match="Unknown HAL_BACKEND"):
            get_sweep_source()
