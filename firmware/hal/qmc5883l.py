"""QMC5883L magnetometer RotationReader — returns azimuth from I2C on Raspberry Pi.

Wiring (Pi 5 GPIO header):
    GY-271 VCC → Pin 1 (3.3V)
    GY-271 GND → Pin 6 (GND)
    GY-271 SDA → Pin 3 (GPIO 2, I2C1 SDA)
    GY-271 SCL → Pin 5 (GPIO 3, I2C1 SCL)

Requires: smbus2  (pre-installed on Raspberry Pi OS)
"""

from __future__ import annotations

import math
import time
import logging

_logger = logging.getLogger(__name__)

_ADDRESS = 0x0D
_I2C_BUS = 1

# Registers
_REG_DATA_START = 0x00  # X_LSB; 6 consecutive bytes: XL XH YL YH ZL ZH
_REG_STATUS = 0x06
_REG_CONTROL_1 = 0x09
_REG_CONTROL_2 = 0x0A
_REG_RST_PERIOD = 0x0B
_REG_CHIP_ID = 0x0D

# Control flags
_SOFT_RST = 0x80
_INT_ENB = 0x01
_MODE_CONT = 0x01
_ODR_50HZ = 0x04
_RNG_8G = 0x10
_OSR_512 = 0x00

_STAT_DRDY = 0x01


def _to_signed_16(lsb: int, msb: int) -> int:
    val = (msb << 8) | lsb
    if val >= 0x8000:
        val -= 0x10000
    return val


class QMC5883LRotationReader:
    """RotationReader backed by a QMC5883L magnetometer over I2C.

    Convention: azimuth is degrees clockwise from magnetic north, 0-360.
    The sensor must be mounted level (Z axis pointing up).
    """

    def __init__(self, bus: int = _I2C_BUS, address: int = _ADDRESS) -> None:
        import smbus2
        self._address = address
        self._bus = smbus2.SMBus(bus)
        self._init_sensor()

    def _init_sensor(self) -> None:
        self._bus.write_byte_data(self._address, _REG_CONTROL_2, _SOFT_RST)
        time.sleep(0.05)
        self._bus.write_byte_data(self._address, _REG_CONTROL_2, _INT_ENB)
        self._bus.write_byte_data(self._address, _REG_RST_PERIOD, 0x01)
        ctrl1 = _MODE_CONT | _ODR_50HZ | _RNG_8G | _OSR_512
        self._bus.write_byte_data(self._address, _REG_CONTROL_1, ctrl1)
        time.sleep(0.05)
        _logger.info("QMC5883L initialised at 0x%02x (50 Hz, 8G, OSR 512)", self._address)

    def _read_raw(self) -> tuple[int, int, int]:
        """Wait for data ready, then read X, Y, Z as signed 16-bit."""
        for _ in range(50):
            status = self._bus.read_byte_data(self._address, _REG_STATUS)
            if status & _STAT_DRDY:
                break
            time.sleep(0.005)
        data = self._bus.read_i2c_block_data(self._address, _REG_DATA_START, 6)
        x = _to_signed_16(data[0], data[1])
        y = _to_signed_16(data[2], data[3])
        z = _to_signed_16(data[4], data[5])
        return x, y, z

    def read_azimuth(self) -> float:
        """Return heading in degrees, clockwise from magnetic north, 0-360."""
        x, y, _z = self._read_raw()
        heading = math.degrees(math.atan2(y, x))
        if heading < 0:
            heading += 360.0
        return heading

    def close(self) -> None:
        self._bus.close()
