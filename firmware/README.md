# firmware/

Python code that runs on the Raspberry Pi (or a laptop during development): GSM cell observation via `grgsm_scanner` + SoapySDR, OpenCellID tower lookup, RSSI-based trilateration, and e-ink display output.

## Project structure

```
firmware/
├── hal/                    # Hardware Abstraction Layer
│   ├── __init__.py         # Public API: CellKey, SweepSample, get_sweep_source()
│   ├── types.py            # CellKey, SweepSample dataclasses + JSON (de)serialization
│   ├── protocols.py        # SweepSampleSource, RotationReader, CellRssiReader, DisplaySink
│   ├── mock.py             # MockSweepSource — deterministic synthetic sweep for tests
│   ├── replay.py           # JsonlReplaySource — replay a recorded .jsonl sweep file
│   ├── grgsm_scanner.py    # Parse grgsm_scanner stdout, run it as subprocess
│   ├── factory.py          # get_sweep_source() — env-driven backend selection
│   └── _stub_rotation.py   # Placeholder RotationReader (increments azimuth each call)
│
├── tests/
│   ├── fixtures/
│   │   └── golden_sweep.jsonl   # 20-sample fixture generated from MockSweepSource
│   └── test_hal.py              # pytest suite for the HAL
│
├── data/
│   └── 284.csv             # OpenCellID tower database for MCC 284 (Bulgaria)
│
├── scripts/
│   └── install_tiles.py    # Download offline OSM tiles for the e-ink map
│
├── opencellid.py           # lookup_tower(mcc, mnc, lac, cell_id) → (lat, lon)
├── log_config.py           # Shared logging setup
├── run.py                  # Main entry point (WIP)
└── requirements.txt        # Pi runtime dependencies (requests, Pillow, gpiozero, spidev)
```

## Running tests

Tests use **pytest** and have no hardware dependencies.

```bash
# From the repo root:
python -m pytest firmware/tests/ -v
```

pytest is configured in the top-level `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["firmware/tests"]
```

Install the dev dependency if needed:

```bash
pip install pytest
```

## HAL backends

The HAL is selected at runtime via the `HAL_BACKEND` environment variable:

| `HAL_BACKEND` | What it does | Extra env vars |
|---------------|-------------|----------------|
| `mock` (default) | Deterministic synthetic 360-degree sweep | — |
| `replay` | Replays a recorded JSONL file | `HAL_REPLAY_PATH` — path to `.jsonl` file |
| `grgsm` | Runs `grgsm_scanner` (SoapySDR) as a subprocess | `HAL_GRGSM_SCANNER_CMD` — full shell command, e.g. `grgsm_scanner -b GSM900 -a 'driver=sdrplay'` |

Example — replay a previously recorded sweep:

```bash
HAL_BACKEND=replay HAL_REPLAY_PATH=firmware/tests/fixtures/golden_sweep.jsonl \
  python -c "from firmware.hal import get_sweep_source; print(list(get_sweep_source()))"
```

## JSONL format

Each line in a `.jsonl` sweep file is a self-contained JSON object:

```json
{"schema_version": 1, "t": 0.0, "azimuth_deg": 0.0, "cells": [{"mcc": 284, "mnc": 1, "lac": 1000, "ci": 101, "rssi_dbm": -50.0}]}
```

The `schema_version` field guards against silent format drift between the recorder and the replay/trilateration code.
