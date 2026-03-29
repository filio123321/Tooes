from __future__ import annotations

from firmware.ui.redraw import runtime_change_requires_redraw
from firmware.ui.state import RuntimeSnapshot, Screen


def _snapshot(
    *,
    lat: float = 42.0,
    lon: float = 23.0,
    heading_deg: float = 0.0,
    trace_points: tuple[tuple[float, float], ...] = ((42.0, 23.0),),
) -> RuntimeSnapshot:
    return RuntimeSnapshot(
        user_lat=lat,
        user_lon=lon,
        heading_deg=heading_deg,
        trace_points=trace_points,
        towers=tuple(),
        scan_done=False,
        scan_active=False,
        nav_ready=True,
        sdr_pending=False,
        sdr_accuracy_m=None,
    )


def test_map_runtime_change_ignores_tiny_heading_jitter() -> None:
    previous = _snapshot(heading_deg=10.0)
    current = _snapshot(heading_deg=12.0)

    assert not runtime_change_requires_redraw(
        screen=Screen.MAP,
        menu_open=False,
        previous=previous,
        current=current,
        redraw_distance_m=2.0,
    )


def test_map_runtime_change_skips_background_redraw_while_menu_open() -> None:
    previous = _snapshot()
    current = _snapshot(trace_points=((42.0, 23.0), (42.0001, 23.0001)))

    assert not runtime_change_requires_redraw(
        screen=Screen.MAP,
        menu_open=True,
        previous=previous,
        current=current,
        redraw_distance_m=2.0,
    )


def test_map_runtime_change_redraws_after_meaningful_move() -> None:
    previous = _snapshot()
    current = _snapshot(lat=42.00003, lon=23.0)

    assert runtime_change_requires_redraw(
        screen=Screen.MAP,
        menu_open=False,
        previous=previous,
        current=current,
        redraw_distance_m=2.0,
    )
