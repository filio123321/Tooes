# RF Localization Pipeline

This document explains the current RF path in the repository and stays aligned
with the algorithms that are actually implemented today.

## Scope

The current codebase implements a GSM-oriented pipeline built around:

- `grgsm_scanner`
- a `CellKey` made from `(mcc, mnc, lac, ci)`
- local OpenCellID CSV lookup
- a heuristic final position estimate in the main app

This is not yet an LTE protocol document. The end of this file explains how the
same documentation structure can be extended once LTE and richer SDR work land.

## 1. Observations And Data Structures

The RF path uses two central data structures:

- `CellKey`
  - `mcc`
  - `mnc`
  - `lac`
  - `ci`
- `SweepSample`
  - time `t`
  - azimuth `azimuth_deg`
  - map of `CellKey -> RSSI`

The RF pipeline is therefore built around the question:

"At this heading and this time, which cells were visible and how strong were
they?"

## 2. Scanner Parsing

`firmware/hal/grgsm_scanner.py` expects stdout lines like:

```text
ARFCN: 512, Freq: 1930.2M, CID: 3492, LAC: 32451, MCC: 310, MNC: 26, Pwr: -57
```

The parser extracts:

- `CID`
- `LAC`
- `MCC`
- `MNC`
- `Pwr`

and converts them into:

```text
CellKey(mcc, mnc, lac, ci), rssi_dbm
```

This means the current code is only as rich as the fields exposed by that text
format. The parser does not currently retain additional scanner metadata such
as bandwidth, channel confidence, timing information, or modulation details.

## 3. Sweep Construction

The sweep source `GrgsmScannerSource` performs repeated scan iterations.

For each iteration it:

1. reads the current azimuth from a `RotationReader`
2. runs the scanner command
3. parses the scanner output
4. emits one `SweepSample`

So the mathematical object produced by the live scan path is:

```text
sample_i = (t_i, heading_i, {cell_j -> rssi_ij})
```

The scan count is controlled by `HAL_GRGSM_N_SCANS`, defaulting to `36`.

## 4. Tower Resolution Through OpenCellID

The module `firmware/opencellid.py` resolves a `CellKey` to coordinates by
opening:

```text
firmware/data/<mcc>.csv
```

and finding a row that matches:

- `mcc`
- `net` as MNC
- `area` as LAC
- `cell` as CI

If a matching row is found, the code returns:

- `lat`
- `lon`

If the CSV for that MCC does not exist, the current implementation exits the
program with a fatal error. In other words, tower lookup is not an optional
detail in the main e-paper flow; it is a required dependency.

## 5. Scan Aggregation In The Main App

The scan worker in `firmware/ui/app.py` consumes `SweepSample` values and keeps
the best RSSI seen for each discovered tower.

For each observed cell:

- if the tower is new, it is labeled `T1`, `T2`, `T3`, and so on
- if the tower was already seen, its stored RSSI is replaced only when a
  stronger observation arrives
- tower coordinates are looked up through OpenCellID

The resulting tower state shown in the UI is therefore:

```text
DiscoveredTower = (
    key,
    lat,
    lon,
    best_rssi,
    label
)
```

This aggregation step is a "best observation per tower" heuristic, not a full
probabilistic observation history.

## 6. Current Position Estimate In The Main App

When the scan finishes, `firmware/ui/app.py` computes a single latitude and
longitude estimate from the resolved towers.

Only towers with known coordinates are used.

The code computes a weight for each tower:

```text
w_i = 10^(rssi_i / -20)
```

Then:

```text
lat_est = sum(w_i * lat_i) / sum(w_i)
lon_est = sum(w_i * lon_i) / sum(w_i)
```

This is an RSSI-weighted centroid heuristic.

Two important truthfulness notes belong in the documentation:

1. This is not trilateration or multilateration.
2. Because RSSI values are negative in dBm, the implemented formula
   `10^(rssi / -20)` increases as RSSI becomes weaker. That means the current
   code gives more weight to weaker signals, even though the nearby code
   comment says the opposite. The documentation should describe the formula as
   implemented and flag this as a heuristic that likely needs revision.

So the most accurate wording today is:

"The main app currently uses a centroid-style RF position heuristic, not a
geometrically derived trilateration solver."

## 7. What The Mock Backends Simulate

The development backends are mathematically useful because they turn the system
into something deterministic and testable.

### Mock Sweep Source

`firmware/hal/mock.py` creates directional tower observations across a synthetic
360 degree sweep.

For each tower, RSSI is:

```text
rssi(az) = -50 - 40 (1 - cos(delta))
```

where `delta` is the wrapped angular difference between the current azimuth and
the tower's peak azimuth.

This gives each synthetic tower a smooth directional lobe.

### Mock Cell Reader

`firmware/hal/mock_cells.py` creates a position-dependent RSSI field over a
local `(x, y)` map.

For each tower at distance `d` meters:

```text
rssi(d) = -30 - 20 log10(max(d, 1))
```

This is a simple log-distance path-loss model. It is intentionally lightweight
and should be documented as a test heuristic, not as a validated propagation
model.

## 8. What The Current RF Stack Does Not Do

The current implementation does not do any of the following:

- true trilateration
- multilateration using time or phase
- confidence estimation
- probabilistic filtering over multiple scans
- sector modeling
- calibration against measured propagation environments
- LTE-specific identity parsing
- fusion between RF observations and the dead-reckoning track

These absences matter, because they determine how ambitious the docs should be.
The project is already technically interesting without pretending it does more
than the current code supports.

## 9. Future LTE And SDR Documentation Structure

When LTE work lands, the cleanest extension is to keep this same layered
structure and add richer details in the protocol-specific sections.

### Section That Can Stay The Same

- observation model
- capture and parsing layer
- tower-resolution layer
- positioning layer
- limitations and assumptions

### Section That Will Likely Change For LTE

- identity model
  - current GSM-oriented key: `(mcc, mnc, lac, ci)`
  - future LTE observations may need TAC, ECI, PCI, EARFCN, or related fields
- signal metrics
  - current code is effectively RSSI-centric
  - LTE may use RSRP, RSRQ, SINR, or other measurements
- scan tooling
  - current: `grgsm_scanner`
  - future: LTE-capable demodulation or a lower-level SDR pipeline

### Recommended Documentation Strategy For Future Commits

When LTE support is added, extend this file with new sections rather than
replacing the GSM story:

- `GSM Path`
- `LTE Path`
- `Shared Geolocation Model`
- `Positioning Heuristics And Filters`

That keeps the documentation historically accurate and makes algorithm changes
easier to compare over time.
