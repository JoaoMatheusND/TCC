#!/usr/bin/env python3
"""benchmarks/usb_throughput.py — real USB CDC throughput of the device.

Runs a continuous ('C') capture for --duration seconds and measures, on the
host, the raw bytes received per second (wire throughput) and the decoded
samples per second. The RP2350 USB is Full-Speed (12 Mbit/s): bulk tops out
around 1.2 MB/s in theory (19 x 64-byte packets per 1 ms frame), ~1 MB/s in
practice. This script gives the measured number for the TCC.

To measure the link, not the RLE: the stream must be incompressible, so feed
a fast square wave (e.g. 1 MHz from the generator) to the enabled channels and
sample at a few samples per period. With a constant input RLE collapses the
stream and the result is just the encoder's rate, not the bus's.

Each run appends one row to results/usb_throughput.csv (tag it with
--label, e.g. the firmware build being compared).

Usage:
  python3 benchmarks/usb_throughput.py --rate 4000000 --channels 12 --duration 10
  python3 benchmarks/usb_throughput.py --label B-ep4k --rate 4000000 --channels 4
"""
import argparse
import sys
import time

from common import (
    ABORT_MARKER,
    RESULTS_DIR,
    SIGROK_SAMPLE_LIMIT_MAX,
    connect,
    csv_writer,
    decode_continuous_stream,
    digital_bytes_per_sample,
    setup_channels,
)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=None)
    ap.add_argument("--rate", type=int, default=4_000_000, help="sample rate (Hz)")
    ap.add_argument("--channels", type=int, default=12, help="enabled channels D0..D(n-1)")
    ap.add_argument("--duration", type=float, default=10.0, help="seconds to stream")
    ap.add_argument("--no-decode", action="store_true",
                    help="skip decoding (only wire bytes/s); faster for long runs")
    ap.add_argument("--label", default="", help="firmware/build tag for the CSV row")
    ap.add_argument("--output", default=None,
                    help="CSV path (default: results/usb_throughput.csv)")
    args = ap.parse_args()

    dbg = connect(args.port)
    try:
        if not setup_channels(dbg, args.channels, args.rate, SIGROK_SAMPLE_LIMIT_MAX):
            raise SystemExit(f"device refused {args.rate} Hz (above its sampler limit)")

        old_timeout = dbg.ser.timeout
        dbg.ser.timeout = 0.05
        raw = bytearray()
        try:
            dbg.ser.reset_input_buffer()
            dbg._send(b"C\n")
            t_start = time.monotonic()
            t_first = None
            while True:
                chunk = dbg.ser.read(dbg.ser.in_waiting or 1)
                now = time.monotonic()
                if chunk:
                    if t_first is None:
                        t_first = now
                    raw.extend(chunk)
                    if ABORT_MARKER in raw[-8:]:
                        break
                if t_first is not None and now - t_first >= args.duration:
                    break
                if t_first is None and now - t_start > 10.0:
                    raise SystemExit("no data within 10 s")
            t_last = time.monotonic()
            dbg._send(b"+")
            time.sleep(0.3)
            dbg.ser.read(1 << 20)  # drain the tail; not counted
        finally:
            dbg.ser.timeout = old_timeout
    finally:
        dbg.close()

    elapsed = t_last - t_first
    wire_bps = len(raw) / elapsed
    print(f"rate={args.rate} Hz  channels={args.channels}  elapsed={elapsed:.2f} s")
    print(f"wire throughput : {wire_bps / 1e6:.3f} MB/s  ({len(raw)} bytes)")
    print(f"USB FS ceiling  : ~1.216 MB/s theoretical "
          f"({wire_bps / 1.216e6 * 100:.1f}% of it)")
    if ABORT_MARKER in raw:
        print("NOTE: device aborted the capture ('!!!')")

    n = None
    if not args.no_decode:
        result = decode_continuous_stream(bytes(raw), digital_bytes_per_sample(args.channels))
        n = len(result["digital"])
        print(f"samples/s       : {n / elapsed:,.0f}  (requested {args.rate:,})")
        if n:
            print(f"bytes/sample    : {len(raw) / n:.3f}  (RLE ratio; "
                  f"{digital_bytes_per_sample(args.channels)} uncompressed)")
        if result["errors"]:
            print(f"decode errors   : {len(result['errors'])} (first: {result['errors'][0]})")

    out_path = args.output or (RESULTS_DIR / "usb_throughput.csv")
    f, writer = csv_writer(out_path, ["label", "rate_hz", "channels", "elapsed_s", "wire_bytes",
                                      "wire_MBps", "fs_ceiling_pct", "samples",
                                      "samples_per_s", "delivered_fraction", "bytes_per_sample",
                                      "aborted"])
    with f:
        writer.writerow({
            "label": args.label,
            "rate_hz": args.rate,
            "channels": args.channels,
            "elapsed_s": round(elapsed, 3),
            "wire_bytes": len(raw),
            "wire_MBps": round(wire_bps / 1e6, 4),
            "fs_ceiling_pct": round(wire_bps / 1.216e6 * 100, 2),
            "samples": n if n is not None else "",
            "samples_per_s": round(n / elapsed) if n else "",
            "delivered_fraction": round(n / elapsed / args.rate, 4) if n else "",
            "bytes_per_sample": round(len(raw) / n, 4) if n else "",
            "aborted": int(ABORT_MARKER in raw),
        })
    print(f"row appended to {out_path}")


if __name__ == "__main__":
    sys.exit(main())
