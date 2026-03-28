"""Environment-driven factory for selecting a SweepSampleSource backend.

Environment variables
---------------------
HAL_BACKEND            mock | replay | grgsm   (default: mock)
HAL_REPLAY_PATH        Path to .jsonl file     (required when backend=replay)
HAL_GRGSM_SCANNER_CMD  Full shell command      (required when backend=grgsm)
HAL_GRGSM_N_SCANS     Number of scan invocations per sweep (default: 36)
HAL_ROTATION           stub | qmc5883l         (default: stub; used by grgsm backend)
"""

from __future__ import annotations

import os
from typing import Iterator

from firmware.hal.types import SweepSample
from firmware.hal.protocols import SweepSampleSource


def get_sweep_source() -> SweepSampleSource:
    """Return a ``SweepSampleSource`` based on ``HAL_BACKEND`` env var."""
    backend = os.environ.get("HAL_BACKEND", "mock").lower()

    if backend == "mock":
        from firmware.hal.mock import MockSweepSource
        return MockSweepSource()

    if backend == "replay":
        path = os.environ.get("HAL_REPLAY_PATH")
        if not path:
            raise RuntimeError("HAL_BACKEND=replay requires HAL_REPLAY_PATH")
        from firmware.hal.replay import JsonlReplaySource
        return JsonlReplaySource(path)

    if backend == "grgsm":
        cmd = os.environ.get("HAL_GRGSM_SCANNER_CMD")
        if not cmd:
            raise RuntimeError(
                "HAL_BACKEND=grgsm requires HAL_GRGSM_SCANNER_CMD "
                "(full shell command, e.g. \"grgsm_scanner -b GSM900 -a 'driver=sdrplay'\")"
            )
        n_scans = int(os.environ.get("HAL_GRGSM_N_SCANS", "36"))

        from firmware.hal.grgsm_scanner import GrgsmScannerSource
        rotation = _get_rotation_reader()
        return GrgsmScannerSource(cmd=cmd, rotation=rotation, n_scans=n_scans)

    raise ValueError(f"Unknown HAL_BACKEND: {backend!r} (expected mock|replay|grgsm)")


def _get_rotation_reader():
    """Return a RotationReader based on ``HAL_ROTATION`` env var."""
    kind = os.environ.get("HAL_ROTATION", "stub").lower()
    if kind == "stub":
        from firmware.hal._stub_rotation import StubRotationReader
        return StubRotationReader()
    if kind == "qmc5883l":
        from firmware.hal.qmc5883l import QMC5883LRotationReader
        return QMC5883LRotationReader()
    raise ValueError(f"Unknown HAL_ROTATION: {kind!r} (expected stub|qmc5883l)")
