from __future__ import annotations

from pathlib import Path

from .models import PositionEstimate

# Bundled station catalogue shipped with the package
DEFAULT_CATALOGUE = Path(__file__).parent / "stations.json"


class PositioningSystem:
    """High-level facade for passive RF localisation.

    Usage::

        from signal_processing.sdr_positioning import PositioningSystem
        ps = PositioningSystem("stations.json")   # or pass DEFAULT_CATALOGUE
        ps.feed_imu(ax=0.0, ay=0.0, heading_deg=90.0, dt=0.1)
        estimate = ps.step()
        ps.close()
    """

    def __init__(
        self,
        catalogue_path: str | Path = DEFAULT_CATALOGUE,
        driver: str = "sdrplay",
        serial: str | None = None,
        sigma_a: float = 0.1,
        origin: tuple[float, float] | None = None,
    ) -> None:
        from .fusion import FusionEngine
        from .sdr_module import SDRModule

        sdr = SDRModule(catalogue_path, driver=driver, serial=serial)
        self._engine = FusionEngine(sdr, sigma_a, origin=origin)

    def feed_imu(
        self,
        ax: float,
        ay: float,
        heading_deg: float,
        dt: float,
    ) -> None:
        """Forward IMU reading to the Kalman predict step."""
        self._engine.feed_imu(ax, ay, heading_deg, dt)

    def step(self) -> PositionEstimate | None:
        """Run one SDR scan and return the fused position, or None before first fix."""
        return self._engine.step()

    def close(self) -> None:
        """Release the SDR hardware."""
        self._engine._sdr.close()


__all__ = ["PositioningSystem", "DEFAULT_CATALOGUE", "PositionEstimate"]
