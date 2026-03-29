"""Compatibility wrapper for the non-blocking firmware orchestrator."""

from __future__ import annotations

from pathlib import Path

from firmware.runtime.orchestrator import FirmwareOrchestrator


class UiRuntime(FirmwareOrchestrator):
    """Keep the UI-facing runtime API stable while using the queue-backed core."""

    def __init__(self, repo_root: Path) -> None:
        super().__init__(repo_root)
