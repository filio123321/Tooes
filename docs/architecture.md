# Architecture

This document describes how the current codebase is structured and how data
flows through it. It is intentionally grounded in the code that exists today,
not in the longer-term vision for the project.

## System Overview

The repository has two main runtime paths:

1. The e-paper application in `firmware/ui/app.py`, which scans for towers and
   renders a compact map-oriented UI on the Raspberry Pi.
2. The walking proof-of-concept in `firmware/scripts/sweep_poc.py`, which uses
   motion sensors and periodic cell measurements to visualize a relative path
   and live RSSI values on a desktop window.

Both paths are built on top of the HAL in `firmware/hal/`, which provides
small protocol interfaces and environment-driven factories for selecting real,
mock, or replay backends.

## Core Data Model

Two data structures connect almost all RF-related components:

- `CellKey` in `firmware/hal/types.py`
  - identifies a cell observation as `(mcc, mnc, lac, ci)`
- `SweepSample` in `firmware/hal/types.py`
  - stores one observation instant as:
    - elapsed time `t`
    - current azimuth `azimuth_deg`
    - `cells: Dict[CellKey, float]`

The serialized JSONL format uses the same structure, which makes the replay
path and the live path share the same downstream logic.

## HAL Layer

The HAL protocols in `firmware/hal/protocols.py` define the minimum contracts
used by the rest of the code:

- `SweepSampleSource`
- `RotationReader`
- `CellRssiReader`
- `TiltReader`
- `AccelerationReader`

The factory module `firmware/hal/factory.py` maps environment variables to
concrete implementations:

- RF sweep source:
  - `mock`
  - `replay`
  - `grgsm`
- Rotation reader:
  - `stub`
  - `qmc5883l`
- Tilt reader:
  - `stub`
  - `mpu6050`
- Acceleration reader:
  - `stub`
  - `mpu6050`
- Cell reader:
  - `mock`
  - `grgsm`

This arrangement is what makes the same app logic usable against both real
hardware and deterministic development backends.

## RF Discovery Pipeline

The current RF path is layered like this:

1. Capture and parsing
   - `firmware/hal/grgsm_scanner.py` runs `grgsm_scanner` and parses stdout into
     `CellKey -> RSSI` maps.
2. Sweep assembly
   - `GrgsmScannerSource` pairs each parsed RF snapshot with a heading from a
     `RotationReader`, producing a `SweepSample`.
3. Tower resolution
   - `firmware/opencellid.py` looks up each `CellKey` in a local OpenCellID CSV
     file named after the mobile country code.
4. UI aggregation
   - `firmware/ui/app.py` keeps the strongest observed RSSI per tower during the
     scan and assigns human-readable labels such as `T1`, `T2`, and `T3`.
5. Position estimate
   - after scanning completes, the app computes a latitude/longitude estimate
     from the resolved towers using the heuristic currently implemented in
     `_finish_scan()`.

## Main E-Paper App

The e-paper application in `firmware/ui/app.py` is a small state machine with
four screens:

- `BOOT`
- `TUTORIAL`
- `SCANNING`
- `MAP`

The startup sequence is:

1. initialize display, rotary encoder, button, and compass
2. show boot splash
3. step through tutorial pages
4. launch a background scan thread
5. transition to the map after the scan is done

Important implementation details:

- scanning runs in a background thread and atomically replaces `self.towers`
  when better data appears
- the map view updates zoom from the rotary encoder and heading from the
  magnetometer
- offline map rendering is handled by `firmware/ui/tiles.py`

## Walking POC Architecture

The desktop POC in `firmware/scripts/sweep_poc.py` uses a different structure
from the e-paper app:

1. get a rotation reader and acceleration reader
2. construct a `DeadReckoningTracker`
3. construct a `CellRssiReader` using the tracker position
4. repeatedly update relative position `(x, y)` in meters
5. trigger a cell measurement when the operator walks at least
   `HAL_TRIGGER_DISTANCE` meters or presses Space
6. render the path, current heading, tower layout, and RSSI bars in Pygame

This path produces a local relative coordinate frame, not a geographic
latitude/longitude estimate.

## Position Outputs

The repo currently has two distinct notions of "position":

- absolute geographic estimate in the main app
  - a heuristic latitude/longitude estimate derived from resolved towers
- relative local estimate in the walking POC
  - dead-reckoned `(x, y)` coordinates in meters from the starting point

These should not be treated as the same output. They solve different problems
and use different inputs.

## Offline Assets And External Boundaries

Several pieces of runtime behavior depend on assets or tools that are not fully
managed by the Python package itself:

- `external/waveshare-epd/`
  - git submodule used by the e-paper display path
- `firmware/data/`
  - local OpenCellID CSV files, not committed
- `firmware/offline_tiles/`
  - optional offline OpenStreetMap tile cache
- `grgsm_scanner`, SoapySDR, and SDR-specific drivers
  - external system dependencies for the live GSM path

## Current Algorithmic Truths

The documentation should stay aligned with these implementation facts:

- the main app does not currently perform true trilateration or multilateration
- the motion stack does not currently use gyroscope integration or IMU fusion
- the dead-reckoning path is a lightweight POC, not a navigation-grade motion
  solution
- the RF stack is currently GSM-specific at the parser and identity layer

## Extension Points For LTE And Richer SDR Work

The cleanest way to grow the documentation when LTE support is added is to keep
the current layered structure and swap in richer detail at the protocol layer:

- capture layer
  - current: `grgsm_scanner`
  - future: richer SDR capture paths, LTE-specific tools, or direct SDR APIs
- identity layer
  - current: `CellKey(mcc, mnc, lac, ci)`
  - future: LTE identifiers such as TAC, ECI, PCI, EARFCN, and related metadata
- geolocation layer
  - current: local CSV lookup
  - future: same conceptual layer, possibly with additional tower datasets
- positioning layer
  - current: heuristic centroid
  - future: calibrated RF propagation models, multilateration, filtering, or
    fusion with motion estimates

This is why the algorithm docs are split into separate motion and RF documents:
the system will grow, but the high-level architecture can stay stable.
