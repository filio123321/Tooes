"""MPU-6050 accelerometer TiltReader — returns pitch/roll for tilt compensation.

Wiring (shares the same I2C bus as the GY-271 magnetometer):
    MPU-6050 VCC → Pin 1 (3.3V)
    MPU-6050 GND → Pin 6 (GND)
    MPU-6050 SDA → Pin 3 (GPIO 2, I2C1 SDA)
    MPU-6050 SCL → Pin 5 (GPIO 3, I2C1 SCL)

I2C address: 0x68 (default, AD0 low) or 0x69 (AD0 high).
Requires: smbus2
"""

from __future__ import annotations

import math
import time
import logging

_logger = logging.getLogger(__name__)

_ADDRESS = 0x68
_I2C_BUS = 1

# Registers
_REG_PWR_MGMT_1 = 0x6B
_REG_ACCEL_XOUT_H = 0x3B  # 6 bytes: XH XL YH YL ZH ZL
_REG_WHO_AM_I = 0x75

_ACCEL_SCALE_2G = 16384.0  # LSB/g at ±2g (default range)


def _to_signed_16(msb: int, lsb: int) -> int:
    val = (msb << 8) | lsb
    if val >= 0x8000:
        val -= 0x10000
    return val


class MPU6050TiltReader:
    """TiltReader backed by MPU-6050 accelerometer over I2C.

    Returns pitch and roll in degrees derived from the gravity vector.
    Pitch: rotation around the Y axis (nose up/down).
    Roll: rotation around the X axis (wing tilt).
    """

    def __init__(self, bus: int = _I2C_BUS, address: int = _ADDRESS) -> None:
        import smbus2
        self._address = address
        self._bus = smbus2.SMBus(bus)
        self._wake()

    def _wake(self) -> None:
        who = self._bus.read_byte_data(self._address, _REG_WHO_AM_I)
        if who not in (0x68, 0x70, 0x72, 0x73, 0x75):
            _logger.warning("MPU-6050 WHO_AM_I returned 0x%02x (unexpected)", who)
        # Clear sleep bit (bit 6 of PWR_MGMT_1) to wake the sensor.
        self._bus.write_byte_data(self._address, _REG_PWR_MGMT_1, 0x00)
        time.sleep(0.05)
        _logger.info("MPU-6050 awake at 0x%02x", self._address)

    def _read_accel_raw(self) -> tuple[int, int, int]:
        data = self._bus.read_i2c_block_data(self._address, _REG_ACCEL_XOUT_H, 6)
        ax = _to_signed_16(data[0], data[1])
        ay = _to_signed_16(data[2], data[3])
        az = _to_signed_16(data[4], data[5])
        return ax, ay, az

    def read_accel_g(self) -> tuple[float, float, float]:
        """Return (ax, ay, az) in g-force units (1.0 ≈ 9.81 m/s²)."""
        ax, ay, az = self._read_accel_raw()
        return ax / _ACCEL_SCALE_2G, ay / _ACCEL_SCALE_2G, az / _ACCEL_SCALE_2G

    def read_pitch_roll(self) -> tuple[float, float]:
        """Return (pitch, roll) in degrees from the accelerometer gravity vector."""
        ax, ay, az = self._read_accel_raw()
        gx = ax / _ACCEL_SCALE_2G
        gy = ay / _ACCEL_SCALE_2G
        gz = az / _ACCEL_SCALE_2G

        pitch = math.degrees(math.atan2(-gx, math.sqrt(gy * gy + gz * gz)))
        roll = math.degrees(math.atan2(gy, gz))
        return pitch, roll

    def close(self) -> None:
        self._bus.close()
