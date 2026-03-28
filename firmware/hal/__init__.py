"""Hardware Abstraction Layer for GSM cell-mapping rig.

Public surface:
    CellKey, SweepSample   — data contracts
    SweepSampleSource      — protocol any source must satisfy
    get_sweep_source()     — env-driven factory (sweep backends)
    get_rotation_reader()  — env-driven factory (rotation sensor)
    get_tilt_reader()      — env-driven factory (tilt sensor)
    get_accel_reader()     — env-driven factory (accelerometer)
    get_cell_reader()      — env-driven factory (cell RSSI reader)
"""

from firmware.hal.types import CellKey, SweepSample, SCHEMA_VERSION
from firmware.hal.protocols import (
    AccelerationReader,
    CellRssiReader,
    RotationReader,
    SweepSampleSource,
    TiltReader,
)
from firmware.hal.factory import (
    get_accel_reader,
    get_cell_reader,
    get_rotation_reader,
    get_sweep_source,
    get_tilt_reader,
)

__all__ = [
    "CellKey",
    "SweepSample",
    "SCHEMA_VERSION",
    "AccelerationReader",
    "CellRssiReader",
    "RotationReader",
    "SweepSampleSource",
    "TiltReader",
    "get_accel_reader",
    "get_cell_reader",
    "get_rotation_reader",
    "get_sweep_source",
    "get_tilt_reader",
]
