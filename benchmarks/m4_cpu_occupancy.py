#!/usr/bin/env python3
"""benchmarks/m4_cpu_occupancy.py — M4: Ocupação de CPU (TCC Cap.3, Sec.
"Métricas e procedimentos experimentais").

Runs a fixed capture, then queries the 'o' command (busy/total DWT cycles
for that capture — see src/occupancy.c and handle_get_cpu_occupancy.c) to
compute the occupancy fraction. Requires firmware built with the DWT
occupancy instrumentation; plain sigrok-pico firmware without it will make
this script raise (see common.query_occupancy).

What counts as "busy" differs by firmware and is defined in
src/sigrok_handler.c, not by this script:
  - Firmware A: only the RLE+TX regions around each
    ana_send_packet_channels() call (DMA-wait polling is NOT busy).
  - Firmware B: the whole capture (sampling is inherently CPU-bound).

Usage:
  python3 benchmarks/m4_cpu_occupancy.py --firmware-label A --port /dev/ttyACM0
  python3 benchmarks/m4_cpu_occupancy.py --firmware-label B --samples 65536 --reps 30
"""
import argparse
import csv
import sys
from pathlib import Path

from common import (
    CHANNEL_SWEEP,
    abort_capture,
    N_REPETITIONS,
    RESULTS_DIR,
    capture_fixed_digital,
    connect,
    csv_writer,
    log_rate_sweep,
    query_occupancy,
    setup_channels,
)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=None)
    ap.add_argument("--firmware-label", required=True)
    ap.add_argument("--samples", type=int, default=65536,
                    help="fixed-capture length per repetition (larger = less relative "
                         "overhead from the 'o' query itself)")
    ap.add_argument("--reps", type=int, default=N_REPETITIONS)
    ap.add_argument("--channels", default=",".join(str(c) for c in CHANNEL_SWEEP))
    ap.add_argument("--rate-min", type=int, default=None)
    ap.add_argument("--rate-max", type=int, default=None)
    ap.add_argument("--points-per-decade", type=int, default=6)
    ap.add_argument("--output", default=None)
    ap.add_argument("--resume", action="store_true",
                    help="skip (channels, rate, rep) already in the output CSV for this "
                         "--firmware-label, continuing an interrupted run")
    args = ap.parse_args()

    channels = [int(c) for c in args.channels.split(",")]
    rate_kwargs = {}
    if args.rate_min:
        rate_kwargs["rate_min"] = args.rate_min
    if args.rate_max:
        rate_kwargs["rate_max"] = args.rate_max
    rates = log_rate_sweep(points_per_decade=args.points_per_decade, **rate_kwargs)

    out_path = args.output or (RESULTS_DIR / f"m4_{args.firmware_label}.csv")

    # Every repetition is flushed to the CSV as soon as it is measured, so the
    # CSV itself is the run's saved state.
    done = set()
    if args.resume and Path(out_path).exists():
        with open(out_path, newline="") as fh:
            for r in csv.DictReader(fh):
                if r["firmware"] == args.firmware_label:
                    done.add((int(r["channels"]), int(r["rate_hz"]), int(r["rep"])))
        print(f"resume: {len(done)} repetitions already in {out_path}")
    f, writer = csv_writer(
        out_path,
        ["firmware", "channels", "rate_hz", "rep", "busy_cycles", "total_cycles",
         "occupancy_fraction", "decode_errors"],
    )

    dbg = connect(args.port)
    try:
        # Fail fast with a clear message if this firmware predates the 'o'
        # command, instead of one confusing error per repetition.
        setup_channels(dbg, channels[0], rates[0], args.samples)
        capture_fixed_digital(dbg, channels[0])
        query_occupancy(dbg)

        for n_channels in channels:
            print(f"\n== M4: {n_channels} channel(s) ==")
            for rate_hz in rates:
                todo = [rep for rep in range(args.reps)
                        if (n_channels, rate_hz, rep) not in done]
                if not todo:
                    print(f"  rate={rate_hz:>10} Hz  already done")
                    continue
                if not setup_channels(dbg, n_channels, rate_hz, args.samples):
                    print(f"  rate={rate_hz:>10} Hz  refused by the device (above its "
                          f"sampler limit) — stopping sweep")
                    break
                capture_failed = False
                for rep in todo:
                    # A capture that times out leaves its tail in the input
                    # buffer, where the 'o' reply then gets mixed with
                    # sample bytes. Redo such a repetition from a clean
                    # state instead of recording garbage or aborting the run.
                    for attempt in range(3):
                        result = capture_fixed_digital(dbg, n_channels)
                        try:
                            if len(result["digital"]) != args.samples:
                                raise RuntimeError(
                                    f"got {len(result['digital'])} of {args.samples} samples "
                                    f"({result['errors'][:1]})")
                            busy, total = query_occupancy(dbg)
                            break
                        except (RuntimeError, ValueError) as e:
                            print(f"    [retry] rate={rate_hz} rep={rep}: {e} "
                                  f"({attempt + 1}/3)", flush=True)
                            abort_capture(dbg)
                            setup_channels(dbg, n_channels, rate_hz, args.samples)
                    else:
                        # Every attempt aborted: the firmware cannot sustain
                        # this rate (e.g. Firmware A's streaming fixed capture
                        # overflows its DMA ping-pong when RLE+TX falls
                        # behind). Higher rates would fail too.
                        capture_failed = True
                        break
                    fraction = (busy / total) if total > 0 else float("nan")

                    writer.writerow({
                        "firmware": args.firmware_label,
                        "channels": n_channels,
                        "rate_hz": rate_hz,
                        "rep": rep,
                        "busy_cycles": busy,
                        "total_cycles": total,
                        "occupancy_fraction": round(fraction, 6) if total > 0 else "",
                        "decode_errors": len(result["errors"]),
                    })
                    f.flush()
                if capture_failed:
                    print(f"  rate={rate_hz:>10} Hz  capture aborted 3 times (device cannot "
                          f"sustain this rate) — stopping sweep", flush=True)
                    abort_capture(dbg)
                    break
                print(f"  rate={rate_hz:>10} Hz  done ({args.reps} reps)")
    finally:
        dbg.close()
        f.close()

    print(f"\nResults written to {out_path}")


if __name__ == "__main__":
    sys.exit(main())
