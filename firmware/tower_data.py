"""Helpers for reading local OpenCellID tower data from CSV exports."""

from __future__ import annotations

import csv
import dataclasses
from pathlib import Path
from typing import Iterable, Iterator, Optional


RAW_TOWER_HEADERS = [
    "radio",
    "mcc",
    "net",
    "area",
    "cell",
    "unit",
    "lon",
    "lat",
    "range",
    "samples",
    "changeable",
    "created",
    "updated",
    "averageSignal",
]


@dataclasses.dataclass(frozen=True)
class CatalogTower:
    """Tower entry used for map rendering and local lookup."""

    radio: str
    mcc: int
    net: int
    area: int
    cell: int
    lat: float
    lon: float


def default_data_dir() -> Path:
    return Path(__file__).resolve().parent / "data"


def _iter_row_dicts(csv_path: Path) -> Iterator[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        first_row = next(reader, None)
        if first_row is None:
            return

        stripped = [value.strip() for value in first_row]
        if stripped == RAW_TOWER_HEADERS:
            yield dict(zip(RAW_TOWER_HEADERS, first_row))
        elif stripped and stripped[0].lower() == "mcc":
            headers = stripped
            for row in reader:
                if len(row) != len(headers):
                    continue
                yield dict(zip(headers, row))
            return
        elif len(first_row) == len(RAW_TOWER_HEADERS):
            yield dict(zip(RAW_TOWER_HEADERS, first_row))

        for row in reader:
            if len(row) != len(RAW_TOWER_HEADERS):
                continue
            yield dict(zip(RAW_TOWER_HEADERS, row))


def iter_catalog_towers(csv_path: Path) -> Iterator[CatalogTower]:
    for row in _iter_row_dicts(csv_path):
        try:
            yield CatalogTower(
                radio=str(row.get("radio", "OTHER") or "OTHER").upper(),
                mcc=int(row["mcc"]),
                net=int(row["net"]),
                area=int(row["area"]),
                cell=int(row["cell"]),
                lat=float(row["lat"]),
                lon=float(row["lon"]),
            )
        except (KeyError, TypeError, ValueError):
            continue


def load_catalog_towers(csv_path: Optional[Path] = None) -> list[CatalogTower]:
    target = csv_path or default_data_dir() / "284.csv"
    if not target.exists():
        return []
    return list(iter_catalog_towers(target))
