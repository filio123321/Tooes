from __future__ import annotations
import json
from pathlib import Path

from firmware.navigation.config import load_navigation_config
from firmware.navigation.imu import ProcessedImuSample
from firmware.navigation.path_logger import PathLogger
from firmware.navigation.sdr import SdrFix
from firmware.navigation.service import NavigationEngine
from firmware.navigation.config import NavigationConfig


def _sample(
    timestamp_s: float,
    mag_g: float,
    heading_deg: float = 90.0,
    stationary: bool = False,
) -> ProcessedImuSample:
    return ProcessedImuSample(
        timestamp_s=timestamp_s,
        dt_s=0.5,
        heading_deg=heading_deg,
        accel_g=(0.0, 0.0, 1.0),
        gravity_g=(0.0, 0.0, 1.0),
        linear_g=(0.0, 0.0, 0.0),
        linear_avg_g=(mag_g, 0.0, 0.0),
        linear_avg_mag_g=mag_g,
        stationary=stationary,
    )


def test_load_navigation_config_reads_env_local(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("INITIAL_L", raising=False)
    (tmp_path / ".env.local").write_text(
        "INITIAL_L=42.1000,23.2000\nNAV_TRIGGER_DISTANCE_M=30\n",
        encoding="utf-8",
    )

    config = load_navigation_config(tmp_path)

    assert config.initial_lat == 42.1
    assert config.initial_lon == 23.2
    assert config.trigger_distance_m == 30.0
    assert config.path_log_enabled is True
    assert config.path_log_path is not None
    assert config.path_log_path.parent == tmp_path / "firmware" / "logs"


def test_navigation_engine_detects_distance_trigger() -> None:
    config = NavigationConfig(
        initial_lat=42.0,
        initial_lon=23.0,
        trigger_distance_m=0.5,
        step_length_m=1.0,
        peak_threshold_g=0.2,
        min_step_seconds=0.1,
    )
    engine = NavigationEngine(config)

    engine.update_with_sample(_sample(0.0, 0.1))
    engine.update_with_sample(_sample(0.5, 0.5))
    snapshot = engine.update_with_sample(_sample(1.0, 0.1))

    assert snapshot.relative_x_m == 1.0
    assert engine.distance_since_anchor_m == 1.0
    assert engine.needs_sdr_scan(now_s=10.0)


def test_navigation_engine_blends_sdr_fix_and_resets_anchor() -> None:
    config = NavigationConfig(
        initial_lat=42.0,
        initial_lon=23.0,
        trigger_distance_m=0.5,
        step_length_m=1.0,
        peak_threshold_g=0.2,
        min_step_seconds=0.1,
        sdr_confidence_radius_m=500.0,
        sdr_blend_floor=0.05,
        sdr_blend_cap=0.35,
    )
    engine = NavigationEngine(config)
    engine.update_with_sample(_sample(0.0, 0.1))
    engine.update_with_sample(_sample(0.5, 0.5))
    engine.update_with_sample(_sample(1.0, 0.1))

    before_lat = engine.snapshot().lat
    before_lon = engine.snapshot().lon
    engine.apply_sdr_fix(
        SdrFix(
            lat=42.0100,
            lon=23.0100,
            accuracy_m=50.0,
            n_sources=5,
        )
    )
    after = engine.snapshot()

    assert after.fix_source == "RF_BLEND"
    assert after.lat != before_lat or after.lon != before_lon
    assert after.distance_since_anchor_m == 0.0
    assert after.sdr_accuracy_m == 500.0


def test_trace_history_is_capped() -> None:
    config = NavigationConfig(
        initial_lat=42.0,
        initial_lon=23.0,
        trace_max_points=3,
    )
    engine = NavigationEngine(config)
    engine.apply_sdr_fix(SdrFix(lat=42.0001, lon=23.0001, accuracy_m=500.0, n_sources=4))
    engine.apply_sdr_fix(SdrFix(lat=42.0002, lon=23.0002, accuracy_m=500.0, n_sources=4))
    engine.apply_sdr_fix(SdrFix(lat=42.0003, lon=23.0003, accuracy_m=500.0, n_sources=4))

    assert len(engine.snapshot().trace_points) == 3


def test_path_logger_writes_jsonl_points(tmp_path: Path) -> None:
    log_path = tmp_path / "navigation_trace.jsonl"
    config = NavigationConfig(
        initial_lat=42.0,
        initial_lon=23.0,
        trigger_distance_m=0.5,
        step_length_m=1.0,
        peak_threshold_g=0.2,
        min_step_seconds=0.1,
    )
    logger = PathLogger(log_path)
    engine = NavigationEngine(config, path_logger=logger)

    engine.update_with_sample(_sample(0.0, 0.1))
    engine.update_with_sample(_sample(0.5, 0.5))
    engine.update_with_sample(_sample(1.0, 0.1))
    engine.apply_sdr_fix(
        SdrFix(lat=42.0005, lon=23.0005, accuracy_m=50.0, n_sources=4)
    )
    engine.close()

    rows = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows[0]["source"] == "INITIAL"
    assert any(row["source"] == "IMU" for row in rows)
    assert rows[-1]["source"] == "RF_BLEND"
    assert "timestamp_iso" in rows[-1]
