# Architecture

This document describes how the current codebase is structured and how data
flows through it. It is intentionally grounded in the code that exists today,
not in the longer-term vision for the project.

## System Overview

The repository has two main runtime paths:

1. The e-paper application, started via `python3 -m firmware.run`, which fuses
   IMU dead-reckoning with periodic SDR fixes and renders a map UI on the
   Raspberry Pi.
2. The walking proof-of-concept in `firmware/scripts/sweep_poc.py`, which uses
   motion sensors and periodic cell measurements to visualise a relative path
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

The serialised JSONL format uses the same structure, which makes the replay
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

- RF sweep source: `mock`, `replay`, `grgsm`
- Rotation reader: `stub`, `qmc5883l`
- Tilt reader: `stub`, `mpu6050`
- Acceleration reader: `stub`, `mpu6050`
- Cell reader: `mock`, `grgsm`

This arrangement makes the same app logic usable against both real hardware
and deterministic development backends.

## Navigation Engine (`firmware/navigation/`)

The `firmware/navigation/` package is the core positioning logic. It was
introduced as part of the component-integration refactor that unified the
previously separate UI and sensor threads.

Key components:

- `config.py` — `NavigationConfig` dataclass loaded from `.env.local` and
  environment variables. Controls IMU update rate, step detection thresholds,
  SDR blending weights, and trace logging. See
  `docs/operations/pi-setup.md` for the full list of settable variables.
- `service.py` — `NavigationEngine`. The main fusion loop. Accepts IMU samples
  from `ImuSampleProcessor` and SDR fixes from `SdrFixProvider`, and emits
  `NavigationSnapshot` objects with current `(lat, lon)`, heading, trace
  history, and SDR confidence.
- `imu.py` — `ImuSampleProcessor`. Reads raw accelerometer and magnetometer
  samples, runs step detection, and converts relative displacement into
  geographic increments.
- `sdr.py` — `SdrFixProvider`. Schedules periodic SDR measurements and
  computes a confidence-weighted geographic fix to blend into the IMU track.
- `geo.py` — distance and bearing helpers used by the navigation engine.
- `path_logger.py` — writes the fused navigation trace to a JSONL file.
- `trace.py` — manages the in-memory trace point history.

## Runtime Orchestrator (`firmware/runtime/`)

The `firmware/runtime/` package owns the background threads and produces the
`RuntimeSnapshot` that the UI consumes.

- `orchestrator.py` — `FirmwareOrchestrator`. Starts the navigation worker
  thread and the scan thread, protects shared state with a lock, and exposes
  a single non-blocking `snapshot()` call that the UI polls on each render
  cycle.
- `navigation_worker.py` — the navigation update loop. Reads IMU sensors at
  the configured rate (`IMU_UPDATE_HZ`, default 50 Hz), feeds
  `NavigationEngine`, and pushes updated `RuntimeSnapshot` objects into the
  orchestrator's queue.

### Thread model

```
main thread (UI event loop)
    │
    ├── calls orchestrator.snapshot() on each frame
    │
    └── FirmwareOrchestrator
            ├── navigation thread  ← runs at IMU_UPDATE_HZ (50 Hz default)
            │       reads MPU-6050 + QMC5883L → NavigationEngine
            │       pushes RuntimeSnapshot into a maxsize-1 queue
            │
            └── scan thread (on demand)
                    runs grgsm_scanner subprocess
                    resolves towers via OpenCellID CSV
                    updates tower list in RuntimeSnapshot
```

The `RuntimeSnapshot` published by the orchestrator is a frozen dataclass, so
the UI thread never needs to acquire a lock to read it.

## RF Discovery Pipeline

The current RF path is layered like this:

1. **Capture and parsing** — `firmware/hal/grgsm_scanner.py` runs
   `grgsm_scanner` and parses stdout into `CellKey -> RSSI` maps.
2. **Sweep assembly** — `GrgsmScannerSource` pairs each parsed RF snapshot
   with a heading from a `RotationReader`, producing a `SweepSample`.
3. **Tower resolution** — `firmware/opencellid.py` looks up each `CellKey`
   in a local OpenCellID CSV file named after the mobile country code.
4. **Aggregation** — the scan thread keeps the strongest observed RSSI per
   tower and assigns human-readable labels such as `T1`, `T2`, `T3`.
5. **Position estimate** — after scanning completes, a heuristic
   RSSI-weighted centroid is computed from the resolved towers.

## Main E-Paper App

The e-paper application entry point is `firmware/run.py`, which delegates to
`firmware/ui/app.py`. The UI layer is split into focused modules:

- `app.py` — initialises the display, rotary encoder, and button; starts the
  orchestrator; runs the event loop.
- `runtime.py` — bridges `app.py` with `FirmwareOrchestrator`.
- `state.py` — `UiState`, `RuntimeSnapshot`, and `DiscoveredTower` dataclasses
  (frozen, safe to pass across thread boundaries).
- `screens.py` — rendering functions for each screen: `BOOT`, `TUTORIAL`,
  `SCANNING`, `MAP`.
- `redraw.py` — decides when a full e-paper refresh is warranted to avoid
  unnecessary redraws on the slow display.

The four UI screens are:

- `BOOT` — splash screen shown during startup
- `TUTORIAL` — one-time usage hint pages
- `SCANNING` — progress indicator while the first scan runs
- `MAP` — map view with trace overlay, heading arrow, and tower markers

The trace overlay is enabled by default and shows the recent navigation path
on the map.

## Walking POC Architecture

The desktop POC in `firmware/scripts/sweep_poc.py` uses a different structure
from the e-paper app:

1. get a rotation reader and acceleration reader
2. construct a `DeadReckoningTracker`
3. construct a `CellRssiReader` using the tracker position
4. repeatedly update relative position `(x, y)` in metres
5. trigger a cell measurement when the operator walks at least
   `HAL_TRIGGER_DISTANCE` metres or presses Space
6. render the path, heading, tower layout, and RSSI bars in Pygame

This path produces a local relative coordinate frame, not a geographic
latitude/longitude estimate.

## Position Outputs

The repo currently has two distinct notions of "position":

- **Absolute geographic estimate** in the main app — a fused
  latitude/longitude that starts from `INITIAL_L` and is continuously updated
  by IMU dead-reckoning with periodic SDR-based anchor corrections.
- **Relative local estimate** in the walking POC — dead-reckoned `(x, y)`
  coordinates in metres from the starting point.

These should not be treated as the same output.

## Navigation Trace Logging

The main app writes the fused navigation path to a JSONL file under
`firmware/logs/` by default. Each line records a timestamped `(lat, lon)`
checkpoint. This log is useful for post-session analysis and replay testing.

Control it with:

```
NAV_PATH_LOG_ENABLED=true          # default; set to false to disable
NAV_PATH_LOG_PATH=firmware/logs/my_trace.jsonl  # optional custom path
```

## Offline Assets And External Boundaries

Several pieces of runtime behaviour depend on assets or tools not managed by
the Python package itself:

- `external/waveshare-epd/` — git submodule used by the e-paper display path
- `firmware/data/` — local OpenCellID CSV files, not committed
- `firmware/offline_tiles/` — optional offline OpenStreetMap tile cache
- `grgsm_scanner`, SoapySDR, and SDR-specific drivers — external system
  dependencies for the live GSM path

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

- **capture layer** — current: `grgsm_scanner`; future: richer SDR capture
  paths, LTE-specific tools, or direct SDR APIs
- **identity layer** — current: `CellKey(mcc, mnc, lac, ci)`; future: LTE
  identifiers such as TAC, ECI, PCI, EARFCN
- **geolocation layer** — current: local CSV lookup; future: same conceptual
  layer, possibly with additional tower datasets
- **positioning layer** — current: heuristic centroid; future: calibrated RF
  propagation models, multilateration, filtering, or fusion with motion
  estimates

This is why the algorithm docs are split into separate motion and RF documents:
the system will grow, but the high-level architecture can stay stable.
