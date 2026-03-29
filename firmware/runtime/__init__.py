"""Runtime orchestration for the firmware app."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from firmware.runtime.orchestrator import FirmwareOrchestrator

__all__ = ["FirmwareOrchestrator"]


def __getattr__(name: str):
    if name == "FirmwareOrchestrator":
        from firmware.runtime.orchestrator import FirmwareOrchestrator as _FirmwareOrchestrator

        return _FirmwareOrchestrator
    raise AttributeError(name)
