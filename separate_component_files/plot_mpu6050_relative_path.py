#!/usr/bin/env python3
"""Estimate and plot a relative 2D path from MPU-6050 IMU logs.

This script does not recover a true ground-truth path. It produces a first-pass
dead-reckoning estimate by:
1. detecting likely steps from linear acceleration magnitude
2. using one integrated gyro angle axis as the relative heading
3. advancing a fixed step length in that heading

That makes it useful for visualizing relative movement around the start point,
but it will drift and depends heavily on sensor mounting.

Examples:
    python3 separate_component_files/plot_mpu6050_relative_path.py
    python3 separate_component_files/plot_mpu6050_relative_path.py my_log.csv --show
    python3 separate_component_files/plot_mpu6050_relative_path.py my_log.csv --heading-axis z
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from dataclasses import dataclass
from pathlib import Path


_DEFAULT_LOG_DIR = Path(__file__).resolve().parent / "logs"


@dataclass
class ImuPathInput:
    time_s: list[float]
    linear_mag_g: list[float]
    stationary: list[bool]
    angle_x_deg: list[float]
    angle_y_deg: list[float]
    angle_z_deg: list[float]


@dataclass
class PathEstimate:
    heading_axis: str
    threshold_g: float
    step_indices: list[int]
    step_times_s: list[float]
    step_headings_deg: list[float]
    step_peaks_g: list[float]
    x_m: list[float]
    y_m: list[float]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Estimate a relative 2D path from an MPU-6050 IMU CSV log."
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
        help="PNG output path. Defaults next to the CSV with a _path suffix.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Open the plot interactively after saving.",
    )
    parser.add_argument(
        "--heading-axis",
        choices=("auto", "x", "y", "z"),
        default="auto",
        help="Which integrated gyro angle to treat as heading.",
    )
    parser.add_argument(
        "--invert-heading",
        action="store_true",
        help="Flip the heading sign if the path comes out mirrored.",
    )
    parser.add_argument(
        "--heading-offset-deg",
        type=float,
        default=0.0,
        help="Rotate the whole path by a fixed offset in degrees.",
    )
    parser.add_argument(
        "--step-length-m",
        type=float,
        default=0.70,
        help="Assumed step length in meters.",
    )
    parser.add_argument(
        "--min-step-seconds",
        type=float,
        default=0.35,
        help="Minimum time between detected steps.",
    )
    parser.add_argument(
        "--peak-threshold-g",
        type=float,
        default=None,
        help="Manual threshold for linear acceleration peaks. If omitted, auto-estimated.",
    )
    parser.add_argument(
        "--start-seconds",
        type=float,
        default=None,
        help="Optional lower bound for elapsed time.",
    )
    parser.add_argument(
        "--end-seconds",
        type=float,
        default=None,
        help="Optional upper bound for elapsed time.",
    )
    args = parser.parse_args()

    if args.step_length_m <= 0:
        parser.error("--step-length-m must be > 0")
    if args.min_step_seconds <= 0:
        parser.error("--min-step-seconds must be > 0")
    if args.peak_threshold_g is not None and args.peak_threshold_g <= 0:
        parser.error("--peak-threshold-g must be > 0 when provided")

    return args


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


def _load_input(
    path: Path,
    start_seconds: float | None,
    end_seconds: float | None,
) -> ImuPathInput:
    time_s: list[float] = []
    linear_mag_g: list[float] = []
    stationary: list[bool] = []
    angle_x_deg: list[float] = []
    angle_y_deg: list[float] = []
    angle_z_deg: list[float] = []

    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            elapsed = _float(row, "elapsed_s")
            if start_seconds is not None and elapsed < start_seconds:
                continue
            if end_seconds is not None and elapsed > end_seconds:
                continue

            time_s.append(elapsed)
            linear_mag_g.append(_float(row, "linear_avg_mag_g"))
            stationary.append(_bool(row, "stationary"))
            angle_x_deg.append(_float(row, "gyro_angle_x_deg"))
            angle_y_deg.append(_float(row, "gyro_angle_y_deg"))
            angle_z_deg.append(_float(row, "gyro_angle_z_deg"))

    if not time_s:
        raise ValueError("The selected CSV has no rows in the requested time window.")

    return ImuPathInput(
        time_s=time_s,
        linear_mag_g=linear_mag_g,
        stationary=stationary,
        angle_x_deg=angle_x_deg,
        angle_y_deg=angle_y_deg,
        angle_z_deg=angle_z_deg,
    )


def _choose_heading_axis(data: ImuPathInput, mode: str) -> str:
    if mode != "auto":
        return mode

    active_indices = [i for i, is_stationary in enumerate(data.stationary) if not is_stationary]
    if not active_indices:
        active_indices = list(range(len(data.time_s)))

    axis_to_values = {
        "x": [data.angle_x_deg[i] for i in active_indices],
        "y": [data.angle_y_deg[i] for i in active_indices],
        "z": [data.angle_z_deg[i] for i in active_indices],
    }
    axis_ranges = {
        axis: (max(values) - min(values)) if values else 0.0
        for axis, values in axis_to_values.items()
    }
    return max(axis_ranges, key=axis_ranges.get)


def _heading_series(data: ImuPathInput, axis: str) -> list[float]:
    if axis == "x":
        return data.angle_x_deg
    if axis == "y":
        return data.angle_y_deg
    return data.angle_z_deg


def _auto_peak_threshold(data: ImuPathInput) -> float:
    stationary_values = [
        value
        for value, is_stationary in zip(data.linear_mag_g, data.stationary)
        if is_stationary
    ]
    if stationary_values:
        mean = statistics.fmean(stationary_values)
        std = statistics.pstdev(stationary_values) if len(stationary_values) > 1 else 0.0
        return max(0.02, mean + (4.0 * std))

    return max(0.02, statistics.fmean(data.linear_mag_g) * 2.5)


def _detect_steps(
    data: ImuPathInput,
    threshold_g: float,
    min_step_seconds: float,
) -> list[int]:
    peaks: list[int] = []
    last_peak_time = float("-inf")

    for index in range(1, len(data.linear_mag_g) - 1):
        value = data.linear_mag_g[index]
        if data.stationary[index]:
            continue
        if value < threshold_g:
            continue
        if value < data.linear_mag_g[index - 1] or value <= data.linear_mag_g[index + 1]:
            continue

        current_time = data.time_s[index]
        if current_time - last_peak_time < min_step_seconds:
            if peaks and value > data.linear_mag_g[peaks[-1]]:
                peaks[-1] = index
                last_peak_time = current_time
            continue

        peaks.append(index)
        last_peak_time = current_time

    return peaks


def _estimate_path(
    data: ImuPathInput,
    heading_axis: str,
    threshold_g: float,
    step_indices: list[int],
    step_length_m: float,
    invert_heading: bool,
    heading_offset_deg: float,
) -> PathEstimate:
    headings = _heading_series(data, heading_axis)
    x_m = [0.0]
    y_m = [0.0]
    step_times_s: list[float] = []
    step_headings_deg: list[float] = []
    step_peaks_g: list[float] = []

    x = 0.0
    y = 0.0
    sign = -1.0 if invert_heading else 1.0

    for index in step_indices:
        heading_deg = (sign * headings[index]) + heading_offset_deg
        heading_rad = math.radians(heading_deg)
        x += step_length_m * math.sin(heading_rad)
        y += step_length_m * math.cos(heading_rad)
        x_m.append(x)
        y_m.append(y)
        step_times_s.append(data.time_s[index])
        step_headings_deg.append(heading_deg)
        step_peaks_g.append(data.linear_mag_g[index])

    return PathEstimate(
        heading_axis=heading_axis,
        threshold_g=threshold_g,
        step_indices=step_indices,
        step_times_s=step_times_s,
        step_headings_deg=step_headings_deg,
        step_peaks_g=step_peaks_g,
        x_m=x_m,
        y_m=y_m,
    )


def _path_length_m(path: PathEstimate) -> float:
    total = 0.0
    for i in range(1, len(path.x_m)):
        dx = path.x_m[i] - path.x_m[i - 1]
        dy = path.y_m[i] - path.y_m[i - 1]
        total += math.hypot(dx, dy)
    return total


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


def _add_stationary_shading(axis, spans: list[tuple[float, float]]) -> None:
    first = True
    for start, end in spans:
        axis.axvspan(
            start,
            end,
            color="#d8f3dc",
            alpha=0.25,
            label="stationary" if first else None,
        )
        first = False


def _plot(
    series: ImuPathInput,
    path: PathEstimate,
    input_path: Path,
    output_path: Path,
    show: bool,
) -> None:
    try:
        import matplotlib
        if not show:
            matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "matplotlib is required for plotting. Install it with `pip install matplotlib`."
        ) from exc

    fig = plt.figure(figsize=(13, 12))
    grid = fig.add_gridspec(3, 1, height_ratios=[1.0, 1.0, 1.4])
    ax_linear = fig.add_subplot(grid[0, 0])
    ax_heading = fig.add_subplot(grid[1, 0], sharex=ax_linear)
    ax_path = fig.add_subplot(grid[2, 0])

    spans = _stationary_spans(series.time_s, series.stationary)
    _add_stationary_shading(ax_linear, spans)
    _add_stationary_shading(ax_heading, spans)

    ax_linear.plot(series.time_s, series.linear_mag_g, label="linear_avg_mag_g", color="black")
    ax_linear.axhline(path.threshold_g, color="crimson", linestyle="--", label="step threshold")
    if path.step_indices:
        ax_linear.scatter(
            [series.time_s[i] for i in path.step_indices],
            [series.linear_mag_g[i] for i in path.step_indices],
            color="tab:blue",
            s=25,
            label="detected steps",
            zorder=3,
        )
    ax_linear.set_ylabel("Linear |a| (g)")
    ax_linear.grid(True, alpha=0.3)
    ax_linear.legend(loc="upper right")

    heading_values = _heading_series(series, path.heading_axis)
    ax_heading.plot(series.time_s, heading_values, label=f"gyro_angle_{path.heading_axis}_deg")
    if path.step_indices:
        ax_heading.scatter(
            [series.time_s[i] for i in path.step_indices],
            [heading_values[i] for i in path.step_indices],
            color="tab:orange",
            s=25,
            label="heading at step",
            zorder=3,
        )
    ax_heading.set_ylabel("Heading (deg)")
    ax_heading.set_xlabel("Elapsed time (s)")
    ax_heading.grid(True, alpha=0.3)
    ax_heading.legend(loc="upper right")

    ax_path.plot(path.x_m, path.y_m, color="tab:blue", linewidth=2.0, label="estimated path")
    ax_path.scatter([path.x_m[0]], [path.y_m[0]], color="green", s=80, marker="o", label="start")
    ax_path.scatter([path.x_m[-1]], [path.y_m[-1]], color="red", s=100, marker="X", label="end")
    if len(path.x_m) > 1:
        ax_path.quiver(
            path.x_m[:-1],
            path.y_m[:-1],
            [path.x_m[i + 1] - path.x_m[i] for i in range(len(path.x_m) - 1)],
            [path.y_m[i + 1] - path.y_m[i] for i in range(len(path.y_m) - 1)],
            angles="xy",
            scale_units="xy",
            scale=1,
            width=0.003,
            color="tab:blue",
            alpha=0.45,
        )
    ax_path.axhline(0.0, color="#999999", linewidth=0.8)
    ax_path.axvline(0.0, color="#999999", linewidth=0.8)
    ax_path.set_aspect("equal", adjustable="datalim")
    ax_path.set_xlabel("X relative (m)")
    ax_path.set_ylabel("Y relative (m)")
    ax_path.grid(True, alpha=0.3)
    ax_path.legend(loc="upper right")

    net_distance = math.hypot(path.x_m[-1], path.y_m[-1])
    fig.suptitle(
        (
            f"Relative Path Estimate: {input_path.name}\n"
            f"axis={path.heading_axis} steps={len(path.step_indices)} "
            f"step_length={_format2(path.x_m, path.y_m, net_distance, _path_length_m(path), path.threshold_g)}"
        ),
        fontsize=13,
    )

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    print(f"Saved path plot to {output_path}")
    print(f"Heading axis: {path.heading_axis}")
    print(f"Detected steps: {len(path.step_indices)}")
    print(f"Peak threshold: {path.threshold_g:.4f} g")
    print(f"Estimated path length: {_path_length_m(path):.2f} m")
    print(f"Estimated net displacement: {net_distance:.2f} m")

    if show:
        plt.show()
    else:
        plt.close(fig)


def _format2(
    x_m: list[float],
    y_m: list[float],
    net_distance: float,
    path_length: float,
    threshold_g: float,
) -> str:
    end_x = x_m[-1]
    end_y = y_m[-1]
    return (
        f"path={path_length:.2f}m net={net_distance:.2f}m "
        f"end=({end_x:.2f},{end_y:.2f})m thr={threshold_g:.3f}g"
    )


def main() -> int:
    args = _parse_args()
    input_path = args.input or _latest_log_path()
    if not input_path.exists():
        raise FileNotFoundError(f"CSV log not found: {input_path}")

    output_path = args.output or input_path.with_name(f"{input_path.stem}_path.png")
    series = _load_input(
        path=input_path,
        start_seconds=args.start_seconds,
        end_seconds=args.end_seconds,
    )
    heading_axis = _choose_heading_axis(series, args.heading_axis)
    threshold_g = (
        args.peak_threshold_g
        if args.peak_threshold_g is not None
        else _auto_peak_threshold(series)
    )
    step_indices = _detect_steps(
        data=series,
        threshold_g=threshold_g,
        min_step_seconds=args.min_step_seconds,
    )
    path = _estimate_path(
        data=series,
        heading_axis=heading_axis,
        threshold_g=threshold_g,
        step_indices=step_indices,
        step_length_m=args.step_length_m,
        invert_heading=args.invert_heading,
        heading_offset_deg=args.heading_offset_deg,
    )
    _plot(
        series=series,
        path=path,
        input_path=input_path,
        output_path=output_path,
        show=args.show,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
