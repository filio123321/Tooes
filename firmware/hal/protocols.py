"""Minimal protocols that every HAL backend must satisfy."""

from __future__ import annotations

from typing import Dict, Iterator, Protocol, runtime_checkable

from firmware.hal.types import CellKey, SweepSample


@runtime_checkable
class SweepSampleSource(Protocol):
    """Iterable source of SweepSample — the only thing downstream code needs."""

    def __iter__(self) -> Iterator[SweepSample]: ...


@runtime_checkable
class RotationReader(Protocol):
    """Returns current azimuth in degrees (clockwise from north, 0-360)."""

    def read_azimuth(self) -> float: ...


@runtime_checkable
class CellRssiReader(Protocol):
    """Returns latest cell→RSSI snapshot from the RF path."""

    def read_cells(self) -> Dict[CellKey, float]: ...


@runtime_checkable
class TiltReader(Protocol):
    """Returns current pitch and roll in degrees for tilt compensation."""

    def read_pitch_roll(self) -> tuple[float, float]: ...


@runtime_checkable
class DisplaySink(Protocol):
    """Stub for e-ink or other display output."""

    def show_status(self, text: str) -> None: ...
