import csv
import sys
import logging
import dataclasses
from pathlib import Path
from typing import Optional, Dict, Type, Union

if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firmware.log_config import configure_logging


@dataclasses.dataclass
class TowerCoordinatesDict:
    """Float lat, lng pair."""
    lat: float
    lon: float


_logger = logging.getLogger(__name__)


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

    Raises:
        SystemExit: If the required CSV database file is missing or unreadable.
    """
    # Resolve paths relative to the script location
    script_dir = Path(__file__).resolve().parent
    data_dir = script_dir / "data"
    csv_path = data_dir / f"{mcc}.csv"

    # Strict check for database existence
    if not csv_path.exists():
        error_msg = (
            f"\nCRITICAL ERROR: Database file for country code {mcc} not found.\n"
            f"Expected location: {csv_path}\n"
            "Please download the OpenCelliD CSV for this MCC from https://opencellid.org/"
        )
        print(error_msg, file=sys.stderr)
        sys.exit(1)

    try:
        # Convert search parameters to integers once for efficient comparison
        target_mcc = int(mcc)
        target_mnc = int(mnc)
        target_lac = int(lac)
        target_cell = int(cell_id)

        with open(csv_path, mode='r', encoding='utf-8', newline='') as f:
            # OpenCelliD CSVs typically have headers. net=mnc, area=lac, cell=cell_id
            reader = csv.DictReader(f)
            
            for row in reader:
                try:
                    # Validate row content before comparison
                    if (int(row['mcc']) == target_mcc and
                        int(row['net']) == target_mnc and
                        int(row['area']) == target_lac and
                        int(row['cell']) == target_cell):
                        
                        return TowerCoordinatesDict(
                            lat=float(row['lat']),
                            lon=float(row['lon'])
                        )
                except (ValueError, KeyError, TypeError):
                    # Silently skip rows with missing columns or non-numeric data
                    continue

    except PermissionError:
        print(f"CRITICAL ERROR: Permission denied when accessing {csv_path}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"CRITICAL ERROR: Unexpected error reading database: {e}", file=sys.stderr)
        sys.exit(1)

    return None


def _test_poc():
    configure_logging()

    # Example evaluation with some random sample data from the SDR demodulator
    # (Cell #4521, LAC #8240, MCC 389, MNC 70)
    test_params = {
        "mcc": 284,
        "mnc": 70,
        "lac": 8240,
        "cell_id": 4521
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