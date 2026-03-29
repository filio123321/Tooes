# Hardware And Wiring

This document collects the hardware assumptions currently encoded in the repo.
It covers what the code expects today, how the main sensors are wired, and
which dependencies are external to the Python code.

## Hardware In The Current Repo

### Raspberry Pi

The current firmware targets a Raspberry Pi and assumes:

- Python 3 is available
- I2C is enabled
- GPIO access is available
- the process can talk to the display driver and sensors

### Display

The main app imports the Waveshare 2.9 inch display driver from the git
submodule:

- `external/waveshare-epd/RaspberryPi_JetsonNano/python/lib`

The display code currently expects `waveshare_epd.epd2in9_V2`.

Important note:

- the submodule is declared in `.gitmodules`, but it may not be populated in a
  fresh checkout
- run:

```bash
git submodule update --init --recursive
```

before attempting to launch the real e-paper UI

### Rotary Encoder

The main app uses an HW-040 style rotary encoder on these BCM pins:

- `CLK`: BCM 5
- `DT`: BCM 6
- `SW`: BCM 13

These values are hard-coded in `firmware/ui/app.py`.

### Magnetometer: QMC5883L / GY-271

The heading path uses a QMC5883L magnetometer over I2C.

Expected address:

- `0x0d`

Wiring:

| GY-271 pin | Pi physical pin | Function |
| --- | --- | --- |
| VCC | Pin 1 | 3.3V |
| GND | Pin 6 | Ground |
| SDA | Pin 3 | GPIO 2 / I2C1 SDA |
| SCL | Pin 5 | GPIO 3 / I2C1 SCL |

Verification:

```bash
i2cdetect -y 1
```

The device should appear at `0x0d`.

### Accelerometer: MPU-6050

The tilt and acceleration path uses the accelerometer half of the MPU-6050.
The current code does not read the gyroscope registers.

Expected address:

- `0x68` by default
- `0x69` if AD0 is pulled high

Wiring:

| MPU-6050 pin | Pi physical pin | Function |
| --- | --- | --- |
| VCC | Pin 1 | 3.3V |
| GND | Pin 6 | Ground |
| SDA | Pin 3 | GPIO 2 / I2C1 SDA |
| SCL | Pin 5 | GPIO 3 / I2C1 SCL |

Verification:

```bash
i2cdetect -y 1
```

The device should appear at `0x68` unless the board wiring changes the address.

### SDR And GSM Scan Path

The live RF path expects an external GSM scanner command. The code currently
assumes `grgsm_scanner` and a working SoapySDR-compatible radio chain. Those
pieces are not installed or configured by the Python package itself.

In practice this means:

- the SDR hardware choice lives outside this repo
- the exact scanner command is supplied via `HAL_GRGSM_SCANNER_CMD`
- radio drivers, SoapySDR, GNU Radio dependencies, and scan permissions are
  system concerns

## Python And System Dependencies

The current runtime dependency file is `firmware/requirements.txt`:

- `requests`
- `Pillow`
- `gpiozero`
- `spidev`
- `RPi.GPIO`
- `smbus2`

Important operational note:

- `scripts/setup_pi.sh` currently enables I2C and installs `smbus2`, but it
  does not install the entire Python dependency set or the radio stack

For the Pi runtime, an explicit install step is still recommended:

```bash
pip3 install --break-system-packages -r firmware/requirements.txt
```

## Data Dependencies

### OpenCellID CSVs

Tower lookup depends on local CSV files in:

- `firmware/data/`

The lookup code searches for a file named after the MCC, for example:

- `firmware/data/284.csv`

These files are intentionally not committed. If the file is missing,
`firmware/opencellid.py` exits with a fatal error.

### Offline Map Tiles

The map renderer loads PNG tiles from:

- `firmware/offline_tiles/`

If tiles are missing, the app still runs, but it draws placeholder tiles with
`z/x/y` labels instead of a real map.

The tile preloader is:

- `firmware/scripts/install_tiles.py`

## Current Hardware Assumptions In The Math

The motion and heading code assume:

- the device is held roughly level
- the QMC5883L Z axis points upward
- pitch and roll are small enough that simple tilt compensation is usable
- the motion code can treat the horizontal accelerometer axes as meaningful
  horizontal acceleration

These assumptions are good enough for a prototype, but they should not be
mistaken for a calibrated inertial navigation stack.

## Known Gaps

- No explicit magnetometer calibration procedure is implemented.
- No accelerometer calibration procedure is implemented.
- No gyroscope path is wired into the current motion estimator.
- No single document in code defines the physical sensor mounting frame beyond
  comments and formulas.
- No automated setup in the repo currently installs `grgsm_scanner`, GNU Radio,
  or SoapySDR-related tooling.
