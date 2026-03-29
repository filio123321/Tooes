"""SDR absolute-fix provider used by the firmware navigation loop."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path


_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SdrFix:
    lat: float
    lon: float
    accuracy_m: float
    n_sources: int


class SdrFixProvider:
    def __init__(
        self,
        driver: str = "sdrplay",
        serial: str | None = None,
        catalogue_path: Path | None = None,
        signal_types: tuple[str, ...] | None = None,
    ) -> None:
        self._driver = driver
        self._serial = serial
        self._catalogue_path = catalogue_path
        self._signal_types = list(signal_types) if signal_types else None
        self._sdr_module = None
        self._trilaterate = None
        self._default_catalogue = None
        self._failed = False

    def _ensure_runtime(self) -> bool:
        if self._failed:
            return False
        if self._sdr_module is not None and self._trilaterate is not None:
            return True

        try:
            from signal_processing.sdr_positioning import DEFAULT_CATALOGUE
            from signal_processing.sdr_positioning.sdr_module import SDRModule
            from signal_processing.sdr_positioning.trilateration import trilaterate
        except Exception as exc:
            _log.warning("SDR runtime unavailable: %s", exc)
            self._failed = True
            return False

        catalogue = self._catalogue_path or Path(DEFAULT_CATALOGUE)
        self._default_catalogue = catalogue
        try:
            self._sdr_module = SDRModule(
                catalogue_path=catalogue,
                driver=self._driver,
                serial=self._serial,
            )
            self._trilaterate = trilaterate
            return True
        except Exception as exc:
            _log.warning("Failed to initialize SDR module: %s", exc)
            self._failed = True
            return False

    def scan_once(
        self,
        *,
        origin: tuple[float, float] | None = None,
    ) -> SdrFix | None:
        if not self._ensure_runtime():
            return None

        assert self._sdr_module is not None
        assert self._trilaterate is not None

        measurements = self._sdr_module.scan(types=self._signal_types)
        result = self._trilaterate(measurements, origin=origin)
        if result is None:
            return None

        lat, lon, accuracy_m = result
        return SdrFix(
            lat=lat,
            lon=lon,
            accuracy_m=accuracy_m,
            n_sources=len(measurements),
        )

    def close(self) -> None:
        if self._sdr_module is None:
            return
        try:
            self._sdr_module.close()
        finally:
            self._sdr_module = None
