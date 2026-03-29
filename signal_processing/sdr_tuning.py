#!/usr/bin/env python3
"""
SDR Positioning — Field Test
Tests live signal reception, trilateration, and (optionally) Kalman-fused positioning.

Usage
-----
  python3 sdr_tuning.py                          # scan + trilaterate, default catalogue
  python3 sdr_tuning.py --types FM VOR           # filter signal types
  python3 sdr_tuning.py --kalman                 # add Kalman filter to smooth fixes
  python3 sdr_tuning.py --full                   # use PositioningSystem facade (no raw output)
  python3 sdr_tuning.py --cycles 10              # stop after N cycles
  python3 sdr_tuning.py --driver rtlsdr          # different SDR driver
  python3 sdr_tuning.py --catalogue my.json -v   # custom catalogue, verbose
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from pathlib import Path

from sdr_positioning import DEFAULT_CATALOGUE, PositionEstimate, PositioningSystem
from sdr_positioning.kalman import KalmanFilter, enu_to_latlon, latlon_to_enu
from sdr_positioning.models import Measurement
from sdr_positioning.sdr_module import CatalogueLoader, SDRModule
from sdr_positioning.trilateration import Environment, trilaterate

logging.basicConfig(format="%(levelname)s %(name)s: %(message)s")

DIVIDER = "─" * 68


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 2.0 * R * math.asin(math.sqrt(a))


def _print_catalogue_summary(catalogue: Path) -> None:
    entries = CatalogueLoader().load(catalogue)
    by_type: dict[str, int] = {}
    for e in entries:
        by_type[e.signal_type] = by_type.get(e.signal_type, 0) + 1
    summary = "  ".join(f"{t}:{n}" for t, n in sorted(by_type.items()))
    print(f"  Catalogue  : {catalogue}  ({len(entries)} entries — {summary})")
    print(f"  Environment thresholds: "
          + "  ".join(f"{env.name}({env.extra_loss_db:.0f}dB)" for env in Environment))


def _print_measurements(measurements: list[Measurement], verbose: bool) -> None:
    n_be = sum(1 for m in measurements if m.best_effort)
    print(f"  Signals    : {len(measurements)} received  ({n_be} best-effort)")
    if verbose:
        for m in measurements:
            flag = "*" if m.best_effort else " "
            print(
                f"    {flag}{m.source_id:22s}  {m.signal_type:6s}  "
                f"{m.freq_hz / 1e6:9.3f} MHz  "
                f"rssi={m.rssi_dbm:7.2f} dBm  "
                f"gain={m.gain_used:.1f} dB  "
                f"tx=({m.lat:.4f},{m.lon:.4f})"
            )


def _run_scan_trilaterate(args: argparse.Namespace) -> None:
    """Core loop: SDRModule.scan() → trilaterate() with optional KalmanFilter."""
    sdr = SDRModule(args.catalogue, driver=args.driver, serial=args.serial)
    kf: KalmanFilter | None = KalmanFilter(sigma_a=args.sigma_a) if args.kalman else None
    origin: tuple[float, float] | None = None
    exclude_ids: set[str] = set(args.exclude) if args.exclude else set()

    errors_m: list[float] = []   # actual error per cycle (when ground truth provided)
    accs_m:   list[float] = []   # reported accuracy per cycle

    cycle = 0
    try:
        while args.cycles == 0 or cycle < args.cycles:
            cycle += 1
            print(f"\n{DIVIDER}")
            print(f"  Cycle {cycle}" + (f"/{args.cycles}" if args.cycles else ""))

            t0           = time.perf_counter()
            measurements = sdr.scan(types=args.types or None)
            scan_ms      = (time.perf_counter() - t0) * 1000

            # Manual exclusion: filter before trilateration (and before verbose print)
            if exclude_ids:
                excl_shown = [m for m in measurements if m.source_id in exclude_ids]
                measurements = [m for m in measurements if m.source_id not in exclude_ids]

            _print_measurements(measurements, args.verbose)

            if args.verbose and exclude_ids and excl_shown:
                for m in excl_shown:
                    print(f"    x{m.source_id:22s}  {m.signal_type:6s}  "
                          f"{m.freq_hz / 1e6:9.3f} MHz  rssi={m.rssi_dbm:7.2f} dBm  "
                          f"[manually excluded]")

            t1 = time.perf_counter()
            auto_rejected: list = []
            result = trilaterate(
                measurements,
                auto_reject=args.auto_reject,
                outlier_sigma=args.outlier_sigma,
                rejected=auto_rejected if args.auto_reject else None,
            )
            tri_ms = (time.perf_counter() - t1) * 1000

            if args.auto_reject and auto_rejected:
                print(f"  Auto-reject: {len(auto_rejected)} station(s) removed — "
                      + ", ".join(m.source_id for m in auto_rejected))
                if args.verbose:
                    for m in auto_rejected:
                        print(f"    !{m.source_id:22s}  {m.signal_type:6s}  "
                              f"{m.freq_hz / 1e6:9.3f} MHz  rssi={m.rssi_dbm:7.2f} dBm  "
                              f"[outlier — residual exceeded {args.outlier_sigma}σ]")

            print(f"  Scan={scan_ms:.0f}ms  Trilaterate={tri_ms:.1f}ms")

            if result is None:
                print(f"  Position   : — (need ≥3 sources, got {len(measurements)})")
            else:
                lat_rf, lon_rf, acc_rf = result
                accs_m.append(acc_rf)

                gt_str = ""
                if args.ground_truth:
                    gt_lat, gt_lon = args.ground_truth
                    err = _haversine_m(gt_lat, gt_lon, lat_rf, lon_rf)
                    errors_m.append(err)
                    gt_str = f"  error={err:.0f}m"

                print(f"  RF fix     : lat={lat_rf:.6f}  lon={lon_rf:.6f}  acc={acc_rf:.1f}m{gt_str}")

                if kf is not None:
                    if origin is None:
                        origin = (lat_rf, lon_rf)

                    px, py   = latlon_to_enu(lat_rf, lon_rf, *origin)
                    accepted = kf.update(px, py, acc_rf)

                    if kf.initialized:
                        est_lat, est_lon = enu_to_latlon(kf.x[0], kf.x[1], *origin)
                        speed  = math.sqrt(kf.x[2] ** 2 + kf.x[3] ** 2)

                        kf_gt_str = ""
                        if args.ground_truth:
                            gt_lat, gt_lon = args.ground_truth
                            kf_err = _haversine_m(gt_lat, gt_lon, est_lat, est_lon)
                            kf_gt_str = f"  error={kf_err:.0f}m"

                        print(
                            f"  Kalman     : lat={est_lat:.6f}  lon={est_lon:.6f}  "
                            f"acc={kf.accuracy_m:.1f}m  spd={speed:.2f}m/s  "
                            f"rf={'ok' if accepted else 'gated'}{kf_gt_str}"
                        )

            if args.cycles == 0 or cycle < args.cycles:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n\n  Stopped.")
    finally:
        sdr.close()

    _print_summary(accs_m, errors_m, args.ground_truth)


def _print_summary(accs_m: list[float], errors_m: list[float], ground_truth: tuple[float, float] | None) -> None:
    if not accs_m:
        return
    print(f"\n{DIVIDER}")
    print(f"  Summary  ({len(accs_m)} fixes)")
    print(f"  Reported accuracy  avg={sum(accs_m)/len(accs_m):.1f}m  "
          f"min={min(accs_m):.1f}m  max={max(accs_m):.1f}m")
    if ground_truth and errors_m:
        print(f"  Error vs truth     avg={sum(errors_m)/len(errors_m):.1f}m  "
              f"min={min(errors_m):.1f}m  max={max(errors_m):.1f}m")
        print(f"  Ground truth       lat={ground_truth[0]:.6f}  lon={ground_truth[1]:.6f}")


def _run_full_pipeline(args: argparse.Namespace) -> None:
    """PositioningSystem facade — highest-level API, no raw measurement output."""
    ps     = PositioningSystem(
        catalogue_path=args.catalogue,
        driver=args.driver,
        serial=args.serial,
        sigma_a=args.sigma_a,
    )
    errors_m: list[float] = []
    accs_m:   list[float] = []

    cycle  = 0
    last_t = time.time()
    try:
        while args.cycles == 0 or cycle < args.cycles:
            cycle += 1
            now   = time.time()
            dt    = max(now - last_t, 1e-3)
            last_t = now

            t0       = time.perf_counter()
            estimate: PositionEstimate | None = ps.step()
            elapsed  = (time.perf_counter() - t0) * 1000

            print(f"\n{DIVIDER}")
            print(f"  Cycle {cycle}" + (f"/{args.cycles}" if args.cycles else "") +
                  f"  ({elapsed:.0f} ms)")

            if estimate is None:
                print("  Position : — (awaiting first RF fix)")
            else:
                accs_m.append(estimate.accuracy_m)

                gt_str = ""
                if args.ground_truth:
                    gt_lat, gt_lon = args.ground_truth
                    err = _haversine_m(gt_lat, gt_lon, estimate.lat, estimate.lon)
                    errors_m.append(err)
                    gt_str = f"  error={err:.0f}m"

                print(
                    f"  Position : lat={estimate.lat:.6f}  lon={estimate.lon:.6f}  "
                    f"acc={estimate.accuracy_m:.1f}m{gt_str}"
                )
                print(
                    f"  Motion   : spd={estimate.speed_ms:.2f}m/s  "
                    f"hdg={estimate.heading_deg:.1f}°  "
                    f"src={estimate.source}"
                )
                print(
                    f"  RF       : {estimate.n_rf_sources} sources  "
                    f"last_fix={estimate.last_rf_age:.1f}s ago"
                )

            if args.cycles == 0 or cycle < args.cycles:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n\n  Stopped.")
    finally:
        ps.close()

    _print_summary(accs_m, errors_m, args.ground_truth)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--full",       action="store_true",
                   help="Use PositioningSystem facade instead of raw scan+trilaterate")
    p.add_argument("--kalman",     action="store_true",
                   help="Add KalmanFilter smoothing to scan+trilaterate mode")
    p.add_argument("--catalogue",  type=Path, default=DEFAULT_CATALOGUE, metavar="PATH",
                   help="Station catalogue JSON (default: bundled stations.json)")
    p.add_argument("--driver",     default="sdrplay", metavar="DRIVER",
                   help="SoapySDR driver (default: sdrplay)")
    p.add_argument("--serial",     default=None, metavar="SERIAL",
                   help="SDR serial number (default: auto)")
    p.add_argument("--types",      nargs="+", default=None, metavar="TYPE",
                   help="Signal types to scan: FM VOR DAB DVB-T GSM (default: all)")
    p.add_argument("--sigma-a",    type=float, dest="sigma_a", default=0.1, metavar="FLOAT",
                   help="Kalman process noise std dev m/s² (default: 0.1)")
    p.add_argument("--cycles",     type=int, default=0, metavar="N",
                   help="Cycles to run, 0=infinite (default: 0)")
    p.add_argument("--interval",   type=float, default=1.0, metavar="SEC",
                   help="Seconds between cycles (default: 1.0)")
    p.add_argument("--ground-truth", nargs=2, type=float, metavar=("LAT", "LON"),
                   dest="ground_truth",
                   help="Known position for error reporting, e.g. --ground-truth 42.0117 23.0949")
    p.add_argument("--exclude",      nargs="+", default=[], metavar="SOURCE_ID",
                   help="Exclude station(s) by source_id (e.g. FM_91.4_РРС Благоевград); "
                        "not supported with --full")
    p.add_argument("--auto-reject", action="store_true", dest="auto_reject",
                   help="Automatically remove outlier stations by MAD residual; "
                        "not supported with --full")
    p.add_argument("--outlier-sigma", type=float, default=2.5, dest="outlier_sigma",
                   metavar="FLOAT",
                   help="Outlier rejection threshold in normalised-MAD units (default: 2.5)")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show per-signal measurement detail")
    p.add_argument("--log-level",  default="WARNING",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                   help="Internal log level (default: WARNING)")
    args = p.parse_args()

    if args.ground_truth:
        args.ground_truth = (args.ground_truth[0], args.ground_truth[1])

    logging.getLogger("sdr_positioning").setLevel(args.log_level)

    print(DIVIDER)
    print("  SDR Positioning — Field Test")
    _print_catalogue_summary(args.catalogue)
    print(f"  Driver     : {args.driver}  serial={args.serial or '(auto)'}")
    print(f"  Mode       : {'full pipeline (PositioningSystem)' if args.full else 'scan + trilaterate' + (' + Kalman' if args.kalman else '')}")
    print(f"  Types      : {args.types or 'all'}")
    print(f"  sigma_a    : {args.sigma_a} m/s²")
    if args.full and (args.exclude or args.auto_reject):
        print("  Note       : --exclude and --auto-reject are ignored in --full mode")

    try:
        if args.full:
            _run_full_pipeline(args)
        else:
            _run_scan_trilaterate(args)
    except Exception as exc:
        print(f"\nFATAL: {exc}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
