#!/usr/bin/env python3
"""Log MPU-6050 IMU data to CSV for later plotting and analysis.

The logger records:
- raw 16-bit accelerometer counts
- scaled acceleration in g
- a low-pass gravity estimate
- linear acceleration (accel - gravity estimate)
- a moving-average linear acceleration
- raw 16-bit gyroscope counts
- scaled gyroscope turn rate in deg/s
- gyro bias estimated during the initial stillness window
- bias-corrected gyroscope turn rate
- integrated gyro angles in degrees
- pitch/roll estimated from the gravity vector
- a simple stationary hint

This is useful groundwork for later step detection or heading fusion. It is
not enough on its own to produce an accurate walked path via double
integration; accelerometer-only dead reckoning drifts quickly.

Example:
    python3 separate_component_files/mpu6050_accel_logger.py \
        --duration-seconds 60 \
        --sample-rate-hz 50
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import statistics
import time
from collections import deque
from datetime import datetime
from pathlib import Path


_LOG = logging.getLogger("mpu6050_accel_logger")

_ADDRESS = 0x68
_I2C_BUS = 1

_REG_PWR_MGMT_1 = 0x6B
_REG_GYRO_CONFIG = 0x1B
_REG_ACCEL_CONFIG = 0x1C
_REG_ACCEL_XOUT_H = 0x3B
_REG_WHO_AM_I = 0x75

_ACCEL_SCALE_2G = 16384.0
_GYRO_SCALE_250DPS = 131.0
_EXPECTED_WHO_AM_I = {0x68, 0x70, 0x72, 0x73, 0x75}

_AXES = ("x", "y", "z")

_CSV_FIELDS = [
    "sample",
    "timestamp_iso",
    "elapsed_s",
    "dt_s",
    "raw_ax",
    "raw_ay",
    "raw_az",
    "raw_gx",
    "raw_gy",
    "raw_gz",
    "ax_g",
    "ay_g",
    "az_g",
    "gx_dps",
    "gy_dps",
    "gz_dps",
    "gyro_bias_x_dps",
    "gyro_bias_y_dps",
    "gyro_bias_z_dps",
    "gyro_corrected_x_dps",
    "gyro_corrected_y_dps",
    "gyro_corrected_z_dps",
    "gyro_angle_x_deg",
    "gyro_angle_y_deg",
    "gyro_angle_z_deg",
    "gravity_x_g",
    "gravity_y_g",
    "gravity_z_g",
    "linear_x_g",
    "linear_y_g",
    "linear_z_g",
    "linear_avg_x_g",
    "linear_avg_y_g",
    "linear_avg_z_g",
    "accel_mag_g",
    "gyro_mag_dps",
    "gyro_corrected_mag_dps",
    "linear_mag_g",
    "linear_avg_mag_g",
    "pitch_deg",
    "roll_deg",
    "stationary",
]


def _to_signed_16(msb: int, lsb: int) -> int:
    value = (msb << 8) | lsb
    if value >= 0x8000:
        value -= 0x10000
    return value


def _vector_norm(vector: tuple[float, float, float]) -> float:
    x, y, z = vector
    return math.sqrt((x * x) + (y * y) + (z * z))


def _round6(value: float) -> float:
    return round(value, 6)


def _vector_dict(vector: tuple[float, float, float]) -> dict[str, float]:
    return {axis: _round6(value) for axis, value in zip(_AXES, vector)}


def _format_vector(vector: tuple[float, float, float]) -> str:
    return "(" + ", ".join(f"{value:+.4f}" for value in vector) + ")"


def _vector_subtract(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(a - b for a, b in zip(left, right))


def _vector_add_scaled(
    base: tuple[float, float, float],
    delta: tuple[float, float, float],
    scale: float,
) -> tuple[float, float, float]:
    return tuple(value + (step * scale) for value, step in zip(base, delta))


def _vector_deadband(
    vector: tuple[float, float, float],
    threshold: float,
) -> tuple[float, float, float]:
    if threshold <= 0:
        return vector
    return tuple(0.0 if abs(value) < threshold else value for value in vector)


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _compute_pitch_roll(accel_g: tuple[float, float, float]) -> tuple[float, float]:
    gx, gy, gz = accel_g
    pitch = math.degrees(math.atan2(-gx, math.sqrt((gy * gy) + (gz * gz))))
    roll = math.degrees(math.atan2(gy, gz))
    return pitch, roll


class MPU6050Device:
    """Minimal MPU-6050 accelerometer + gyroscope reader over I2C."""

    def __init__(self, bus: int = _I2C_BUS, address: int = _ADDRESS) -> None:
        try:
            import smbus2
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "smbus2 is required. Install it with `pip install smbus2`."
            ) from exc

        self._address = address
        self._bus = smbus2.SMBus(bus)
        self._wake()

    def _wake(self) -> None:
        who_am_i = self._bus.read_byte_data(self._address, _REG_WHO_AM_I)
        if who_am_i not in _EXPECTED_WHO_AM_I:
            _LOG.warning("Unexpected WHO_AM_I value 0x%02x", who_am_i)
        self._bus.write_byte_data(self._address, _REG_PWR_MGMT_1, 0x00)
        self._bus.write_byte_data(self._address, _REG_GYRO_CONFIG, 0x00)
        self._bus.write_byte_data(self._address, _REG_ACCEL_CONFIG, 0x00)
        time.sleep(0.05)
        _LOG.info(
            "MPU-6050 awake on I2C address 0x%02x (accel=+/-2g gyro=+/-250dps)",
            self._address,
        )

    def read_motion_raw(
        self,
    ) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        data = self._bus.read_i2c_block_data(self._address, _REG_ACCEL_XOUT_H, 14)
        ax = _to_signed_16(data[0], data[1])
        ay = _to_signed_16(data[2], data[3])
        az = _to_signed_16(data[4], data[5])
        gx = _to_signed_16(data[8], data[9])
        gy = _to_signed_16(data[10], data[11])
        gz = _to_signed_16(data[12], data[13])
        return (ax, ay, az), (gx, gy, gz)

    def read_raw(self) -> tuple[int, int, int]:
        accel_raw, _ = self.read_motion_raw()
        return accel_raw

    def read_g(self) -> tuple[float, float, float]:
        raw = self.read_raw()
        return tuple(axis / _ACCEL_SCALE_2G for axis in raw)

    def read_dps(self) -> tuple[float, float, float]:
        _, gyro_raw = self.read_motion_raw()
        return tuple(axis / _GYRO_SCALE_250DPS for axis in gyro_raw)

    def close(self) -> None:
        self._bus.close()


class LowPassVectorFilter:
    """Simple low-pass filter with time-constant based smoothing."""

    def __init__(
        self,
        time_constant_s: float,
        initial: tuple[float, float, float] | None = None,
    ) -> None:
        self._time_constant_s = max(time_constant_s, 1e-6)
        self._value = initial

    def update(
        self,
        sample: tuple[float, float, float],
        dt_s: float,
    ) -> tuple[float, float, float]:
        if self._value is None:
            self._value = sample
            return sample

        alpha = math.exp(-max(dt_s, 1e-6) / self._time_constant_s)
        self._value = tuple(
            (alpha * previous) + ((1.0 - alpha) * current)
            for previous, current in zip(self._value, sample)
        )
        return self._value


class MovingAverageVector:
    """Small moving average for noise reduction in the linear signal."""

    def __init__(self, window_size: int) -> None:
        self._samples: deque[tuple[float, float, float]] = deque(
            maxlen=max(window_size, 1)
        )

    def update(self, sample: tuple[float, float, float]) -> tuple[float, float, float]:
        self._samples.append(sample)
        count = len(self._samples)
        sx = sum(item[0] for item in self._samples)
        sy = sum(item[1] for item in self._samples)
        sz = sum(item[2] for item in self._samples)
        return (sx / count, sy / count, sz / count)


def _sleep_until(target_time_s: float) -> None:
    remaining = target_time_s - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)


def _default_output_path() -> Path:
    logs_dir = Path(__file__).resolve().parent / "logs"
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    return logs_dir / f"mpu6050_imu_{stamp}.csv"


def _write_metadata(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _collect_calibration(
    device: MPU6050Device,
    sample_rate_hz: float,
    calibration_seconds: float,
) -> dict | None:
    if calibration_seconds <= 0:
        return None

    _LOG.info(
        "Calibration started: keep the sensor still for %.1f seconds",
        calibration_seconds,
    )
    accel_samples: list[tuple[float, float, float]] = []
    gyro_samples: list[tuple[float, float, float]] = []
    period_s = 1.0 / sample_rate_hz
    deadline = time.monotonic() + calibration_seconds
    next_sample_at = time.monotonic()

    while time.monotonic() < deadline:
        accel_raw, gyro_raw = device.read_motion_raw()
        accel_samples.append(tuple(axis / _ACCEL_SCALE_2G for axis in accel_raw))
        gyro_samples.append(tuple(axis / _GYRO_SCALE_250DPS for axis in gyro_raw))
        next_sample_at += period_s
        _sleep_until(next_sample_at)

    if not accel_samples:
        return None

    accel_axes = list(zip(*accel_samples))
    accel_mean_g = tuple(statistics.fmean(axis_values) for axis_values in accel_axes)
    accel_std_g = tuple(
        statistics.pstdev(axis_values) if len(axis_values) > 1 else 0.0
        for axis_values in accel_axes
    )
    accel_magnitudes = [_vector_norm(sample) for sample in accel_samples]
    accel_magnitude_mean_g = statistics.fmean(accel_magnitudes)
    accel_magnitude_std_g = (
        statistics.pstdev(accel_magnitudes) if len(accel_magnitudes) > 1 else 0.0
    )

    gyro_axes = list(zip(*gyro_samples))
    gyro_mean_dps = tuple(statistics.fmean(axis_values) for axis_values in gyro_axes)
    gyro_std_dps = tuple(
        statistics.pstdev(axis_values) if len(axis_values) > 1 else 0.0
        for axis_values in gyro_axes
    )
    gyro_magnitudes = [_vector_norm(sample) for sample in gyro_samples]
    gyro_magnitude_mean_dps = statistics.fmean(gyro_magnitudes)
    gyro_magnitude_std_dps = (
        statistics.pstdev(gyro_magnitudes) if len(gyro_magnitudes) > 1 else 0.0
    )

    calibration = {
        "seconds": calibration_seconds,
        "samples": len(accel_samples),
        "accel_mean_g": _vector_dict(accel_mean_g),
        "accel_std_g": _vector_dict(accel_std_g),
        "accel_magnitude_mean_g": _round6(accel_magnitude_mean_g),
        "accel_magnitude_std_g": _round6(accel_magnitude_std_g),
        "gyro_mean_dps": _vector_dict(gyro_mean_dps),
        "gyro_std_dps": _vector_dict(gyro_std_dps),
        "gyro_magnitude_mean_dps": _round6(gyro_magnitude_mean_dps),
        "gyro_magnitude_std_dps": _round6(gyro_magnitude_std_dps),
    }

    _LOG.info(
        "Calibration accel mean[g]=%s std[g]=%s |mag|=%.4f g | gyro mean[dps]=%s std[dps]=%s",
        _format_vector(accel_mean_g),
        _format_vector(accel_std_g),
        accel_magnitude_mean_g,
        _format_vector(gyro_mean_dps),
        _format_vector(gyro_std_dps),
    )
    if abs(accel_magnitude_mean_g - 1.0) > 0.15:
        _LOG.warning(
            "Resting acceleration magnitude is %.3f g, which suggests the sensor may "
            "need bias calibration or a mounting/orientation sanity check.",
            accel_magnitude_mean_g,
        )

    return calibration


def _build_row(
    sample_index: int,
    timestamp_iso: str,
    elapsed_s: float,
    dt_s: float,
    accel_raw: tuple[int, int, int],
    accel_g: tuple[float, float, float],
    gyro_raw: tuple[int, int, int],
    gyro_dps: tuple[float, float, float],
    gyro_bias_dps: tuple[float, float, float],
    gyro_corrected_dps: tuple[float, float, float],
    gyro_angle_deg: tuple[float, float, float],
    gravity_g: tuple[float, float, float],
    linear_g: tuple[float, float, float],
    linear_avg_g: tuple[float, float, float],
    pitch_deg: float,
    roll_deg: float,
    stationary: bool,
) -> dict[str, object]:
    accel_mag_g = _vector_norm(accel_g)
    gyro_mag_dps = _vector_norm(gyro_dps)
    gyro_corrected_mag_dps = _vector_norm(gyro_corrected_dps)
    linear_mag_g = _vector_norm(linear_g)
    linear_avg_mag_g = _vector_norm(linear_avg_g)
    return {
        "sample": sample_index,
        "timestamp_iso": timestamp_iso,
        "elapsed_s": _round6(elapsed_s),
        "dt_s": _round6(dt_s),
        "raw_ax": accel_raw[0],
        "raw_ay": accel_raw[1],
        "raw_az": accel_raw[2],
        "raw_gx": gyro_raw[0],
        "raw_gy": gyro_raw[1],
        "raw_gz": gyro_raw[2],
        "ax_g": _round6(accel_g[0]),
        "ay_g": _round6(accel_g[1]),
        "az_g": _round6(accel_g[2]),
        "gx_dps": _round6(gyro_dps[0]),
        "gy_dps": _round6(gyro_dps[1]),
        "gz_dps": _round6(gyro_dps[2]),
        "gyro_bias_x_dps": _round6(gyro_bias_dps[0]),
        "gyro_bias_y_dps": _round6(gyro_bias_dps[1]),
        "gyro_bias_z_dps": _round6(gyro_bias_dps[2]),
        "gyro_corrected_x_dps": _round6(gyro_corrected_dps[0]),
        "gyro_corrected_y_dps": _round6(gyro_corrected_dps[1]),
        "gyro_corrected_z_dps": _round6(gyro_corrected_dps[2]),
        "gyro_angle_x_deg": _round6(gyro_angle_deg[0]),
        "gyro_angle_y_deg": _round6(gyro_angle_deg[1]),
        "gyro_angle_z_deg": _round6(gyro_angle_deg[2]),
        "gravity_x_g": _round6(gravity_g[0]),
        "gravity_y_g": _round6(gravity_g[1]),
        "gravity_z_g": _round6(gravity_g[2]),
        "linear_x_g": _round6(linear_g[0]),
        "linear_y_g": _round6(linear_g[1]),
        "linear_z_g": _round6(linear_g[2]),
        "linear_avg_x_g": _round6(linear_avg_g[0]),
        "linear_avg_y_g": _round6(linear_avg_g[1]),
        "linear_avg_z_g": _round6(linear_avg_g[2]),
        "accel_mag_g": _round6(accel_mag_g),
        "gyro_mag_dps": _round6(gyro_mag_dps),
        "gyro_corrected_mag_dps": _round6(gyro_corrected_mag_dps),
        "linear_mag_g": _round6(linear_mag_g),
        "linear_avg_mag_g": _round6(linear_avg_mag_g),
        "pitch_deg": _round6(pitch_deg),
        "roll_deg": _round6(roll_deg),
        "stationary": int(stationary),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read MPU-6050 accelerometer and gyroscope data and log it to CSV."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="CSV output path. Defaults to separate_component_files/logs/...",
    )
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=0.0,
        help="How long to log. Use 0 to run until Ctrl-C.",
    )
    parser.add_argument(
        "--sample-rate-hz",
        type=float,
        default=50.0,
        help="Polling rate for the sensor.",
    )
    parser.add_argument(
        "--calibration-seconds",
        type=float,
        default=3.0,
        help="Collect a stillness baseline before logging starts.",
    )
    parser.add_argument(
        "--gravity-time-constant",
        type=float,
        default=0.75,
        help="Seconds for the gravity low-pass filter.",
    )
    parser.add_argument(
        "--linear-smoothing-window",
        type=int,
        default=5,
        help="Moving-average window for the linear acceleration output.",
    )
    parser.add_argument(
        "--stationary-linear-threshold",
        type=float,
        default=0.08,
        help="Threshold in g for marking a sample as stationary.",
    )
    parser.add_argument(
        "--stationary-mag-threshold",
        type=float,
        default=0.12,
        help="Allowed distance from 1 g for stationary detection.",
    )
    parser.add_argument(
        "--stationary-gyro-threshold",
        type=float,
        default=2.5,
        help="Allowed corrected gyro magnitude in deg/s for stationary detection.",
    )
    parser.add_argument(
        "--gyro-deadband-dps",
        type=float,
        default=0.75,
        help="Zero out tiny bias-corrected gyro rates before integrating angles.",
    )
    parser.add_argument(
        "--status-every-seconds",
        type=float,
        default=1.0,
        help="How often to print a status line. Use 0 to disable.",
    )
    parser.add_argument(
        "--flush-every",
        type=int,
        default=25,
        help="Flush the CSV file every N samples.",
    )
    parser.add_argument(
        "--bus",
        type=int,
        default=_I2C_BUS,
        help="I2C bus number.",
    )
    parser.add_argument(
        "--address",
        type=lambda value: int(value, 0),
        default=_ADDRESS,
        help="I2C address, for example 0x68 or 0x69.",
    )
    args = parser.parse_args()

    if args.sample_rate_hz <= 0:
        parser.error("--sample-rate-hz must be > 0")
    if args.gravity_time_constant <= 0:
        parser.error("--gravity-time-constant must be > 0")
    if args.linear_smoothing_window <= 0:
        parser.error("--linear-smoothing-window must be > 0")
    if args.flush_every <= 0:
        parser.error("--flush-every must be > 0")
    if args.duration_seconds < 0:
        parser.error("--duration-seconds must be >= 0")
    if args.calibration_seconds < 0:
        parser.error("--calibration-seconds must be >= 0")
    if args.stationary_gyro_threshold < 0:
        parser.error("--stationary-gyro-threshold must be >= 0")
    if args.gyro_deadband_dps < 0:
        parser.error("--gyro-deadband-dps must be >= 0")

    return args


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    output_path = args.output or _default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path = output_path.with_suffix(".meta.json")

    device = MPU6050Device(bus=args.bus, address=args.address)
    calibration = None
    samples_written = 0
    interrupted = False
    metadata = {
        "output_csv": str(output_path),
        "bus": args.bus,
        "address": hex(args.address),
        "sample_rate_hz": args.sample_rate_hz,
        "calibration_seconds": args.calibration_seconds,
        "gravity_time_constant": args.gravity_time_constant,
        "linear_smoothing_window": args.linear_smoothing_window,
        "stationary_linear_threshold": args.stationary_linear_threshold,
        "stationary_mag_threshold": args.stationary_mag_threshold,
        "stationary_gyro_threshold": args.stationary_gyro_threshold,
        "gyro_deadband_dps": args.gyro_deadband_dps,
        "started_at": None,
        "finished_at": None,
        "samples_logged": 0,
        "interrupted": False,
        "calibration": None,
    }

    try:
        calibration = _collect_calibration(
            device=device,
            sample_rate_hz=args.sample_rate_hz,
            calibration_seconds=args.calibration_seconds,
        )
        metadata["calibration"] = calibration

        gravity_initial = None
        gyro_bias_dps = (0.0, 0.0, 0.0)
        if calibration is not None:
            gravity_initial = tuple(calibration["accel_mean_g"][axis] for axis in _AXES)
            gyro_bias_dps = tuple(calibration["gyro_mean_dps"][axis] for axis in _AXES)

        gravity_filter = LowPassVectorFilter(
            time_constant_s=args.gravity_time_constant,
            initial=gravity_initial,
        )
        linear_average = MovingAverageVector(args.linear_smoothing_window)
        gyro_angle_deg = (0.0, 0.0, 0.0)

        started_at_iso = _iso_now()
        metadata["started_at"] = started_at_iso
        _write_metadata(metadata_path, metadata)

        _LOG.info("Writing IMU log to %s", output_path)
        period_s = 1.0 / args.sample_rate_hz
        run_started_monotonic = time.monotonic()
        last_sample_monotonic = run_started_monotonic
        next_sample_at = run_started_monotonic
        last_status_at = run_started_monotonic

        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=_CSV_FIELDS)
            writer.writeheader()

            while True:
                now_monotonic = time.monotonic()
                elapsed_s = now_monotonic - run_started_monotonic
                if args.duration_seconds and elapsed_s >= args.duration_seconds:
                    break

                accel_raw, gyro_raw = device.read_motion_raw()
                accel_g = tuple(axis / _ACCEL_SCALE_2G for axis in accel_raw)
                gyro_dps = tuple(axis / _GYRO_SCALE_250DPS for axis in gyro_raw)
                dt_s = 0.0 if samples_written == 0 else now_monotonic - last_sample_monotonic
                if dt_s <= 0.0:
                    dt_s = period_s

                gravity_g = gravity_filter.update(accel_g, dt_s)
                linear_g = _vector_subtract(accel_g, gravity_g)
                linear_avg_g = linear_average.update(linear_g)
                gyro_corrected_dps = _vector_subtract(gyro_dps, gyro_bias_dps)
                gyro_corrected_dps = _vector_deadband(
                    gyro_corrected_dps,
                    args.gyro_deadband_dps,
                )
                gyro_angle_deg = _vector_add_scaled(
                    gyro_angle_deg,
                    gyro_corrected_dps,
                    dt_s,
                )
                pitch_deg, roll_deg = _compute_pitch_roll(accel_g)
                stationary = (
                    _vector_norm(linear_avg_g) <= args.stationary_linear_threshold
                    and abs(_vector_norm(accel_g) - 1.0) <= args.stationary_mag_threshold
                    and _vector_norm(gyro_corrected_dps) <= args.stationary_gyro_threshold
                )

                writer.writerow(
                    _build_row(
                        sample_index=samples_written,
                        timestamp_iso=_iso_now(),
                        elapsed_s=elapsed_s,
                        dt_s=dt_s,
                        accel_raw=accel_raw,
                        accel_g=accel_g,
                        gyro_raw=gyro_raw,
                        gyro_dps=gyro_dps,
                        gyro_bias_dps=gyro_bias_dps,
                        gyro_corrected_dps=gyro_corrected_dps,
                        gyro_angle_deg=gyro_angle_deg,
                        gravity_g=gravity_g,
                        linear_g=linear_g,
                        linear_avg_g=linear_avg_g,
                        pitch_deg=pitch_deg,
                        roll_deg=roll_deg,
                        stationary=stationary,
                    )
                )
                samples_written += 1
                last_sample_monotonic = now_monotonic

                if samples_written % args.flush_every == 0:
                    handle.flush()

                if (
                    args.status_every_seconds > 0
                    and (now_monotonic - last_status_at) >= args.status_every_seconds
                ):
                    _LOG.info(
                        "samples=%d elapsed=%.1fs accel=%s gyro_corr[dps]=%s angle[deg]=%s linear_avg=%s pitch=%.1f roll=%.1f stationary=%s",
                        samples_written,
                        elapsed_s,
                        _format_vector(accel_g),
                        _format_vector(gyro_corrected_dps),
                        _format_vector(gyro_angle_deg),
                        _format_vector(linear_avg_g),
                        pitch_deg,
                        roll_deg,
                        stationary,
                    )
                    last_status_at = now_monotonic

                next_sample_at += period_s
                _sleep_until(next_sample_at)

        metadata["finished_at"] = _iso_now()
        metadata["samples_logged"] = samples_written
        metadata["interrupted"] = interrupted
        _write_metadata(metadata_path, metadata)
        _LOG.info("Finished logging %d samples", samples_written)
        return 0
    except KeyboardInterrupt:
        interrupted = True
        _LOG.info("Interrupted by user after %d samples", samples_written)
        metadata["finished_at"] = _iso_now()
        metadata["samples_logged"] = samples_written
        metadata["interrupted"] = True
        _write_metadata(metadata_path, metadata)
        return 130
    finally:
        device.close()


if __name__ == "__main__":
    raise SystemExit(main())
