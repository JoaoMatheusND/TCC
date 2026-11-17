#!/usr/bin/env python3
"""benchmarks/m1_max_rate.py — M1: Taxa máxima de amostragem sustentada
(TCC Cap.3, Sec. "Métricas e procedimentos experimentais").

Procedure: the bench's waveform generator feeds the same square wave to every
channel under test; the DAQ_REF captures it as the external reference. For
each DAQ sample rate, each repetition arms a rising-edge trigger on the first
signal channel and keeps the next --window seconds (1 s) after it fires, then
counts the rising edges seen on every signal channel. A repetition is clean
when:

  1. the whole window was delivered (rate * window samples, no decode errors);
  2. every signal channel's edge count matches the DAQ_REF reference within
     --tolerance-pct (the reference count excludes the trigger edge, which the
     DAQ cannot see as an edge: it is sample 0);
  3. all signal channels have the same edge count (±1: the window cut can
     fall between two channels' edges).

The sustained rate is the highest rate where every repetition is clean.
Swept across channel counts {1, 8, 12} (CHANNEL_SWEEP). Rates below
--min-samples-per-period x f_sig are skipped (undersampling aliases the
count; it is not an acquisition failure).

Reference: the DAQ_REF as external edge counter. With --daq-ref-live, every
repetition arms the DAQ and the DAQ_REF on the same rising-edge trigger, waits
for the generator (turn it on whenever; both keep waiting) and compares the
two captures of that same repetition at the end. Otherwise: a Logic 2 digital CSV of the same signal (--daq-ref-csv, cut to
trigger + window like daq_ref.py), or a known edge count (--ref-edges with
--signal-hz).

Note: comparing Firmware B's result against the theoretical ceiling
f_clk/C_iter (Eq. "eq:fsmax") requires C_iter from the sampling loop's
disassembly (Cap.3, Firmware B) — that step is manual/offline, not computed
by this script.

Usage:
  python3 benchmarks/m1_max_rate.py --firmware-label B --port /dev/ttyACM0 \\
      --daq-ref-csv ~/Documents/digital.csv --t-trigger 0
  python3 benchmarks/m1_max_rate.py --firmware-label B --daq-ref-live
  python3 benchmarks/m1_max_rate.py --firmware-label B --channels 12 \\
      --signal-channels 11 --ref-edges 99999 --signal-hz 100000 --reps 3
"""
import argparse
import sys

from common import (
    CHANNEL_SWEEP,
    N_REPETITIONS,
    RESULTS_DIR,
    SIGROK_SAMPLE_LIMIT_MAX,
    TRIGGER_WINDOW_S,
    capture_triggered_edges,
    connect,
    csv_writer,
    log_rate_sweep,
    setup_channels,
)
from daq_ref import (DaqRefLive, capture_live_csv, paired_capture, period_stats,
                        read_window_edges)


def evaluate_repetition(cap, signal_channels, ref_edges, tolerance_pct):
    """Apply the three clean-repetition criteria to one triggered capture.

    @return (ok, reason, counts_by_channel, worst_count_error_ppm)
    """
    counts = {c: len(cap["edges_by_channel"][c]) for c in signal_channels}
    worst_ppm = max(abs(n - ref_edges) / ref_edges * 1e6 for n in counts.values())

    if not cap["triggered"]:
        return False, "no_trigger", counts, worst_ppm
    if cap["aborted"] or cap["errors"]:
        return False, "decode_errors", counts, worst_ppm
    if cap["n_samples"] < cap["n_needed"]:
        return False, "window_not_delivered", counts, worst_ppm
    tol = max(1.0, ref_edges * tolerance_pct / 100.0)
    if any(abs(n - ref_edges) > tol for n in counts.values()):
        return False, "count_mismatch", counts, worst_ppm
    if max(counts.values()) - min(counts.values()) > 1:
        return False, "channels_disagree", counts, worst_ppm
    return True, "", counts, worst_ppm


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=None)
    ap.add_argument("--firmware-label", required=True, help='e.g. "A" or "B" — tags the CSV rows')
    ap.add_argument("--window", type=float, default=TRIGGER_WINDOW_S,
                    help="seconds captured after the trigger (must match the reference window)")
    ap.add_argument("--reps", type=int, default=N_REPETITIONS)
    ap.add_argument("--channels", default=",".join(str(c) for c in CHANNEL_SWEEP),
                    help="comma-separated channel counts to sweep")
    ap.add_argument("--signal-channels", default=None,
                    help="channels carrying the square wave (default: all enabled, D0..D(n-1))")
    ap.add_argument("--daq-ref-csv", default=None, help="Logic 2 digital export of the signal")
    ap.add_argument("--trigger-channel", type=int, default=None,
                    help="DAQ channel carrying the rising-edge trigger (default: first "
                         "signal channel)")
    ap.add_argument("--daq-ref-live", action="store_true",
                    help="capture the reference from a running Logic 2 (Automation API) "
                         "instead of --daq-ref-csv")
    ap.add_argument("--daq-ref-rate", type=int, default=24_000_000,
                    help="DAQ_REF digital sample rate for --daq-ref-live")
    ap.add_argument("--daq-ref-host", default="127.0.0.1")
    ap.add_argument("--daq-ref-port", type=int, default=10430)
    ap.add_argument("--daq-ref-launch", default=None,
                    help="Logic 2 executable/AppImage: started if not running, restarted "
                         "if it crashes mid-sweep")
    ap.add_argument("--daq-ref-channel", default="0")
    ap.add_argument("--min-daq-ref-spp", type=float, default=8.0,
                    help="abort if the DAQ_REF sees fewer samples per signal period than "
                         "this: its edge count is then unreliable (aliasing)")
    ap.add_argument("--trigger-timeout", type=float, default=300.0,
                    help="seconds to wait for the generator's first edge per capture")
    ap.add_argument("--t-trigger", type=float, default=None,
                    help="trigger time in the DAQ_REF table's clock (default: first rising edge)")
    ap.add_argument("--ref-edges", type=int, default=None,
                    help="reference rising-edge count in the window, instead of --daq-ref-csv")
    ap.add_argument("--signal-hz", type=float, default=None,
                    help="signal frequency (required with --ref-edges)")
    ap.add_argument("--tolerance-pct", type=float, default=0.1,
                    help="allowed edge-count deviation from the reference, in %% (default 0.1; "
                         "the DAQ's real rate differs slightly from the requested one)")
    ap.add_argument("--min-samples-per-period", type=float, default=4.0)
    ap.add_argument("--rate-min", type=int, default=None)
    ap.add_argument("--rate-max", type=int, default=None)
    ap.add_argument("--points-per-decade", type=int, default=6)
    ap.add_argument("--no-early-stop", action="store_true",
                    help="keep sweeping past the first fully-failed rate (slower, more thorough)")
    ap.add_argument("--output", default=None, help="CSV path (default: results/m1_<label>_edges.csv)")
    args = ap.parse_args()

    live = None
    if args.daq_ref_live:
        # Calibration capture: the signal frequency sets the undersampling skip
        # rule below. Per-repetition references are taken during the sweep.
        live = DaqRefLive(args.daq_ref_channel, args.window, args.daq_ref_rate,
                          host=args.daq_ref_host, port=args.daq_ref_port,
                          launch=args.daq_ref_launch)
        args.daq_ref_csv, args.t_trigger = capture_live_csv(
            args.daq_ref_channel, args.window, live=live)
    if args.daq_ref_csv:
        ref_list, t_trig, _ = read_window_edges(
            args.daq_ref_csv, args.daq_ref_channel, None, args.t_trigger, args.window)
        stats = period_stats(ref_list)
        ref_edges = len(ref_list) - 1  # trigger edge is DAQ sample 0, not an edge to it
        signal_hz = stats["f_sig_hz"]
        if args.daq_ref_live and args.daq_ref_rate / signal_hz < args.min_daq_ref_spp:
            raise SystemExit(
                f"DAQ_REF measured f_sig={signal_hz:.0f} Hz at {args.daq_ref_rate} S/s = "
                f"{args.daq_ref_rate / signal_hz:.1f} samples/period (< {args.min_daq_ref_spp:g}): "
                f"the reference is aliased and meaningless. Lower the generator frequency "
                f"(e.g. <= {args.daq_ref_rate / 12:.0f} Hz) or raise --daq-ref-rate.")
        print(f"reference: {len(ref_list)} DAQ_REF edges in {args.window:g} s after trigger "
              f"(t={t_trig:.9f} s), f_sig={signal_hz:.3f} Hz -> expecting {ref_edges} DAQ edges")
    elif args.ref_edges and args.signal_hz:
        ref_edges, signal_hz = args.ref_edges, args.signal_hz
    else:
        raise SystemExit("give --daq-ref-live, --daq-ref-csv, or both --ref-edges and --signal-hz")

    channels = [int(c) for c in args.channels.split(",")]
    explicit_signal = ([int(c) for c in args.signal_channels.split(",")]
                       if args.signal_channels else None)
    rate_kwargs = {}
    if args.rate_min:
        rate_kwargs["rate_min"] = args.rate_min
    if args.rate_max:
        rate_kwargs["rate_max"] = args.rate_max
    rates = log_rate_sweep(points_per_decade=args.points_per_decade, **rate_kwargs)
    min_rate = args.min_samples_per_period * signal_hz

    out_path = args.output or (RESULTS_DIR / f"m1_{args.firmware_label}_edges.csv")
    f, writer = csv_writer(
        out_path,
        ["firmware", "channels", "rate_hz", "rep", "ok", "reason", "n_samples",
         "delivered_fraction", "ref_edges", "edges_min", "edges_max", "count_error_ppm"],
    )

    dbg = connect(args.port)
    try:
        for n_channels in channels:
            signal_channels = explicit_signal or list(range(n_channels))
            if max(signal_channels) >= n_channels:
                print(f"skip {n_channels} channel(s): signal channel "
                      f"D{max(signal_channels)} would not be enabled")
                continue
            print(f"\n== M1: {n_channels} channel(s), signal on "
                  f"{','.join('D' + str(c) for c in signal_channels)} ==")
            if args.trigger_channel is not None and args.trigger_channel >= n_channels:
                print(f"skip {n_channels} channel(s): trigger channel "
                      f"D{args.trigger_channel} would not be enabled")
                continue
            max_sustained = None
            for rate_hz in rates:
                if rate_hz < min_rate:
                    print(f"  rate={rate_hz:>10} Hz  skipped (< {args.min_samples_per_period:g} "
                          f"samples per signal period)")
                    continue
                n_window = int(round(rate_hz * args.window))
                if not setup_channels(dbg, n_channels, rate_hz,
                                      min(n_window, SIGROK_SAMPLE_LIMIT_MAX)):
                    print(f"  rate={rate_hz:>10} Hz  refused by the device (above its "
                          f"calibrated sampler limit) — stopping sweep")
                    break
                passes = 0
                reasons = set()
                for rep in range(args.reps):
                    rep_ref = ref_edges
                    print(f"    rep {rep}: DAQ{' + DAQ_REF' if live else ''} armed, "
                          f"waiting for generator...", flush=True)
                    trig_ch = (args.trigger_channel if args.trigger_channel is not None
                               else signal_channels[0])
                    cap, ref = paired_capture(live, lambda: capture_triggered_edges(
                        dbg, n_channels, rate_hz, trig_ch, args.window,
                        trigger_timeout=args.trigger_timeout,
                        extra_channels=signal_channels))
                    no_ref = False
                    if live and cap["triggered"]:
                        if ref:
                            rep_ref = len(ref[0]) - 1  # trigger edge = DAQ sample 0
                        else:
                            no_ref = True
                    ok, reason, counts, worst_ppm = evaluate_repetition(
                        cap, signal_channels, rep_ref, args.tolerance_pct)
                    if no_ref:
                        ok, reason = False, "no_reference"
                    writer.writerow({
                        "firmware": args.firmware_label,
                        "channels": n_channels,
                        "rate_hz": rate_hz,
                        "rep": rep,
                        "ok": int(ok),
                        "reason": reason,
                        "n_samples": cap["n_samples"],
                        "delivered_fraction": round(cap["n_samples"] / cap["n_needed"], 6),
                        "ref_edges": rep_ref,
                        "edges_min": min(counts.values()),
                        "edges_max": max(counts.values()),
                        "count_error_ppm": round(worst_ppm, 1),
                    })
                    f.flush()
                    passes += int(ok)
                    if reason:
                        reasons.add(reason)
                all_ok = passes == args.reps
                print(f"  rate={rate_hz:>10} Hz  {passes:>2}/{args.reps} clean"
                      f"  {'OK' if all_ok else 'FAIL ' + ','.join(sorted(reasons))}")
                if all_ok:
                    max_sustained = rate_hz
                elif not args.no_early_stop:
                    print("  (stopping sweep for this channel count — first failed rate)")
                    break
            print(f"  -> max sustained rate: {max_sustained} Hz")
    finally:
        dbg.close()
        f.close()
        if live:
            live.close()

    print(f"\nResults written to {out_path}")


if __name__ == "__main__":
    sys.exit(main())
