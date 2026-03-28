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
│   ├── qmc5883l.py         # QMC5883L magnetometer RotationReader (real hardware, I2C)
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

## Rotation reader

The `HAL_ROTATION` env var selects how azimuth is read (used by the `grgsm` backend):

| `HAL_ROTATION` | What it does |
|----------------|--------------|
| `stub` (default) | Increments azimuth by a fixed step each call (no hardware) |
| `qmc5883l` | Reads heading from a QMC5883L magnetometer over I2C (GY-271 board) |

Wiring the GY-271 to the Raspberry Pi 5:

| GY-271 pin | Pi 5 physical pin | Function |
|------------|-------------------|----------|
| VCC | Pin 1 | 3.3V (**not** 5V) |
| GND | Pin 6 | Ground |
| SDA | Pin 3 | GPIO 2 (I2C1 SDA) |
| SCL | Pin 5 | GPIO 3 (I2C1 SCL) |

Verify with `i2cdetect -y 1` — the QMC5883L appears at address `0x0d`.

## Deploying to the Raspberry Pi

The Pi is at `team@tooes.local` (or `team@10.15.86.130` as fallback).

### 1. Clone the repo (one-time)

```bash
ssh team@tooes.local
git clone https://github.com/filio123321/Tooes.git ~/Tooes
```

### 2. Sync the OpenCellID data (not in git)

The `firmware/data/` directory contains large CSV files from OpenCellID and is
excluded from git via `firmware/.gitignore`. Sync it from your laptop:

```bash
# From your laptop, at the repo root:
rsync -avz --progress firmware/data/ team@tooes.local:~/Tooes/firmware/data/
```

Run this again whenever you update the CSV files. The Pi does **not** need
internet access to use the data — it's all local after the sync.

### 3. Pull code updates

```bash
ssh team@tooes.local "cd ~/Tooes && git pull"
```

### 4. Run with real hardware

```bash
ssh team@tooes.local
cd ~/Tooes
HAL_BACKEND=grgsm \
  HAL_ROTATION=qmc5883l \
  HAL_GRGSM_SCANNER_CMD="grgsm_scanner -b GSM900 -a 'driver=sdrplay'" \
  python3 -m firmware.run
```
