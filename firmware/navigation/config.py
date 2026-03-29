"""Navigation configuration sourced from `.env.local` and process env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


_DEFAULT_INITIAL_LAT = 42.012280
_DEFAULT_INITIAL_LON = 23.095261


@dataclass(frozen=True)
class NavigationConfig:
    initial_lat: float
    initial_lon: float
    update_hz: float = 20.0
    trigger_distance_m: float = 25.0
    trace_point_distance_m: float = 5.0
    trace_max_points: int = 256
    step_length_m: float = 0.70
    peak_threshold_g: float = 0.12
    min_step_seconds: float = 0.35
    gravity_time_constant_s: float = 0.75
    linear_smoothing_window: int = 5
    stationary_linear_threshold_g: float = 0.08
    stationary_magnitude_threshold_g: float = 0.12
    sdr_enabled: bool = True
    sdr_driver: str = "sdrplay"
    sdr_serial: str | None = None
    sdr_catalogue: Path | None = None
    sdr_types: tuple[str, ...] | None = None
    sdr_confidence_radius_m: float = 500.0
    sdr_blend_floor: float = 0.05
    sdr_blend_cap: float = 0.35
    sdr_min_interval_s: float = 5.0
    nav_update_hz: float = 25.0
    redraw_distance_m: float = 2.0
    path_log_enabled: bool = False
    path_log_path: Path | None = None


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_optional_tuple(value: str | None) -> tuple[str, ...] | None:
    if value is None or not value.strip():
        return None
    items = [item.strip() for item in value.split(",")]
    return tuple(item for item in items if item)


def _parse_initial_location(value: str | None) -> tuple[float, float]:
    if not value:
        return _DEFAULT_INITIAL_LAT, _DEFAULT_INITIAL_LON

    cleaned = value.replace(";", ",").replace(" ", "")
    parts = [part for part in cleaned.split(",") if part]
    if len(parts) != 2:
        raise ValueError(
            "INITIAL_L must be two comma-separated numbers, for example "
            "\"42.012280,23.095261\""
        )
    return float(parts[0]), float(parts[1])


def load_navigation_config(repo_root: Path | None = None) -> NavigationConfig:
    repo_root = repo_root or Path(__file__).resolve().parents[2]
    env_file_values = _read_env_file(repo_root / ".env.local")

    def get(name: str, default: str | None = None) -> str | None:
        if name in os.environ:
            return os.environ[name]
        if name in env_file_values:
            return env_file_values[name]
        return default

    initial_lat, initial_lon = _parse_initial_location(get("INITIAL_L"))

    catalogue = get("SDR_CATALOGUE")
    catalogue_path = Path(catalogue).expanduser() if catalogue else None
    if catalogue_path and not catalogue_path.is_absolute():
        catalogue_path = repo_root / catalogue_path

    path_log_enabled = _parse_bool(get("NAV_PATH_LOG_ENABLED", "true"), True)
    path_log_path = None
    if path_log_enabled:
        configured_log_path = get("NAV_PATH_LOG_PATH")
        if configured_log_path:
            path_log_path = Path(configured_log_path).expanduser()
            if not path_log_path.is_absolute():
                path_log_path = repo_root / path_log_path
        else:
            stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
            path_log_path = (
                repo_root / "firmware" / "logs" / f"navigation_trace_{stamp}.jsonl"
            )

    return NavigationConfig(
        initial_lat=initial_lat,
        initial_lon=initial_lon,
        update_hz=float(get("NAV_UPDATE_HZ", "20.0")),
        trigger_distance_m=float(get("NAV_TRIGGER_DISTANCE_M", "25.0")),
        trace_point_distance_m=float(get("NAV_TRACE_POINT_DISTANCE_M", "5.0")),
        trace_max_points=int(get("NAV_TRACE_MAX_POINTS", "256")),
        step_length_m=float(get("IMU_STEP_LENGTH_M", "0.70")),
        peak_threshold_g=float(get("IMU_PEAK_THRESHOLD_G", "0.12")),
        min_step_seconds=float(get("IMU_MIN_STEP_SECONDS", "0.35")),
        gravity_time_constant_s=float(get("IMU_GRAVITY_TIME_CONSTANT_S", "0.75")),
        linear_smoothing_window=int(get("IMU_LINEAR_SMOOTHING_WINDOW", "5")),
        stationary_linear_threshold_g=float(
            get("IMU_STATIONARY_LINEAR_THRESHOLD_G", "0.08")
        ),
        stationary_magnitude_threshold_g=float(
            get("IMU_STATIONARY_MAG_THRESHOLD_G", "0.12")
        ),
        sdr_enabled=_parse_bool(get("SDR_ENABLED"), True),
        sdr_driver=get("SDR_DRIVER", "sdrplay") or "sdrplay",
        sdr_serial=get("SDR_SERIAL"),
        sdr_catalogue=catalogue_path,
        sdr_types=_parse_optional_tuple(get("SDR_TYPES")),
        sdr_confidence_radius_m=float(get("SDR_CONFIDENCE_RADIUS_M", "500.0")),
        sdr_blend_floor=float(get("SDR_BLEND_FLOOR", "0.05")),
        sdr_blend_cap=float(get("SDR_BLEND_CAP", "0.35")),
        sdr_min_interval_s=float(get("SDR_MIN_INTERVAL_S", "5.0")),
        nav_update_hz=float(get("NAV_UPDATE_HZ", "25.0")),
        redraw_distance_m=float(get("NAV_REDRAW_DISTANCE_M", "2.0")),
        path_log_enabled=path_log_enabled,
        path_log_path=path_log_path,
    )
