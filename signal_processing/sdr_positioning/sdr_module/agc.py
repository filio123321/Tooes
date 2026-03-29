from __future__ import annotations

from dataclasses import dataclass

from ..models import Measurement
from .catalogue import CatalogueEntry
from .receiver import SDRReceiverProtocol

_MAX_RSSI_DBM = -20.0  # universal upper bound for valid reception
_AGC_STEPS = 4


@dataclass(frozen=True)
class GainStrategy:
    target_lo: float   # lower bound of acceptable ADC-level window (dBm)
    target_hi: float   # upper bound of acceptable ADC-level window (dBm)
    start_gain: float  # initial gain setting (dB)
    step_db: float = 10.0


GAIN_STRATEGIES: dict[str, GainStrategy] = {
    "FM":    GainStrategy(target_lo=-75.0, target_hi=-55.0, start_gain=40.0),
    "VOR":   GainStrategy(target_lo=-85.0, target_hi=-65.0, start_gain=45.0),
    "DAB":   GainStrategy(target_lo=-80.0, target_hi=-60.0, start_gain=40.0),
    "DVB-T": GainStrategy(target_lo=-75.0, target_hi=-55.0, start_gain=40.0),
    "GSM":   GainStrategy(target_lo=-55.0, target_hi=-35.0, start_gain=20.0),
}


class GainController:
    def measure(
        self,
        entry: CatalogueEntry,
        receiver: SDRReceiverProtocol,
    ) -> Measurement | None:
        """Tune to the entry's frequency, run AGC loop, return normalised Measurement.

        Returns None when the signal is absent or saturated.
        Returns a Measurement with best_effort=True if gain never fully settled.
        """
        strategy = GAIN_STRATEGIES[entry.signal_type]
        receiver.set_freq(entry.freq_hz)
        receiver.set_sample_rate(2e6)

        gain = strategy.start_gain
        midpoint = (strategy.target_lo + strategy.target_hi) / 2.0
        last_gain = gain
        p_measured = 0.0
        true_power_dbm = 0.0

        for _ in range(_AGC_STEPS):
            receiver.set_gain(gain)
            last_gain = gain  # record the gain actually applied for this read
            p_measured = receiver.read_power_dbm()
            # Normalisation: remove receiver gain to recover true received power.
            # This formula appears exactly once in the codebase.
            true_power_dbm = p_measured - last_gain

            if true_power_dbm < entry.min_rssi_dbm:
                return None  # signal absent / below noise floor
            if true_power_dbm > _MAX_RSSI_DBM:
                return None  # ADC saturated even at lowest gain

            if strategy.target_lo <= p_measured <= strategy.target_hi:
                return Measurement(
                    source_id=entry.source_id,
                    rssi_dbm=true_power_dbm,
                    freq_hz=entry.freq_hz,
                    signal_type=entry.signal_type,
                    lat=entry.lat,
                    lon=entry.lon,
                    power_w=entry.power_w,
                    antenna_gain_dbi=entry.antenna_gain_dbi,
                    gain_used=last_gain,
                    best_effort=False,
                )
            # Steer gain towards midpoint, bounded by step_db to avoid large jumps
            delta = midpoint - p_measured
            gain += max(-strategy.step_db, min(strategy.step_db, delta))

        # Loop exhausted without settling — signal present but gain is unstable
        return Measurement(
            source_id=entry.source_id,
            rssi_dbm=true_power_dbm,
            freq_hz=entry.freq_hz,
            signal_type=entry.signal_type,
            lat=entry.lat,
            lon=entry.lon,
            power_w=entry.power_w,
            antenna_gain_dbi=entry.antenna_gain_dbi,
            gain_used=last_gain,
            best_effort=True,
        )
