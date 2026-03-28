# SDR Positioning System — Complete Technical Specification

Module structure · AGC · Attenuation · Trilateration · Kalman | 2026-03-28

---

## 1. Module Structure

The entire project is a single Python package: `sdr_positioning/`. Nothing outside it needs to know about SoapySDR, gain values, or signal physics.

```
sdr_positioning/
    __init__.py              # public API: PositioningSystem
    sdr_module/              # hardware abstraction
        __init__.py          # public: SDRModule, Measurement, CatalogueEntry
        catalogue.py         # CatalogueLoader
        receiver.py          # SDRReceiver (SoapySDR wrapper — only file that imports SoapySDR)
        agc.py               # GainController
    trilateration.py         # attenuation model + trilateration solver
    kalman.py                # KalmanFilter + coordinate helpers
    fusion.py                # FusionEngine (owns Kalman + sources)
    models.py                # shared dataclasses: Measurement, PositionEstimate
    stations.json            # signal catalogue (ships with module)
```

**Dependency rule — imports flow in one direction only:**

| File | May import from |
|---|---|
| `sdr_module/*` | Standard library + numpy + SoapySDR only |
| `trilateration.py` | sdr_module, models, numpy, scipy |
| `kalman.py` | models, numpy |
| `fusion.py` | trilateration, kalman, models, sdr_module |
| `__init__.py` | fusion only (exposes PositioningSystem) |

> `models.py` exists to break circular imports. All shared dataclasses live there. No file imports from `fusion.py` except `__init__.py`.

**External usage — the entire system in 5 lines:**

```python
from sdr_positioning import PositioningSystem

ps = PositioningSystem("sdr_positioning/stations.json")
ps.feed_imu(ax=0.1, ay=0.0, heading_deg=45., dt=0.02)
estimate = ps.step()   # returns PositionEstimate | None
```

---

## 2. Shared Dataclasses (models.py)

```python
from dataclasses import dataclass, field
import time

@dataclass
class Measurement:
    source_id:        str
    rssi_dbm:         float   # normalised: measured_power - gain_used
    freq_hz:          float
    signal_type:      str
    lat:              float   # transmitter position
    lon:              float
    power_w:          float   # transmitter ERP in watts
    gain_used:        float   # dB — for diagnostics
    antenna_gain_dbi: float = 0.0
    best_effort:      bool  = False
    timestamp:        float = field(default_factory=time.time)

@dataclass
class PositionEstimate:
    lat:          float
    lon:          float
    accuracy_m:   float   # 1-sigma radius
    speed_ms:     float
    heading_deg:  float
    source:       str     # "IMU" | "RF_UPDATE"
    n_rf_sources: int
    last_rf_age:  float   # seconds since last RF update
    timestamp:    float = field(default_factory=time.time)
```

> `Measurement` carries the transmitter `lat`/`lon` and `power_w` so that `trilateration.py` does not need to re-query the catalogue. The catalogue lookup happens once in `sdr_module`, not twice.

---

## 3. Signal Catalogue (catalogue.py)

### 3.1 JSON Schema

The existing station JSON is the canonical format. Key is frequency in MHz as string:

```json
{
  "91.4": {
    "lat": 42.035704,  "lon": 23.095343,
    "station": "РРС Благоевград",
    "name": "БГ Радио",
    "type": "FM",
    "power_w": 300
  },
  "506.0": {
    "lat": 42.6635,  "lon": 23.2946,
    "station": "Витоша предавател",
    "name": "БНТ 1",
    "type": "DVB-T",
    "power_w": 10000,
    "antenna_gain_dbi": 6.0
  }
}
```

### 3.2 Type Defaults Registry

| type | freq range MHz | antenna_gain_dbi | min_rssi_dbm |
|---|---|---|---|
| FM | 87.5–108 | 0.0 | −90 |
| VOR | 108–118 | 0.0 | −90 |
| DAB | 174–240 | 0.0 | −95 |
| DVB-T | 470–862 | 6.0 | −95 |
| GSM | 935–960 | 9.0 | −95 |

`antenna_gain_dbi` in the JSON overrides the type default. `power_w` is always required.

### 3.3 CatalogueEntry Dataclass

```python
@dataclass(frozen=True)
class CatalogueEntry:
    source_id:        str    # e.g. "FM_91.4_Blagoevgrad"
    freq_hz:          float  # "91.4" -> 91.4e6
    lat:              float
    lon:              float
    station:          str
    name:             str
    signal_type:      str
    power_w:          float
    antenna_gain_dbi: float
    min_rssi_dbm:     float
    max_rssi_dbm:     float = -20.0
```

---

## 4. SDR Module (sdr_module/)

### 4.1 SDRReceiver

Thin SoapySDR wrapper. The only file in the project that imports SoapySDR.

```python
class SDRReceiver:
    def set_gain(self, gain_db: float) -> None
    def set_freq(self, freq_hz: float) -> None
    def set_sample_rate(self, rate_hz: float) -> None
    def read_power_dbm(self, n_samples=4096) -> float
        # IQ capture -> Hanning window -> FFT -> mean power in dBm
    def close(self) -> None
```

### 4.2 Adaptive Gain Control

The AGC produces calibrated RSSI readings by finding the optimal gain for each signal type and normalising out the gain applied.

#### Normalisation — the one formula

```
true_power_dbm = measured_power_dbm − gain_db
```

`measured_power_dbm` is what `read_power_dbm()` returns. `gain_db` is what was set on the receiver. `true_power_dbm` is what `Measurement.rssi_dbm` contains. This formula appears exactly once in `GainController.measure()`.

#### Per-type gain strategy

| Signal type | Target window (dBm) | Start gain (dB) | Step size (dB) |
|---|---|---|---|
| FM | −55 to −75 | 50 | 10 |
| VOR | −65 to −85 | 70 | 10 |
| DAB | −60 to −80 | 60 | 10 |
| DVB-T | −55 to −75 | 55 | 10 |
| GSM | −35 to −55 | 20 | 10 |

**Algorithm:** set `start_gain`, read power, compute `true_power = measured - gain`. If `true_power` is outside the target window, adjust gain by `(window_midpoint - measured)` and retry. Max 4 steps. Return best-effort result if gain never fully settles.

> Gain is reset to `start_gain` between signal types. GSM at gain=20 must not bleed into VOR which needs gain=70.

```python
class GainController:
    MAX_STEPS = 4
    GAIN_MIN  = 0.
    GAIN_MAX  = 80.

    def measure(self, entry: CatalogueEntry,
                receiver: SDRReceiver) -> Measurement | None:
        strategy = GAIN_STRATEGIES[entry.signal_type]
        gain = strategy.start_gain
        receiver.set_freq(entry.freq_hz)
        receiver.set_sample_rate(2e6)

        for step in range(self.MAX_STEPS):
            receiver.set_gain(gain)
            p_measured = receiver.read_power_dbm()
            p_true     = p_measured - gain

            if p_true < entry.min_rssi_dbm:
                return None  # signal too weak, below noise floor

            if p_true > entry.max_rssi_dbm:
                return None  # signal too strong, near-field

            lo, hi = strategy.target_window
            if lo <= p_measured <= hi:
                return Measurement(
                    source_id   = f"{entry.signal_type}_{entry.freq_hz/1e6}",
                    rssi_dbm    = p_true,
                    gain_used   = gain,
                    freq_hz     = entry.freq_hz,
                    signal_type = entry.signal_type,
                    lat         = entry.lat,
                    lon         = entry.lon,
                    power_w     = entry.power_w,
                    antenna_gain_dbi = entry.antenna_gain_dbi,
                )

            midpoint = (lo + hi) / 2
            gain    += (midpoint - p_measured)
            gain     = max(self.GAIN_MIN, min(self.GAIN_MAX, gain))

        # Best effort — return last values
        return Measurement(
            source_id   = f"{entry.signal_type}_{entry.freq_hz/1e6}",
            rssi_dbm    = p_true,
            gain_used   = gain,
            freq_hz     = entry.freq_hz,
            signal_type = entry.signal_type,
            lat         = entry.lat,
            lon         = entry.lon,
            power_w     = entry.power_w,
            antenna_gain_dbi = entry.antenna_gain_dbi,
            best_effort = True,
        )
```

### 4.3 SDRModule Public API

```python
class SDRModule:
    def __init__(self, catalogue_path, driver="sdrplay", serial=None)
    def scan(self, types=None) -> list[Measurement]
        # Scans catalogue entries, returns normalised Measurements
        # Measurement already contains transmitter lat/lon/power_w
    def close(self)
```

### 4.4 Recommended Scan Order

| Order | Signal type | Reason |
|---|---|---|
| 1 | FM (87–108 MHz) | Strongest, most reliable. Sets initial environment class. |
| 2 | VOR (108–118 MHz) | Adjacent band — cheap re-tune from FM. |
| 3 | DAB (174–240 MHz) | Medium band. Good geometry supplement to FM. |
| 4 | DVB-T (470–862 MHz) | High power broadcast. Good GDOP filler. |
| 5 | GSM (935–960 MHz) | Low gain required. Scan last to avoid AGC disruption. |

---

## 5. Attenuation Model (trilateration.py)

Converts `Measurement.rssi_dbm` into an estimated distance in metres. Every physical parameter is explicit.

### 5.1 Free-Space Path Loss

The received power at distance `d` from a transmitter:

```
EIRP_dbm  = 10·log10(power_w) + 30 + antenna_gain_dbi
FSPL_dB   = 20·log10(d) + 20·log10(freq_hz) − 147.55
RSSI_pred = EIRP_dbm − FSPL_dB
```

The constant `−147.55 = 20·log10(4π/c)` where `c = 3×10⁸ m/s`. It embeds wavelength: `λ = c/f`, so higher frequency = shorter wavelength = more FSPL. No separate wavelength correction is needed.

### 5.2 Inverting for Distance

```
path_loss_dB = EIRP_dbm − rssi_dbm
d_freespace  = 10^((path_loss_dB + 147.55 − 20·log10(freq_hz)) / 20)
```

### 5.3 Frequency Effect

| Signal type | Frequency | Extra FSPL vs FM 100 MHz at same distance |
|---|---|---|
| FM | 100 MHz | 0 dB (reference) |
| VOR | 113 MHz | +1.1 dB |
| DAB | 200 MHz | +6.0 dB |
| DVB-T | 650 MHz | +16.2 dB |
| GSM | 948 MHz | +19.5 dB |

> This is why GSM needs lower gain than FM even at the same distance — the path loss model accounts for this automatically via `20·log10(freq_hz)`.

### 5.4 Implementation

```python
from math import log10, sqrt

def rssi_to_distance(rssi_dbm, freq_hz, power_w,
                     antenna_gain_dbi, environment) -> float:
    eirp_dbm  = 10 * log10(power_w) + 30 + antenna_gain_dbi
    path_loss = eirp_dbm - rssi_dbm
    path_loss -= environment.extra_loss_db   # obstruction compensation
    d = 10 ** ((path_loss + 147.55 - 20 * log10(freq_hz)) / 20)
    return max(d, 1.0)   # clamp to 1 m minimum
```

---

## 6. Obstruction Compensation (trilateration.py)

### 6.1 Environment Classes

| Class | Description | extra_loss_dB |
|---|---|---|
| OUTDOOR_LOS | Clear line of sight | 0 |
| OUTDOOR_URBAN | Outdoors, buildings present | 5 |
| INDOOR_LIGHT | Inside building, one exterior wall | 15 |
| INDOOR_DEEP | Multiple walls or floors | 27 |

### 6.2 Corrected Distance Formula

Obstruction adds extra loss on top of FSPL. Compensate by subtracting `extra_loss_dB` from the measured path loss before inverting:

```
path_loss_corrected = path_loss_dB − extra_loss_dB
d = 10^((path_loss_corrected + 147.55 − 20·log10(freq_hz)) / 20)
```

Without this correction, an indoor receiver would appear further from all transmitters than it actually is.

### 6.3 Auto-Detection

Run one pass with `OUTDOOR_LOS`. Compute excess loss per source:

```
excess_loss = RSSI_predicted_at_d_freespace − rssi_dbm_measured
```

| Max excess_loss across sources | Detected environment |
|---|---|
| < 3 dB | OUTDOOR_LOS |
| 3–12 dB | OUTDOOR_URBAN |
| 12–22 dB | INDOOR_LIGHT |
| > 22 dB | INDOOR_DEEP |

Re-run with detected class. Two passes total.

### 6.4 RSSI Quality Filter

- `rssi_dbm < entry.min_rssi_dbm` → discard (noise floor)
- `rssi_dbm > −20 dBm` → discard (near-field, model invalid)
- `best_effort = True` → weight reduced by 0.5 in trilateration
- Fewer than 3 valid sources after filtering → return `None`

---

## 7. Trilateration Solver (trilateration.py)

### 7.1 Coordinate System

All solver coordinates are in local East-North metres relative to the centroid of source positions:

```
px = (lon − lon0) × 111320 × cos(radians(lat0))
py = (lat − lat0) × 110540
```

### 7.2 Objective Function

For N sources at known positions `(xi, yi)` with estimated distances `di` and weights `wi`:

```
E(px,py) = Σ wi · (sqrt((px−xi)² + (py−yi)²) − di)²
```

### 7.3 Source Weighting

Weight each source by inverse variance of its distance estimate. Relative uncertainty κ = 0.3 (30% of estimated distance):

```
σi = di · κ
wi = 1 / σi²  =  1 / (di · 0.3)²
```

`best_effort` sources get `wi × 0.5`. This is the only place where source quality affects the solver — a weight multiplier, not an if-else chain.

### 7.4 Solver

```python
from scipy.optimize import minimize
from math import sqrt

def trilaterate_solver(sources) -> tuple[tuple[float,float], float]:
    cx = sum(s["x"] for s in sources) / len(sources)
    cy = sum(s["y"] for s in sources) / len(sources)
    weights = [1. / (s["d_est"] * 0.3)**2 for s in sources]

    def objective(pos):
        px, py = pos
        return sum(
            w * (sqrt((px - s["x"])**2 + (py - s["y"])**2) - s["d_est"])**2
            for s, w in zip(sources, weights)
        )

    result = minimize(
        objective, [cx, cy],
        method="Nelder-Mead",
        options={"xatol": 1.0, "fatol": 1.0, "maxiter": 500}
    )
    return tuple(result.x), result.fun
```

### 7.5 Accuracy Estimation

```
accuracy_m = sqrt(residual / N)
```

Where `N` is number of sources. This is the RMS distance error at the solution. Fed directly into the Kalman filter as `accuracy_m`, which sets measurement noise `R`.

### 7.6 Geometry Check (GDOP)

Detect degenerate geometry where all sources cluster in one direction:

```python
from math import atan2, degrees

def geometry_ok(sources, px, py) -> bool:
    """Reject if all sources lie within a 90-degree arc."""
    angles = sorted(
        degrees(atan2(s["x"] - px, s["y"] - py))
        for s in sources
    )
    gaps = [b - a for a, b in zip(angles, angles[1:])]
    gaps.append(360 - angles[-1] + angles[0])  # wrap-around gap
    return max(gaps) < 270  # False = sources span less than 90 deg
```

If geometry check fails: `accuracy_m *= 3.0`. Do not discard — a poor fix is better than no fix for the Kalman filter.

### 7.7 Environment Accuracy Inflation

| Environment class | accuracy_m multiplier |
|---|---|
| OUTDOOR_LOS | 1.0× |
| OUTDOOR_URBAN | 1.3× |
| INDOOR_LIGHT | 2.0× |
| INDOOR_DEEP | 3.0× |

### 7.8 Public Function

```python
def trilaterate(
    measurements: list[Measurement],
    environment: Environment = Environment.OUTDOOR_LOS,
    origin: tuple[float, float] | None = None
) -> RFMeasurement | None:
    # Stage 1: quality filter (min_rssi, max_rssi, min 3 sources)
    # Stage 2: auto-detect environment (2 passes)
    # Stage 3: rssi_to_distance() with obstruction compensation
    # Stage 4: weighted Nelder-Mead solve
    # Stage 5: geometry check, accuracy inflation
    # Returns RFMeasurement(lat, lon, accuracy_m) or None
```

---

## 8. Kalman Filter (kalman.py)

### 8.1 State Vector

```
x = [px, py, vx, vy]ᵀ   (metres and m/s in local ENU frame)
```

Acceleration is not in the state — it arrives from the IMU each step and is applied via the control input matrix B.

### 8.2 Delta Time (dt)

`dt` is the time elapsed since the last IMU reading in seconds. For a 50 Hz IMU: `dt = 1/50 = 0.02 s`. Pass the actual measured `dt` each call — this handles timing jitter and variable-rate IMUs correctly.

### 8.3 Predict Step

IMU delivers pre-processed `(ax_body, ay_body, heading_deg, dt)`. `FusionEngine` rotates to world frame before calling `predict()`:

```
ax_world = ax_body·sin(ψ) + ay_body·cos(ψ)
ay_world = ax_body·cos(ψ) − ay_body·sin(ψ)

where ψ = radians(heading_deg)
```

State transition:

```
x̂ = F·x̂ + B·[ax_world, ay_world]ᵀ

F = [[1, 0, dt, 0 ],
     [0, 1, 0,  dt],
     [0, 0, 1,  0 ],
     [0, 0, 0,  1 ]]

B = [[dt²/2, 0    ],
     [0,     dt²/2],
     [dt,    0    ],
     [0,     dt   ]]

P = F·P·Fᵀ + Q
```

Process noise Q uses `σ_a = 0.1 m/s²` (tunable constructor argument):

```
Q = σ_a² · [[dt⁴/4, 0,     dt³/2, 0    ],
             [0,     dt⁴/4, 0,     dt³/2],
             [dt³/2, 0,     dt²,   0    ],
             [0,     dt³/2, 0,     dt²  ]]
```

### 8.4 Update Step

Fires when a valid RF fix arrives. `z = [px_rf, py_rf]ᵀ` in local ENU metres. `R = diag(accuracy_m², accuracy_m²)`.

```
H = [[1, 0, 0, 0],
     [0, 1, 0, 0]]

S = H·P·Hᵀ + R
K = P·Hᵀ·S⁻¹
x̂ = x̂ + K·(z − H·x̂)
P = (I − K·H)·P
```

**Outlier gate:** reject fix if Mahalanobis distance `d² = (z−Hx̂)ᵀ·S⁻¹·(z−Hx̂) > 5.991` (χ²₀.₉₅, 2 DOF). Widen to 13.816 during first 10 seconds.

### 8.5 Coordinate Helpers

```python
def latlon_to_enu(lat, lon, lat0, lon0) -> tuple[float, float]:
    px = (lon - lon0) * 111320 * cos(radians(lat0))
    py = (lat - lat0) * 110540
    return px, py

def enu_to_latlon(px, py, lat0, lon0) -> tuple[float, float]:
    lat = lat0 + py / 110540
    lon = lon0 + px / (111320 * cos(radians(lat0)))
    return lat, lon
```

### 8.6 Accuracy Radius Output

```
accuracy_m = sqrt((P[0,0] + P[1,1]) / 2)
```

---

## 9. FusionEngine (fusion.py)

```python
class FusionEngine:
    def __init__(self, sdr: SDRModule, sigma_a=0.1)

    def feed_imu(self, ax_body, ay_body, heading_deg, dt) -> None:
        # Rotates to world frame using heading_deg
        # Calls kf.predict(ax_world, ay_world, dt)

    def step(self) -> PositionEstimate | None:
        # 1. sdr.scan() -> measurements
        # 2. trilaterate(measurements) -> RFMeasurement | None
        # 3. If fix: set origin on first fix, kf.update(px, py, accuracy_m)
        # 4. Return PositionEstimate from current kf state
```

### 9.1 PositioningSystem (public facade)

```python
# sdr_positioning/__init__.py
class PositioningSystem:
    def __init__(self, catalogue_path, driver="sdrplay",
                 serial=None, sigma_a=0.1):
        sdr = SDRModule(catalogue_path, driver, serial)
        self._engine = FusionEngine(sdr, sigma_a)

    def feed_imu(self, ax, ay, heading_deg, dt): ...
    def step(self) -> PositionEstimate | None: ...
    def close(self): ...
```

---

## 10. Implementation Checkpoints

Build in order. Each checkpoint is independently runnable. Do not proceed until the test passes.

---

### Checkpoint 1 — models.py and catalogue loading

Create `models.py` with `Measurement` and `PositionEstimate`. Create `CatalogueLoader`. Verify the existing `stations.json` loads correctly.

```python
from sdr_positioning.sdr_module.catalogue import CatalogueLoader

entries = CatalogueLoader().load("sdr_positioning/stations.json")
fm = [e for e in entries if e.signal_type == "FM"]

assert all(e.freq_hz > 1e6 for e in fm),  "freq must be Hz not MHz"
assert all(e.power_w > 0 for e in entries)
assert all(e.min_rssi_dbm < 0 for e in entries)
assert all(e.max_rssi_dbm == -20. for e in entries)
assert fm[0].antenna_gain_dbi == 0.0  # type default applied

dvbt = [e for e in entries if e.signal_type == "DVB-T"]
if dvbt:
    assert dvbt[0].antenna_gain_dbi == 6.0  # DVB-T type default
```

---

### Checkpoint 2 — Attenuation model: rssi_to_distance()

Implement `rssi_to_distance()` in `trilateration.py`. Test round-trip accuracy and obstruction compensation.

```python
from sdr_positioning.trilateration import rssi_to_distance, Environment
from math import log10

# Synthetic: 50 kW FM at 103.2 MHz, receiver 10 km away, outdoor LOS
power_w, freq_hz, d_true = 50000., 103.2e6, 10000.
eirp_dbm  = 10 * log10(power_w) + 30 + 0.0
fspl      = 20 * log10(d_true) + 20 * log10(freq_hz) - 147.55
rssi_pred = eirp_dbm - fspl

d_est = rssi_to_distance(rssi_pred, freq_hz, power_w, 0.0, Environment.OUTDOOR_LOS)
assert abs(d_est - d_true) < 1., "round-trip must recover true distance"

# Indoor: 15 dB extra loss, INDOOR_LIGHT (extra_loss=15) corrects it
d_indoor = rssi_to_distance(rssi_pred - 15., freq_hz, power_w, 0.0, Environment.INDOOR_LIGHT)
assert abs(d_indoor - d_true) < 100., "indoor correction must approximately recover"

# Frequency effect: higher freq = more FSPL = shorter apparent distance at same RSSI
d_gsm = rssi_to_distance(rssi_pred, 948e6, power_w, 0.0, Environment.OUTDOOR_LOS)
assert d_gsm < d_true, "GSM higher freq must imply shorter apparent distance"
```

---

### Checkpoint 3 — Trilateration solver

Implement the Nelder-Mead solver and geometry check. Test with synthetic data at a known position.

```python
import numpy as np
from sdr_positioning.trilateration import trilaterate_solver, geometry_ok

true_px, true_py = 500., 300.
source_positions = [(0., 0.), (1000., 0.), (1000., 1000.), (0., 1000.)]
np.random.seed(42)
sources = []
for sx, sy in source_positions:
    d = np.sqrt((true_px - sx)**2 + (true_py - sy)**2)
    sources.append({"x": sx, "y": sy, "d_est": d + np.random.normal(0, d * 0.1)})

(px, py), residual = trilaterate_solver(sources)
error = np.sqrt((px - true_px)**2 + (py - true_py)**2)
assert error < 100., f"position error {error:.1f}m must be under 100m"
assert residual >= 0.

# Poor geometry — sources clustered in one direction
bad = [
    {"x": 900., "y": 250., "d_est": 400.},
    {"x": 950., "y": 300., "d_est": 450.},
    {"x": 980., "y": 310., "d_est": 490.},
]
assert not geometry_ok(bad, px, py), "clustered sources must fail geometry check"
```

---

### Checkpoint 4 — AGC and SDRModule with mock hardware

Test `GainController` normalisation and the full `scan()` pipeline.

```python
from sdr_positioning.sdr_module.agc import GainController
from sdr_positioning.sdr_module.catalogue import CatalogueLoader

class MockReceiver:
    TRUE_POWER = -65.   # signal is -65 dBm at antenna
    def __init__(self): self.gain = 0.
    def set_gain(self, g): self.gain = g
    def set_freq(self, f): pass
    def set_sample_rate(self, r): pass
    def read_power_dbm(self, n=4096): return self.TRUE_POWER + self.gain

entry = CatalogueLoader().load("sdr_positioning/stations.json")[0]
agc   = GainController()
m     = agc.measure(entry, MockReceiver())

assert m is not None
assert abs(m.rssi_dbm - (-65.)) < 0.5, "normalised RSSI must equal true power"
assert m.lat == entry.lat and m.lon == entry.lon, "transmitter coords must pass through"

# Saturation — signal too strong
class StrongRx(MockReceiver): TRUE_POWER = -10.
assert agc.measure(entry, StrongRx()) is None

# Noise floor — signal too weak
class WeakRx(MockReceiver): TRUE_POWER = -95.
assert agc.measure(entry, WeakRx()) is None
```

---

### Checkpoint 5 — KalmanFilter

Test predict, update, and outlier rejection.

```python
import numpy as np
from sdr_positioning.kalman import KalmanFilter

kf = KalmanFilter(sigma_a=0.1)
kf.x = np.array([100., 200., 0., 0.])
kf.P = np.diag([100., 100., 25., 25.])

# Static: zero accel, position must not drift
for _ in range(100):
    kf.predict(0., 0., 0.02)
assert abs(kf.x[0] - 100.) < 1e-6
assert kf.P[0, 0] > 100., "uncertainty must grow without updates"

# Update pulls state toward measurement
kf.x = np.zeros(4)
kf.P = np.diag([500., 500., 25., 25.])
ok = kf.update(px_rf=50., py_rf=50., accuracy_m=30.)
assert ok
assert kf.x[0] > 0. and kf.x[1] > 0.
assert kf.P[0, 0] < 500., "uncertainty must shrink after update"

# Outlier rejected — tight P, wildly inconsistent fix
kf.P = np.diag([10., 10., 1., 1.])
assert not kf.update(9999., 9999., 30.), "outlier must be rejected"
```

---

### Checkpoint 6 — Full integration smoke test

Wire all components through `PositioningSystem`. Uses mock hardware throughout.

```python
from unittest.mock import patch, MagicMock
from sdr_positioning import PositioningSystem

with patch("sdr_positioning.sdr_module.receiver.SoapySDR") as mock_soapy:
    mock_soapy.Device.return_value = MagicMock()

    ps = PositioningSystem("sdr_positioning/stations.json")
    ps._engine.sdr.receiver.read_power_dbm = lambda n=4096: -60.
    ps._engine.sdr.receiver.set_gain = lambda g: None
    ps._engine.sdr.receiver.set_freq = lambda f: None
    ps._engine.sdr.receiver.set_sample_rate = lambda r: None

    # Before first fix — no estimate
    assert ps.step() is None

    # IMU feed must not crash
    ps.feed_imu(ax=0.1, ay=0., heading_deg=45., dt=0.02)
    result = ps.step()
    if result is not None:
        assert result.accuracy_m > 0.
        assert -90 < result.lat < 90
        assert -180 < result.lon < 180

    ps.close()
    print("Integration smoke test: OK")
```

---

## 11. Extension Points

| Extension | Where and how |
|---|---|
| New signal type | Add to `GAIN_STRATEGIES` in `agc.py` + `TYPE_DEFAULTS` in `catalogue.py`. Zero other changes. |
| New hardware | Implement `SDRReceiver` interface. Pass `driver=` to `SDRModule`. |
| Phone LTE cells (v2) | Add `PhoneCellSource` to `sdr_module/` implementing same `Measurement` output. `FusionEngine` polls it transparently. |
| VOR bearing (EKF) | Add `kf.update_bearing()` to `kalman.py`. `trilateration.py` unchanged. |
| GPS cross-check | Add `GPSValidator` to `fusion.py`. Never integrates GPS — compares against `kf.x` and emits spoofing flag. |
| REST API output | Wrap `PositioningSystem` in FastAPI. `PositionEstimate` is a dataclass — `json.dumps(asdict(estimate))`. |

---

## 12. Design Rules

- SoapySDR imported in exactly one file: `receiver.py`
- `true_power = measured - gain` appears exactly once: `GainController.measure()`
- FSPL formula appears exactly once: `rssi_to_distance()` in `trilateration.py`
- `CatalogueEntry` is frozen — immutable after load
- `Measurement.rssi_dbm` is always normalised before leaving `sdr_module/`
- Adding a signal type requires editing two dicts, zero if-else chains
- `scan()` never raises — returns empty list if no signals found
- `trilaterate()` never raises — returns `None` if insufficient sources
- `step()` never raises — returns `None` if no fix available yet
