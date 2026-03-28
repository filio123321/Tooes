"""Hardware Abstraction Layer for GSM cell-mapping rig.

Public surface:
    CellKey, SweepSample   — data contracts
    SweepSampleSource      — protocol any source must satisfy
    get_sweep_source()     — env-driven factory
"""

from firmware.hal.types import CellKey, SweepSample, SCHEMA_VERSION
from firmware.hal.protocols import SweepSampleSource, RotationReader, CellRssiReader, TiltReader
from firmware.hal.factory import get_sweep_source

__all__ = [
    "CellKey",
    "SweepSample",
    "SCHEMA_VERSION",
    "SweepSampleSource",
    "RotationReader",
    "CellRssiReader",
    "TiltReader",
    "get_sweep_source",
]
