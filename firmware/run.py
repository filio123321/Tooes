#!/usr/bin/env python3
"""Tooes - Passive RF Navigator.  Run on the Raspberry Pi."""

import sys
from pathlib import Path

# Ensure repo root is importable
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from firmware.ui.app import main  # noqa: E402

if __name__ == "__main__":
    main()
