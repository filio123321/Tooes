# Tooes

Tooes is a Raspberry Pi-based passive RF navigation prototype. The current
firmware listens to broadcast cellular information, resolves observed towers
against a local OpenCellID database, and renders results on a small e-paper
display. The repo also contains a walking proof-of-concept that combines
motion sensors with mock or real cell reads.

## What The Repo Does Today

- The main e-paper app performs a directional GSM scan, resolves towers from
  local CSV data, and estimates position with the heuristic currently
  implemented in code.
- The walking sweep POC performs 2D dead reckoning from accelerometer data and
  magnetometer heading. It does not yet use the MPU-6050 gyroscope.
- The radio path currently documents and implements the GSM `grgsm_scanner`
  flow. LTE protocol-specific work can be added later without changing the
  top-level documentation structure.

## Documentation

- [System architecture](docs/architecture.md)
- [Hardware and wiring](docs/hardware.md)
- [Motion estimation math](docs/algorithms/motion-estimation.md)
- [RF localization pipeline](docs/algorithms/rf-localization.md)
- [Raspberry Pi setup and operations](docs/operations/pi-setup.md)
- [Experiments and proof-of-concepts](docs/experiments.md)
- [Firmware-specific reference](firmware/README.md)

## Repository Layout

- `firmware/`: Raspberry Pi firmware, HAL backends, UI code, scripts, and tests.
- `scripts/`: repo-level utilities such as Pi setup.
- `external/waveshare-epd/`: Waveshare e-paper driver submodule used by the
  real display path.
- `assets/`: 3D-printable hardware assets.
- `separate_component_files/`: standalone experiments and component-specific
  utilities.

## Quick Start

### 1. Run the test suite

```bash
python -m pytest firmware/tests/ -v
```

### 2. Exercise the mock or replay RF path without Pi hardware

```bash
HAL_BACKEND=replay HAL_REPLAY_PATH=firmware/tests/fixtures/golden_sweep.jsonl \
python -c "from firmware.hal import get_sweep_source; print(list(get_sweep_source())[:2])"
```

### 3. Run on the Raspberry Pi with real hardware

```bash
git submodule update --init --recursive

cat > .env.local <<'EOF'
INITIAL_L=42.012280,23.095261
EOF

HAL_BACKEND=grgsm \
HAL_ROTATION=qmc5883l \
HAL_ACCEL=mpu6050 \
HAL_GRGSM_SCANNER_CMD="grgsm_scanner -b GSM900 -a 'driver=sdrplay'" \
python3 -m firmware.run
```

`INITIAL_L` is the absolute starting point used by the firmware navigation
runtime. The app then uses IMU-based relative movement locally and periodically
blends in SDR fixes as it moves away from the last anchor.

## Required Runtime Assets

- `external/waveshare-epd/` must be populated with
  `git submodule update --init --recursive` before the e-paper display path can
  import the Waveshare driver.
- `firmware/data/<mcc>.csv` must exist locally for any country code you want to
  resolve through OpenCellID. These files are intentionally not committed.
- `firmware/offline_tiles/` is optional but required for the map view to render
  real offline tiles instead of placeholders.

## Important Notes

- The absolute position estimate in the main app is not true trilateration.
  The current implementation is a heuristic RSSI-weighted centroid over the
  resolved towers.
- The motion stack is currently accelerometer plus magnetometer, not
  accelerometer plus gyroscope fusion.
- The repo contains both product code and experiments. See
  [docs/experiments.md](docs/experiments.md) for the current boundary.
