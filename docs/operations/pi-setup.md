# Raspberry Pi Setup And Operations

This guide covers the operational setup needed to run the current repo on a
Raspberry Pi. It stays close to what the repo already automates and calls out
the steps that are still manual.

## What This Setup Covers

- syncing the repo to the Pi (rsync + git pseudoclone)
- populating the Waveshare display submodule
- enabling I2C and verifying sensors
- installing Python runtime dependencies
- creating the required `.env.local` configuration
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

The repo contains a helper script that rsyncs the code, initialises a git repo
on the Pi, sets the GitHub remote, and configures I2C in one shot:

```bash
PI_PASS=<password> bash scripts/setup_pi.sh [user@host]
```

What the script does:

1. Rsyncs the full repo to `~/Tooes/` on the Pi (faster than cloning over a
   hotspot)
2. Rsyncs `firmware/data/` separately (OpenCellID CSVs are excluded from git)
3. Initialises a git repo on the Pi, adds the GitHub remote, and checks out
   `main` — so `git pull` works for all future updates
4. Enables I2C via `raspi-config`
5. Installs `i2c-tools` and `smbus2`
6. Runs `i2cdetect -y 1` to verify the magnetometer is wired correctly

Default target: `team@tooes.local` (fallback: `team@10.15.86.130`)

Requires `sshpass` on the laptop:

```bash
apt install sshpass   # Debian/Ubuntu
```

After the first sync, pull updates from GitHub directly on the Pi:

```bash
ssh team@tooes.local "cd ~/Tooes && git pull"
```

## 3. Install Python Dependencies

The Pi runtime dependencies live in `firmware/requirements.txt`. The setup
script currently installs only `smbus2`, so install the rest manually:

```bash
ssh team@tooes.local
cd ~/Tooes
pip3 install --break-system-packages -r firmware/requirements.txt
```

Notes:

- `RPi.GPIO` and `spidev` are Pi-specific; install them on the Pi, not on a
  laptop development machine
- The `smbus2` package may already be installed by the setup script

## 4. Enable And Verify I2C

The setup script runs `raspi-config` automatically. If you need to do it
manually:

```bash
sudo raspi-config nonint do_i2c 0
```

Then verify the bus:

```bash
i2cdetect -y 1
```

Expected sensor addresses:

- `0x0d` — QMC5883L magnetometer (GY-271 board)
- `0x68` — MPU-6050 accelerometer / gyroscope

If those addresses do not appear, debug wiring before debugging the software.
See the wiring tables in `docs/hardware.md`.

## 5. Create The `.env.local` Configuration File

The navigation runtime requires a starting location. Create `.env.local` at
the repo root on the Pi:

```bash
ssh team@tooes.local
cd ~/Tooes
cat > .env.local <<'EOF'
INITIAL_L=42.012280,23.095261
EOF
```

Replace the coordinates with your actual starting location. `INITIAL_L` is the
absolute position the firmware uses as its initial anchor. The navigation
engine then tracks short-range IMU movement locally and blends in SDR fixes as
corrections over time.

Additional optional settings you can add to `.env.local`:

```bash
# SDR hardware
SDR_DRIVER=sdrplay          # SoapySDR driver name (default: sdrplay)
SDR_SERIAL=                 # Serial number if you have multiple devices

# Tuning
NAV_TRIGGER_DISTANCE_M=25.0 # SDR fix interval in metres (default: 25.0)
SDR_CONFIDENCE_RADIUS_M=500.0 # Radius within which an SDR fix is trusted (m)

# Path logging (enabled by default)
NAV_PATH_LOG_ENABLED=true   # Set to false to disable
# NAV_PATH_LOG_PATH=firmware/logs/my_trace.jsonl  # Custom log path
```

All settings can also be passed as shell environment variables; shell exports
take precedence over `.env.local`.

## 6. Provide OpenCellID Data

The tower lookup path requires local CSV files in `firmware/data/`.

The lookup code expects one file per MCC, for example:

- `firmware/data/284.csv` (Bulgaria, MCC 284)

If the file is missing, the live tower-resolution path will fail at runtime
even though the code deployed successfully.

The setup script syncs `firmware/data/` if the directory exists locally.
To re-sync it manually:

```bash
# From your laptop, at the repo root:
rsync -avz --progress firmware/data/ team@tooes.local:~/Tooes/firmware/data/
```

## 7. Download Offline Tiles If You Need The Map View

The map view reads from `firmware/offline_tiles/`. To populate it:

```bash
python3 firmware/scripts/install_tiles.py
```

Only use this with a tile provider whose terms allow offline or bulk download.
If tiles are missing, the app falls back to placeholder tiles rather than
crashing.

## 8. Run The Main E-Paper Application

### Prerequisites

- Waveshare submodule populated (`git submodule update --init --recursive`)
- I2C enabled and sensors visible on the bus
- `grgsm_scanner` installed and tested in a shell (see step 9)
- `firmware/data/<mcc>.csv` present on the device
- `.env.local` created with `INITIAL_L`

### Launch command

```bash
HAL_BACKEND=grgsm \
HAL_ROTATION=qmc5883l \
HAL_ACCEL=mpu6050 \
HAL_GRGSM_SCANNER_CMD="grgsm_scanner -b GSM900 -a 'driver=sdrplay'" \
python3 -m firmware.run
```

The app reads `.env.local` automatically on startup. While running, it:

- displays a boot splash then a tutorial screen
- launches a background navigation worker that fuses IMU position with
  periodic SDR fixes
- renders a map with your trace path, heading, and discovered towers
- writes the fused trace to `firmware/logs/navigation_trace_*.jsonl` (disable
  with `NAV_PATH_LOG_ENABLED=false`)

Use the rotary encoder to zoom the map. The button triggers a manual scan.

## 9. Radio Prerequisites

The repo assumes the scanner command itself is already working.

That usually means:

- SDR device connected and recognised by the OS
- appropriate SoapySDR driver installed
- `grgsm_scanner` invocations tested directly in a shell before passing them
  to `HAL_GRGSM_SCANNER_CMD`

Recommended approach:

1. Plug in the SDR dongle and confirm it is detected:

   ```bash
   SoapySDRUtil --find
   ```

2. Test the scanner manually:

   ```bash
   grgsm_scanner -b GSM900 -a 'driver=sdrplay'
   ```

3. Only after confirming output, wire the exact command into the environment
   variable.

## 10. Run The Walking Sweep POC

All stubs on a desktop machine (no hardware required):

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

- tracks relative motion in local metres
- triggers cell reads after walking a configured distance
- is a visualisation and experimentation tool, not the production e-paper flow

Press **Space** for a manual cell measurement trigger.

## 11. Troubleshooting

### Display import fails

Likely cause: the Waveshare submodule is missing.

Fix:

```bash
git submodule update --init --recursive
```

### `lookup_tower()` exits with a CSV error

Likely cause: `firmware/data/<mcc>.csv` is missing on the device.

Fix: sync the required OpenCellID CSV files to the Pi (see step 6).

### Navigation starts at wrong location

Likely cause: `.env.local` is missing or has wrong coordinates.

Fix: create or update `INITIAL_L` in `.env.local` at the repo root.

### Map renders blank placeholders

Likely cause: offline tiles are not downloaded.

Fix:

```bash
python3 firmware/scripts/install_tiles.py
```

### I2C sensor not detected

Likely causes: wiring mistake, I2C not enabled, wrong voltage, wrong address.

Fix:

```bash
i2cdetect -y 1
```

Verify wiring tables in `docs/hardware.md`. Expected addresses: `0x0d` and
`0x68`.

### `grgsm_scanner` path fails

Likely causes: command not installed, SDR driver not configured, SoapySDR
device string incorrect.

Fix: validate the exact command in a shell first, then pass the tested command
to `HAL_GRGSM_SCANNER_CMD`.

### Git pull on Pi fails after initial setup

Likely cause: the pseudoclone step in `setup_pi.sh` was interrupted.

Fix: re-run the setup script, or manually on the Pi:

```bash
cd ~/Tooes
git fetch origin
git checkout -B main origin/main
```
