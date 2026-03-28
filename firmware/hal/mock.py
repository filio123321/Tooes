"""Deterministic mock sweep source for unit tests — no files, no hardware."""

from __future__ import annotations

import math
from typing import Dict, Iterator, List, Optional

from firmware.hal.types import CellKey, SweepSample


# Three Bulgarian towers to use as synthetic targets.
DEFAULT_CELLS: List[CellKey] = [
    CellKey(mcc=284, mnc=1, lac=1000, ci=101),
    CellKey(mcc=284, mnc=1, lac=1000, ci=102),
    CellKey(mcc=284, mnc=3, lac=3400, ci=201),
]

# Peak azimuths (degrees) for each default cell — the mock RSSI is
# strongest when the antenna points this way.
_PEAK_AZIMUTHS = [45.0, 160.0, 280.0]


def _rssi_for_azimuth(azimuth: float, peak_az: float) -> float:
    """Cosine-shaped RSSI that peaks at *peak_az* and falls off smoothly."""
    delta = abs(azimuth - peak_az) % 360
    if delta > 180:
        delta = 360 - delta
    return -50.0 - 40.0 * (1.0 - math.cos(math.radians(delta)))


class MockSweepSource:
    """Generates a full 360-degree sweep of synthetic SweepSamples.

    Parameters
    ----------
    n_samples:
        Number of evenly-spaced azimuth ticks across 0–360.
    cells:
        Cell keys to include; ``None`` uses ``DEFAULT_CELLS``.
    peak_azimuths:
        Per-cell peak azimuth in degrees (same order as *cells*).
        ``None`` uses ``_PEAK_AZIMUTHS`` (requires len(cells) == 3).
    """

    def __init__(
        self,
        n_samples: int = 36,
        cells: Optional[List[CellKey]] = None,
        peak_azimuths: Optional[List[float]] = None,
    ) -> None:
        self.n_samples = n_samples
        self.cells = cells if cells is not None else DEFAULT_CELLS
        self.peak_azimuths = peak_azimuths if peak_azimuths is not None else _PEAK_AZIMUTHS
        if len(self.cells) != len(self.peak_azimuths):
            raise ValueError("cells and peak_azimuths must have the same length")

    def __iter__(self) -> Iterator[SweepSample]:
        step = 360.0 / self.n_samples
        for i in range(self.n_samples):
            az = i * step
            t = float(i) * 0.5  # half-second ticks
            cells: Dict[CellKey, float] = {
                cell: _rssi_for_azimuth(az, peak)
                for cell, peak in zip(self.cells, self.peak_azimuths)
            }
            yield SweepSample(t=t, azimuth_deg=az, cells=cells)
