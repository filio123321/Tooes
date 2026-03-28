import csv
import sys
from pathlib import Path

def lookup_tower(mcc, mnc, lac, cell_id):
    """
    Looks up a cell tower in the local OpenCelliD database (CSV) stored in a 'data' directory.
    OpenCelliD CSV format (typical columns):
    radio, mcc, net (mnc), area (lac), cell (cell_id), unit, lon, lat, range, samples, changeable, created, updated, averageSignal
    """
    # Define the directory and file path relative to this script
    script_path = Path(__file__).resolve().parent
    data_dir = script_path / "data"
    csv_filename = f"{mcc}.csv"
    csv_path = data_dir / csv_filename

    # If the file doesn't exist, crash with a helpful message
    if not csv_path.exists():
        print(f"\nCRITICAL ERROR: Database file for country code {mcc} not found.")
        print(f"You must download the OpenCelliD CSV file for MCC {mcc} and save it as:")
        print(f"  {csv_path}")
        print("\nNote: You can download tower data from https://opencellid.org/ or relevant data providers.")
        sys.exit(1)

    # Perform the lookup in the CSV file
    try:
        with open(csv_path, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Compare as integers to avoid formatting issues
                try:
                    if (int(row['mcc']) == int(mcc) and
                        int(row['net']) == int(mnc) and
                        int(row['area']) == int(lac) and
                        int(row['cell']) == int(cell_id)):
                        return {
                            "lat": float(row['lat']),
                            "lon": float(row['lon'])
                        }
                except (ValueError, KeyError):
                    # Skip malformed rows or rows missing keys
                    continue
    except Exception as e:
        print(f"An error occurred while reading the database: {e}")
        sys.exit(1)

    return None

if __name__ == "__main__":
    # Test data from prompt: (Cell #4521, LAC #8240, MCC 389, MNC 70)
    cell_id = 4521
    lac = 8240
    mcc = 389
    mnc = 70

    print(f"Looking up Cell Tower: MCC={mcc}, MNC={mnc}, LAC={lac}, CellID={cell_id}...")
    tower = lookup_tower(mcc, mnc, lac, cell_id)
    
    if tower:
        print(f"SUCCESS: Tower found at Lat {tower['lat']}, Lon {tower['lon']}")
    else:
        print("NOT FOUND: This tower is not in the local database.")
