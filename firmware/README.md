# firmware/

Firmware-specific reference for the Raspberry Pi application, HAL backends, and
supporting scripts.

For project-wide documentation, start at:

- `README.md`
- `docs/architecture.md`
- `docs/algorithms/motion-estimation.md`
- `docs/algorithms/rf-localization.md`

Python code that runs on the Raspberry Pi (or a laptop during development):
GSM cell observation via `grgsm_scanner` + SoapySDR, OpenCellID tower lookup,
heuristic RF position estimation, and e-ink display output.

## Project structure

```
firmware/
├── hal/                       # Hardware Abstraction Layer
│   ├── __init__.py            # Public API: protocols, types, factory getters
│   ├── types.py               # CellKey, SweepSample dataclasses + JSON (de)serialization
│   ├── protocols.py           # SweepSampleSource, RotationReader, CellRssiReader,
│   │                          #   TiltReader, AccelerationReader, DisplaySink
│   ├── mock.py                # MockSweepSource — deterministic synthetic sweep
│   ├── mock_cells.py          # MockCellRssiReader — position-dependent fake RSSI
│   ├── replay.py              # JsonlReplaySource — replay a recorded .jsonl sweep file
│   ├── grgsm_scanner.py       # Parse grgsm_scanner stdout, GrgsmCellReader adapter
│   ├── factory.py             # Env-driven factories: get_sweep_source(), get_rotation_reader(),
│   │                          #   get_tilt_reader(), get_accel_reader(), get_cell_reader()
│   ├── dead_reckoning.py      # DeadReckoningTracker — ZUPT-based position tracking
│   ├── qmc5883l.py            # QMC5883L magnetometer RotationReader (real hardware, I2C)
│   ├── mpu6050.py             # MPU-6050 TiltReader + AccelerationReader (I2C)
│   └── _stub_rotation.py     # Stubs: StubRotationReader, StubTiltReader, StubAccelerationReader
│
├── tests/
│   ├── fixtures/
│   │   └── golden_sweep.jsonl # 20-sample fixture generated from MockSweepSource
│   └── test_hal.py            # pytest suite for the HAL
│
├── data/
│   └── 284.csv                # OpenCellID tower database for MCC 284 (Bulgaria)
│
├── scripts/
│   ├── orientation_cube.py    # 3D cube visualisation of sensor orientation (Pi desktop)
│   ├── sweep_poc.py           # Walking-sweep POC with dead reckoning + live map/RSSI viz
│   └── install_tiles.py       # Download offline OSM tiles for the e-ink map
│
├── opencellid.py              # lookup_tower(mcc, mnc, lac, cell_id) → (lat, lon)
├── log_config.py              # Shared logging setup
├── run.py                     # Main entry point (WIP)
└── requirements.txt           # Pi runtime dependencies (requests, Pillow, gpiozero, spidev)
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

The `schema_version` field guards against silent format drift between the
recorder and the replay/localization code.

## Component factories

All sensor backends are selected at runtime via environment variables:

### Rotation (`HAL_ROTATION`)

| Value | What it does |
|-------|--------------|
| `stub` (default) | Increments azimuth by a fixed step each call (no hardware) |
| `qmc5883l` | Reads heading from a QMC5883L magnetometer over I2C (GY-271 board) |

### Tilt (`HAL_TILT`)

| Value | What it does |
|-------|--------------|
| `stub` (default) | Always returns pitch=0, roll=0 |
| `mpu6050` | Reads pitch/roll from MPU-6050 accelerometer (I2C) |

### Acceleration (`HAL_ACCEL`)

| Value | What it does |
|-------|--------------|
| `stub` (default) | Returns (0, 0, 1g) — stationary, gravity down |
| `mpu6050` | Reads 3-axis acceleration from MPU-6050 (I2C) |

### Cell reader (`HAL_CELLS`)

| Value | What it does | Extra env vars |
|-------|--------------|----------------|
| `mock` (default) | Position-dependent fake RSSI from 3 virtual towers | — |
| `grgsm` | Runs `grgsm_scanner` subprocess | `HAL_GRGSM_SCANNER_CMD` |

### Trigger distance (`HAL_TRIGGER_DISTANCE`)

Distance in metres between automatic measurements in the sweep POC (default: `2.0`).

### Wiring

**GY-271 (QMC5883L)** — verify at `0x0d` with `i2cdetect -y 1`:

| GY-271 pin | Pi 5 physical pin | Function |
|------------|-------------------|----------|
| VCC | Pin 1 | 3.3V (**not** 5V) |
| GND | Pin 6 | Ground |
| SDA | Pin 3 | GPIO 2 (I2C1 SDA) |
| SCL | Pin 5 | GPIO 3 (I2C1 SCL) |

**MPU-6050** — verify at `0x68` with `i2cdetect -y 1`:

| MPU-6050 pin | Pi 5 physical pin | Function |
|--------------|-------------------|----------|
| VCC | Pin 1 | 3.3V |
| GND | Pin 6 | Ground |
| SDA | Pin 3 | GPIO 2 (I2C1 SDA) |
| SCL | Pin 5 | GPIO 3 (I2C1 SCL) |

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
cat > .env.local <<'EOF'
INITIAL_L=42.012280,23.095261
EOF

HAL_BACKEND=grgsm \
  HAL_ROTATION=qmc5883l \
  HAL_ACCEL=mpu6050 \
  HAL_GRGSM_SCANNER_CMD="grgsm_scanner -b GSM900 -a 'driver=sdrplay'" \
  python3 -m firmware.run
```

The runtime starts from `.env.local` via `INITIAL_L=lat,lon`, tracks short-range
movement with the MPU-6050, and uses SDR fixes as periodic anchor corrections.

### 5. Walking-sweep POC (mock radio + real sensors)

Run on the Pi desktop to visualise dead-reckoning position + RSSI bars.
Press **Space** for a manual measurement, or walk ≥ `HAL_TRIGGER_DISTANCE` metres for an automatic one.

```bash
# All stubs (desktop testing on a laptop without sensors):
python3 firmware/scripts/sweep_poc.py

# Real sensors + mock radio on the Pi:
HAL_ROTATION=qmc5883l HAL_TILT=mpu6050 HAL_ACCEL=mpu6050 \
  HAL_CELLS=mock HAL_TRIGGER_DISTANCE=2.0 \
  python3 firmware/scripts/sweep_poc.py
```

The window is split into a 2D path map (left) and a live RSSI bar chart (right).
Tower triangles show the virtual tower positions used by the mock cell reader.
