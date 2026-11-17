"""benchmarks/common.py — shared helpers for the M1-M4 experiments (TCC Cap.3,
"Métricas e procedimentos experimentais").

Reuses the protocol implementation already in tools/debug_firmware.py
(FirmwareDebugger, decode_stream, find_port) instead of re-parsing the
srpico wire format here; these scripts only add the experiment procedures
(sweeps, repetitions, integrity/gap checks) and CSV output on top of it.

Assumes a counter-pattern stimulus is already being fed to the enabled
digital channels by the bench's waveform generator (Cap.3, "Bancada
experimental") — these scripts capture and validate, they do not generate
the stimulus.
"""
import csv
import sys
import time
from pathlib import Path

BENCHMARKS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = BENCHMARKS_DIR.parent / "tools"
RESULTS_DIR = BENCHMARKS_DIR / "results"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from debug_firmware import (  # noqa: E402
    ABORT_MARKER,
    DONE_MARKER_RE,
    FirmwareDebugger,
    decode_stream,
    find_port,
)


# Cap.3, Sec. "Métricas e procedimentos experimentais": channel counts swept
# for every metric, and repetitions per measurement point.
CHANNEL_SWEEP = (1, 8, 12)
N_REPETITIONS = 30

# Window captured after the trigger fires (Cap.3, bancada: DAQ and DAQ_REF both
# arm the same edge trigger and keep the next TRIGGER_WINDOW_S seconds).
TRIGGER_WINDOW_S = 1.0

# SIGROK_SAMPLE_LIMIT_MAX (includes/handles/handles_internal.h): largest chunk
# the firmware allows per 'L', hence per continuous-mode re-arm cycle.
SIGROK_SAMPLE_LIMIT_MAX = 1_000_000

# Cap.3, Firmware A/B command table: full sigrok-pico rate range.
RATE_MIN_HZ = 5_000
RATE_MAX_HZ = 120_000_000


def log_rate_sweep(rate_min=RATE_MIN_HZ, rate_max=RATE_MAX_HZ, points_per_decade=6):
    """Sample rates in ascending logarithmic steps within [rate_min, rate_max],
    rounded to whole Hz, de-duplicated. Matches "varre-se a taxa de amostragem
    em passos logarítmicos" (Cap.3)."""
    import math

    decades = math.log10(rate_max / rate_min)
    n_points = max(2, int(decades * points_per_decade) + 1)
    rates = []
    for i in range(n_points):
        r = rate_min * (rate_max / rate_min) ** (i / (n_points - 1))
        rates.append(int(round(r)))
    # de-duplicate while preserving order
    seen = set()
    out = []
    for r in rates:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def connect(port=None) -> FirmwareDebugger:
    port = port or find_port()
    if not port:
        raise SystemExit(
            "No serial port found. Pass --port explicitly, e.g. --port /dev/ttyACM0"
        )
    return FirmwareDebugger(port)


def abort_capture(dbg: FirmwareDebugger):
    """Stop any capture still armed/running ('+') and drop what's left in the
    input buffer, so the next command's ACK isn't swallowed by stale data."""
    dbg._send(b"+")
    time.sleep(0.3)
    dbg.ser.reset_input_buffer()


def setup_channels(dbg: FirmwareDebugger, n_channels: int, rate_hz: int, n_samples: int):
    """Reset the device and enable exactly D0..D(n_channels-1), no analog,
    no trigger, at the given rate/sample-count.

    Channels are enabled before the rate is set, in the same order as the
    libsigrok driver (A, D, L, R): Firmware B checks the requested rate
    against its calibrated sampler limit for the enabled channels and refuses
    (no '*' ACK) a rate it cannot sustain.

    @return True if the device accepted the rate; False means the rate is
    above the device's maximum and the point must be skipped (the device
    would otherwise keep its previous rate).
    """
    abort_capture(dbg)
    dbg.reset()
    dbg.identify()
    dbg.disable_all_analog()
    dbg.clear_trigger()
    for ch in range(dbg.n_digital):
        dbg.enable_digital(ch, ch < n_channels)
    # Remembered so capture_fixed_digital() can size its read timeout to the
    # expected capture duration (n_samples / rate) instead of a fixed 10 s.
    dbg._bench_rate_hz = rate_hz
    dbg._bench_n_samples = n_samples
    dbg.set_sample_limit(n_samples)
    return dbg.set_sample_rate(rate_hz)


def validate_counter_sequence(samples, n_bits):
    """Check that `samples` (already masked to n_bits) is a free-running
    binary counter with no missing/duplicated steps.

    @return (ok, first_bad_index) — first_bad_index is None when ok.
    """
    if not samples:
        return False, None
    modulus = 1 << n_bits
    prev = samples[0] & (modulus - 1)
    for i in range(1, len(samples)):
        cur = samples[i] & (modulus - 1)
        expected = (prev + 1) % modulus
        if cur != expected:
            return False, i
        prev = cur
    return True, None


def find_counter_gaps(samples, n_bits):
    """Like validate_counter_sequence, but keeps scanning and reports every
    gap instead of stopping at the first one (Cap.3, M3: "número e tamanho
    das lacunas via quebras na sequência do contador").

    @return list of {'index': int, 'size': int} — size = samples actually
    missing between the two observed values (0 for a plain duplicate).
    """
    if not samples:
        return []
    modulus = 1 << n_bits
    gaps = []
    prev = samples[0] & (modulus - 1)
    for i in range(1, len(samples)):
        cur = samples[i] & (modulus - 1)
        expected = (prev + 1) % modulus
        if cur != expected:
            missing = (cur - expected) % modulus
            gaps.append({"index": i, "size": missing})
        prev = cur
    return gaps


def digital_bytes_per_sample(n_channels: int) -> int:
    """Bytes needed to carry n_channels digital bits on the wire, matching
    the firmware's tx_init(): bytes_per_dig_sample = (highest_bit // 7) + 1,
    where highest_bit = n_channels - 1 for a contiguous D0..D(n-1) mask.

    NOTE: FirmwareDebugger.dig_bps (from tools/debug_firmware.py) is instead
    derived once from the device's *total* digital channel count (the
    identify string), which only matches the wire format when every
    channel is enabled. These benchmarks deliberately enable a subset
    (CHANNEL_SWEEP = 1/8/12), so callers must use THIS value — not
    dbg.dig_bps — when decoding a subset capture (see capture_fixed_digital
    below).
    """
    if n_channels <= 0:
        return 0
    return ((n_channels - 1) // 7) + 1


def capture_fixed_digital(dbg: FirmwareDebugger, n_channels: int, timeout=None):
    """Run a fixed ('F') capture and decode it as a digital-only, n_channels
    stream, using the correct subset-aware dig_bps (see
    digital_bytes_per_sample) instead of FirmwareDebugger.fixed_capture's
    fixed, total-channel-count-based one.

    @return the same result dict as debug_firmware.decode_stream().
    """
    dig_bps = digital_bytes_per_sample(n_channels)
    if timeout is None:
        rate = getattr(dbg, "_bench_rate_hz", None)
        n = getattr(dbg, "_bench_n_samples", None)
        timeout = 10.0 if not rate or not n else max(10.0, n / rate + 10.0)
    dbg.ser.reset_input_buffer()
    dbg._send(b"F\n")
    raw = dbg._read_stream(timeout=timeout)
    return decode_stream(raw, dig_bps, ana_bps=0)


def decode_continuous_stream(raw: bytes, dig_bps: int):
    """Decode a continuous ('C') capture's raw stream.

    Continuous mode re-arms and re-sends a "$<n>+" done marker once per
    firmware-side chunk (capped at SIGROK_SAMPLE_LIMIT_MAX = 1,000,000
    samples by the 'L' command), so a multi-second capture embeds several
    of these markers mid-stream — not just one at the end, as F captures
    do. decode_stream() only strips the LAST marker; any earlier one would
    have its ASCII digit bytes (0x30-0x39) misread as spurious RLE count
    bytes (RLE range is 0x30-0x7F). This splits the raw stream at every
    marker and decodes each segment independently before concatenating.

    A trailing segment with no marker (the capture was stopped by '+'
    before its chunk finished — the firmware sends no marker at all for a
    host-initiated stop) is decoded too; it may report a truncation error
    for a sample cut off mid-byte, which is an expected edge effect of
    stopping at an arbitrary point in time, not a real integrity issue.

    @return dict with 'digital' (concatenated samples), 'errors'
    (concatenated, tagged per segment), 'total_sent' (sum of each
    segment's reported byte count), 'n_segments'.
    """
    digital = []
    errors = []
    total_sent = 0
    start = 0
    n_segments = 0

    for m in DONE_MARKER_RE.finditer(raw):
        segment = raw[start:m.end()]
        result = decode_stream(segment, dig_bps, ana_bps=0)
        digital.extend(result["digital"])
        errors.extend(f"segment {n_segments}: {e}" for e in result["errors"])
        total_sent += result["total_sent"]
        start = m.end()
        n_segments += 1

    tail = raw[start:]
    if tail:
        result = decode_stream(tail, dig_bps, ana_bps=0)
        digital.extend(result["digital"])
        errors.extend(f"tail segment: {e}" for e in result["errors"])
        n_segments += 1

    return {
        "digital": digital,
        "errors": errors,
        "total_sent": total_sent,
        "n_segments": n_segments,
    }


def query_occupancy(dbg: FirmwareDebugger):
    """Send the 'o' command (added alongside occupancy.c/handle_get_cpu_occupancy.c)
    and parse the "<busy_cycles>/<total_cycles>" response.

    Requires firmware built with the DWT occupancy instrumentation — see
    src/occupancy.c and the 'o' entry in sigrok_commands[]. Raises
    RuntimeError if the firmware doesn't understand 'o' (no response before
    timeout, or a bare '*' with nothing else).
    """
    dbg.ser.reset_input_buffer()
    dbg._send(b"o\n")
    resp = b""
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        c = dbg.ser.read(1)
        if c:
            resp += c
            if resp.endswith(b"*"):
                break
    text = resp.rstrip(b"*").strip().decode(errors="replace")
    if "/" not in text:
        raise RuntimeError(
            f"no occupancy data in response {resp!r} — is this firmware built "
            f"with the DWT occupancy instrumentation (src/occupancy.c)?"
        )
    busy_str, total_str = text.split("/", 1)
    return int(busy_str), int(total_str)


def csv_writer(path: Path, fieldnames):
    path = Path(path)  # --output arrives as a str
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    f = open(path, "a", newline="")
    writer = csv.DictWriter(f, fieldnames=fieldnames, restval="")
    if is_new:
        writer.writeheader()
    return f, writer


def decode_rising_edges(raw: bytes, dig_bps: int, channel_bit, n_needed: int):
    """Stream-decode a digital capture ('F' or 'C') straight to the sample
    indices of the rising edges of one or more channels, keeping only the
    first n_needed samples.

    `channel_bit` is a channel index or a sequence of them. Unlike
    decode_stream(), runs repeated by RLE count bytes are skipped
    arithmetically instead of being expanded sample by sample, so a 1 s
    window at 120 MS/s (1.2e8 samples) costs one step per transition rather
    than per sample. Done markers ("$<n>+") are handled per segment, like
    decode_continuous_stream().

    @return dict: 'edges' (sample indices for the first channel; 0 = first
    sample after the trigger), 'edges_by_channel' ({channel: indices}),
    'n_samples' (samples seen, <= n_needed), 'errors'.
    """
    bits = [channel_bit] if isinstance(channel_bit, int) else list(channel_bit)
    edges = {c: [] for c in bits}
    prev = {c: None for c in bits}
    errors = []
    sample_idx = 0

    segments = []
    start = 0
    for m in DONE_MARKER_RE.finditer(raw):
        segments.append(raw[start:m.start()])
        start = m.end()
    if start < len(raw):
        segments.append(raw[start:])

    for seg_no, payload in enumerate(segments):
        if payload.startswith(ABORT_MARKER):
            errors.append(f"segment {seg_no}: abort marker '!!!'")
            payload = payload[len(ABORT_MARKER):]
        i = 0
        have_sample = False
        while i < len(payload) and sample_idx < n_needed:
            b = payload[i]
            if b < 0x80:
                if not (0x30 <= b <= 0x7F) or not have_sample:
                    errors.append(f"segment {seg_no}: bad RLE byte 0x{b:02X} at offset {i}")
                else:
                    sample_idx += (b - 47) if b <= 79 else (b - 78) * 32
                i += 1
                continue
            if i + dig_bps > len(payload):
                errors.append(f"segment {seg_no}: truncated digital sample at offset {i}")
                break
            word = 0
            for k in range(dig_bps):
                word |= (payload[i + k] & 0x7F) << (7 * k)
            i += dig_bps
            for c in bits:
                bit = (word >> c) & 1
                if prev[c] == 0 and bit == 1:
                    edges[c].append(sample_idx)
                prev[c] = bit
            have_sample = True
            sample_idx += 1

    return {"edges": edges[bits[0]], "edges_by_channel": edges,
            "n_samples": min(sample_idx, n_needed), "errors": errors}


def _read_continuous_after_trigger(dbg: FirmwareDebugger, window_s, trigger_timeout, margin_s=1.0):
    """Run 'C', wait for the first byte (= trigger fired), then keep reading
    for window_s + margin_s of wall time and stop with '+'. The margin only
    over-reads; the caller truncates by sample count, which is what defines
    the window.

    @return (raw_bytes, aborted_by_device, trigger_seen)
    """
    old_timeout = dbg.ser.timeout
    dbg.ser.timeout = 0.1
    raw = bytearray()
    aborted = False
    t_first = None
    t_start = time.monotonic()
    try:
        dbg.ser.reset_input_buffer()
        dbg._send(b"C\n")
        while True:
            now = time.monotonic()
            if t_first is None and now - t_start > trigger_timeout:
                break
            if t_first is not None and now - t_first > window_s + margin_s:
                break
            chunk = dbg.ser.read(dbg.ser.in_waiting or 1)
            if chunk:
                if t_first is None:
                    t_first = now
                raw.extend(chunk)
                if ABORT_MARKER in raw[-8:]:
                    aborted = True
                    break
        dbg._send(b"+")
        time.sleep(0.3)
        raw.extend(dbg.ser.read(1 << 20))
    finally:
        dbg.ser.timeout = old_timeout
    return bytes(raw), aborted, t_first is not None


def capture_triggered_edges(dbg: FirmwareDebugger, n_channels: int, rate_hz: int,
                            square_channel: int, window_s=TRIGGER_WINDOW_S,
                            trigger_timeout=30.0, extra_channels=()):
    """Arm a rising-edge trigger on D<square_channel> (pretrigger 0), then
    capture exactly window_s seconds' worth of samples (rate_hz * window_s)
    starting at the trigger, returning the square wave's rising-edge sample
    indices. Sample 0 is the first sample after the trigger.

    Windows that fit the firmware's per-capture cap use one fixed ('F')
    capture; longer ones use continuous ('C') mode. Expects the device to be
    already set up (setup_channels). Mirrors what the DAQ_REF export is
    cut to in daq_ref.py (trigger edge + window_s).

    `extra_channels` also get their rising edges extracted ('edges_by_channel',
    keyed by channel); the trigger stays on `square_channel`.

    @return dict: 'edges', 'edges_by_channel', 'n_samples', 'n_needed',
    'errors', 'aborted', 'triggered'.
    """
    n_needed = int(round(rate_hz * window_s))
    dig_bps = digital_bytes_per_sample(n_channels)
    dbg.set_pretrigger(0)
    dbg.set_trigger(2, square_channel)  # 2 = rising edge

    if n_needed <= SIGROK_SAMPLE_LIMIT_MAX:
        dbg.set_sample_limit(n_needed)
        dbg.ser.reset_input_buffer()
        dbg._send(b"F\n")
        raw = dbg._read_stream(timeout=trigger_timeout + window_s + 10.0)
        aborted = ABORT_MARKER in raw
        triggered = len(raw) > 0
        if not triggered:
            abort_capture(dbg)
    else:
        dbg.set_sample_limit(SIGROK_SAMPLE_LIMIT_MAX)
        raw, aborted, triggered = _read_continuous_after_trigger(
            dbg, window_s, trigger_timeout)

    watch = [square_channel] + [c for c in extra_channels if c != square_channel]
    result = decode_rising_edges(raw, dig_bps, watch, n_needed)
    result.update(n_needed=n_needed, aborted=aborted, triggered=triggered)
    return result
