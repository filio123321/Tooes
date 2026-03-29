# Motion Estimation

This document describes the motion-estimation code that exists in the repo
today. It is intentionally precise about what is implemented and what is not.

## Scope

The current motion stack uses:

- the MPU-6050 accelerometer
- the QMC5883L magnetometer
- optional accelerometer-derived tilt compensation
- a simple dead-reckoning loop with zero-velocity updates

The current motion stack does not use:

- the MPU-6050 gyroscope
- complementary filtering
- Kalman filtering
- Madgwick or Mahony fusion
- quaternion attitude estimation

If the documentation says "accelerometer + gyroscope" at this stage, that would
be inaccurate.

## Inputs And Units

The HAL protocols define the units used by the motion code:

- `AccelerationReader.read_accel_g()`
  - returns `(ax, ay, az)` in `g`
  - `1.0 g` is approximately `9.81 m/s^2`
- `TiltReader.read_pitch_roll()`
  - returns `(pitch, roll)` in degrees
- `RotationReader.read_azimuth()`
  - returns heading in degrees clockwise from north

The dead-reckoning output is:

- `(x, y)` in meters
- relative to the start point
- in a local 2D frame, not in latitude/longitude

## 1. Accelerometer Processing With The MPU-6050

The MPU-6050 code in `firmware/hal/mpu6050.py` reads six accelerometer bytes
from the standard register block beginning at `0x3B`.

### Raw Conversion

Each axis is reconstructed as a signed 16-bit integer:

```text
raw = (msb << 8) | lsb
if raw >= 0x8000:
    raw -= 0x10000
```

The code assumes the default +/-2 g range, so the scale factor is:

```text
ACCEL_SCALE = 16384 LSB / g
```

Converted values are:

```text
g_x = raw_x / 16384
g_y = raw_y / 16384
g_z = raw_z / 16384
```

This is a fixed-range assumption. If the sensor range were changed at the
register level, the math in the current implementation would become incorrect
unless the scale factor were updated too.

### Tilt Estimation

The current tilt estimator derives pitch and roll from the gravity vector:

```text
pitch = atan2(-g_x, sqrt(g_y^2 + g_z^2))
roll  = atan2(g_y, g_z)
```

The implementation returns the angles in degrees.

Interpretation:

- pitch is rotation around the device Y axis
- roll is rotation around the device X axis

Important caveat:

- this is accelerometer-derived tilt only
- fast motion corrupts the gravity estimate
- no gyroscope smoothing or dynamic fusion is applied

## 2. Magnetometer Heading With Optional Tilt Compensation

The QMC5883L code in `firmware/hal/qmc5883l.py` reads the magnetic field vector
`(mx, my, mz)` over I2C.

### Heading Without Tilt Compensation

If no `TiltReader` is supplied, the code computes:

```text
heading = atan2(my, mx)
```

The result is converted to degrees and normalized to the range `[0, 360)`.

### Heading With Tilt Compensation

If a `TiltReader` is supplied, the raw magnetic vector is projected onto the
horizontal plane before the azimuth is computed.

Let:

- `p = pitch` in radians
- `r = roll` in radians

Then the code computes:

```text
x_h = mx cos(p) + my sin(r) sin(p) + mz cos(r) sin(p)
y_h = my cos(r) - mz sin(r)
heading = atan2(y_h, x_h)
```

The heading is then normalized to `[0, 360)`.

### What That Heading Means

The current heading is:

- magnetic heading
- not corrected for magnetic declination
- sensitive to hard-iron and soft-iron distortion
- dependent on the physical mounting of the sensor

So the correct interpretation is:

"clockwise angle from magnetic north, according to the current uncalibrated
sensor frame."

## 3. Dead-Reckoning Loop

The dead-reckoning code lives in `firmware/hal/dead_reckoning.py`.

Its update loop is:

1. read acceleration `(ax, ay, az)` in `g`
2. read heading `h` in degrees
3. detect stillness using a zero-velocity rule
4. if moving, rotate horizontal acceleration into a world frame
5. integrate acceleration into velocity
6. integrate velocity into position

### Stillness Detection (ZUPT)

The code computes acceleration magnitude:

```text
|a| = sqrt(ax^2 + ay^2 + az^2)
```

It then checks:

```text
abs(|a| - 1.0) < 0.06
```

If that condition is true, the tracker assumes the device is stationary and
forces:

```text
vx = 0
vy = 0
```

This is a simple zero-velocity update, or ZUPT.

### Horizontal Acceleration

If the stillness test fails, the current implementation does:

```text
lin_x = ax * 9.81
lin_y = ay * 9.81
```

This is an important point of truthfulness:

- the code comment says it "removes gravity"
- but it does not subtract a fully rotated gravity vector
- it simply treats the accelerometer X and Y channels as horizontal linear
  acceleration after the stillness check

That means the approximation is only reasonable when the device stays roughly
level.

### Rotation Into The World Frame

With heading `h` in radians:

```text
world_ax = lin_x cos(h) - lin_y sin(h)
world_ay = lin_x sin(h) + lin_y cos(h)
```

This converts the body-frame horizontal acceleration into the local world frame
used by the tracker.

### Integration

The tracker uses first-order Euler integration with wall-clock `dt`:

```text
vx = vx + world_ax * dt
vy = vy + world_ay * dt

x = x + vx * dt
y = y + vy * dt
```

This is simple and easy to reason about, but it is also drift-prone.

## 4. Measurement Trigger In The Walking POC

The walking POC in `firmware/scripts/sweep_poc.py` uses the dead-reckoned
position to decide when to collect a new cell measurement.

If the operator has moved at least:

```text
HAL_TRIGGER_DISTANCE
```

meters from the previous measurement, the code triggers `cell_reader.read_cells()`.

The default threshold is `2.0` meters.

This means the motion estimator is not just visualization support. It actively
controls the spatial sampling of the POC measurements.

## 5. Coordinate Frames

One thing the repo does not yet define rigorously is the full physical
coordinate-frame convention.

What is clear:

- the tracker maintains a 2D local `(x, y)` frame
- heading is used to rotate horizontal acceleration into that frame
- the frame origin is the start point

What is not yet formalized:

- which physical device axis is "forward"
- which world axis should be interpreted as north or east
- how mounting offsets should be calibrated

The current implementation therefore works best as:

- a relative motion estimate for a controlled prototype
- a trigger source for collecting spaced measurements

It should not be described as a fully specified inertial navigation frame.

## 6. Limitations

The main limitations of the current motion estimator are:

- no gyroscope integration
- no fusion algorithm
- no explicit gravity-vector subtraction in an arbitrary orientation
- no accelerometer bias calibration
- no magnetometer hard-iron or soft-iron calibration
- no magnetic declination correction
- simple ZUPT based only on acceleration magnitude
- Euler integration with variable wall-clock `dt`

These are not documentation problems; they are real algorithmic limitations in
the present code, and the documentation should say so plainly.

## 7. Upgrade Path

A future IMU-navigation version of the project would likely introduce these
steps:

1. read and bias-correct gyroscope data from the MPU-6050
2. estimate attitude with a complementary filter, Mahony, Madgwick, or EKF
3. rotate body-frame acceleration into the world frame with that attitude
4. subtract gravity in the world frame, not by the current "roughly level"
   shortcut
5. use a stronger stillness detector that also considers gyro motion and window
   statistics
6. calibrate magnetometer offsets and optionally apply declination

Until that work lands, the honest description is:

"prototype dead reckoning from accelerometer data and magnetic heading."
