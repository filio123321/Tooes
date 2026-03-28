"""Environment-driven factory for selecting HAL backends.

Environment variables
---------------------
HAL_BACKEND            mock | replay | grgsm   (default: mock)
HAL_REPLAY_PATH        Path to .jsonl file     (required when backend=replay)
HAL_GRGSM_SCANNER_CMD  Full shell command      (required when backend=grgsm)
HAL_GRGSM_N_SCANS      Number of scan invocations per sweep (default: 36)
HAL_ROTATION           stub | qmc5883l         (default: stub)
HAL_TILT               stub | mpu6050          (default: stub)
HAL_ACCEL              stub | mpu6050          (default: stub)
HAL_CELLS              mock | grgsm            (default: mock)
HAL_TRIGGER_DISTANCE   Metres between measurements (default: 2.0, used by sweep_poc)
"""

from __future__ import annotations

import os
from typing import Callable, Optional, Tuple

from firmware.hal.types import SweepSample
from firmware.hal.protocols import (
    AccelerationReader,
    CellRssiReader,
    RotationReader,
    SweepSampleSource,
    TiltReader,
)


# ---------------------------------------------------------------------------
# Sweep-source factory (existing)
# ---------------------------------------------------------------------------

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
        rotation = get_rotation_reader()
        return GrgsmScannerSource(cmd=cmd, rotation=rotation, n_scans=n_scans)

    raise ValueError(f"Unknown HAL_BACKEND: {backend!r} (expected mock|replay|grgsm)")


# ---------------------------------------------------------------------------
# Component factories
# ---------------------------------------------------------------------------

def get_tilt_reader() -> TiltReader:
    """Return a TiltReader based on ``HAL_TILT`` env var."""
    kind = os.environ.get("HAL_TILT", "stub").lower()
    if kind == "stub":
        from firmware.hal._stub_rotation import StubTiltReader
        return StubTiltReader()
    if kind == "mpu6050":
        from firmware.hal.mpu6050 import MPU6050TiltReader
        return MPU6050TiltReader()
    raise ValueError(f"Unknown HAL_TILT: {kind!r} (expected stub|mpu6050)")


def get_rotation_reader() -> RotationReader:
    """Return a RotationReader based on ``HAL_ROTATION`` env var."""
    kind = os.environ.get("HAL_ROTATION", "stub").lower()
    if kind == "stub":
        from firmware.hal._stub_rotation import StubRotationReader
        return StubRotationReader()
    if kind == "qmc5883l":
        from firmware.hal.qmc5883l import QMC5883LRotationReader
        tilt = get_tilt_reader()
        return QMC5883LRotationReader(tilt=tilt)
    raise ValueError(f"Unknown HAL_ROTATION: {kind!r} (expected stub|qmc5883l)")


def get_accel_reader() -> AccelerationReader:
    """Return an AccelerationReader based on ``HAL_ACCEL`` env var."""
    kind = os.environ.get("HAL_ACCEL", "stub").lower()
    if kind == "stub":
        from firmware.hal._stub_rotation import StubAccelerationReader
        return StubAccelerationReader()
    if kind == "mpu6050":
        from firmware.hal.mpu6050 import MPU6050TiltReader
        return MPU6050TiltReader()
    raise ValueError(f"Unknown HAL_ACCEL: {kind!r} (expected stub|mpu6050)")


def get_cell_reader(
    position_fn: Optional[Callable[[], Tuple[float, float]]] = None,
) -> CellRssiReader:
    """Return a CellRssiReader based on ``HAL_CELLS`` env var.

    *position_fn* is only used when ``HAL_CELLS=mock`` — it tells the mock
    reader where the device currently is so RSSI can vary with distance.
    """
    kind = os.environ.get("HAL_CELLS", "mock").lower()
    if kind == "mock":
        from firmware.hal.mock_cells import MockCellRssiReader
        return MockCellRssiReader(
            position_fn=position_fn or (lambda: (0.0, 0.0)),
        )
    if kind == "grgsm":
        cmd = os.environ.get("HAL_GRGSM_SCANNER_CMD")
        if not cmd:
            raise RuntimeError(
                "HAL_CELLS=grgsm requires HAL_GRGSM_SCANNER_CMD"
            )
        from firmware.hal.grgsm_scanner import GrgsmCellReader
        return GrgsmCellReader(cmd=cmd)
    raise ValueError(f"Unknown HAL_CELLS: {kind!r} (expected mock|grgsm)")
