"""Location trace storage for map rendering."""

from __future__ import annotations

from dataclasses import dataclass

from firmware.navigation.geo import haversine_m


@dataclass(frozen=True)
class TracePoint:
    lat: float
    lon: float
    source: str


class TraceHistory:
    def __init__(self, max_points: int = 256) -> None:
        self._max_points = max(max_points, 1)
        self._points: list[TracePoint] = []

    def append(self, lat: float, lon: float, source: str) -> bool:
        point = TracePoint(lat=lat, lon=lon, source=source)
        if self._points and self._points[-1] == point:
            return False
        self._points.append(point)
        if len(self._points) > self._max_points:
            self._points = self._points[-self._max_points :]
        return True

    def append_if_far_enough(
        self,
        lat: float,
        lon: float,
        min_distance_m: float,
        source: str,
    ) -> bool:
        if not self._points:
            return self.append(lat, lon, source)

        last = self._points[-1]
        if haversine_m(last.lat, last.lon, lat, lon) < min_distance_m:
            return False
        return self.append(lat, lon, source)

    def as_tuples(self) -> list[tuple[float, float]]:
        return [(point.lat, point.lon) for point in self._points]

    def __len__(self) -> int:
        return len(self._points)
