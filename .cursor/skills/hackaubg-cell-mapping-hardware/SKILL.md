---
name: hackaubg-cell-mapping-hardware
description: Guides hardware and software integration for GSM tower sniffing with SDRPlay RSP1A, Raspberry Pi 5, Waveshare e-ink, OpenCellID lookup, and RSSI-based trilateration (with directional-antenna rotation) for a hackathon demo. Use when working on firmware, SDR capture, Gr-GSM, cell ID decoding, display wiring, OpenCellID data, multilateration, path loss, demo prep, or hardware bring-up for this repository.
---

# HackAUBG cell-mapping hardware

## Hardware inventory

| Component | Role |
|-----------|------|
| **SDRPlay RSP1A** | Receive GSM downlink (BCCH); USB to capture host (laptop). |
| **Unidirectional / yagi-style antenna** | **Rotation matters:** the operator sweeps azimuth so each cell’s RSSI is measured near **on-boresight** (peak reading). That peak RSSI feeds the **range** estimate for trilateration. Keep tilt/polarization stable during a sweep. |
| **Raspberry Pi 5** | Runs Python: parse tower observations, OpenCellID lookup, **trilateration**, drive e-ink. |
| **Waveshare e-ink (SPI)** | Low-power map / status; partial refresh where the panel supports it. |

## Positioning idea: trilateration (not triangulation)

- **Triangulation** solves for position using **angles** (bearings)—e.g. two direction lines crossing.
- **Trilateration** solves for position using **distances** from **three or more** known points (cell tower coordinates from OpenCellID). Each RSSI-derived range defines a circle (2D); feasible receivers lie where those circles **intersect**—in practice use **least squares** when you have noisy ranges and **N ≥ 3** towers.

This project’s story: **tower positions** come from the database; **ranges** come from **RSSI** via a simple **path-loss** mapping (calibrated or monotonic proxy). More towers ⇒ more constraints ⇒ usually more stable fixes—**multilateration** when N > 3.

**Why the person rotates the device**

With a **directional** antenna, RSSI is meaningless for range until you know you’re roughly aligned with the tower’s azimuth. The operator **rotates the SDR + antenna assembly** through a sweep (e.g. full 360° slowly), logging RSSI vs time or heading. For each **(MCC, MNC, LAC, CI)** you take the **peak RSSI** over the sweep (or the best stable plateau) and feed **that** into the path-loss → **distance** step. Rotation turns “random side-lobe level” into a **comparable** per-tower range input for trilateration.

## How things connect

**SDR → laptop**

- RSP1A attaches over **USB** (device end is often **USB-B** or bundled cable per SDRPlay kit) to the machine running GNU Radio / **gr-gsm**.
- No SPI for the SDR; RF in from antenna port only.

**E-ink → Raspberry Pi 5 (typical SPI)**

Wire **SPI0** (or the bus your Waveshare HAT/doc specifies) plus power and control:

- **3.3 V** and **GND**
- **MOSI** (GPIO10), **SCLK** (GPIO11)
- **CS** chip select (often GPIO8 CE0 or board-specific)
- **DC** data/command
- **RST** reset
- **BUSY** (read busy line before sending another frame)

Always follow the exact pin table for your **Waveshare SKU** (HAT vs standalone breakout differ). Enable SPI with `raspi-config` or device tree overlay as per Waveshare wiki.

**Data path (conceptual)**

```text
Antenna sweep → RSP1A → USB → Laptop (gr-gsm) → per-cell peak RSSI + IDs
                                                    ↓
                           Network or file drop → RPi → OpenCellID (tower lat/lon)
                                                    ↓
                           RSSI → range → trilaterate / least squares → render → SPI → e-ink
```

## What is in the air (GSM)

From the **BCCH** / system information you care about:

- **MCC** — mobile country code (e.g. Bulgaria often **284**).
- **MNC** — mobile network code (operator).
- **LAC** — location area code (often called **area** in OpenCellID dumps).
- **Cell ID (CI)** — serving cell identifier (OpenCellID column **cell**).
- **Signal strength** — RSSI (after **rotational peak pick**, use as input to **range** for trilateration; not for OpenCellID lookup).

**Laptop + gr-gsm** = capture carriers, decode SI, emit **MCC/MNC/LAC/CI** and time-series RSSI during sweeps.

## OpenCellID in this project

**Download**

- Register / use token per [OpenCellID](https://opencellid.org/) terms; download **CSV** for the needed **MCC** (country).

**Local layout (repo)**

- Place per-country files under `firmware/data/` as `{mcc}.csv` (see `firmware/opencellid.py`).
- Lookup API: `lookup_tower(mcc, mnc, lac, cell_id)` → `TowerCoordinatesDict(lat, lon)` or `None`.

**Column mapping (typical OpenCellID export)**

`firmware/opencellid.py` uses `csv.DictReader` and expects a **header row** with at least **mcc**, **net**, **area**, **cell**, **lat**, **lon**. Raw OpenCellID dumps are often **positional** (no header; **lon** before **lat**); add a header line or convert once before dropping files into `firmware/data/`. After conversion, verify a known cell resolves to the correct map position.

**Lookup rule**

- One row per tower key `(MCC, MNC, LAC, CI)`. Missing row ⇒ tower not in DB; demo fallback: show “unknown tower” list or last known fix.

## Software architecture

1. **Laptop + GNU Radio + gr-gsm** — tune ARFCN, decode GSM; during a **sweep**, log **RSSI time series** keyed by cell. **Reduce** each cell to **peak RSSI** (and optional sweep metadata) before sending downstream.
2. **RPi + Python** — for each resolved cell, **`firmware/opencellid.lookup_tower`** → tower **(lat, lon)**; map peak RSSI → **estimated range**; **trilaterate** (closed form for 3 towers in ideal geometry, or **nonlinear least squares** for N towers with noisy ranges).
3. **Waveshare driver** — draw a **map**: circles optional (debug), tower dots, estimated receiver position, optional “walk this way” hint toward a goal or stronger sector.

**HAL placeholder**

- `firmware/hal.py` is the right place to isolate **SPI / display**, **sweep / heading** stubs, and **SDR ingest** mocks for tests on a laptop without hardware.

## Three-day timeline

### Day 1 — RF path proves out

- [ ] Laptop: SDR drivers + **gr-gsm** build or container; verify **BCCH** and **MCC/MNC/LAC/CI**.
- [ ] **Rotation discipline:** slow sweep, log RSSI; confirm **peak RSSI** per cell behaves sensibly when the antenna aims through the tower direction.
- [ ] Log to **JSONL** or CSV for replay (include sweep id or timestamp).
- [ ] Download OpenCellID **284** (or target MCC); confirm `lookup_tower` returns coordinates for known cells.

### Day 2 — Pi + trilateration + display

- [ ] RPi: Python env, `firmware/data/*.csv`, pipeline: lookup → range → **trilateration / least squares**.
- [ ] SPI e-ink: **full refresh** baseline; then **partial** if supported.
- [ ] End-to-end: new sweep summary → updated map on display.

### Day 3 — Demo hardening

- [ ] Scripted **demo data** if live RF is flaky indoors; still show **one real sweep** if possible.
- [ ] Accuracy slide: path-loss fudge, DB age, multipath—**km-scale** is plausible; rotation reduces gross pointing error but does not beat physics.
- [ ] Judge script: **rotate → peaks → trilaterate → map** (below).

## Challenges and mitigations

| Challenge | Mitigation |
|-----------|------------|
| **gr-gsm / deps pain** | Pin OS (Ubuntu LTS), use Docker or prebuilt images; minimize GNU Radio version drift. |
| **No decode / wrong band** | Confirm **GSM900 vs DCS1800** for your region; correct **clock ppm** / sample rate in source block. |
| **OpenCellID gaps** | Show resolved vs unresolved tower counts; do not invent ranges for missing cells. |
| **RSSI → range is approximate** | Document the log model or calibration constants; prefer **least squares** over a fragile three-circle analytic intersection when ranges are noisy. |
| **Trilateration error budget** | Tower coordinates are **site** locations; RSSI maps poorly to meters in clutter—quote **honest** uncertainty (often **km**). |
| **Rotation speed** | Too fast ⇒ smeared peaks; too slow ⇒ demo timeout. Rehearse a **10–20 s** sweep. |
| **E-ink ghosting / slow** | Full refresh periodically; partial for small updates; avoid rapid full-screen churn. |
| **Judge Wi-Fi** | Prefer **offline** CSV on SD; avoid live API dependency during demo. |

## Demo walkthrough (what judges see)

1. **Sweep:** “We rotate the rig and decode **K** distinct cells; we take **peak RSSI** per cell.”
2. **Map on e-ink:** tower positions from OpenCellID, receiver estimate from **trilateration** (show uncertainty if you have it).
3. **Punchline:** “We’re about **±3 km** of this point given **path loss, DB, and multipath**; **walk toward** [bearing] for the next sweep.”

Align numbers with your actual run—do not claim sub-100 m without measurement.

## Key takeaways (project narrative)

1. **Laptop + gr-gsm** = cell **IDs** + RSSI time series; **rotation** yields **peak RSSI** per tower for fair range input.
2. **RPi + Python** = OpenCellID **positions** + RSSI-derived **ranges** → **trilaterate** → **draw** map.
3. **E-ink** = readable status without glare.
4. **Demo line:** “We detected **3** towers, swept the antenna, **trilaterated** to about **±3 km**, **here’s where to walk**.”
