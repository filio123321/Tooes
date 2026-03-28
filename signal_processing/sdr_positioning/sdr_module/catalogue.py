from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple


class _TypeDefault(NamedTuple):
    antenna_gain_dbi: float
    min_rssi_dbm: float


TYPE_DEFAULTS: dict[str, _TypeDefault] = {
    "FM":    _TypeDefault(antenna_gain_dbi=0.0, min_rssi_dbm=-90.0),
    "VOR":   _TypeDefault(antenna_gain_dbi=0.0, min_rssi_dbm=-90.0),
    "DAB":   _TypeDefault(antenna_gain_dbi=0.0, min_rssi_dbm=-95.0),
    "DVB-T": _TypeDefault(antenna_gain_dbi=6.0, min_rssi_dbm=-95.0),
    "GSM":   _TypeDefault(antenna_gain_dbi=9.0, min_rssi_dbm=-95.0),
}


@dataclass(frozen=True)
class CatalogueEntry:
    source_id: str
    freq_hz: float
    freq_mhz: float
    signal_type: str
    lat: float
    lon: float
    power_w: float
    antenna_gain_dbi: float
    min_rssi_dbm: float
    station: str
    name: str


class CatalogueLoader:
    def load(self, path: str | Path) -> list[CatalogueEntry]:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Catalogue not found: {path}")
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        entries: list[CatalogueEntry] = []
        for key, entry in data.items():
            sig_type = entry.get("type", "")
            if sig_type not in TYPE_DEFAULTS:
                # Unknown signal type — skip gracefully
                continue
            defaults = TYPE_DEFAULTS[sig_type]
            # Keys may have a suffix to disambiguate co-channel transmitters (e.g. "538_bor")
            freq_mhz = float(key.split("_")[0])
            freq_hz = freq_mhz * 1e6
            station = entry.get("station", "")
            source_id = f"{sig_type}_{freq_mhz}_{station}"
            entries.append(CatalogueEntry(
                source_id=source_id,
                freq_hz=freq_hz,
                freq_mhz=freq_mhz,
                signal_type=sig_type,
                lat=float(entry["lat"]),
                lon=float(entry["lon"]),
                power_w=float(entry["power_w"]),
                antenna_gain_dbi=float(entry.get("antenna_gain_dbi", defaults.antenna_gain_dbi)),
                min_rssi_dbm=defaults.min_rssi_dbm,
                station=station,
                name=entry.get("name", ""),
            ))
        return entries
