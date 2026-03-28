from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np

# SoapySDR is a system package — wrap import so the rest of the package remains importable
# without it. Tests patch this name to inject a mock.
try:
    import SoapySDR
    from SoapySDR import SOAPY_SDR_RX, SOAPY_SDR_CF32  # type: ignore[import]
except ImportError:
    SoapySDR = None  # type: ignore[assignment]
    SOAPY_SDR_RX = 0
    SOAPY_SDR_CF32 = "CF32"

_FFT_SIZE = 4096
_SAMPLE_RATE = 2e6


@runtime_checkable
class SDRReceiverProtocol(Protocol):
    def set_gain(self, gain_db: float) -> None: ...
    def set_freq(self, freq_hz: float) -> None: ...
    def set_sample_rate(self, rate_hz: float) -> None: ...
    def read_power_dbm(self, n_samples: int = _FFT_SIZE) -> float: ...
    def close(self) -> None: ...


class SDRReceiver:
    """Thin SoapySDR wrapper. The ONLY class in this package that imports SoapySDR."""

    def __init__(self, driver: str = "sdrplay", serial: str | None = None) -> None:
        if SoapySDR is None:
            raise ImportError(
                "SoapySDR is not installed. Install it via your system package manager "
                "(e.g. libsoapysdr-dev) together with the SDRplay plugin."
            )
        args: dict[str, str] = {"driver": driver}
        if serial:
            args["serial"] = serial
        self._sdr = SoapySDR.Device(args)
        self._sdr.setSampleRate(SOAPY_SDR_RX, 0, _SAMPLE_RATE)
        self._stream = self._sdr.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32)
        self._sdr.activateStream(self._stream)
        # Flush ADC pipeline to discard any stale samples
        flush_buf = np.zeros(65536, dtype=np.complex64)
        self._sdr.readStream(self._stream, [flush_buf], len(flush_buf))

    def set_gain(self, gain_db: float) -> None:
        self._sdr.setGain(SOAPY_SDR_RX, 0, gain_db)

    def set_freq(self, freq_hz: float) -> None:
        self._sdr.setFrequency(SOAPY_SDR_RX, 0, freq_hz)

    def set_sample_rate(self, rate_hz: float) -> None:
        self._sdr.setSampleRate(SOAPY_SDR_RX, 0, rate_hz)

    def read_power_dbm(self, n_samples: int = _FFT_SIZE) -> float:
        """Return FFT-averaged power in dBm (includes current gain).

        Uses the same FFT approach as sdr_example.py: Hanning window, 4096-point FFT,
        with a 1e-12 floor guard to prevent log10(0).
        """
        buf = np.zeros(n_samples, dtype=np.complex64)
        read = 0
        while read < n_samples:
            sr = self._sdr.readStream(self._stream, [buf[read:]], n_samples - read)
            if sr.ret <= 0:
                break
            read += sr.ret
        window = np.hanning(n_samples)
        fft = np.fft.fftshift(np.fft.fft(buf * window))
        return float(10.0 * np.log10(np.mean(np.abs(fft) ** 2) + 1e-12))

    def close(self) -> None:
        self._sdr.deactivateStream(self._stream)
        self._sdr.closeStream(self._stream)
