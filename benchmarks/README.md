# benchmarks/

Data-collection scripts for the four metrics defined in the TCC, Cap.3,
"Métricas e procedimentos experimentais" (Sec. 3.5), plus the statistical
comparison procedure from Sec. 3.6 ("Análise estatística").

These scripts run against **one firmware at a time**, over the serial/USB
CDC port. To compare Firmware A and Firmware B you run the same script
twice — once per firmware/board — tagging each run with `--firmware-label`,
then feed both CSVs to `compare.py`.

## Setup

```
pip install -r benchmarks/requirements.txt
```

`pyserial` is enough to run `m1`/`m2`/`m3`/`m4` (data collection). `numpy`
and `scipy` are only needed for `stats.py`/`compare.py` (the Sec. 3.6
analysis).

These scripts import `tools/debug_firmware.py` from this repo (protocol
implementation, stream decoder) — run them from the repo root or with
`benchmarks/` on `sys.path` (they add `../tools` themselves; no extra setup
needed beyond `cd` into the repo).

## Bench assumptions (Cap.3, "Bancada experimental")

- A waveform generator feeds a reference square wave (known frequency) to
  the enabled channels, in parallel with the DAQ_REF, for M1/M2; M3 still
  needs a free-running binary counter. These scripts capture and validate;
  they do not generate the stimulus.
- One host, one port, one firmware build at a time.

## What's covered vs. manual/out of scope

| Metric | Script | Fully automated? |
|---|---|---|
| M1 — taxa máxima sustentada | `m1_max_rate.py` | Yes: edge count in the 1 s after the trigger vs the DAQ_REF reference (see below). (Comparing B's result against the theoretical ceiling f_clk/C_iter needs C_iter from the sampling loop's disassembly — a manual, offline step, not computed here.) |
| M2 — jitter de amostragem | `m2_jitter.py` | Only the in-band half (period distribution from the decoded samples). The oscilloscope cross-check of a firmware-toggled debug pin is a separate manual bench step — the firmware has no such debug-pin instrumentation. |
| M3 — perda em captura contínua | `m3_continuous_loss.py` | Yes, via gaps in the decoded counter sequence. Firmware doesn't currently report an overflow counter over the wire, so overflow is inferred from gaps/abort, not a dedicated field. |
| M4 — ocupação de CPU | `m4_cpu_occupancy.py` | Yes, but **requires firmware built with the DWT occupancy instrumentation** (`src/occupancy.c`, `handle_get_cpu_occupancy.c`, the `'o'` command). Firmware without it will make this script fail fast with a clear error. |

## Usage

Each script sweeps `--channels` (default `1,8,12`, matching Cap.3's
CHANNEL_SWEEP) and a log-spaced rate sweep within the firmware's supported
range, `N_REPETITIONS = 30` repetitions per point by default (Cap.3, "N=30
repetições").

```
# Firmware A (main branch): flash it, then
python3 benchmarks/m1_max_rate.py --firmware-label A --port /dev/ttyACM0 \
    --daq-ref-csv digital.csv --t-trigger 0
python3 benchmarks/m2_jitter.py   --firmware-label A --square-channel 0
python3 benchmarks/m3_continuous_loss.py --firmware-label A --duration 60
python3 benchmarks/m4_cpu_occupancy.py   --firmware-label A

# Firmware B (ana-gpio-cpu branch): flash it, then repeat with label B
python3 benchmarks/m1_max_rate.py --firmware-label B --port /dev/ttyACM0 \
    --daq-ref-csv digital.csv --t-trigger 0
...

# Firmware B's dual-core / XIP-vs-SRAM / interrupts-on-off variants: same
# scripts, one firmware rebuild per variant, distinct labels, e.g.:
python3 benchmarks/m2_jitter.py --firmware-label B-sram
python3 benchmarks/m2_jitter.py --firmware-label B-xip
```

Results land in `benchmarks/results/m{1,2,3,4}_<label>.csv` (`m1_<label>_edges.csv` for M1, `m2_<label>_trig.csv` for the triggered M2) (append-only —
re-running a script adds more repetitions rather than overwriting).

## Cross-check against the DAQ_REF (1 s after the trigger)

DAQ and DAQ_REF arm the same rising-edge trigger on the same square wave
(generator → both inputs); arm both, then fire the generator.

- DAQ: `m2_jitter.py` arms a rising-edge trigger on `--square-channel`
  (pretrigger 0) and keeps `--window` seconds (default 1) after it. Results:
  `results/m2_<label>_trig.csv` (adds `window_s`, `n_samples`,
  `delivered_fraction`, `first_edge_sample`). Windows up to 1 MS (rate ≤ 1 MS/s) use one fixed
  capture; longer ones use continuous mode and are cut to `rate * window`
  samples. `delivered_fraction < 1` = the DAQ did not deliver the full window.
  Whether the firmware re-waits for the trigger at each continuous re-arm
  (1 MS chunk) is not verified — check `delivered_fraction`.
- DAQ_REF, automated (`--daq-ref-live` in `m1_max_rate.py` / `m2_jitter.py`):
  with Logic 2 open and *Preferences → Enable scripting socket server* on
  (port 10430), every repetition arms the DAQ **and** the DAQ_REF on the same
  rising-edge trigger, then both wait for the generator. Start the script,
  turn the function generator on, leave it running: the script sweeps, takes a
  per-repetition DAQ_REF reference (`results/daq_ref/digital.csv`, trigger at
  t=0) and evaluates at the end. M1 compares each repetition's edge counts to
  that repetition's DAQ_REF count; M2 adds `ref_t_ns`, `ref_n_periods`,
  `fs_real_hz`, `rate_error_pct`, `period_count_diff` to each row. M1 takes one
  extra DAQ_REF capture at startup to learn the signal frequency (generator
  must already be on). `--trigger-timeout` (default 300 s) is how long each
  capture waits for the generator. One-shot: `daq_ref.py --live`. Needs
  `logic2-automation`.
  Pass `--daq-ref-launch ~/Downloads/Logic-2.4.44-linux-x64.AppImage` to let
  the script start Logic 2 itself (`--no-sandbox --disable-gpu --automation`,
  log in `results/daq_ref/logic2.log`) and restart it if it crashes: the
  repetition in progress is then redone on both instruments, so a Logic 2
  crash never counts as a DAQ failure.
- DAQ_REF, manual: export the digital table (Logic 2 → Export Data → CSV), then
  `python3 benchmarks/daq_ref.py digital.csv --channel 0 --m2-csv results/m2_B_trig.csv`.
  Only trigger edge → trigger + `--window` s is used (trigger = first rising
  edge of `--trigger-channel`, default `--channel`; or `--t-trigger`). Keep
  `--window` equal on both sides. The calibrated CSV gets `fs_real_hz`,
  `rate_error_pct`, `n_periods_ref` and `period_count_diff` (DAQ − DAQ_REF
  periods in the window).

### M1 pass criteria

Same signal on every channel under test and on the DAQ_REF. Per repetition
(trigger + 1 s): window fully delivered with no decode errors; every
channel's rising-edge count within `--tolerance-pct` (0.1%) of the DAQ_REF's
count (trigger edge excluded: it is the DAQ's sample 0); all channels equal
within ±1 edge. Rates below 4 samples/period are skipped. Use
`--signal-channels` when the wave reaches only some channels (e.g. `11`).
`reason` in the CSV says why a repetition failed; `count_error_ppm` shows the
real deviation.

## Comparing A vs B (Cap.3, Sec. 3.6)

```
python3 benchmarks/compare.py results/m4_A.csv results/m4_B.csv \
    --value-column occupancy_fraction
```

`--value-column` picks the column to compare per condition
(`(channels, rate_hz)` pair): `occupancy_fraction` for M4,
`period_mean_ns`/`period_std_ns`/`period_pkpk_ns` for M2,
`effective_rate_hz`/`gap_samples_total` for M3, `ok` for M1 (though M1's
pass/fail is usually read directly off its own CSV rather than compared
statistically — a fixed max-rate value doesn't have a distribution to test
per se; the reps mainly guard against a rate that only sometimes fails).

Applies, in one call, Mann-Whitney U (always, non-parametric — no assumed
normality), rank-biserial effect size, Holm-Bonferroni correction across
every condition compared in that call (one metric = one family), and a
95% bootstrap CI (10,000 resamples) for the difference of medians.

## Device-side rate limit (Firmware B)

Firmware B measures its own sampling-loop cost at boot
(`ana_module_calibrate()`) and refuses (no `'*'` ACK) any `R` above the rate
that loop can sustain for the enabled channels, instead of silently sampling
slower than requested. `setup_channels()` therefore enables the channels
before setting the rate (driver order A, D, L, R) and returns whether the
rate was accepted; every script stops the sweep for that channel count at the
first refused rate.

## Known correctness note baked into these scripts

`tools/debug_firmware.py`'s `FirmwareDebugger.dig_bps` is derived once from
the device's *total* digital channel count (the identify string), which
only matches the wire format when every channel is enabled. These
benchmarks deliberately enable a **subset** (1, 8 or 12 out of however many
the board has), so they compute the wire's actual
`bytes_per_dig_sample` themselves (`common.digital_bytes_per_sample`,
mirroring the firmware's `tx_init()`) instead of trusting `dig_bps` —
see `common.capture_fixed_digital` / `common.decode_continuous_stream`.
Continuous ('C') captures also embed a `"$<n>+"` done marker once per
1,000,000-sample chunk (the `'L'` command's cap), not just once at the end
like a fixed ('F') capture — `common.decode_continuous_stream` splits on
every occurrence before decoding, instead of naively decoding the whole
raw stream in one pass.
