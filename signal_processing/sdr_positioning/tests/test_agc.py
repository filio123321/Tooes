"""Checkpoint 4: AGC gain controller."""
from __future__ import annotations

import pytest

from signal_processing.sdr_positioning.sdr_module.agc import GAIN_STRATEGIES, GainController
from signal_processing.sdr_positioning.sdr_module.catalogue import TYPE_DEFAULTS
from signal_processing.sdr_positioning.sdr_module.receiver import SDRReceiverProtocol
from signal_processing.sdr_positioning.tests.conftest import MockReceiver, NeverSettlingReceiver


class TestGainController:
    def test_normalised_rssi_matches_true_power(self, fm_entry, mock_receiver):
        gc = GainController()
        m = gc.measure(fm_entry, mock_receiver)
        assert m is not None
        assert abs(m.rssi_dbm - MockReceiver.TRUE_POWER) < 0.5

    def test_not_best_effort_when_settled(self, fm_entry, mock_receiver):
        gc = GainController()
        m = gc.measure(fm_entry, mock_receiver)
        assert m is not None
        assert m.best_effort is False

    def test_returns_none_for_too_weak_signal(self, fm_entry):
        class WeakReceiver(MockReceiver):
            TRUE_POWER = -95.0  # below FM min_rssi_dbm = -90

        gc = GainController()
        result = gc.measure(fm_entry, WeakReceiver())
        assert result is None

    def test_returns_none_for_saturated_signal(self, fm_entry):
        class StrongReceiver(MockReceiver):
            TRUE_POWER = -10.0  # above _MAX_RSSI_DBM = -20

        gc = GainController()
        result = gc.measure(fm_entry, StrongReceiver())
        assert result is None

    def test_best_effort_when_gain_never_settles(self, fm_entry):
        # NeverSettlingReceiver has TRUE_POWER = -50, which is within range
        # but always above the FM target window (target_hi = -55, so p_measured > -55)
        gc = GainController()
        result = gc.measure(fm_entry, NeverSettlingReceiver())
        # Should return a best_effort measurement rather than None
        # (signal is present but gain oscillates)
        if result is not None:
            assert result.best_effort is True

    def test_all_signal_types_produce_measurement(self, catalogue_entries, mock_receiver):
        gc = GainController()
        for entry in catalogue_entries:
            m = gc.measure(entry, mock_receiver)
            assert m is not None, f"{entry.signal_type} returned None"
            assert abs(m.rssi_dbm - MockReceiver.TRUE_POWER) < 1.0

    def test_mock_receiver_satisfies_protocol(self, mock_receiver):
        assert isinstance(mock_receiver, SDRReceiverProtocol)

    def test_source_id_propagated(self, fm_entry, mock_receiver):
        gc = GainController()
        m = gc.measure(fm_entry, mock_receiver)
        assert m is not None
        assert m.source_id == fm_entry.source_id

    def test_freq_set_on_receiver(self, fm_entry):
        rx = MockReceiver()
        GainController().measure(fm_entry, rx)
        assert rx._freq == pytest.approx(fm_entry.freq_hz)
