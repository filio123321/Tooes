from __future__ import annotations

from pathlib import Path

import firmware.opencellid as opencellid


def test_lookup_tower_missing_database_returns_none(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(opencellid, "__file__", str(tmp_path / "opencellid.py"))
    opencellid._warned_missing_csvs.clear()

    assert opencellid.lookup_tower(284, 3, 3400, 15023) is None
