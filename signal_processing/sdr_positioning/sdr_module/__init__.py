from __future__ import annotations

import logging
from pathlib import Path

from ..models import Measurement
from .agc import GainController
from .catalogue import CatalogueEntry, CatalogueLoader
from .receiver import SDRReceiver, SDRReceiverProtocol

_log = logging.getLogger(__name__)

# Recommended scan order: FM first (most stations), GSM last (highest frequency jump)
SCAN_ORDER = ["FM", "VOR", "DAB", "DVB-T", "GSM"]


class SDRModule:
    def __init__(
        self,
        catalogue_path: str | Path,
        driver: str = "sdrplay",
        serial: str | None = None,
    ) -> None:
        self._entries = CatalogueLoader().load(catalogue_path)
        self._receiver = SDRReceiver(driver=driver, serial=serial)
        self._agc = GainController()

    def scan(self, types: list[str] | None = None) -> list[Measurement]:
        """Scan all catalogued transmitters in recommended order.

        Returns whatever measurements were collected; never raises.
        Hardware errors on individual entries are logged and skipped.
        """
        allowed = set(types) if types else None
        order = [t for t in SCAN_ORDER if allowed is None or t in allowed]
        results: list[Measurement] = []
        for sig_type in order:
            entries = [e for e in self._entries if e.signal_type == sig_type]
            for entry in entries:
                try:
                    m = self._agc.measure(entry, self._receiver)
                    if m is not None:
                        results.append(m)
                except Exception:
                    _log.warning("Error measuring %s", entry.source_id, exc_info=True)
        return results

    def close(self) -> None:
        self._receiver.close()


__all__ = [
    "SDRModule",
    "CatalogueEntry",
    "CatalogueLoader",
    "SDRReceiver",
    "SDRReceiverProtocol",
    "GainController",
]
