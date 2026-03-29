"""Shared UI state and render snapshots for the e-paper shell."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from firmware.hal.types import CellKey


class Screen(Enum):
    BOOT = auto()
    TUTORIAL = auto()
    SCANNING = auto()
    MAP = auto()


@dataclass
class UiState:
    """Mutable UI-only state owned by the display shell."""

    screen: Screen = Screen.BOOT
    tutorial_page: int = 0
    zoom: int = 16
    show_overlay: bool = False
    show_catalog_towers: bool = True
    show_trace: bool = True
    menu_open: bool = False
    menu_index: int = 0


@dataclass
class DiscoveredTower:
    """A cell tower found during scanning."""

    key: CellKey
    lat: float | None
    lon: float | None
    best_rssi: float
    label: str


@dataclass(frozen=True)
class RuntimeSnapshot:
    """Read-only snapshot produced by the background runtime service."""

    user_lat: float
    user_lon: float
    heading_deg: float
    trace_points: tuple[tuple[float, float], ...]
    towers: tuple[DiscoveredTower, ...]
    scan_done: bool
    scan_active: bool
    nav_ready: bool
    sdr_pending: bool
    sdr_accuracy_m: float | None


@dataclass(frozen=True)
class RenderState:
    """Everything the screen renderers need for one frame."""

    screen: Screen
    tutorial_page: int
    zoom: int
    heading_deg: float
    user_lat: float
    user_lon: float
    trace_points: tuple[tuple[float, float], ...]
    towers: tuple[DiscoveredTower, ...]
    catalog_towers: tuple[object, ...]
    scan_done: bool
    scan_active: bool
    show_overlay: bool
    show_catalog_towers: bool
    show_trace: bool
    menu_open: bool
    menu_index: int
