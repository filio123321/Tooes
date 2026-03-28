#!/usr/bin/env python3
"""Estimate and plot a relative 2D path from MPU-6050 IMU logs.

This version is tuned for walking traces where:
- steps come from the linear acceleration magnitude
- heading changes come from actual turn events, not continuous gyro drift
- the sensor may be tilted or mounted off-axis

The heading estimate is built from the gyroscope projected onto the gravity
vector, which is a better approximation of yaw than assuming body-axis Z is
always vertical. Turn accumulation is suppressed between turn events so the
path stays straighter while walking.

Examples:
    python3 separate_component_files/plot_mpu6050_relative_path.py
    python3 separate_component_files/plot_mpu6050_relative_path.py my_log.csv --show
    python3 separate_component_files/plot_mpu6050_relative_path.py my_log.csv --snap-turn-deg 90
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
    gyro_x_dps: list[float]
    gyro_y_dps: list[float]
    gyro_z_dps: list[float]
    gravity_x_g: list[float]
    gravity_y_g: list[float]
    gravity_z_g: list[float]


@dataclass
class TurnSegment:
    start_index: int
    end_index: int
    raw_delta_deg: float
    used_delta_deg: float


@dataclass
class PathEstimate:
    heading_mode: str
    step_threshold_g: float
    turn_threshold_dps: float
    step_indices: list[int]
    step_times_s: list[float]
    step_headings_deg: list[float]
    step_peaks_g: list[float]
    yaw_rate_dps: list[float]
    yaw_rate_smooth_dps: list[float]
    yaw_rate_filtered_dps: list[float]
    heading_deg: list[float]
    x_m: list[float]
    y_m: list[float]
    turn_segments: list[TurnSegment]


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
        "--heading-mode",
        choices=("auto", "vertical", "x", "y", "z"),
        default="auto",
        help="How to derive heading. 'vertical' projects gyro onto gravity; this is the default via 'auto'.",
    )
    parser.add_argument(
        "--invert-heading",
        action="store_true",
        help="Flip the heading sign if the path still comes out mirrored.",
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
        "--turn-rate-threshold-dps",
        type=float,
        default=None,
        help="Only gyro rates above this are treated as turns. If omitted, auto-estimated.",
    )
    parser.add_argument(
        "--turn-smoothing-window",
        type=int,
        default=7,
        help="Moving-average window for stabilizing the turn detector.",
    )
    parser.add_argument(
        "--snap-turn-deg",
        type=float,
        default=0.0,
        help="Snap each detected turn to the nearest multiple of this amount, for example 90.",
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
    if args.turn_rate_threshold_dps is not None and args.turn_rate_threshold_dps <= 0:
        parser.error("--turn-rate-threshold-dps must be > 0 when provided")
    if args.turn_smoothing_window <= 0:
        parser.error("--turn-smoothing-window must be > 0")
    if args.snap_turn_deg < 0:
        parser.error("--snap-turn-deg must be >= 0")

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
    gyro_x_dps: list[float] = []
    gyro_y_dps: list[float] = []
    gyro_z_dps: list[float] = []
    gravity_x_g: list[float] = []
    gravity_y_g: list[float] = []
    gravity_z_g: list[float] = []

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
            gyro_x_dps.append(_float(row, "gyro_corrected_x_dps"))
            gyro_y_dps.append(_float(row, "gyro_corrected_y_dps"))
            gyro_z_dps.append(_float(row, "gyro_corrected_z_dps"))
            gravity_x_g.append(_float(row, "gravity_x_g"))
            gravity_y_g.append(_float(row, "gravity_y_g"))
            gravity_z_g.append(_float(row, "gravity_z_g"))

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
        gyro_x_dps=gyro_x_dps,
        gyro_y_dps=gyro_y_dps,
        gyro_z_dps=gyro_z_dps,
        gravity_x_g=gravity_x_g,
        gravity_y_g=gravity_y_g,
        gravity_z_g=gravity_z_g,
    )


def _moving_average(values: list[float], window: int) -> list[float]:
    if window <= 1:
        return list(values)

    out: list[float] = []
    running = 0.0
    history: list[float] = []
    for value in values:
        history.append(value)
        running += value
        if len(history) > window:
            running -= history.pop(0)
        out.append(running / len(history))
    return out


def _normalize(x: float, y: float, z: float) -> tuple[float, float, float]:
    magnitude = math.sqrt((x * x) + (y * y) + (z * z))
    if magnitude < 1e-9:
        return (0.0, 0.0, 1.0)
    return (x / magnitude, y / magnitude, z / magnitude)


def _heading_mode(mode: str) -> str:
    return "vertical" if mode == "auto" else mode


def _yaw_rate_dps(data: ImuPathInput, mode: str) -> list[float]:
    resolved_mode = _heading_mode(mode)
    if resolved_mode == "x":
        return list(data.gyro_x_dps)
    if resolved_mode == "y":
        return list(data.gyro_y_dps)
    if resolved_mode == "z":
        return list(data.gyro_z_dps)

    yaw_rate: list[float] = []
    for gx, gy, gz, grav_x, grav_y, grav_z in zip(
        data.gyro_x_dps,
        data.gyro_y_dps,
        data.gyro_z_dps,
        data.gravity_x_g,
        data.gravity_y_g,
        data.gravity_z_g,
    ):
        up_x, up_y, up_z = _normalize(grav_x, grav_y, grav_z)
        yaw_rate.append((gx * up_x) + (gy * up_y) + (gz * up_z))
    return yaw_rate


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


def _auto_turn_threshold(yaw_rate_dps: list[float], stationary: list[bool]) -> float:
    active = [
        abs(rate)
        for rate, is_stationary in zip(yaw_rate_dps, stationary)
        if not is_stationary
    ]
    if not active:
        active = [abs(rate) for rate in yaw_rate_dps]

    mean = statistics.fmean(active)
    std = statistics.pstdev(active) if len(active) > 1 else 0.0
    return max(20.0, mean + (1.75 * std))


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


def _turn_mask(
    yaw_rate_smooth_dps: list[float],
    turn_threshold_dps: float,
) -> list[bool]:
    return [abs(rate) >= turn_threshold_dps for rate in yaw_rate_smooth_dps]


def _turn_segments(mask: list[bool]) -> list[tuple[int, int]]:
    segments: list[tuple[int, int]] = []
    start_index: int | None = None

    for index, active in enumerate(mask):
        if active and start_index is None:
            start_index = index
        elif not active and start_index is not None:
            segments.append((start_index, index - 1))
            start_index = None

    if start_index is not None:
        segments.append((start_index, len(mask) - 1))

    return segments


def _build_heading(
    data: ImuPathInput,
    yaw_rate_dps: list[float],
    yaw_rate_smooth_dps: list[float],
    turn_threshold_dps: float,
    invert_heading: bool,
    heading_offset_deg: float,
    snap_turn_deg: float,
) -> tuple[list[float], list[float], list[TurnSegment]]:
    mask = _turn_mask(yaw_rate_smooth_dps, turn_threshold_dps)
    filtered_yaw_rate_dps = [
        rate if active else 0.0
        for rate, active in zip(yaw_rate_dps, mask)
    ]
    raw_turn_heading_deg = [0.0 for _ in data.time_s]
    for index in range(1, len(data.time_s)):
        raw_turn_heading_deg[index] = (
            raw_turn_heading_deg[index - 1]
            + (filtered_yaw_rate_dps[index] * data.dt_s[index])
        )

    segments = _turn_segments(mask)
    turn_segments: list[TurnSegment] = []
    heading_deg = [0.0 for _ in data.time_s]
    current_heading = 0.0
    previous_end = 0

    for start_index, end_index in segments:
        for index in range(previous_end, start_index):
            heading_deg[index] = current_heading

        segment_start_heading = raw_turn_heading_deg[start_index - 1] if start_index > 0 else 0.0
        raw_delta_deg = raw_turn_heading_deg[end_index] - segment_start_heading
        used_delta_deg = raw_delta_deg
        if snap_turn_deg > 0:
            used_delta_deg = round(raw_delta_deg / snap_turn_deg) * snap_turn_deg

        segment_length = max(1, end_index - start_index + 1)
        for offset, index in enumerate(range(start_index, end_index + 1), start=1):
            fraction = offset / segment_length
            heading_deg[index] = current_heading + (used_delta_deg * fraction)

        current_heading += used_delta_deg
        previous_end = end_index + 1
        turn_segments.append(
            TurnSegment(
                start_index=start_index,
                end_index=end_index,
                raw_delta_deg=raw_delta_deg,
                used_delta_deg=used_delta_deg,
            )
        )

    for index in range(previous_end, len(data.time_s)):
        heading_deg[index] = current_heading

    direction_sign = 1.0 if invert_heading else -1.0
    heading_deg = [
        heading_offset_deg + (direction_sign * value)
        for value in heading_deg
    ]
    return heading_deg, filtered_yaw_rate_dps, turn_segments


def _estimate_path(
    data: ImuPathInput,
    heading_mode: str,
    step_threshold_g: float,
    turn_threshold_dps: float,
    step_indices: list[int],
    yaw_rate_dps: list[float],
    yaw_rate_smooth_dps: list[float],
    heading_deg: list[float],
    yaw_rate_filtered_dps: list[float],
    turn_segments: list[TurnSegment],
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
        step_heading_rad = math.radians(step_heading_deg)
        x += step_length_m * math.sin(step_heading_rad)
        y += step_length_m * math.cos(step_heading_rad)
        x_m.append(x)
        y_m.append(y)
        step_times_s.append(data.time_s[index])
        step_headings_deg.append(step_heading_deg)
        step_peaks_g.append(data.linear_mag_g[index])

    return PathEstimate(
        heading_mode=heading_mode,
        step_threshold_g=step_threshold_g,
        turn_threshold_dps=turn_threshold_dps,
        step_indices=step_indices,
        step_times_s=step_times_s,
        step_headings_deg=step_headings_deg,
        step_peaks_g=step_peaks_g,
        yaw_rate_dps=yaw_rate_dps,
        yaw_rate_smooth_dps=yaw_rate_smooth_dps,
        yaw_rate_filtered_dps=yaw_rate_filtered_dps,
        heading_deg=heading_deg,
        x_m=x_m,
        y_m=y_m,
        turn_segments=turn_segments,
    )


def _path_length_m(path: PathEstimate) -> float:
    total = 0.0
    for index in range(1, len(path.x_m)):
        dx = path.x_m[index] - path.x_m[index - 1]
        dy = path.y_m[index] - path.y_m[index - 1]
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


def _add_span_shading(axis, spans: list[tuple[float, float]], color: str, label: str) -> None:
    first = True
    for start, end in spans:
        axis.axvspan(
            start,
            end,
            color=color,
            alpha=0.18,
            label=label if first else None,
        )
        first = False


def _turn_spans(data: ImuPathInput, turn_segments: list[TurnSegment]) -> list[tuple[float, float]]:
    return [
        (data.time_s[segment.start_index], data.time_s[segment.end_index])
        for segment in turn_segments
    ]


def _plot(
    data: ImuPathInput,
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

    stationary_spans = _stationary_spans(data.time_s, data.stationary)
    turn_spans = _turn_spans(data, path.turn_segments)
    _add_span_shading(ax_linear, stationary_spans, "#d8f3dc", "stationary")
    _add_span_shading(ax_heading, stationary_spans, "#d8f3dc", "stationary")
    _add_span_shading(ax_heading, turn_spans, "#ffe8cc", "turn")

    ax_linear.plot(data.time_s, data.linear_mag_g, label="linear_avg_mag_g", color="black")
    ax_linear.axhline(
        path.step_threshold_g,
        color="crimson",
        linestyle="--",
        label="step threshold",
    )
    if path.step_indices:
        ax_linear.scatter(
            [data.time_s[index] for index in path.step_indices],
            [data.linear_mag_g[index] for index in path.step_indices],
            color="tab:blue",
            s=25,
            label="detected steps",
            zorder=3,
        )
    ax_linear.set_ylabel("Linear |a| (g)")
    ax_linear.grid(True, alpha=0.3)
    ax_linear.legend(loc="upper right")

    ax_heading.plot(
        data.time_s,
        path.yaw_rate_smooth_dps,
        color="#999999",
        linewidth=1.0,
        label="yaw_rate_smooth_dps",
    )
    ax_heading.plot(
        data.time_s,
        path.yaw_rate_filtered_dps,
        color="tab:purple",
        linewidth=1.2,
        label="turn_only_yaw_rate_dps",
    )
    ax_heading.plot(
        data.time_s,
        path.heading_deg,
        color="tab:blue",
        linewidth=1.8,
        label="heading_deg",
    )
    if path.step_indices:
        ax_heading.scatter(
            [data.time_s[index] for index in path.step_indices],
            [path.heading_deg[index] for index in path.step_indices],
            color="tab:orange",
            s=25,
            label="heading at step",
            zorder=3,
        )
    ax_heading.axhline(path.turn_threshold_dps, color="#aa5500", linestyle=":", linewidth=1.0)
    ax_heading.axhline(-path.turn_threshold_dps, color="#aa5500", linestyle=":", linewidth=1.0)
    ax_heading.set_ylabel("Heading / Yaw")
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
            [path.x_m[index + 1] - path.x_m[index] for index in range(len(path.x_m) - 1)],
            [path.y_m[index + 1] - path.y_m[index] for index in range(len(path.y_m) - 1)],
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
            f"mode={path.heading_mode} steps={len(path.step_indices)} "
            f"path={_path_length_m(path):.2f}m net={net_distance:.2f}m "
            f"end=({path.x_m[-1]:.2f},{path.y_m[-1]:.2f})m "
            f"step_thr={path.step_threshold_g:.3f}g turn_thr={path.turn_threshold_dps:.1f}dps"
        ),
        fontsize=13,
    )

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    print(f"Saved path plot to {output_path}")
    print(f"Heading mode: {path.heading_mode}")
    print(f"Detected steps: {len(path.step_indices)}")
    print(f"Step threshold: {path.step_threshold_g:.4f} g")
    print(f"Turn threshold: {path.turn_threshold_dps:.2f} dps")
    print(f"Estimated path length: {_path_length_m(path):.2f} m")
    print(f"Estimated net displacement: {net_distance:.2f} m")
    for index, segment in enumerate(path.turn_segments, start=1):
        start_time = data.time_s[segment.start_index]
        end_time = data.time_s[segment.end_index]
        print(
            f"Turn {index}: {start_time:.2f}s -> {end_time:.2f}s "
            f"raw={segment.raw_delta_deg:.1f}deg used={segment.used_delta_deg:.1f}deg"
        )

    if show:
        plt.show()
    else:
        plt.close(fig)


def main() -> int:
    args = _parse_args()
    input_path = args.input or _latest_log_path()
    if not input_path.exists():
        raise FileNotFoundError(f"CSV log not found: {input_path}")

    output_path = args.output or input_path.with_name(f"{input_path.stem}_path.png")
    data = _load_input(
        path=input_path,
        start_seconds=args.start_seconds,
        end_seconds=args.end_seconds,
    )
    heading_mode = _heading_mode(args.heading_mode)
    yaw_rate_dps = _yaw_rate_dps(data, heading_mode)
    yaw_rate_smooth_dps = _moving_average(yaw_rate_dps, args.turn_smoothing_window)
    step_threshold_g = (
        args.peak_threshold_g
        if args.peak_threshold_g is not None
        else _auto_peak_threshold(data)
    )
    turn_threshold_dps = (
        args.turn_rate_threshold_dps
        if args.turn_rate_threshold_dps is not None
        else _auto_turn_threshold(yaw_rate_smooth_dps, data.stationary)
    )
    step_indices = _detect_steps(
        data=data,
        threshold_g=step_threshold_g,
        min_step_seconds=args.min_step_seconds,
    )
    heading_deg, yaw_rate_filtered_dps, turn_segments = _build_heading(
        data=data,
        yaw_rate_dps=yaw_rate_dps,
        yaw_rate_smooth_dps=yaw_rate_smooth_dps,
        turn_threshold_dps=turn_threshold_dps,
        invert_heading=args.invert_heading,
        heading_offset_deg=args.heading_offset_deg,
        snap_turn_deg=args.snap_turn_deg,
    )
    path = _estimate_path(
        data=data,
        heading_mode=heading_mode,
        step_threshold_g=step_threshold_g,
        turn_threshold_dps=turn_threshold_dps,
        step_indices=step_indices,
        yaw_rate_dps=yaw_rate_dps,
        yaw_rate_smooth_dps=yaw_rate_smooth_dps,
        heading_deg=heading_deg,
        yaw_rate_filtered_dps=yaw_rate_filtered_dps,
        turn_segments=turn_segments,
        step_length_m=args.step_length_m,
    )
    _plot(
        data=data,
        path=path,
        input_path=input_path,
        output_path=output_path,
        show=args.show,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
