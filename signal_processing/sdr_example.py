#!/usr/bin/env python3
"""
Sensitive Signal Strength Monitor
Detects position changes of 10-50m via RSSI variations
Usage: python3 signal_strength.py --freq 947.6e6
"""

import SoapySDR
import numpy as np
import argparse
import time
import sys
from collections import deque
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
SAMPLE_RATE  = 2e6
GAIN         = 80
FFT_SIZE     = 4096
AVG_WINDOW   = 10
UPDATE_HZ    = 10

# ── Args ──────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument('--freq', type=float, default=947.6e6)
parser.add_argument('--gain', type=float, default=GAIN)
parser.add_argument('--rate', type=float, default=SAMPLE_RATE)
args = parser.parse_args()

# ── SDR Setup ─────────────────────────────────────────────────────────────────
print(f"Opening SDRplay RSP1...")
sdr = SoapySDR.Device({'driver': 'sdrplay'})
sdr.setSampleRate(SoapySDR.SOAPY_SDR_RX, 0, args.rate)
sdr.setFrequency(SoapySDR.SOAPY_SDR_RX, 0, args.freq)
sdr.setGain(SoapySDR.SOAPY_SDR_RX, 0, args.gain)
sdr.setGainMode(SoapySDR.SOAPY_SDR_RX, 0, False)

stream = sdr.setupStream(SoapySDR.SOAPY_SDR_RX, SoapySDR.SOAPY_SDR_CF32)
sdr.activateStream(stream)

# Flush
flush = np.zeros(65536, dtype=np.complex64)
sdr.readStream(stream, [flush], 65536, timeoutUs=1000000)

print(f"Monitoring {args.freq/1e6:.3f} MHz | Gain: {args.gain}dB")
print(f"Move device to see RSSI change\n")
print(f"{'Time':12} {'RSSI':8} {'Baseline':9} {'Delta':8} {'Bar'}")
print("-" * 70)

# ── Measurement ───────────────────────────────────────────────────────────────
def read_power():
    buf = np.zeros(FFT_SIZE, dtype=np.complex64)
    received = 0
    while received < FFT_SIZE:
        remaining = FFT_SIZE - received
        tmp = np.zeros(min(4096, remaining), dtype=np.complex64)
        sr = sdr.readStream(stream, [tmp], len(tmp), timeoutUs=1000000)
        if sr.ret > 0:
            buf[received:received+sr.ret] = tmp[:sr.ret]
            received += sr.ret
    window   = np.hanning(FFT_SIZE)
    fft      = np.fft.fftshift(np.fft.fft(buf * window))
    power_db = 10 * np.log10(np.mean(np.abs(fft)**2) + 1e-12)
    return power_db

def rssi_bar(rssi, min_db=-80, max_db=-20, width=30):
    n      = max(0, min(1, (rssi - min_db) / (max_db - min_db)))
    filled = int(n * width)
    return '█' * filled + '░' * (width - filled)

# ── Main Loop ─────────────────────────────────────────────────────────────────
avg_buffer = deque(maxlen=AVG_WINDOW)
baseline   = None
interval   = 1.0 / UPDATE_HZ
warmup     = AVG_WINDOW

try:
    while True:
        t_start = time.time()

        readings = [read_power() for _ in range(3)]
        rssi     = np.mean(readings)
        avg_buffer.append(rssi)
        smoothed = np.mean(avg_buffer)
        warmup   = max(0, warmup - 1)

        if warmup == 0 and baseline is None:
            baseline = smoothed
            print(f"  Baseline set: {baseline:.2f} dB\n")

        if baseline is None:
            sys.stdout.write(f"\r  Warming up... {smoothed:.2f} dB ({AVG_WINDOW - len(avg_buffer)} samples left)")
            sys.stdout.flush()
            time.sleep(interval)
            continue

        delta = smoothed - baseline

        if abs(delta) < 1.0:
            color = '\033[32m'
        elif abs(delta) < 3.0:
            color = '\033[33m'
        else:
            color = '\033[31m'
        reset = '\033[0m'

        ts  = datetime.now().strftime('%H:%M:%S.%f')[:12]
        bar = rssi_bar(smoothed)

        sys.stdout.write(
            f"\r{ts} "
            f"{color}{smoothed:7.2f}dB{reset} "
            f"{baseline:7.2f}dB  "
            f"{color}{delta:+7.2f}dB{reset} "
            f"|{bar}|"
        )
        sys.stdout.flush()

        if abs(delta) > 2.0:
            print(f"\n  *** Position change: {delta:+.2f}dB ***")
            baseline = smoothed

        elapsed = time.time() - t_start
        time.sleep(max(0, interval - elapsed))

except KeyboardInterrupt:
    print("\n\nStopped")
finally:
    sdr.deactivateStream(stream)
    sdr.closeStream(stream)