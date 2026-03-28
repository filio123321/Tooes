"""Parse grgsm_scanner stdout and optionally run it as a subprocess.

grgsm_scanner (backed by SoapySDR) emits lines like:

    ARFCN:  512, Freq: 1930.2M, CID:     0, LAC: 10454, MCC: 310, MNC:  41, Pwr: -58

This module provides:
    parse_scanner_line()  — single-line parser → (CellKey, rssi_dbm) or None
    run_scanner()         — launch the process and yield parsed snapshots
    GrgsmScannerSource    — full SweepSampleSource combining scanner + RotationReader
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from typing import Dict, Iterator, List, Optional

from firmware.hal.types import CellKey, SweepSample
from firmware.hal.protocols import RotationReader

_logger = logging.getLogger(__name__)

# Regex for a single scanner output line.
# Fields may have variable whitespace; Pwr is a signed integer.
_LINE_RE = re.compile(
    r"ARFCN:\s*(?P<arfcn>\d+),\s*"
    r"Freq:\s*[\d.]+M,\s*"
    r"CID:\s*(?P<cid>\d+),\s*"
    r"LAC:\s*(?P<lac>\d+),\s*"
    r"MCC:\s*(?P<mcc>\d+),\s*"
    r"MNC:\s*(?P<mnc>\d+),\s*"
    r"Pwr:\s*(?P<pwr>-?\d+)"
)


def parse_scanner_line(line: str) -> Optional[tuple[CellKey, float]]:
    """Parse one grgsm_scanner stdout line into (CellKey, rssi_dbm).

    Returns ``None`` for blank, header, or malformed lines.
    """
    m = _LINE_RE.search(line)
    if m is None:
        return None
    key = CellKey(
        mcc=int(m["mcc"]),
        mnc=int(m["mnc"]),
        lac=int(m["lac"]),
        ci=int(m["cid"]),
    )
    return key, float(m["pwr"])


def parse_scanner_output(text: str) -> Dict[CellKey, float]:
    """Parse a full (multi-line) grgsm_scanner run into a cell→RSSI map."""
    cells: Dict[CellKey, float] = {}
    for line in text.splitlines():
        parsed = parse_scanner_line(line)
        if parsed is not None:
            key, rssi = parsed
            cells[key] = rssi
    return cells


def run_scanner(cmd: str, timeout: float = 60.0) -> Dict[CellKey, float]:
    """Execute *cmd* (grgsm_scanner shell command) and return parsed cells.

    The command is run with ``shell=True`` so it can contain Soapy device
    args, band flags, etc. exactly as tested on the CLI.
    """
    _logger.info("Running: %s", cmd)
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        _logger.warning("grgsm_scanner exited %d: %s",
                        result.returncode, result.stderr.strip())
    return parse_scanner_output(result.stdout)


class GrgsmCellReader:
    """CellRssiReader adapter that runs grgsm_scanner once per ``read_cells`` call."""

    def __init__(self, cmd: str, timeout: float = 60.0) -> None:
        self.cmd = cmd
        self.timeout = timeout

    def read_cells(self) -> Dict[CellKey, float]:
        return run_scanner(self.cmd, self.timeout)


class GrgsmScannerSource:
    """SweepSampleSource that repeatedly invokes grgsm_scanner during a sweep.

    Each iteration runs the scanner once, reads the current azimuth from
    *rotation*, and yields one ``SweepSample`` combining both.

    Parameters
    ----------
    cmd:
        Full shell command, e.g.
        ``"grgsm_scanner -b GSM900 -a 'driver=sdrplay'"``
    rotation:
        Anything satisfying ``RotationReader``.
    n_scans:
        How many scanner invocations to perform (one per yield).
    scan_timeout:
        Per-invocation timeout in seconds.
    """

    def __init__(
        self,
        cmd: str,
        rotation: RotationReader,
        n_scans: int = 36,
        scan_timeout: float = 60.0,
    ) -> None:
        self.cmd = cmd
        self.rotation = rotation
        self.n_scans = n_scans
        self.scan_timeout = scan_timeout

    def __iter__(self) -> Iterator[SweepSample]:
        t0 = time.monotonic()
        for _ in range(self.n_scans):
            azimuth = self.rotation.read_azimuth()
            cells = run_scanner(self.cmd, timeout=self.scan_timeout)
            t = time.monotonic() - t0
            yield SweepSample(t=t, azimuth_deg=azimuth, cells=cells)
