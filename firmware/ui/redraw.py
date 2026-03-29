"""Redraw policy helpers for the e-paper UI."""

from __future__ import annotations

from firmware.ui.geo import haversine_km
from firmware.ui.state import RuntimeSnapshot, Screen


HEADING_REDRAW_DEG = 15.0


def _heading_delta_deg(current: float, previous: float) -> float:
    delta = abs(current - previous) % 360.0
    if delta > 180.0:
        delta = 360.0 - delta
    return delta


def runtime_change_requires_redraw(
    *,
    screen: Screen,
    menu_open: bool,
    previous: RuntimeSnapshot,
    current: RuntimeSnapshot,
    redraw_distance_m: float,
) -> bool:
    if screen in {Screen.BOOT, Screen.TUTORIAL}:
        return False

    if screen == Screen.SCANNING:
        return (
            current.scan_done != previous.scan_done
            or current.scan_active != previous.scan_active
            or current.towers != previous.towers
        )

    if menu_open:
        return False

    if (
        current.nav_ready != previous.nav_ready
        or current.sdr_pending != previous.sdr_pending
        or current.sdr_accuracy_m != previous.sdr_accuracy_m
        or current.towers != previous.towers
        or current.trace_points != previous.trace_points
    ):
        return True

    moved_m = haversine_km(
        previous.user_lat,
        previous.user_lon,
        current.user_lat,
        current.user_lon,
    ) * 1000.0
    if moved_m >= redraw_distance_m:
        return True

    return _heading_delta_deg(current.heading_deg, previous.heading_deg) >= HEADING_REDRAW_DEG
