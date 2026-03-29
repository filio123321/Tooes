import sys
import logging
import dataclasses
from pathlib import Path
from typing import Optional, Union

if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firmware.log_config import configure_logging
from firmware.tower_data import iter_catalog_towers


@dataclasses.dataclass
class TowerCoordinatesDict:
    """Float lat, lng pair."""
    lat: float
    lon: float


_logger = logging.getLogger(__name__)
_warned_missing_csvs: set[Path] = set()


def lookup_tower(
    mcc: Union[int, str], 
    mnc: Union[int, str], 
    lac: Union[int, str], 
    cell_id: Union[int, str]
) -> Optional[TowerCoordinatesDict]:
    """
    Looks up a cell tower's geographical coordinates in a local OpenCelliD CSV database.

    The function searches for a CSV file named after the Mobile Country Code (MCC) 
    within a 'data' directory located in the same folder as this script.

    Args:
        mcc: Mobile Country Code (e.g., 284 for Bulgaria Vivacom).
        mnc: Mobile Network Code (e.g., 70).
        lac: Location Area Code (e.g., 8240).
        cell_id: Cell Identity (e.g., 4521).

    Returns:
        A dataclass containing 'lat' and 'lon' as floats if the tower is found.
        Returns None if the tower is not present in the database.

    """
    # Resolve paths relative to the script location
    script_dir = Path(__file__).resolve().parent
    data_dir = script_dir / "data"
    csv_path = data_dir / f"{mcc}.csv"

    # Strict check for database existence
    if not csv_path.exists():
        if csv_path not in _warned_missing_csvs:
            _warned_missing_csvs.add(csv_path)
            _logger.warning(
                "OpenCelliD database for MCC %s not found at %s; tower coordinates will be unavailable until that CSV is added.",
                mcc,
                csv_path,
            )
        return None

    try:
        # Convert search parameters to integers once for efficient comparison
        target_mcc = int(mcc)
        target_mnc = int(mnc)
        target_lac = int(lac)
        target_cell = int(cell_id)

        for tower in iter_catalog_towers(csv_path):
            if (
                tower.mcc == target_mcc
                and tower.net == target_mnc
                and tower.area == target_lac
                and tower.cell == target_cell
            ):
                return TowerCoordinatesDict(lat=tower.lat, lon=tower.lon)

    except PermissionError:
        _logger.warning("Permission denied when accessing OpenCelliD database %s", csv_path)
        return None
    except Exception as e:
        _logger.warning("Unexpected error reading OpenCelliD database %s: %s", csv_path, e)
        return None

    return None


def _test_poc():
    configure_logging()

    # Example evaluation with some random sample data from the SDR demodulator
    # (Cell #4521, LAC #8240, MCC 389, MNC 70)
    test_params = {
        "mcc": 284,
        "mnc": 3,
        "lac": 3400,
        "cell_id": 15023
    }

    print(f"--- OpenCelliD Lookup Evaluation ---")
    print(f"Target: MCC={test_params['mcc']}, MNC={test_params['mnc']}, "
          f"LAC={test_params['lac']}, CellID={test_params['cell_id']}")

    result = lookup_tower(**test_params)
    
    if result:
        print(f"RESULT: Found at Latitude {result.lat}, Longitude {result.lon}")
    else:
        print("RESULT: Tower not found in the local database.")


if __name__ == "__main__":
    _test_poc()
