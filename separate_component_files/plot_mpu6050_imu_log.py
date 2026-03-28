#!/usr/bin/env python3
"""Plot MPU-6050 IMU CSV logs produced by mpu6050_accel_logger.py.

Examples:
    python3 separate_component_files/plot_mpu6050_imu_log.py
    python3 separate_component_files/plot_mpu6050_imu_log.py path/to/log.csv
    python3 separate_component_files/plot_mpu6050_imu_log.py --show
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path


_DEFAULT_LOG_DIR = Path(__file__).resolve().parent / "logs"


@dataclass
class ImuSeries:
    time_s: list[float]
    accel_x_g: list[float]
    accel_y_g: list[float]
    accel_z_g: list[float]
    accel_mag_g: list[float]
    linear_x_g: list[float]
    linear_y_g: list[float]
    linear_z_g: list[float]
    linear_mag_g: list[float]
    gyro_x_dps: list[float]
    gyro_y_dps: list[float]
    gyro_z_dps: list[float]
    gyro_mag_dps: list[float]
    gyro_angle_x_deg: list[float]
    gyro_angle_y_deg: list[float]
    gyro_angle_z_deg: list[float]
    pitch_deg: list[float]
    roll_deg: list[float]
    stationary: list[bool]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot an MPU-6050 IMU CSV log into a PNG overview."
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=None,
        help="Path to the CSV log. Defaults to the latest file in separate_component_files/logs/.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="PNG output path. Defaults next to the CSV with a _plot suffix.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Open the plot interactively after saving.",
    )
    parser.add_argument(
        "--start-seconds",
        type=float,
        default=None,
        help="Optional lower bound for the plotted elapsed time.",
    )
    parser.add_argument(
        "--end-seconds",
        type=float,
        default=None,
        help="Optional upper bound for the plotted elapsed time.",
    )
    return parser.parse_args()


def _latest_log_path() -> Path:
    candidates = sorted(_DEFAULT_LOG_DIR.glob("*.csv"))
    if not candidates:
        raise FileNotFoundError(
            f"No CSV logs found in {_DEFAULT_LOG_DIR}. Pass a CSV path explicitly."
        )
    return candidates[-1]


def _float(row: dict[str, str], key: str) -> float:
    return float(row[key])


def _bool(row: dict[str, str], key: str) -> bool:
    return bool(int(row[key]))


def _load_series(
    path: Path,
    start_seconds: float | None,
    end_seconds: float | None,
) -> ImuSeries:
    time_s: list[float] = []
    accel_x_g: list[float] = []
    accel_y_g: list[float] = []
    accel_z_g: list[float] = []
    accel_mag_g: list[float] = []
    linear_x_g: list[float] = []
    linear_y_g: list[float] = []
    linear_z_g: list[float] = []
    linear_mag_g: list[float] = []
    gyro_x_dps: list[float] = []
    gyro_y_dps: list[float] = []
    gyro_z_dps: list[float] = []
    gyro_mag_dps: list[float] = []
    gyro_angle_x_deg: list[float] = []
    gyro_angle_y_deg: list[float] = []
    gyro_angle_z_deg: list[float] = []
    pitch_deg: list[float] = []
    roll_deg: list[float] = []
    stationary: list[bool] = []

    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            elapsed = _float(row, "elapsed_s")
            if start_seconds is not None and elapsed < start_seconds:
                continue
            if end_seconds is not None and elapsed > end_seconds:
                continue

            time_s.append(elapsed)
            accel_x_g.append(_float(row, "ax_g"))
            accel_y_g.append(_float(row, "ay_g"))
            accel_z_g.append(_float(row, "az_g"))
            accel_mag_g.append(_float(row, "accel_mag_g"))
            linear_x_g.append(_float(row, "linear_avg_x_g"))
            linear_y_g.append(_float(row, "linear_avg_y_g"))
            linear_z_g.append(_float(row, "linear_avg_z_g"))
            linear_mag_g.append(_float(row, "linear_avg_mag_g"))
            gyro_x_dps.append(_float(row, "gyro_corrected_x_dps"))
            gyro_y_dps.append(_float(row, "gyro_corrected_y_dps"))
            gyro_z_dps.append(_float(row, "gyro_corrected_z_dps"))
            gyro_mag_dps.append(_float(row, "gyro_corrected_mag_dps"))
            gyro_angle_x_deg.append(_float(row, "gyro_angle_x_deg"))
            gyro_angle_y_deg.append(_float(row, "gyro_angle_y_deg"))
            gyro_angle_z_deg.append(_float(row, "gyro_angle_z_deg"))
            pitch_deg.append(_float(row, "pitch_deg"))
            roll_deg.append(_float(row, "roll_deg"))
            stationary.append(_bool(row, "stationary"))

    if not time_s:
        raise ValueError("The selected CSV has no rows in the requested time window.")

    return ImuSeries(
        time_s=time_s,
        accel_x_g=accel_x_g,
        accel_y_g=accel_y_g,
        accel_z_g=accel_z_g,
        accel_mag_g=accel_mag_g,
        linear_x_g=linear_x_g,
        linear_y_g=linear_y_g,
        linear_z_g=linear_z_g,
        linear_mag_g=linear_mag_g,
        gyro_x_dps=gyro_x_dps,
        gyro_y_dps=gyro_y_dps,
        gyro_z_dps=gyro_z_dps,
        gyro_mag_dps=gyro_mag_dps,
        gyro_angle_x_deg=gyro_angle_x_deg,
        gyro_angle_y_deg=gyro_angle_y_deg,
        gyro_angle_z_deg=gyro_angle_z_deg,
        pitch_deg=pitch_deg,
        roll_deg=roll_deg,
        stationary=stationary,
    )


def _stationary_spans(time_s: list[float], stationary: list[bool]) -> list[tuple[float, float]]:
    spans: list[tuple[float, float]] = []
    span_start: float | None = None

    for current_time, is_stationary in zip(time_s, stationary):
        if is_stationary and span_start is None:
            span_start = current_time
        elif not is_stationary and span_start is not None:
            spans.append((span_start, current_time))
            span_start = None

    if span_start is not None:
        spans.append((span_start, time_s[-1]))

    return spans


def _add_stationary_shading(axes, spans: list[tuple[float, float]]) -> None:
    first = True
    for start, end in spans:
        for axis in axes:
            axis.axvspan(
                start,
                end,
                color="#d8f3dc",
                alpha=0.25,
                label="stationary" if first else None,
            )
        first = False


def _plot(series: ImuSeries, input_path: Path, output_path: Path, show: bool) -> None:
    try:
        import matplotlib
        if not show:
            matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "matplotlib is required for plotting. Install it with `pip install matplotlib`."
        ) from exc

    fig, axes = plt.subplots(5, 1, figsize=(15, 16), sharex=True)
    fig.suptitle(f"MPU-6050 IMU Overview: {input_path.name}", fontsize=14)

    spans = _stationary_spans(series.time_s, series.stationary)
    _add_stationary_shading(axes, spans)

    axes[0].plot(series.time_s, series.accel_x_g, label="ax_g", linewidth=1.0)
    axes[0].plot(series.time_s, series.accel_y_g, label="ay_g", linewidth=1.0)
    axes[0].plot(series.time_s, series.accel_z_g, label="az_g", linewidth=1.0)
    axes[0].plot(series.time_s, series.accel_mag_g, label="|accel|", linewidth=1.4, color="black")
    axes[0].set_ylabel("Accel (g)")
    axes[0].grid(True, alpha=0.3)
    axes[0].legend(loc="upper right", ncol=5)

    axes[1].plot(series.time_s, series.linear_x_g, label="linear_x_g", linewidth=1.0)
    axes[1].plot(series.time_s, series.linear_y_g, label="linear_y_g", linewidth=1.0)
    axes[1].plot(series.time_s, series.linear_z_g, label="linear_z_g", linewidth=1.0)
    axes[1].plot(series.time_s, series.linear_mag_g, label="|linear|", linewidth=1.4, color="black")
    axes[1].set_ylabel("Linear (g)")
    axes[1].grid(True, alpha=0.3)
    axes[1].legend(loc="upper right", ncol=4)

    axes[2].plot(series.time_s, series.gyro_x_dps, label="gyro_x_dps", linewidth=1.0)
    axes[2].plot(series.time_s, series.gyro_y_dps, label="gyro_y_dps", linewidth=1.0)
    axes[2].plot(series.time_s, series.gyro_z_dps, label="gyro_z_dps", linewidth=1.0)
    axes[2].plot(series.time_s, series.gyro_mag_dps, label="|gyro|", linewidth=1.4, color="black")
    axes[2].set_ylabel("Gyro (deg/s)")
    axes[2].grid(True, alpha=0.3)
    axes[2].legend(loc="upper right", ncol=4)

    axes[3].plot(series.time_s, series.gyro_angle_x_deg, label="angle_x_deg", linewidth=1.0)
    axes[3].plot(series.time_s, series.gyro_angle_y_deg, label="angle_y_deg", linewidth=1.0)
    axes[3].plot(series.time_s, series.gyro_angle_z_deg, label="angle_z_deg", linewidth=1.0)
    axes[3].set_ylabel("Gyro Angle (deg)")
    axes[3].grid(True, alpha=0.3)
    axes[3].legend(loc="upper right", ncol=3)

    axes[4].plot(series.time_s, series.pitch_deg, label="pitch_deg", linewidth=1.0)
    axes[4].plot(series.time_s, series.roll_deg, label="roll_deg", linewidth=1.0)
    axes[4].set_ylabel("Tilt (deg)")
    axes[4].set_xlabel("Elapsed time (s)")
    axes[4].grid(True, alpha=0.3)
    axes[4].legend(loc="upper right", ncol=3)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    print(f"Saved plot to {output_path}")

    if show:
        plt.show()
    else:
        plt.close(fig)


def main() -> int:
    args = _parse_args()
    input_path = args.input or _latest_log_path()
    if not input_path.exists():
        raise FileNotFoundError(f"CSV log not found: {input_path}")

    output_path = args.output or input_path.with_name(f"{input_path.stem}_plot.png")
    series = _load_series(
        path=input_path,
        start_seconds=args.start_seconds,
        end_seconds=args.end_seconds,
    )
    _plot(series, input_path=input_path, output_path=output_path, show=args.show)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
