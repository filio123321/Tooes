"""Canonical data types shared by every HAL backend and all downstream code."""

from __future__ import annotations

import dataclasses
import json
from typing import Dict, Any, List

SCHEMA_VERSION = 1


@dataclasses.dataclass(frozen=True)
class CellKey:
    """Immutable GSM cell identifier — matches opencellid.lookup_tower args."""

    mcc: int
    mnc: int
    lac: int
    ci: int

    def to_tuple(self) -> tuple:
        return (self.mcc, self.mnc, self.lac, self.ci)


@dataclasses.dataclass
class SweepSample:
    """One observation tick: current azimuth + all cells heard at that instant."""

    t: float
    azimuth_deg: float
    cells: Dict[CellKey, float]

    # -- serialization helpers ------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "t": self.t,
            "azimuth_deg": self.azimuth_deg,
            "cells": [
                {
                    "mcc": k.mcc,
                    "mnc": k.mnc,
                    "lac": k.lac,
                    "ci": k.ci,
                    "rssi_dbm": v,
                }
                for k, v in self.cells.items()
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> SweepSample:
        version = d.get("schema_version", 1)
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported schema_version {version} (expected {SCHEMA_VERSION})"
            )
        cells: Dict[CellKey, float] = {}
        for c in d["cells"]:
            key = CellKey(mcc=int(c["mcc"]), mnc=int(c["mnc"]),
                          lac=int(c["lac"]), ci=int(c["ci"]))
            cells[key] = float(c["rssi_dbm"])
        return cls(t=float(d["t"]), azimuth_deg=float(d["azimuth_deg"]),
                   cells=cells)

    @classmethod
    def from_json(cls, line: str) -> SweepSample:
        return cls.from_dict(json.loads(line))
