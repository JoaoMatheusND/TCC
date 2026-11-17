#!/usr/bin/env python3
"""benchmarks/daq_ref.py — reference measurement of the bench's square wave
from a DAQ_REF Logic 2 digital CSV export, optionally used to calibrate an M2
result CSV.

The DAQ under test only knows its *requested* sample rate. m2_jitter.py turns
"samples between rising edges" into nanoseconds with that requested rate, so a
rate the firmware did not really achieve silently skews every period. The
DAQ_REF, clocked independently, gives the true period of the generator, which
lets us recover the rate the DAQ actually sampled at:

    period_samples = period_mean_ns * rate_hz / 1e9      (what the DAQ saw)
    fs_real_hz     = period_samples / T_ref_s            (T_ref from DAQ_REF)
    rate_error_pct = (fs_real_hz - rate_hz) / rate_hz * 100

DAQ_REF export: Logic 2 -> Export Data -> Digital (CSV). One row per
transition, columns "Time [s]", "Channel 0", "Channel 1", ...

Trigger window: only the WINDOW seconds (default 1 s) starting at the trigger
are used, matching what m2_jitter.py keeps on the DAQ side. The trigger time is
the first rising edge of --trigger-channel (default: --channel) in the table;
override with --t-trigger when the export's own t=0 is the trigger. The edge
that fires the trigger is included (it is the DAQ's sample 0). The table must
therefore span at least trigger + window; a shorter one is reported and the
stats are flagged as covering less than the window.

Validity: DAQ_REF at 24 MS/s resolves ~42 ns and is only a trustworthy
reference up to a few MHz of signal. Rows where the DAQ saw fewer than
--min-samples-per-period samples per period are flagged "undersampled".

Usage:
  # reference stats only, 1 s after the trigger
  python3 benchmarks/daq_ref.py digital.csv --channel 0

  # calibrate an M2 result file
  python3 benchmarks/daq_ref.py digital.csv --channel 0 \
      --m2-csv benchmarks/results/m2_B.csv

  # no manual export: arm the DAQ_REF through the Logic 2 Automation API
  # (Logic 2 running, Preferences -> Enable scripting socket server), then
  # fire the generator; the capture is exported and used as the CSV
  python3 benchmarks/daq_ref.py --live --channel 0 --m2-csv results/m2_B_trig.csv
"""
import argparse
import csv
import os
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

# Flags the Logic 2 AppImage needs on this bench: no Chromium sandbox (blocked
# by AppArmor user-namespace restrictions) and no GPU (its WebGL renderer
# crashes with exitCode 134 after a while), plus the automation server.
LOGIC2_FLAGS = ("--no-sandbox", "--disable-gpu")


class DaqRefCrashed(Exception):
    """Logic 2 died (or stopped answering) during a capture."""


class DaqRefLive:
    """DAQ_REF driven through the Logic 2 Automation API, for arming it in
    parallel with the DAQ: arm() starts a rising-edge-triggered capture and
    returns immediately (the DAQ_REF then waits for the generator, like the
    DAQ does); collect() blocks until trigger + window_s has been recorded and
    returns the exported CSV path.

    Logic 2 must be running with the scripting socket server enabled
    (Preferences -> Enable scripting socket server, default port 10430), or
    `launch` must point at the Logic 2 executable/AppImage: it is then started
    when the port does not answer, and restarted whenever it crashes (see
    paired_capture()). In a triggered capture the table's t=0 is the trigger.
    """

    def __init__(self, channel, window_s=1.0, sample_rate_hz=24_000_000, out_dir=None,
                 host="127.0.0.1", port=10430, device_id=None, threshold_v=None,
                 launch=None):
        try:
            from saleae import automation
        except ImportError:
            raise SystemExit("live DAQ_REF needs the Logic 2 automation package: "
                             "pip install logic2-automation")
        self._automation = automation
        self.channel = int(channel)
        self.window_s = window_s
        self.sample_rate_hz = int(sample_rate_hz)
        self.device_id = device_id
        self.threshold_v = threshold_v
        self.out_dir = Path(out_dir or Path(__file__).resolve().parent / "results" / "daq_ref")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.host, self.port = host, port
        self.launch = os.path.expanduser(launch) if launch else None
        self._proc = None
        self._manager = None
        self._capture = None
        if not self._try_connect():
            if not self.launch:
                raise SystemExit(
                    f"cannot reach Logic 2 at {host}:{port} (connection refused).\n"
                    "Open Logic 2 and enable Preferences -> Enable scripting socket "
                    "server (port 10430), or pass --daq-ref-launch <Logic AppImage> so "
                    "the script starts (and restarts) it itself.\n"
                    "Only Logic 2-supported devices work; close any other app using "
                    "the analyzer.")
            self._start_logic()

    def _try_connect(self):
        try:
            self._manager = self._automation.Manager.connect(address=self.host,
                                                             port=self.port)
            return True
        except Exception as e:
            if "UNAVAILABLE" not in str(e):
                raise
            return False

    def _start_logic(self, timeout_s=120.0):
        """Launch Logic 2 with the automation server and wait until it answers."""
        log = open(self.out_dir / "logic2.log", "a")
        print(f"    starting Logic 2 ({self.launch}) — log: {log.name}", flush=True)
        self._launch_t = time.time()
        self._proc = subprocess.Popen(
            [self.launch, *LOGIC2_FLAGS, "--automation", "--automationPort", str(self.port)],
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise SystemExit(f"Logic 2 exited during startup (code "
                                 f"{self._proc.returncode}); see {log.name}")
            time.sleep(2.0)
            if self._try_connect():
                return
        raise SystemExit(f"Logic 2 did not open port {self.port} within {timeout_s:g} s")

    def restart(self):
        """Recover from a crash: drop the dead connection, kill the Logic 2 we
        started (if any) and start a fresh one. Without `launch`, abort."""
        self._capture = None
        try:
            self._manager.close()
        except Exception:
            pass
        if not self.launch:
            raise SystemExit("Logic 2 stopped answering (it probably crashed — check its "
                             "terminal). Restart it and rerun, or pass --daq-ref-launch "
                             "so the script restarts it by itself.")
        self._stop_own_logic()
        self._start_logic()

    def arm(self):
        a = self._automation
        cfg = {"enabled_digital_channels": [self.channel],
               "digital_sample_rate": self.sample_rate_hz}
        if self.threshold_v is not None:
            cfg["digital_threshold_volts"] = self.threshold_v
        try:
            self._capture = self._manager.start_capture(
                device_id=self.device_id,
                device_configuration=a.LogicDeviceConfiguration(**cfg),
                capture_configuration=a.CaptureConfiguration(
                    capture_mode=a.DigitalTriggerCaptureMode(
                        trigger_type=a.DigitalTriggerType.RISING,
                        trigger_channel_index=self.channel,
                        after_trigger_seconds=self.window_s)))
        except Exception as e:
            self._capture = None
            if type(e).__name__ == "MissingDeviceError":
                # The original (FX2) Logic re-enumerates on USB after a capture is
                # stopped; it is briefly absent. Wait for it to come back.
                if self._wait_for_device():
                    return self.arm()
                raise DaqRefCrashed("DAQ_REF disappeared from USB and did not come "
                                    "back") from None
            if "UNAVAILABLE" not in str(e):
                raise
            raise DaqRefCrashed(f"Logic 2 crashed before arming: {e}") from None

    def _wait_for_device(self, timeout_s=20.0):
        """Poll Logic 2 until a physical device is listed again."""
        print("    [daq_ref] device missing (USB re-enumeration?), waiting...", flush=True)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            time.sleep(1.0)
            try:
                if any(not d.is_simulation for d in self._manager.get_devices()):
                    time.sleep(1.0)  # let Logic 2 finish loading the device firmware
                    return True
            except Exception:
                return False
        return False

    def collect(self):
        """Wait for the capture to finish, export it, return the CSV path."""
        cap, self._capture = self._capture, None
        print("    DAQ_REF: waiting for trigger + window...", flush=True)
        try:
            cap.wait()
            cap.export_raw_data_csv(directory=str(self.out_dir),
                                    digital_channels=[self.channel])
        except Exception as e:
            # Any failure here (device ReadTimeout, renderer crash, dead
            # socket) leaves this capture unusable; the caller retries.
            raise DaqRefCrashed(f"DAQ_REF capture failed: {e}") from None
        finally:
            self._close_quietly(cap)
        return self.out_dir / "digital.csv"

    def collect_edges(self):
        """collect() + rising edges in the window. @return (edges, t_ref_s) or
        None when the table has too few edges to be a reference."""
        path = self.collect()
        try:
            edges, _, _ = read_window_edges(path, str(self.channel), None, 0.0, self.window_s)
            return edges, period_stats(edges)["t_ref_s"]
        except SystemExit:
            return None

    @staticmethod
    def _close_quietly(cap):
        # If Logic 2 itself died, close() would raise and bury the real error.
        try:
            cap.close()
        except Exception:
            pass

    def cancel(self):
        """Drop an armed capture that will not be collected (DAQ never triggered)."""
        cap, self._capture = self._capture, None
        if cap is not None:
            try:
                cap.stop()
            except Exception:
                pass
            self._close_quietly(cap)

    def close(self):
        self.cancel()
        try:
            self._manager.close()
        except Exception:
            pass
        # A Logic 2 started by us (--daq-ref-launch) runs in its own session and
        # would otherwise outlive the script, holding the device and the port.
        self._stop_own_logic()

    def _own_logic_pids(self):
        """PIDs of the Logic 2 main process(es) we launched. The AppImage
        runtime (self._proc) spawns Logic in its own session, so killing the
        runtime alone leaves Logic running: match it by command line (our
        automation port, not a Chromium --type= helper) and start time."""
        pids = []
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/cmdline", "rb") as fh:
                    # Chromium rewrites its argv as one space-joined string.
                    args = fh.read().replace(b"\0", b" ").split()
                started = os.stat(f"/proc/{entry}").st_mtime
            except OSError:
                continue
            if (args and args[0].endswith(b"/Logic")
                    and b"--automationPort" in args and str(self.port).encode() in args
                    and not any(a.startswith(b"--type=") for a in args)
                    and started >= self._launch_t - 1.0):
                pids.append(int(entry))
        return pids

    def _stop_own_logic(self):
        if self._proc is None:
            return
        for pid in self._own_logic_pids():
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        time.sleep(2.0)  # let the device and the port be released


def paired_capture(live, daq_capture, retries=3):
    """One repetition with the DAQ and (optionally) the DAQ_REF armed together.

    Arms the DAQ_REF, runs `daq_capture()` (which arms the DAQ and blocks until
    its window is in), then collects the DAQ_REF's capture of the same trigger.
    If Logic 2 crashes at any point the whole repetition is redone — both
    instruments — after restarting it, so a tool crash never shows up as an
    acquisition failure in the results.

    @return (cap, ref): `cap` from daq_capture(); `ref` is (edges, t_ref_s),
    or None when there is no live DAQ_REF, the DAQ never triggered, or the
    DAQ_REF table had too few edges.
    """
    if live is None:
        return daq_capture(), None
    for attempt in range(retries):
        try:
            live.arm()
            cap = daq_capture()
            if not cap["triggered"]:
                live.cancel()
                return cap, None
            return cap, live.collect_edges()
        except DaqRefCrashed as e:
            print(f"    [daq_ref] {e} — restarting Logic 2 and redoing this repetition "
                  f"({attempt + 1}/{retries})", flush=True)
            live.restart()
    raise SystemExit(f"Logic 2 crashed {retries} times in a row on the same repetition")


def capture_live_csv(channel, window_s=1.0, sample_rate_hz=24_000_000, out_dir=None,
                     host="127.0.0.1", port=10430, device_id=None, threshold_v=None,
                     launch=None, live=None, retries=3):
    """One-shot arm + wait + export. Returns (csv_path, t_trigger=0.0).
    Reuses `live` when given (kept open), else opens and closes its own."""
    own = live is None
    if own:
        live = DaqRefLive(channel, window_s, sample_rate_hz, out_dir, host, port,
                          device_id, threshold_v, launch)
    try:
        for attempt in range(retries):
            try:
                live.arm()
                print(f"DAQ_REF armed on Channel {channel} (rising edge, {window_s:g} s "
                      f"after trigger) — fire the generator now")
                return live.collect(), 0.0
            except DaqRefCrashed as e:
                print(f"    [daq_ref] {e} — restarting ({attempt + 1}/{retries})")
                live.restart()
        raise SystemExit(f"Logic 2 crashed {retries} times in a row")
    finally:
        if own:
            live.close()


def _column_index(cols, channel):
    if channel.isdigit():
        idx = next((i for i, h in enumerate(cols) if h == f"Channel {channel}"), None)
        if idx is None and len(cols) > int(channel) + 1:
            idx = int(channel) + 1
    else:
        idx = next((i for i, h in enumerate(cols) if h == channel), None)
    if idx is None:
        raise SystemExit(f"channel {channel!r} not found in header {cols}")
    return idx


def read_window_edges(path, channel, trigger_channel=None, t_trigger=None,
                      window_s=1.0):
    """Rising-edge timestamps (s) of `channel` in [t_trig, t_trig + window_s]
    from a Logic 2 CSV, streaming the file once.

    t_trig is `t_trigger` if given, else the first rising edge of
    `trigger_channel` (default: `channel`). With window_s=None the window has
    no end.

    @return (edges, t_trig, t_last) — t_last is the last row's time, used to
    check the table covers the whole window.
    """
    edges = []
    t_last = None
    with open(path, newline="") as f:
        reader = csv.reader(f)
        cols = [h.strip() for h in next(reader)]
        idx = _column_index(cols, channel)
        trig_idx = _column_index(cols, trigger_channel) if trigger_channel else idx

        t_trig = t_trigger
        t_end = None if t_trig is None or window_s is None else t_trig + window_s
        prev = prev_trig = None
        for row in reader:
            if len(row) <= max(idx, trig_idx):
                continue
            t = float(row[0])
            t_last = t
            if t_end is not None and t > t_end:
                break
            bit = int(row[idx])
            trig_bit = int(row[trig_idx])
            if t_trig is None and prev_trig == 0 and trig_bit == 1:
                t_trig = t
                t_end = None if window_s is None else t + window_s
            if t_trig is not None and prev == 0 and bit == 1 and t >= t_trig:
                edges.append(t)
            prev, prev_trig = bit, trig_bit
    if t_trig is None:
        raise SystemExit("no rising edge found on the trigger channel")
    return edges, t_trig, t_last


def period_stats(edges):
    """Period statistics from rising-edge times. f_sig uses the whole span
    (n-1 periods / elapsed), which averages out the DAQ_REF's ~42 ns
    quantisation instead of inheriting it per period."""
    if len(edges) < 3:
        raise SystemExit(f"need at least 3 rising edges, found {len(edges)}")
    periods = [b - a for a, b in zip(edges, edges[1:])]
    span = edges[-1] - edges[0]
    return {
        "n_edges": len(edges),
        "t_ref_s": span / (len(edges) - 1),
        "f_sig_hz": (len(edges) - 1) / span,
        "period_std_ns": statistics.pstdev(periods) * 1e9,
        "period_pkpk_ns": (max(periods) - min(periods)) * 1e9,
    }


def calibrate_m2(m2_path, out_path, t_ref_s, min_spp, n_periods_ref=None):
    with open(m2_path, newline="") as f:
        rows = list(csv.DictReader(f))
    fields = list(rows[0].keys()) + [
        "t_ref_ns", "samples_per_period", "fs_real_hz", "rate_error_pct", "valid",
        "n_periods_ref", "period_count_diff",
    ]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            mean_ns = r.get("period_mean_ns", "")
            if not mean_ns:
                r.update(t_ref_ns="", samples_per_period="", fs_real_hz="",
                         rate_error_pct="", valid="nodata",
                         n_periods_ref="", period_count_diff="")
                w.writerow(r)
                continue
            rate = float(r["rate_hz"])
            spp = float(mean_ns) * rate / 1e9
            fs_real = spp / t_ref_s
            r["t_ref_ns"] = round(t_ref_s * 1e9, 3)
            r["samples_per_period"] = round(spp, 3)
            r["fs_real_hz"] = round(fs_real, 3)
            r["rate_error_pct"] = round((fs_real - rate) / rate * 100, 4)
            r["valid"] = "ok" if spp >= min_spp else "undersampled"
            # DAQ periods in its window minus DAQ_REF periods in the same 1 s;
            # only comparable for triggered-window rows (see window_s column).
            r["n_periods_ref"] = n_periods_ref if n_periods_ref is not None else ""
            r["period_count_diff"] = (int(r["n_edges"]) - n_periods_ref
                                      if n_periods_ref is not None and r.get("window_s")
                                      and r.get("n_edges") else "")
            w.writerow(r)
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="?", default=None,
                    help="DAQ_REF Logic 2 digital export (omit with --live)")
    ap.add_argument("--live", action="store_true",
                    help="capture from a running Logic 2 via the Automation API "
                         "instead of reading a CSV")
    ap.add_argument("--daq-ref-rate", type=int, default=24_000_000,
                    help="DAQ_REF digital sample rate for --live (default 24 MS/s)")
    ap.add_argument("--daq-ref-host", default="127.0.0.1")
    ap.add_argument("--daq-ref-port", type=int, default=10430)
    ap.add_argument("--daq-ref-device", default=None, help="device id (default: first found)")
    ap.add_argument("--daq-ref-launch", default=None,
                    help="Logic 2 executable/AppImage: started if not running, restarted "
                         "if it crashes")
    ap.add_argument("--channel", default="0",
                    help='channel index ("0") or exact column name')
    ap.add_argument("--trigger-channel", default=None,
                    help="channel whose first rising edge is the trigger (default: --channel)")
    ap.add_argument("--t-trigger", type=float, default=None,
                    help="trigger time in the table's clock (s); skips edge detection")
    ap.add_argument("--window", type=float, default=1.0,
                    help="seconds kept after the trigger (default 1; must match "
                         "m2_jitter.py --window)")
    ap.add_argument("--m2-csv", default=None, help="m2_jitter.py result CSV to calibrate")
    ap.add_argument("--min-samples-per-period", type=float, default=4.0)
    ap.add_argument("--output", default=None,
                    help="calibrated CSV path (default: <m2-csv stem>_calibrated.csv)")
    args = ap.parse_args()

    if args.live:
        args.csv, args.t_trigger = capture_live_csv(
            args.channel, args.window, args.daq_ref_rate, host=args.daq_ref_host,
            port=args.daq_ref_port, device_id=args.daq_ref_device,
            launch=args.daq_ref_launch)
        print(f"DAQ_REF export: {args.csv}")
    elif not args.csv:
        ap.error("give a CSV path or --live")
    edges, t_trig, t_last = read_window_edges(
        args.csv, args.channel, args.trigger_channel, args.t_trigger, args.window)
    s = period_stats(edges)

    print(f"trigger at      : {t_trig:.9f} s (window {args.window:g} s)")
    covered = (t_last - t_trig) if t_last is not None else 0.0
    if covered < args.window - 3 * s["t_ref_s"]:
        print(f"WARNING: table ends {covered:.3f} s after the trigger, shorter than the "
              f"{args.window:g} s window — stats cover less than the DAQ window")
    print(f"edges           : {s['n_edges']}")
    print(f"T_ref           : {s['t_ref_s'] * 1e9:.3f} ns")
    print(f"f_sig           : {s['f_sig_hz']:.3f} Hz")
    print(f"period std      : {s['period_std_ns']:.3f} ns")
    print(f"period pk-pk    : {s['period_pkpk_ns']:.3f} ns")
    print("(DAQ_REF resolution ~42 ns at 24 MS/s: pk-pk/std below that is quantisation.)")

    if args.m2_csv:
        out = args.output or str(Path(args.m2_csv).with_name(
            Path(args.m2_csv).stem + "_calibrated.csv"))
        n = calibrate_m2(args.m2_csv, out, s["t_ref_s"], args.min_samples_per_period,
                         n_periods_ref=s["n_edges"] - 1)
        print(f"\ncalibrated {n} rows -> {out}")


if __name__ == "__main__":
    sys.exit(main())
