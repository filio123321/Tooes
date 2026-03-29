"""Persistent path logger for fused navigation points."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path


class PathLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()

    def log_point(
        self,
        *,
        lat: float,
        lon: float,
        relative_x_m: float,
        relative_y_m: float,
        source: str,
        distance_since_anchor_m: float,
        sdr_accuracy_m: float | None,
    ) -> None:
        payload = {
            "timestamp_unix_s": round(time.time(), 6),
            "timestamp_iso": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "lat": round(lat, 7),
            "lon": round(lon, 7),
            "relative_x_m": round(relative_x_m, 3),
            "relative_y_m": round(relative_y_m, 3),
            "distance_since_anchor_m": round(distance_since_anchor_m, 3),
            "source": source,
            "sdr_accuracy_m": None if sdr_accuracy_m is None else round(sdr_accuracy_m, 3),
        }
        with self._lock:
            self._handle.write(json.dumps(payload) + "\n")

    def close(self) -> None:
        with self._lock:
            self._handle.close()
