#!/usr/bin/env python3
"""benchmarks/m3_continuous_loss.py — M3: Captura contínua e perda de amostras
(TCC Cap.3, Sec. "Métricas e procedimentos experimentais").

Continuous ('C') capture with simultaneous host transmission for a fixed
window (default 60s); measures the effective delivered rate, the
number/size of gaps, and reports the requested rate for the
amostragem x USB conflict analysis (hypothesis item iii).

Two stimuli (--stimulus):

  square  (default) the bench's square wave on --signal-channel. The rising
          edges must be exactly one signal period P = rate / f_sig samples
          apart; any interval that deviates from P by more than
          --gap-tolerance samples marks a gap (samples missing from the
          stream, or sampling paused). The deviation |interval - P| is
          recorded as the gap's size. A gap that is an exact multiple of P
          (within the tolerance) is invisible, so P should be large: keep
          rate / f_sig >= --min-samples-per-period.
  counter a free-running binary counter on D0..D(n-1); gaps are breaks in
          the counter sequence (sample-exact).

Note: firmware-reported overflow counters are not currently exposed over
the wire (only the abort marker "!!!", which this script also records) —
gap detection from the decoded stream is the primary signal here.

Usage:
  python3 benchmarks/m3_continuous_loss.py --firmware-label A --channels 4,12 \\
      --signal-channel 3 --signal-hz 10000 --duration 10 --reps 10
  python3 benchmarks/m3_continuous_loss.py --firmware-label B --stimulus counter
"""
import argparse
import csv
import statistics
import sys
import time

from pathlib import Path

from common import (
    CHANNEL_SWEEP,
    N_REPETITIONS,
    RESULTS_DIR,
    SIGROK_SAMPLE_LIMIT_MAX,
    connect,
    csv_writer,
    decode_continuous_stream,
    decode_rising_edges,
    digital_bytes_per_sample,
    find_counter_gaps,
    log_rate_sweep,
    setup_channels,
)


def continuous_capture(dbg, duration_s):
    """Run 'C' for duration_s, then stop it and drain what's left.

    @return (raw_bytes, aborted_by_device: bool, stream_seconds) —
    stream_seconds runs from the first byte received to the stop.
    """
    old_timeout = dbg.ser.timeout
    dbg.ser.timeout = 0.05
    try:
        dbg.ser.reset_input_buffer()
        dbg._send(b"C\n")
        raw = bytearray()
        t_start = time.monotonic()
        t_first = None
        aborted = False
        while True:
            chunk = dbg.ser.read(dbg.ser.in_waiting or 1)
            now = time.monotonic()
            if chunk:
                if t_first is None:
                    t_first = now
                raw.extend(chunk)
                if b"!!!" in raw[-8:]:
                    aborted = True
                    break
            if t_first is not None and now - t_first >= duration_s:
                break
            if t_first is None and now - t_start > 10.0:
                break
        t_stop = time.monotonic()
        dbg._send(b"+")
        time.sleep(0.3)
        raw.extend(dbg.ser.read(1 << 20))
    finally:
        dbg.ser.timeout = old_timeout
    stream_s = (t_stop - t_first) if t_first is not None else 0.0
    return bytes(raw), aborted, stream_s


def square_wave_gaps(edges, period, tolerance):
    """Rising-edge intervals that deviate from the signal period.

    @return list of {'index': sample index of the edge closing the
    interval, 'size': |interval - period| in samples}.
    """
    gaps = []
    for prev, cur in zip(edges, edges[1:]):
        dev = abs((cur - prev) - period)
        if dev > tolerance:
            gaps.append({"index": cur, "size": round(dev)})
    return gaps


def analyse_square(raw, dig_bps, rate_hz, signal_channel, signal_hz, tolerance):
    dec = decode_rising_edges(raw, dig_bps, signal_channel, n_needed=1 << 62)
    edges = dec["edges"]
    if signal_hz:
        period = rate_hz / signal_hz
    elif len(edges) > 2:
        period = statistics.median(b - a for a, b in zip(edges, edges[1:]))
    else:
        period = 0.0
    gaps = square_wave_gaps(edges, period, tolerance) if period else []
    return dec["n_samples"], gaps, dec["errors"], len(edges), period


def analyse_counter(raw, dig_bps, n_channels):
    result = decode_continuous_stream(raw, dig_bps)
    gaps = find_counter_gaps(result["digital"], n_channels)
    return len(result["digital"]), gaps, result["errors"]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=None)
    ap.add_argument("--firmware-label", required=True)
    ap.add_argument("--stimulus", choices=("square", "counter"), default="square")
    ap.add_argument("--duration", type=float, default=60.0, help="capture window in seconds")
    ap.add_argument("--reps", type=int, default=N_REPETITIONS)
    ap.add_argument("--channels", default=",".join(str(c) for c in CHANNEL_SWEEP))
    ap.add_argument("--signal-channel", type=int, default=0,
                    help="square: channel carrying the square wave")
    ap.add_argument("--signal-hz", type=float, default=None,
                    help="square: generator frequency (default: median edge interval)")
    ap.add_argument("--gap-tolerance", type=float, default=1.5,
                    help="square: max |interval - period| in samples before it counts as a gap "
                         "(1 sample is sampling quantization)")
    ap.add_argument("--min-samples-per-period", type=float, default=8.0,
                    help="square: skip rates below this x --signal-hz")
    ap.add_argument("--rate-min", type=int, default=None)
    ap.add_argument("--rate-max", type=int, default=None)
    ap.add_argument("--points-per-decade", type=int, default=4,
                    help="M3 is slow (one full --duration per point) — coarser sweep than M1 by default")
    ap.add_argument("--output", default=None)
    ap.add_argument("--resume", action="store_true",
                    help="skip (channels, rate, rep) already in the output CSV (after a crash)")
    args = ap.parse_args()

    channels = [int(c) for c in args.channels.split(",")]
    rate_kwargs = {}
    if args.rate_min:
        rate_kwargs["rate_min"] = args.rate_min
    if args.rate_max:
        rate_kwargs["rate_max"] = args.rate_max
    rates = log_rate_sweep(points_per_decade=args.points_per_decade, **rate_kwargs)
    square = args.stimulus == "square"

    out_path = args.output or (RESULTS_DIR / f"m3_{args.firmware_label}.csv")
    done = {}  # (channels, rate_hz) -> {rep: aborted}
    if args.resume and Path(out_path).exists():
        with open(out_path, newline="") as prev:
            for row in csv.DictReader(prev):
                key = (int(row["channels"]), int(row["rate_hz"]))
                done.setdefault(key, {})[int(row["rep"])] = int(row["aborted"])
        print(f"resume: {sum(len(v) for v in done.values())} repetitions already in {out_path}")
    f, writer = csv_writer(
        out_path,
        ["firmware", "stimulus", "channels", "rate_hz", "rep", "stream_s", "requested_samples",
         "delivered_samples", "delivered_fraction", "effective_rate_hz", "n_gaps",
         "gap_samples_total", "gap_max", "n_edges", "period_samples", "aborted",
         "decode_errors", "gap_index_mod_chunk"],
    )

    dbg = connect(args.port)
    try:
        for n_channels in channels:
            if square and args.signal_channel >= n_channels:
                print(f"skip {n_channels} channel(s): signal channel D{args.signal_channel} "
                      f"would not be enabled")
                continue
            print(f"\n== M3 ({args.stimulus}): {n_channels} channel(s) ==")
            dig_bps = digital_bytes_per_sample(n_channels)
            for rate_hz in rates:
                if (square and args.signal_hz
                        and rate_hz < args.min_samples_per_period * args.signal_hz):
                    print(f"  rate={rate_hz:>10} Hz  skipped (< {args.min_samples_per_period:g} "
                          f"samples per signal period)")
                    continue
                refused = False
                prior = done.get((n_channels, rate_hz), {})
                n_aborted = sum(prior.values())
                for rep in range(args.reps):
                    if rep in prior:
                        continue
                    if not setup_channels(dbg, n_channels, rate_hz,
                                          n_samples=SIGROK_SAMPLE_LIMIT_MAX):
                        refused = True
                        break
                    raw, aborted, stream_s = continuous_capture(dbg, args.duration)
                    n_aborted += int(aborted)

                    if square:
                        delivered, gaps, errors, n_edges, period = analyse_square(
                            raw, dig_bps, rate_hz, args.signal_channel, args.signal_hz,
                            args.gap_tolerance)
                    else:
                        delivered, gaps, errors = analyse_counter(raw, dig_bps, n_channels)
                        n_edges, period = "", ""
                    # A host-initiated stop cuts the last sample mid-byte: expected, not loss.
                    errors = [e for e in errors if "truncated" not in e
                              and "no done marker" not in e]
                    gap_total = sum(g["size"] for g in gaps)
                    requested = int(rate_hz * stream_s)
                    effective_rate = delivered / stream_s if stream_s > 0 else 0.0

                    writer.writerow({
                        "firmware": args.firmware_label,
                        "stimulus": args.stimulus,
                        "channels": n_channels,
                        "rate_hz": rate_hz,
                        "rep": rep,
                        "stream_s": round(stream_s, 3),
                        "requested_samples": requested,
                        "delivered_samples": delivered,
                        "delivered_fraction": round(delivered / requested, 6) if requested else "",
                        "effective_rate_hz": round(effective_rate, 2),
                        "n_gaps": len(gaps),
                        "gap_samples_total": gap_total,
                        "gap_max": max((g["size"] for g in gaps), default=0),
                        "n_edges": n_edges,
                        "period_samples": round(period, 3) if period != "" else "",
                        "aborted": int(aborted),
                        "decode_errors": len(errors),
                        # Where in the firmware's L-sample chunk each gap fell
                        # (first 50): clustering near 0 points at chunk re-arm.
                        "gap_index_mod_chunk": ";".join(
                            str(g["index"] % SIGROK_SAMPLE_LIMIT_MAX) for g in gaps[:50]),
                    })
                    f.flush()
                    print(f"  rate={rate_hz:>10} Hz rep={rep:>2}  "
                          f"delivered={delivered:>10}  frac={effective_rate / rate_hz:6.4f}"
                          f"  gaps={len(gaps):>5}"
                          f"{'  ABORTED' if aborted else ''}", flush=True)
                if refused:
                    print(f"  rate={rate_hz:>10} Hz  refused by the device (above its "
                          f"sampler limit) — stopping sweep")
                    break
                if n_aborted == args.reps:
                    print(f"  rate={rate_hz:>10} Hz  every repetition aborted — stopping sweep")
                    break
    finally:
        dbg.close()
        f.close()

    print(f"\nResults written to {out_path}")


if __name__ == "__main__":
    sys.exit(main())
