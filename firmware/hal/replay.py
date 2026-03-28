"""Replay a JSONL file of SweepSamples — for integration tests and RPi without SDR."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator, Union

from firmware.hal.types import SweepSample

_logger = logging.getLogger(__name__)


def JsonlReplaySource(path: Union[str, Path]) -> Iterator[SweepSample]:
    """Yield one SweepSample per line from a JSONL file.

    Parameters
    ----------
    path:
        Path to a ``.jsonl`` file where each line is a JSON-serialised
        ``SweepSample`` (as produced by ``SweepSample.to_json()``).

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    """
    resolved = Path(path)
    if not resolved.exists():
        raise FileNotFoundError(f"Replay file not found: {resolved}")
    _logger.info("Replaying sweep from %s", resolved)
    with open(resolved, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                yield SweepSample.from_json(stripped)
            except Exception:
                _logger.warning("Skipping malformed line %d in %s",
                                lineno, resolved)
