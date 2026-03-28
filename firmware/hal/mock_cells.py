"""Position-dependent mock CellRssiReader for POC testing without a radio."""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Tuple

from firmware.hal.types import CellKey

# Virtual tower layout (x, y) in metres from the origin.
TOWER_POSITIONS: Dict[CellKey, Tuple[float, float]] = {
    CellKey(mcc=284, mnc=1, lac=1000, ci=101): (50.0, 30.0),
    CellKey(mcc=284, mnc=1, lac=1000, ci=102): (-40.0, 60.0),
    CellKey(mcc=284, mnc=3, lac=3400, ci=201): (20.0, -50.0),
}


def _path_loss_rssi(distance_m: float) -> float:
    """Simple log-distance path-loss model.  Returns dBm-like value."""
    return -30.0 - 20.0 * math.log10(max(distance_m, 1.0))


class MockCellRssiReader:
    """CellRssiReader that returns RSSI values based on Euclidean distance
    from a set of virtual towers to the current position.

    *position_fn* is a zero-argument callable that returns the current
    ``(x, y)`` in metres — typically bound to a
    :class:`~firmware.hal.dead_reckoning.DeadReckoningTracker`.
    """

    def __init__(
        self,
        position_fn: Callable[[], Tuple[float, float]],
        towers: Dict[CellKey, Tuple[float, float]] | None = None,
    ) -> None:
        self._get_pos = position_fn
        self.towers = towers if towers is not None else dict(TOWER_POSITIONS)

    def read_cells(self) -> Dict[CellKey, float]:
        x, y = self._get_pos()
        cells: Dict[CellKey, float] = {}
        for cell, (tx, ty) in self.towers.items():
            dist = math.sqrt((x - tx) ** 2 + (y - ty) ** 2)
            cells[cell] = _path_loss_rssi(dist)
        return cells
