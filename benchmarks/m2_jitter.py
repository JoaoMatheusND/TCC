#!/usr/bin/env python3
"""benchmarks/m2_jitter.py — M2: Jitter de amostragem (TCC Cap.3, Sec.
"Métricas e procedimentos experimentais").

Captures a reference square wave (fed to one digital channel by the bench's
waveform generator) and measures the distribution of its period, expressed
in samples between consecutive rising edges. Reports mean, stddev and
peak-to-peak of the period (converted to nanoseconds via the configured
sample rate) per repetition.

This covers only the in-band half of M2 — the external oscilloscope
cross-check of a firmware-toggled debug pin ("via pino de debug comutado
pelo firmware") is a separate, manual bench step; the current firmware has
no such debug-pin instrumentation (only the DWT occupancy counter added for
M4), so it isn't automated here.

Extra Firmware-B cases (XIP vs SRAM placement, interrupts enabled vs
disabled) are just separate firmware builds — re-run this same script
against each build, tagging --firmware-label accordingly (e.g. "B-xip",
"B-sram", "B-irq-on", "B-irq-off").

Usage:
  python3 benchmarks/m2_jitter.py --firmware-label A --square-channel 0 --port /dev/ttyACM0
  python3 benchmarks/m2_jitter.py --firmware-label B-sram --reps 30

Triggered window (default): each repetition arms a rising-edge trigger on the
square-wave channel and keeps the next --window seconds (1 s) after it fires,
the same window daq_ref.py cuts from the DAQ_REF table, so the two
instruments see the same stretch of the same signal. Results go to
m2_<label>_trig.csv (extra columns: n_samples, delivered_fraction,
first_edge_sample). With --daq-ref-live each repetition arms the DAQ and the
DAQ_REF on the same rising-edge trigger, waits for the generator, and adds the
DAQ_REF reference to the row (ref_t_ns, ref_n_periods, fs_real_hz,
rate_error_pct, period_count_diff — same meaning as daq_ref.py's calibrated
CSV). --window 0 restores the legacy untriggered fixed capture
of --samples samples (m2_<label>.csv).
"""
import argparse
import sys

from daq_ref import DaqRefLive, paired_capture
from common import (
    CHANNEL_SWEEP,
    N_REPETITIONS,
    RESULTS_DIR,
    TRIGGER_WINDOW_S,
    capture_fixed_digital,
    capture_triggered_edges,
    connect,
    csv_writer,
    log_rate_sweep,
    setup_channels,
)


def rising_edge_periods(samples, channel_bit):
    """Sample-count intervals between consecutive rising edges of
    `channel_bit` in the decoded digital word stream."""
    mask = 1 << channel_bit
    edges = []
    prev_bit = None
    for i, word in enumerate(samples):
        bit = 1 if (word & mask) else 0
        if prev_bit == 0 and bit == 1:
            edges.append(i)
        prev_bit = bit
    return [b - a for a, b in zip(edges, edges[1:])]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=None)
    ap.add_argument("--firmware-label", required=True)
    ap.add_argument("--square-channel", type=int, default=0,
                    help="digital channel index carrying the reference square wave")
    ap.add_argument("--window", type=float, default=TRIGGER_WINDOW_S,
                    help="seconds captured after the trigger (default 1); 0 = legacy "
                         "untriggered fixed capture of --samples")
    ap.add_argument("--samples", type=int, default=8192,
                    help="fixed-capture length per repetition (only with --window 0)")
    ap.add_argument("--reps", type=int, default=N_REPETITIONS)
    ap.add_argument("--channels", default=",".join(str(c) for c in CHANNEL_SWEEP))
    ap.add_argument("--rate-min", type=int, default=None)
    ap.add_argument("--rate-max", type=int, default=None)
    ap.add_argument("--points-per-decade", type=int, default=6)
    ap.add_argument("--daq-ref-live", action="store_true",
                    help="arm a DAQ_REF (Logic 2 Automation API) with the DAQ every repetition")
    ap.add_argument("--daq-ref-channel", default="0")
    ap.add_argument("--daq-ref-rate", type=int, default=24_000_000)
    ap.add_argument("--daq-ref-host", default="127.0.0.1")
    ap.add_argument("--daq-ref-port", type=int, default=10430)
    ap.add_argument("--daq-ref-launch", default=None,
                    help="Logic 2 executable/AppImage: started if not running, restarted "
                         "if it crashes mid-sweep")
    ap.add_argument("--trigger-timeout", type=float, default=300.0,
                    help="seconds to wait for the generator's first edge per capture")
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    channels = [int(c) for c in args.channels.split(",")]
    rate_kwargs = {}
    if args.rate_min:
        rate_kwargs["rate_min"] = args.rate_min
    if args.rate_max:
        rate_kwargs["rate_max"] = args.rate_max
    rates = log_rate_sweep(points_per_decade=args.points_per_decade, **rate_kwargs)

    triggered = args.window > 0
    if args.daq_ref_live and not triggered:
        ap.error("--daq-ref-live needs a triggered window (--window > 0)")
    live = (DaqRefLive(args.daq_ref_channel, args.window, args.daq_ref_rate,
                       host=args.daq_ref_host, port=args.daq_ref_port,
                       launch=args.daq_ref_launch)
            if args.daq_ref_live else None)
    suffix = "_trig" if triggered else ""
    out_path = args.output or (RESULTS_DIR / f"m2_{args.firmware_label}{suffix}.csv")
    fields = ["firmware", "channels", "rate_hz", "rep", "n_edges", "period_mean_ns",
              "period_std_ns", "period_pkpk_ns", "decode_errors"]
    if triggered:
        fields += ["window_s", "n_samples", "delivered_fraction", "first_edge_sample"]
    if live:
        fields += ["ref_t_ns", "ref_n_periods", "fs_real_hz", "rate_error_pct",
                   "period_count_diff"]
    f, writer = csv_writer(out_path, fields)

    dbg = connect(args.port)
    try:
        for n_channels in channels:
            if args.square_channel >= n_channels:
                print(f"skip {n_channels} channel(s): square wave channel "
                      f"D{args.square_channel} would not be enabled")
                continue
            print(f"\n== M2: {n_channels} channel(s), square wave on D{args.square_channel} ==")
            for rate_hz in rates:
                if not setup_channels(dbg, n_channels, rate_hz, args.samples):
                    print(f"  rate={rate_hz:>10} Hz  refused by the device (above its "
                          f"sampler limit) — stopping sweep")
                    break
                period_ns = 1e9 / rate_hz
                for rep in range(args.reps):
                    extra = {}
                    if triggered:
                        cap, ref = paired_capture(live, lambda: capture_triggered_edges(
                            dbg, n_channels, rate_hz, args.square_channel, args.window,
                            trigger_timeout=args.trigger_timeout))
                        edges = cap["edges"]
                        periods_samples = [b - a for a, b in zip(edges, edges[1:])]
                        result = {"errors": cap["errors"]}
                        extra = {
                            "window_s": args.window,
                            "n_samples": cap["n_samples"],
                            "delivered_fraction": round(cap["n_samples"] / cap["n_needed"], 6),
                            "first_edge_sample": edges[0] if edges else "",
                        }
                        if not cap["triggered"]:
                            print(f"  [warn] rate={rate_hz} rep={rep}: no trigger within timeout")
                        elif live:
                            if ref:
                                extra["ref_t_ns"] = round(ref[1] * 1e9, 3)
                                extra["ref_n_periods"] = len(ref[0]) - 1
                                extra["_t_ref_s"] = ref[1]
                            else:
                                print(f"  [warn] rate={rate_hz} rep={rep}: no DAQ_REF reference")
                    else:
                        result = capture_fixed_digital(dbg, n_channels)
                        periods_samples = rising_edge_periods(result["digital"], args.square_channel)

                    if periods_samples:
                        periods_ns = [p * period_ns for p in periods_samples]
                        mean_ns = sum(periods_ns) / len(periods_ns)
                        var = sum((p - mean_ns) ** 2 for p in periods_ns) / len(periods_ns)
                        std_ns = var ** 0.5
                        pkpk_ns = max(periods_ns) - min(periods_ns)
                    else:
                        mean_ns = std_ns = pkpk_ns = float("nan")

                    t_ref_s = extra.pop("_t_ref_s", None)
                    if t_ref_s and periods_samples:
                        fs_real = (mean_ns * rate_hz / 1e9) / t_ref_s
                        extra["fs_real_hz"] = round(fs_real, 3)
                        extra["rate_error_pct"] = round((fs_real - rate_hz) / rate_hz * 100, 4)
                        extra["period_count_diff"] = len(periods_samples) - extra["ref_n_periods"]
                    writer.writerow({
                        "firmware": args.firmware_label,
                        "channels": n_channels,
                        "rate_hz": rate_hz,
                        "rep": rep,
                        "n_edges": len(periods_samples),
                        "period_mean_ns": round(mean_ns, 3) if periods_samples else "",
                        "period_std_ns": round(std_ns, 3) if periods_samples else "",
                        "period_pkpk_ns": round(pkpk_ns, 3) if periods_samples else "",
                        "decode_errors": len(result["errors"]),
                        **extra,
                    })
                    f.flush()
                print(f"  rate={rate_hz:>10} Hz  done ({args.reps} reps)")
    finally:
        dbg.close()
        f.close()
        if live:
            live.close()

    print(f"\nResults written to {out_path}")


if __name__ == "__main__":
    sys.exit(main())
