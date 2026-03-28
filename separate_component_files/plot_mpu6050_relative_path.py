#!/usr/bin/env python3
"""Estimate and plot a relative 2D path from MPU-6050 IMU logs.

This script does not recover a true ground-truth path. It produces a practical
dead-reckoning estimate by:
1. detecting likely steps from linear acceleration magnitude using peak height
   and local prominence
2. estimating heading from the corrected gyro rates, with drift suppression
   between turns
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
    dt_s: list[float]
    linear_mag_g: list[float]
    stationary: list[bool]
    angle_x_deg: list[float]
    angle_y_deg: list[float]
    angle_z_deg: list[float]
    gyro_x_dps: list[float]
    gyro_y_dps: list[float]
    gyro_z_dps: list[float]
    gravity_x_g: list[float]
    gravity_y_g: list[float]
    gravity_z_g: list[float]
    has_rate_data: bool
    has_gravity_data: bool


@dataclass
class PathEstimate:
    heading_axis: str
    threshold_g: float
    turn_threshold_dps: float
    heading_deg: list[float]
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
        help="Heading source: auto uses the corrected gyro rates, while x/y/z force a specific axis.",
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


def _optional_float(row: dict[str, str], key: str, default: float = 0.0) -> float:
    value = row.get(key)
    if value in (None, ""):
        return default
    return float(value)


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
    gyro_x_dps: list[float] = []
    gyro_y_dps: list[float] = []
    gyro_z_dps: list[float] = []
    gravity_x_g: list[float] = []
    gravity_y_g: list[float] = []
    gravity_z_g: list[float] = []

    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        has_rate_data = {
            "gyro_corrected_x_dps",
            "gyro_corrected_y_dps",
            "gyro_corrected_z_dps",
        }.issubset(fieldnames)
        has_gravity_data = {
            "gravity_x_g",
            "gravity_y_g",
            "gravity_z_g",
        }.issubset(fieldnames)
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
            gyro_x_dps.append(_optional_float(row, "gyro_corrected_x_dps"))
            gyro_y_dps.append(_optional_float(row, "gyro_corrected_y_dps"))
            gyro_z_dps.append(_optional_float(row, "gyro_corrected_z_dps"))
            gravity_x_g.append(_optional_float(row, "gravity_x_g"))
            gravity_y_g.append(_optional_float(row, "gravity_y_g"))
            gravity_z_g.append(_optional_float(row, "gravity_z_g"))

    if not time_s:
        raise ValueError("The selected CSV has no rows in the requested time window.")

    dt_s = [0.0]
    for index in range(1, len(time_s)):
        dt_s.append(max(0.0, time_s[index] - time_s[index - 1]))

    return ImuPathInput(
        time_s=time_s,
        dt_s=dt_s,
        linear_mag_g=linear_mag_g,
        stationary=stationary,
        angle_x_deg=angle_x_deg,
        angle_y_deg=angle_y_deg,
        angle_z_deg=angle_z_deg,
        gyro_x_dps=gyro_x_dps,
        gyro_y_dps=gyro_y_dps,
        gyro_z_dps=gyro_z_dps,
        gravity_x_g=gravity_x_g,
        gravity_y_g=gravity_y_g,
        gravity_z_g=gravity_z_g,
        has_rate_data=has_rate_data,
        has_gravity_data=has_gravity_data,
    )


def _choose_heading_axis(data: ImuPathInput, mode: str) -> str:
    if mode != "auto":
        return mode

    if data.has_rate_data and data.has_gravity_data:
        return "vertical"

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


def _median_dt_s(data: ImuPathInput) -> float:
    positive = [dt for dt in data.dt_s if dt > 0]
    if not positive:
        return 0.02
    return statistics.median(positive)


def _moving_average(values: list[float], window_size: int) -> list[float]:
    if window_size <= 1:
        return list(values)

    result: list[float] = []
    running_total = 0.0
    history: list[float] = []
    for value in values:
        history.append(value)
        running_total += value
        if len(history) > window_size:
            running_total -= history.pop(0)
        result.append(running_total / len(history))
    return result


def _normalize(x: float, y: float, z: float) -> tuple[float, float, float]:
    magnitude = math.sqrt((x * x) + (y * y) + (z * z))
    if magnitude <= 1e-9:
        return (0.0, 0.0, 1.0)
    return (x / magnitude, y / magnitude, z / magnitude)


def _turn_rate_series(data: ImuPathInput, axis: str) -> list[float]:
    if not data.has_rate_data:
        return [0.0 for _ in data.time_s]

    if axis == "x":
        return list(data.gyro_x_dps)
    if axis == "y":
        return list(data.gyro_y_dps)
    if axis == "z":
        return list(data.gyro_z_dps)

    rates: list[float] = []
    for gx, gy, gz, grav_x, grav_y, grav_z in zip(
        data.gyro_x_dps,
        data.gyro_y_dps,
        data.gyro_z_dps,
        data.gravity_x_g,
        data.gravity_y_g,
        data.gravity_z_g,
    ):
        up_x, up_y, up_z = _normalize(grav_x, grav_y, grav_z)
        rates.append((gx * up_x) + (gy * up_y) + (gz * up_z))
    return rates


def _auto_turn_threshold(turn_rate_dps: list[float], stationary: list[bool]) -> float:
    stationary_abs = [
        abs(rate)
        for rate, is_stationary in zip(turn_rate_dps, stationary)
        if is_stationary
    ]
    if stationary_abs:
        mean = statistics.fmean(stationary_abs)
        std = statistics.pstdev(stationary_abs) if len(stationary_abs) > 1 else 0.0
        return max(3.0, mean + (4.0 * std))

    active_abs = [abs(rate) for rate in turn_rate_dps]
    mean = statistics.fmean(active_abs)
    std = statistics.pstdev(active_abs) if len(active_abs) > 1 else 0.0
    return max(5.0, mean + (2.0 * std))


def _build_heading_series(
    data: ImuPathInput,
    axis: str,
    invert_heading: bool,
    heading_offset_deg: float,
) -> tuple[list[float], float]:
    if axis in {"x", "y", "z", "vertical"} and data.has_rate_data:
        raw_turn_rate = _turn_rate_series(data, axis)
        sample_dt_s = _median_dt_s(data)
        smoothing_window = max(3, int(round(0.12 / sample_dt_s)))
        raw_turn_rate = _moving_average(raw_turn_rate, smoothing_window)
        turn_threshold_dps = _auto_turn_threshold(raw_turn_rate, data.stationary)
        filtered_turn_rate = [
            rate if abs(rate) >= turn_threshold_dps else 0.0
            for rate in raw_turn_rate
        ]
        sign = 1.0 if invert_heading else -1.0
        heading_deg = [heading_offset_deg]
        for index in range(1, len(data.time_s)):
            heading_deg.append(
                heading_deg[-1] + (sign * filtered_turn_rate[index] * data.dt_s[index])
            )
        return heading_deg, turn_threshold_dps

    if axis == "x":
        return [heading_offset_deg + value for value in data.angle_x_deg], 0.0
    if axis == "y":
        return [heading_offset_deg + value for value in data.angle_y_deg], 0.0
    return [heading_offset_deg + value for value in data.angle_z_deg], 0.0


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


def _auto_prominence_threshold(data: ImuPathInput, peak_threshold_g: float) -> float:
    stationary_values = [
        value
        for value, is_stationary in zip(data.linear_mag_g, data.stationary)
        if is_stationary
    ]
    if stationary_values:
        std = statistics.pstdev(stationary_values) if len(stationary_values) > 1 else 0.0
        return max(0.01, 3.0 * std, peak_threshold_g * 0.18)
    return max(0.01, peak_threshold_g * 0.18)


def _detect_steps(
    data: ImuPathInput,
    threshold_g: float,
    min_step_seconds: float,
) -> list[int]:
    peaks: list[int] = []
    last_peak_time = float("-inf")
    sample_dt_s = _median_dt_s(data)
    neighborhood = max(2, int(round((min_step_seconds * 0.5) / sample_dt_s)))
    prominence_threshold = _auto_prominence_threshold(data, threshold_g)

    for index in range(1, len(data.linear_mag_g) - 1):
        value = data.linear_mag_g[index]
        if data.stationary[index]:
            continue
        if value < threshold_g:
            continue
        if value < data.linear_mag_g[index - 1] or value <= data.linear_mag_g[index + 1]:
            continue

        left_slice = data.linear_mag_g[max(0, index - neighborhood):index]
        right_slice = data.linear_mag_g[index + 1:min(len(data.linear_mag_g), index + 1 + neighborhood)]
        if not left_slice or not right_slice:
            continue
        prominence = value - max(min(left_slice), min(right_slice))
        if prominence < prominence_threshold:
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
    turn_threshold_dps: float,
    heading_deg: list[float],
    step_indices: list[int],
    step_length_m: float,
) -> PathEstimate:
    x_m = [0.0]
    y_m = [0.0]
    step_times_s: list[float] = []
    step_headings_deg: list[float] = []
    step_peaks_g: list[float] = []

    x = 0.0
    y = 0.0

    for index in step_indices:
        step_heading_deg = heading_deg[index]
        heading_rad = math.radians(step_heading_deg)
        x += step_length_m * math.sin(heading_rad)
        y += step_length_m * math.cos(heading_rad)
        x_m.append(x)
        y_m.append(y)
        step_times_s.append(data.time_s[index])
        step_headings_deg.append(step_heading_deg)
        step_peaks_g.append(data.linear_mag_g[index])

    return PathEstimate(
        heading_axis=heading_axis,
        threshold_g=threshold_g,
        turn_threshold_dps=turn_threshold_dps,
        heading_deg=heading_deg,
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

    ax_heading.plot(series.time_s, path.heading_deg, label=f"heading_{path.heading_axis}_deg")
    if path.step_indices:
        ax_heading.scatter(
            [series.time_s[i] for i in path.step_indices],
            [path.heading_deg[i] for i in path.step_indices],
            color="tab:orange",
            s=25,
            label="heading at step",
            zorder=3,
        )
    if path.turn_threshold_dps > 0:
        ax_heading.text(
            0.01,
            0.98,
            f"turn threshold: {path.turn_threshold_dps:.1f} dps",
            transform=ax_heading.transAxes,
            ha="left",
            va="top",
            fontsize=9,
            bbox={"facecolor": "white", "alpha": 0.75, "edgecolor": "#cccccc"},
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
            f"step_length={_format2(path.x_m, path.y_m, net_distance, _path_length_m(path), path.threshold_g, path.turn_threshold_dps)}"
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
    if path.turn_threshold_dps > 0:
        print(f"Turn threshold: {path.turn_threshold_dps:.2f} dps")
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
    turn_threshold_dps: float,
) -> str:
    end_x = x_m[-1]
    end_y = y_m[-1]
    return (
        f"path={path_length:.2f}m net={net_distance:.2f}m "
        f"end=({end_x:.2f},{end_y:.2f})m thr={threshold_g:.3f}g turn={turn_threshold_dps:.1f}dps"
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
    heading_deg, turn_threshold_dps = _build_heading_series(
        data=series,
        axis=heading_axis,
        invert_heading=args.invert_heading,
        heading_offset_deg=args.heading_offset_deg,
    )
    path = _estimate_path(
        data=series,
        heading_axis=heading_axis,
        threshold_g=threshold_g,
        turn_threshold_dps=turn_threshold_dps,
        heading_deg=heading_deg,
        step_indices=step_indices,
        step_length_m=args.step_length_m,
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
