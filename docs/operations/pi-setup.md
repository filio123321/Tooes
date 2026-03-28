# Raspberry Pi Setup And Operations

This guide covers the operational setup needed to run the current repo on a
Raspberry Pi. It stays close to what the repo already automates and calls out
the steps that are still manual.

## What This Setup Covers

- cloning or syncing the repo to the Pi
- populating the Waveshare display submodule
- enabling I2C
- verifying the magnetometer and accelerometer
- installing Python runtime dependencies
- syncing local tower data
- running the main app or the walking POC

## What This Setup Does Not Fully Automate Yet

- installing `grgsm_scanner`
- installing GNU Radio or SoapySDR driver chains
- provisioning SDR hardware drivers
- downloading OpenCellID CSV files
- downloading offline map tiles

Those are all real operational dependencies even though they sit outside the
Python code.

## 1. Prepare The Checkout

On a fresh checkout, populate the Waveshare submodule:

```bash
git submodule update --init --recursive
```

Without this, the main e-paper application cannot import the display driver.

## 2. Sync Or Clone The Repo To The Pi

The repo already contains a helper script:

```bash
PI_PASS=<password> bash scripts/setup_pi.sh [user@host]
```

Today that script:

- rsyncs the repo
- rsyncs `firmware/data/` if it exists locally
- enables I2C
- installs `i2c-tools`
- installs `smbus2`
- runs `i2cdetect -y 1`

Default target:

- `team@tooes.local`

The script is useful, but it is not the entire runtime bootstrap.

## 3. Install Python Dependencies

The Pi runtime dependencies live in `firmware/requirements.txt`.

Recommended install command:

```bash
pip3 install --break-system-packages -r firmware/requirements.txt
```

Notes:

- `scripts/setup_pi.sh` currently installs only `smbus2`
- some dependencies such as `RPi.GPIO` and `spidev` are Pi-specific and should
  be installed on the Pi, not necessarily on a laptop development machine

## 4. Enable And Verify I2C

If you do not use the helper script, you can enable I2C manually:

```bash
sudo raspi-config nonint do_i2c 0
```

Then verify the bus:

```bash
i2cdetect -y 1
```

Expected sensor addresses:

- `0x0d` for the QMC5883L magnetometer
- `0x68` for the MPU-6050 accelerometer

If those addresses do not appear, debug wiring before debugging the software.

## 5. Provide OpenCellID Data

The tower lookup path requires local CSV files in:

- `firmware/data/`

The lookup code expects one file per MCC, for example:

- `firmware/data/284.csv`

If the file is missing, the current code exits the process during lookup.

This means:

- the data is not optional for the live tower-resolution path
- a successful code deployment can still fail at runtime if the CSVs were not
  synced

## 6. Download Offline Tiles If You Need The Map View

The map view reads from:

- `firmware/offline_tiles/`

To populate it:

```bash
python3 firmware/scripts/install_tiles.py
```

Important note:

- the script uses a tile URL template that should only be used with a provider
  whose terms allow offline or bulk download

If tiles are missing, the app falls back to placeholder tiles rather than
crashing.

## 7. Run The Main E-Paper Application

Typical real-hardware invocation:

```bash
HAL_BACKEND=grgsm \
HAL_ROTATION=qmc5883l \
HAL_GRGSM_SCANNER_CMD="grgsm_scanner -b GSM900 -a 'driver=sdrplay'" \
python3 -m firmware.run
```

What this requires:

- populated Waveshare display submodule
- GPIO access
- working I2C sensors
- working `grgsm_scanner` command
- OpenCellID CSV data

## 8. Run The Walking Sweep POC

All stubs on a desktop machine:

```bash
python3 firmware/scripts/sweep_poc.py
```

Real sensors plus mock radio on the Pi:

```bash
HAL_ROTATION=qmc5883l HAL_TILT=mpu6050 HAL_ACCEL=mpu6050 \
HAL_CELLS=mock HAL_TRIGGER_DISTANCE=2.0 \
python3 firmware/scripts/sweep_poc.py
```

This POC:

- tracks relative motion in local meters
- triggers cell reads after walking a configured distance
- is a visualization and experimentation tool, not the production e-paper flow

## 9. Radio Prerequisites

The repo assumes the scanner command itself is already working.

That usually means:

- SDR device connected and recognized
- appropriate driver installed
- SoapySDR configured
- `grgsm_scanner` invocations tested directly in a shell before wiring them into
  `HAL_GRGSM_SCANNER_CMD`

Recommended operational approach:

1. test the scanner command manually on the Pi
2. only then pass the exact working command into the environment variable
3. only then test the Python app

## 10. Troubleshooting

### Display import fails

Likely cause:

- the Waveshare submodule is missing

Fix:

```bash
git submodule update --init --recursive
```

### `lookup_tower()` exits with a CSV error

Likely cause:

- `firmware/data/<mcc>.csv` is missing on the device

Fix:

- sync the required OpenCellID CSV files to the Pi

### Map renders blank placeholders

Likely cause:

- offline tiles are not downloaded

Fix:

```bash
python3 firmware/scripts/install_tiles.py
```

### I2C sensor not detected

Likely causes:

- wiring mistake
- I2C not enabled
- wrong voltage
- wrong address assumption

Fix:

- re-run `i2cdetect -y 1`
- verify the wiring tables in `docs/hardware.md`

### `grgsm_scanner` path fails

Likely causes:

- command not installed
- SDR driver not configured
- SoapySDR device string incorrect
- timeout too aggressive for the current hardware

Fix:

- validate the exact command in the shell first
- then pass the tested command to `HAL_GRGSM_SCANNER_CMD`
