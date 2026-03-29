#!/usr/bin/env python3
"""
sdr_signal_meter.py — SDR signal strength → dithered e-ink fill.

The display is divided into two areas:
  - Top 14 px: status bar (frequency, dBm, fill %)
  - Rest:       Bayer-ordered dither pattern — more black = stronger signal

Usage:
    python3 sdr_signal_meter.py --freq 91.4e6
    python3 sdr_signal_meter.py --freq 91.4e6 --min-dbm -85 --max-dbm -25 --gain 60

Signal range:
    --min-dbm  maps to 0 % fill (all white) — noise floor
    --max-dbm  maps to 100 % fill (all black) — strong signal

Defaults are a reasonable range for FM broadcast (SDRplay RSP1A, gain 40 dB).
"""

import argparse
import logging
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
WAVESHARE_LIB = (
    REPO_ROOT
    / "external"
    / "waveshare-epd"
    / "RaspberryPi_JetsonNano"
    / "python"
    / "lib"
)

if not WAVESHARE_LIB.exists():
    raise FileNotFoundError(
        f"Waveshare lib not found: {WAVESHARE_LIB}\n"
        "From repo root run:\n"
        "  git submodule update --init --recursive"
    )
if str(WAVESHARE_LIB) not in sys.path:
    sys.path.insert(0, str(WAVESHARE_LIB))

from waveshare_epd import epd2in9_V2  # noqa: E402  (after path setup)

# ---------------------------------------------------------------------------
# Display geometry
# ---------------------------------------------------------------------------
EPD_W = 296
EPD_H = 128
STATUS_H = 14   # pixels at the top reserved for the text/bar strip
FILL_H = EPD_H - STATUS_H  # 114 px — the dithered area

# ---------------------------------------------------------------------------
# SDR
# ---------------------------------------------------------------------------
_SAMPLE_RATE = 2e6
_FFT_SIZE = 4096

# ---------------------------------------------------------------------------
# Bayer 4×4 ordered-dither threshold matrix (values 0–255).
#
# Each pixel's grayscale brightness is compared to the threshold at its
# (row % 4, col % 4) position.  If brightness < threshold → black (0),
# otherwise → white (255).  This produces the classic cross-hatch pattern
# that appears to fill uniformly as brightness decreases from 255 to 0.
# ---------------------------------------------------------------------------
_BAYER4 = np.array([
    [  0, 128,  32, 160],
    [192,  64, 224,  96],
    [ 48, 176,  16, 144],
    [240, 112, 208,  80],
], dtype=np.float32)


# ---------------------------------------------------------------------------
# SDR helpers (mirror of sdr_example.py — no dependency on sdr_positioning)
# ---------------------------------------------------------------------------

def _open_sdr(driver: str, freq_hz: float, gain_db: float, sample_rate: float):
    """Open a SoapySDR device and return (sdr, stream)."""
    import SoapySDR  # type: ignore[import]

    sdr = SoapySDR.Device({"driver": driver})
    sdr.setSampleRate(SoapySDR.SOAPY_SDR_RX, 0, sample_rate)
    sdr.setFrequency(SoapySDR.SOAPY_SDR_RX, 0, freq_hz)
    sdr.setGain(SoapySDR.SOAPY_SDR_RX, 0, gain_db)
    sdr.setGainMode(SoapySDR.SOAPY_SDR_RX, 0, False)

    stream = sdr.setupStream(SoapySDR.SOAPY_SDR_RX, SoapySDR.SOAPY_SDR_CF32)
    sdr.activateStream(stream)

    # Flush stale ADC samples
    flush = np.zeros(65536, dtype=np.complex64)
    sdr.readStream(stream, [flush], len(flush), timeoutUs=1_000_000)

    return sdr, stream


def _read_power_dbm(sdr, stream, fft_size: int = _FFT_SIZE) -> float:
    """Read *fft_size* IQ samples and return Hanning-windowed mean power in dBm."""
    import SoapySDR  # type: ignore[import]

    buf = np.zeros(fft_size, dtype=np.complex64)
    received = 0
    while received < fft_size:
        chunk = np.zeros(fft_size - received, dtype=np.complex64)
        sr = sdr.readStream(stream, [chunk], len(chunk), timeoutUs=1_000_000)
        if sr.ret > 0:
            buf[received : received + sr.ret] = chunk[: sr.ret]
            received += sr.ret

    window = np.hanning(fft_size)
    fft = np.fft.fftshift(np.fft.fft(buf * window))
    return float(10.0 * np.log10(np.mean(np.abs(fft) ** 2) + 1e-12))


# ---------------------------------------------------------------------------
# Image rendering
# ---------------------------------------------------------------------------

def _rssi_to_fill(rssi_dbm: float, min_dbm: float, max_dbm: float) -> float:
    """Map RSSI [min_dbm, max_dbm] → fill fraction [0.0, 1.0]."""
    t = (rssi_dbm - min_dbm) / (max_dbm - min_dbm)
    return max(0.0, min(1.0, t))


def _make_dither_frame(fill: float, rssi_dbm: float, freq_hz: float) -> Image.Image:
    """
    Build the full 296×128 "L"-mode frame.

    Top STATUS_H rows: horizontal progress bar + text label.
    Remaining rows:    Bayer-dithered fill (0 % = white, 100 % = black).
    """
    img = Image.new("L", (EPD_W, EPD_H), 255)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    # --- Status bar ---
    bar_px = max(0, int(fill * EPD_W))
    draw.rectangle((0, 0, bar_px - 1, STATUS_H - 1), fill=0)
    label = f"{freq_hz / 1e6:.1f} MHz  {rssi_dbm:+.1f} dBm  {fill * 100:.0f}%"
    label_fill = 255 if fill > 0.45 else 0
    draw.text((3, 2), label, font=font, fill=label_fill)

    # --- Dithered fill area ---
    # Tile the 4×4 Bayer matrix across the fill region
    tile_rows = (FILL_H + 3) // 4
    tile_cols = (EPD_W + 3) // 4
    thresholds = np.tile(_BAYER4, (tile_rows, tile_cols))[:FILL_H, :EPD_W]

    # fill=1.0 → brightness=0 (all pixels < any threshold → black)
    # fill=0.0 → brightness=255 (all pixels >= any threshold → white)
    brightness = (1.0 - fill) * 255.0
    pixels = np.where(thresholds > brightness, 255, 0).astype(np.uint8)

    fill_img = Image.fromarray(pixels, mode="L")
    img.paste(fill_img, (0, STATUS_H))

    return img


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Display SDR signal strength as a dithered pattern on the e-ink display."
    )
    parser.add_argument(
        "--freq", type=float, default=91.4e6,
        help="Centre frequency in Hz (default: 91.4e6 = 91.4 MHz / БГ Радио)",
    )
    parser.add_argument(
        "--gain", type=float, default=40.0,
        help="SDR gain in dB (default: 40)",
    )
    parser.add_argument(
        "--min-dbm", type=float, default=-80.0,
        help="RSSI floor → 0 %% fill (default: -80)",
    )
    parser.add_argument(
        "--max-dbm", type=float, default=-30.0,
        help="RSSI ceiling → 100 %% fill (default: -30)",
    )
    parser.add_argument(
        "--interval", type=float, default=1.5,
        help="Seconds between display refreshes (default: 1.5). "
             "Keep ≥ 1 s to avoid overdriving the panel.",
    )
    parser.add_argument(
        "--driver", default="sdrplay",
        help="SoapySDR driver string (default: sdrplay)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    logging.info("Opening SDR (driver=%s, freq=%.3f MHz, gain=%.0f dB)…",
                 args.driver, args.freq / 1e6, args.gain)
    sdr, stream = _open_sdr(args.driver, args.freq, args.gain, _SAMPLE_RATE)

    logging.info("Initialising e-ink display…")
    epd = epd2in9_V2.EPD()
    epd.init()
    epd.Clear(0xFF)

    logging.info(
        "Running — range [%.0f, %.0f] dBm → [white, black].  Ctrl-C to stop.",
        args.min_dbm, args.max_dbm,
    )

    import SoapySDR  # type: ignore[import]

    try:
        while True:
            t0 = time.monotonic()

            rssi = _read_power_dbm(sdr, stream)
            fill = _rssi_to_fill(rssi, args.min_dbm, args.max_dbm)

            logging.info("%.1f dBm  →  fill %.0f%%", rssi, fill * 100)

            frame = _make_dither_frame(fill, rssi, args.freq)
            epd.display(epd.getbuffer(frame))

            elapsed = time.monotonic() - t0
            time.sleep(max(0.0, args.interval - elapsed))

    except KeyboardInterrupt:
        logging.info("Interrupted — clearing display and sleeping.")

    finally:
        sdr.deactivateStream(stream)
        sdr.closeStream(stream)
        try:
            epd.Clear(0xFF)
            epd.sleep()
        except Exception:
            pass


if __name__ == "__main__":
    main()
