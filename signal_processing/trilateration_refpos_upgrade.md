# SDR Positioning System
## Trilateration Upgrade — Reference Position Integration

Upgrade instructions for trilateration.py | 2026-03-28

---

## 1. What This Upgrade Does

The existing `trilaterate()` function works without any prior position knowledge. This upgrade adds an optional `ref_pos` argument — a tuple of `(px, py)` in local ENU metres already computed by the operations loop — that improves accuracy in four ways when provided.

| Improvement | What it does |
|---|---|
| Source pre-selection | Skip sources too far to be useful at ref_pos. Shorter scan, less noise. |
| RTK n calibration | Solve for the real path loss exponent n using geometric distances from ref_pos. Replaces the fixed environment default. |
| Per-source outlier flag | Sources whose implied distance badly disagrees with the geometric distance from ref_pos get half weight. Not discarded. |
| Solver seed | Nelder-Mead starts at ref_pos instead of the source centroid. Fewer iterations, less risk of local minima. |

> When `ref_pos` is `None` all four improvements are skipped. Behaviour is identical to the existing implementation. No existing tests should break.

---

## 2. What Is Already Available

The following already exist in `sdr_positioning` and must be used directly. Do not reimplement them in `trilateration.py`.

| Already exists in sdr_positioning | Used here for |
|---|---|
| `latlon_to_enu(lat, lon, lat0, lon0) -> (px, py)` | Converting source lat/lon to ENU for geometric distance computation |
| `enu_to_latlon(px, py, lat0, lon0) -> (lat, lon)` | Converting solver output back to lat/lon for RFMeasurement |
| `Measurement` dataclass | Input — already carries freq_hz, power_w, antenna_gain_dbi, lat, lon, rssi_dbm |
| `RFMeasurement` dataclass | Output — lat, lon, accuracy_m, n_sources |
| `Environment` enum + `extra_loss_db` | Already used in `rssi_to_distance()` |

> **TODO for the implementer:** `trilaterate()` already uses `latlon_to_enu()` internally to convert source positions into ENU for the solver. Verify this is already in place before adding the `ref_pos` steps. If it is not, add it first as a prerequisite — convert each source lat/lon to `(x, y)` ENU using the shared origin, store as `source.x` and `source.y`. This is what the `ref_pos` comparisons below rely on.

---

## 3. Signature Change

One new optional argument. Everything else stays the same.

```python
# Before
def trilaterate(measurements, environment, origin) -> RFMeasurement | None

# After
def trilaterate(measurements, environment, origin,
                ref_pos=None) -> RFMeasurement | None
#
# ref_pos: tuple[float, float] | None
# ref_pos is (px, py) in local ENU metres
# — the same frame as kf.x[0], kf.x[1] in the operations loop
```

`FusionEngine` passes `ref_pos` from the Kalman state on every triggered scan after initialisation:

```python
ref_pos = (self.kf.x[0], self.kf.x[1])
result  = trilaterate(measurements, environment, self.origin, ref_pos=ref_pos)
```

On the very first scan (before initialisation), `FusionEngine` passes `ref_pos=None`.

---

## 4. Accuracy Stability Across Cycles

A key concern: the reference position used for calibration comes from the previous cycle's fix. If a bad fix calibrates a bad `n`, and that bad `n` produces another bad fix, the system can drift. Two rules prevent this.

### 4.1 Clamp n to physical bounds

The calibrated `n` must always be clamped, regardless of what the data suggests:

```python
n_calibrated = max(1.5, min(5.0, n_raw))
```

`n < 1.5` is physically impossible (better than free space). `n > 5.0` occurs only in extreme multipath and likely indicates a bad measurement. Clamping prevents a single bad RSSI from driving the model into nonsense.

### 4.2 Only calibrate when ref_pos is credible

The Kalman filter provides the accuracy of the reference position via the Kalman covariance. `FusionEngine` should only pass `ref_pos` when the current Kalman accuracy is below a useful threshold:

```python
# In FusionEngine._rf_scan():
kf_accuracy = sqrt((self.kf.P[0,0] + self.kf.P[1,1]) / 2)
ref_pos = (self.kf.x[0], self.kf.x[1]) if kf_accuracy < 300. else None
```

300 m is a generous threshold. If Kalman accuracy is worse than 300 m, the reference position is too uncertain to calibrate against — fall back to `ref_pos=None` and let the solver run without guidance. After a good RF fix the accuracy will be well within this limit for the next cycle.

> The 300 m threshold means: after a long IMU-only dropout where position uncertainty has grown large, the next RF scan runs without `ref_pos` assistance. This is correct — a degraded reference would corrupt the calibration. Once the scan produces a new fix, accuracy drops back down and subsequent cycles use `ref_pos` normally.

### 4.3 Accuracy does not drift between cycles

With these two rules in place, each RF scan cycle is independent of the previous calibration. The `n` used for one scan does not persist to the next — it is re-solved fresh from the current RSSI measurements and the current `ref_pos`. A bad `n` in cycle K cannot corrupt cycle K+1 because cycle K+1 re-derives `n` from scratch.

The only persistent state between cycles is the Kalman filter state (position and velocity), which is self-correcting by design — the Kalman update always pulls the state toward the measurement, bounded by the innovation gate.

---

## 5. Implementation — Four Steps

Add these four blocks inside `trilaterate()`, in order, after the quality filter and before the solver call. Each block is gated on `ref_pos is not None` and is fully self-contained.

### Step A — Source Pre-selection

Drop sources whose expected signal at `ref_pos` is clearly below the noise floor. Never drop below 3 sources total.

```python
if ref_pos is not None:
    kept = []
    for s in located:
        # s.x, s.y already in ENU — from latlon_to_enu() earlier in function
        d_geo = max(sqrt((ref_pos[0]-s.x)**2 + (ref_pos[1]-s.y)**2), 1.)
        fspl  = 20*log10(d_geo) + 20*log10(s.freq_hz) - 147.55
        rssi_expected = s.eirp_dbm - fspl
        if rssi_expected >= s.min_rssi_dbm - 5.:
            kept.append(s)
    if len(kept) >= 3:
        located = kept
    # else: keep original list — never drop below 3 sources
```

### Step B — RTK Path Loss Calibration

Solve for `n` per source using the geometric distance from `ref_pos`. Average. Clamp.

```python
n_calibrated = 2.0  # default if ref_pos absent or calibration fails
if ref_pos is not None:
    ns = []
    for s in located:
        d_geo = max(sqrt((ref_pos[0]-s.x)**2 + (ref_pos[1]-s.y)**2), 1.)
        path_loss = s.eirp_dbm - s.rssi_dbm - environment.extra_loss_db
        fspl_1m   = 20*log10(s.freq_hz) - 147.55  # FSPL at d=1m
        if path_loss > fspl_1m and d_geo > 10.:
            n = (path_loss - fspl_1m) / (10 * log10(d_geo))
            ns.append(max(1.5, min(5.0, n)))  # clamp immediately
    if len(ns) >= 2:  # require at least 2 sources to trust calibration
        n_calibrated = sum(ns) / len(ns)
```

Pass `n_calibrated` into `rssi_to_distance()` for all sources in this scan cycle.

### Step C — Per-source Outlier Flag

After calibration, reduce weight of sources that disagree badly with the reference geometry. Do not discard.

```python
if ref_pos is not None:
    for s in located:
        d_geo  = max(sqrt((ref_pos[0]-s.x)**2 + (ref_pos[1]-s.y)**2), 1.)
        delta  = abs(s.d_est - d_geo)
        if delta > 0.3 * d_geo:
            s.weight *= 0.5
```

> 30% threshold is deliberately loose. The solver handles residual noise. This only catches gross outliers — multipath, obstructions, a far-off source whose AGC measurement was unreliable.

### Step D — Solver Seed

Seed Nelder-Mead at `ref_pos` when available, source centroid otherwise.

```python
x0 = list(ref_pos) if ref_pos is not None else \
     [mean(s.x for s in located), mean(s.y for s in located)]

result = minimize(objective, x0, method="Nelder-Mead",
                  args=(located,),
                  options={"xatol": 1.0, "fatol": 1.0, "maxiter": 500})
```

---

## 6. Checkpoints

---

### Checkpoint 1 — latlon_to_enu already used inside trilaterate()

Prerequisite check — verify source positions are already being converted to ENU before adding `ref_pos` steps. If not, this must be fixed first.

```python
# Inspect trilaterate() source — look for latlon_to_enu() calls
# Each located source must have .x and .y attributes in ENU metres
# before the solver loop runs.

# Quick verification:
import inspect, sdr_positioning.trilateration as t
src = inspect.getsource(t.trilaterate)
assert "latlon_to_enu" in src, "TODO: add ENU conversion before proceeding"
```

---

### Checkpoint 2 — ref_pos=None gives same result as before

```python
fix_none = trilaterate(measurements, env, origin, ref_pos=None)
fix_base = trilaterate(measurements, env, origin)  # old signature

# Both must return a fix or both None
assert (fix_none is None) == (fix_base is None)
if fix_none:
    assert abs(fix_none.lat - fix_base.lat) < 1e-6
    assert abs(fix_none.lon - fix_base.lon) < 1e-6
```

---

### Checkpoint 3 — RTK calibration improves accuracy vs wrong environment class

True environment is n=3.5 (indoor). Environment class is `OUTDOOR_LOS` (n=2.0). With a good `ref_pos`, calibration should recover `n` closer to 3.5 and produce a more accurate fix.

```python
true_px, true_py = 300., 400.
sources = make_sources_with_n(true_px, true_py, n=3.5)

fix_no_ref = trilaterate(sources, OUTDOOR_LOS, origin, ref_pos=None)
fix_ref    = trilaterate(sources, OUTDOOR_LOS, origin,
                         ref_pos=(true_px, true_py))

err_no_ref = dist_m(fix_no_ref, true_px, true_py)
err_ref    = dist_m(fix_ref,    true_px, true_py)
assert err_ref < err_no_ref, "ref_pos must improve indoor accuracy"
```

---

### Checkpoint 4 — Bad ref_pos (accuracy > 300 m) falls back to ref_pos=None

Tests the `FusionEngine` threshold logic — not `trilaterate()` itself.

```python
import numpy as np
engine = FusionEngine(sdr, sigma_a=0.1)
engine._initialised = True
engine.kf.P = np.diag([310.**2, 310.**2, 25., 25.])  # >300m accuracy
engine.kf.x = np.array([100., 200., 0., 0.])

# Patch trilaterate to capture what ref_pos was passed
captured = {}
def mock_trilat(meas, env, origin, ref_pos=None):
    captured["ref_pos"] = ref_pos
    return None

with patch("fusion.trilaterate", mock_trilat):
    engine._rf_scan(ref_pos=(100., 200.))
assert captured["ref_pos"] is None, "degraded Kalman must pass ref_pos=None"
```

---

### Checkpoint 5 — n is clamped — bad RSSI cannot produce extreme n

```python
# Synthesise a source with absurd RSSI (near-field bleed)
# that would compute n < 1.5 or n > 5.0 without clamping
sources_extreme = make_sources_extreme_rssi()
fix = trilaterate(sources_extreme, OUTDOOR_LOS, origin,
                  ref_pos=(true_px, true_py))
# Should not raise, should not return None
assert fix is not None, "clamped n must still produce a fix"
assert fix.accuracy_m < 5000., "accuracy must be finite and sane"
```

---

## 7. What Not to Change

- The FSPL formula — `rssi_to_distance()` is untouched
- The environment class `extra_loss_db` — still applied before n calibration
- The geometry check (GDOP) — runs after the solver regardless of `ref_pos`
- The `accuracy_m` formula — `sqrt(residual / N)` unchanged
- The `RFMeasurement` return type — unchanged
- `latlon_to_enu` / `enu_to_latlon` — already in `sdr_positioning`, import from there
