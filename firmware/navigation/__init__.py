"""Navigation runtime for IMU dead reckoning, SDR anchoring, and trace history."""

from __future__ import annotations

from typing import TYPE_CHECKING

from firmware.navigation.config import NavigationConfig, load_navigation_config
from firmware.navigation.imu import (
    ImuSampleProcessor,
    ProcessedImuSample,
    RelativePathTracker,
)
from firmware.navigation.path_logger import PathLogger
from firmware.navigation.sdr import SdrFix, SdrFixProvider
from firmware.navigation.service import NavigationEngine, NavigationSnapshot
from firmware.navigation.trace import TraceHistory, TracePoint

if TYPE_CHECKING:
    from firmware.runtime.orchestrator import FirmwareOrchestrator

__all__ = [
    "ImuSampleProcessor",
    "NavigationConfig",
    "NavigationEngine",
    "NavigationSnapshot",
    "PathLogger",
    "ProcessedImuSample",
    "RelativePathTracker",
    "FirmwareOrchestrator",
    "SdrFix",
    "SdrFixProvider",
    "TraceHistory",
    "TracePoint",
    "load_navigation_config",
]


def __getattr__(name: str):
    if name == "FirmwareOrchestrator":
        from firmware.runtime.orchestrator import FirmwareOrchestrator as _FirmwareOrchestrator

        return _FirmwareOrchestrator
    raise AttributeError(name)
