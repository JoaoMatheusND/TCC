#!/usr/bin/env python3
"""benchmarks/plots.py — article-style figures for the TCC (Cap.4, Resultados).

Reads the CSVs in results/ and writes one vector PDF (plus a PNG preview) per
figure. Each figure compares Firmware A and Firmware B on the same axes, one
panel per channel count; the curve joins the mean over the repetitions and the
shaded band and bars span the minimum to the maximum repetition, so the
extremes stay visible (compare.py's Mann-Whitney tests still compare the full
distributions). A figure whose data
is missing for one firmware is drawn
with the firmware it has; one with no data at all is skipped.

Encoding: A = blue, solid line, circles; B = orange, dashed line, squares, so
the two stay apart in grayscale print and for colour-blind readers
(palette checked with the dataviz validator: CVD dE 24.7).

Usage:
  python3 benchmarks/plots.py                       # all figures -> results/figures/
  python3 benchmarks/plots.py --outdir ../tcc/TCC/Cap04/img --only m2 m4
  python3 benchmarks/plots.py --width 3.4           # single-column width (inches)
"""
import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.ticker import FuncFormatter, LogLocator  # noqa: E402

from common import RESULTS_DIR  # noqa: E402

FIRMWARES = {
    "A": {"label": "Firmware A (PIO+DMA)", "color": "#2a78d6", "marker": "o", "ls": "-"},
    "B": {"label": "Firmware B (CPU/GPIO)", "color": "#eb6834", "marker": "s", "ls": "--"},
}
INK = "#2b2b2b"
MUTED = "#6b6b6b"
GRID = "#e4e4e4"
USB_FS_CEILING_MBPS = 1.216

LM_FONT_DIRS = ["/usr/share/texmf/fonts/opentype/public/lm",
                "/usr/share/texlive/texmf-dist/fonts/opentype/public/lm"]


def setup_style():
    """Serif text matching the LaTeX body (Latin Modern when installed)."""
    family = "DejaVu Serif"
    for d in LM_FONT_DIRS:
        p = Path(d)
        if p.is_dir():
            for f in p.glob("lmroman10-*.otf"):
                font_manager.fontManager.addfont(str(f))
            family = "Latin Modern Roman"
            break
    plt.rcParams.update({
        "font.family": "serif",
        "font.serif": [family, "DejaVu Serif"],
        "mathtext.fontset": "cm",
        "font.size": 9,
        "axes.titlesize": 9,
        "axes.labelsize": 9,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.fontsize": 8,
        "axes.edgecolor": MUTED,
        "axes.labelcolor": INK,
        "text.color": INK,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "axes.linewidth": 0.6,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "xtick.minor.width": 0.4,
        "ytick.minor.width": 0.4,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.grid.which": "major",
        "grid.color": GRID,
        "grid.linewidth": 0.5,
        "lines.linewidth": 1.2,
        "lines.markersize": 4,
        "legend.frameon": False,
        "figure.dpi": 150,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.02,
        "pdf.fonttype": 42,
    })


def rate_formatter(x, _pos):
    """Sample rate tick in units of 10^6 Sa/s (the axis label carries the decade)."""
    return f"{x / 1e6:.3g}"


def read(name):
    path = RESULTS_DIR / name
    return pd.read_csv(path) if path.exists() else None


def load_pair(pattern_a, pattern_b):
    """{firmware: DataFrame} for the CSVs that exist."""
    out = {}
    for fw, name in (("A", pattern_a), ("B", pattern_b)):
        if isinstance(name, (list, tuple)):
            name = next((n for n in name if (RESULTS_DIR / n).exists()), name[-1])
        df = read(name)
        if df is not None and len(df):
            out[fw] = df
    return out


def summarize(df, value, by=("channels", "rate_hz")):
    """Mean, minimum and maximum of `value` per (channels, rate)."""
    g = df.groupby(list(by))[value]
    return pd.DataFrame({"mean": g.mean(), "min": g.min(), "max": g.max(),
                         "n": g.size()}).reset_index()


def draw_series(ax, s, fw, label=True):
    """Curve through the means, a shaded min-max band, and min/max ticks."""
    st = FIRMWARES[fw]
    ax.fill_between(s["rate_hz"], s["min"], s["max"], color=st["color"], alpha=0.15,
                    lw=0, zorder=2)
    yerr = [(s["mean"] - s["min"]).clip(lower=0), (s["max"] - s["mean"]).clip(lower=0)]
    ax.errorbar(s["rate_hz"], s["mean"], yerr=yerr, color=st["color"], ls=st["ls"],
                marker=st["marker"], mfc=st["color"], mec="white", mew=0.6,
                elinewidth=0.8, capsize=0,
                label=st["label"] if label else None, zorder=3)
    # Min and max as explicit ticks, drawn over the mean marker so they stay
    # visible even when the repetitions agree (min = max = mean).
    for y in (s["min"], s["max"]):
        ax.plot(s["rate_hz"], y, ls="none", marker="_", ms=7, mew=1.3,
                color=st["color"], zorder=4)


def panels(channel_counts, width, height):
    n = len(channel_counts)
    fig, axes = plt.subplots(1, n, figsize=(width, height), sharey=True, squeeze=False)
    axes = axes[0]
    for ax, ch in zip(axes, channel_counts):
        ax.set_title(f"{ch} canal" if ch == 1 else f"{ch} canais", loc="left", color=INK)
        ax.set_xscale("log")
        ax.xaxis.set_major_formatter(FuncFormatter(rate_formatter))
        ax.xaxis.set_minor_formatter(FuncFormatter(lambda *_: ""))
    return fig, axes


def finish(fig, axes, xlabel, ylabel, outdir, name, legend=True):
    for ax in axes:
        ax.set_xlabel(xlabel)
    axes[0].set_ylabel(ylabel)
    if legend:
        handles, labels = axes[0].get_legend_handles_labels()
        for ax in axes[1:]:
            h, l = ax.get_legend_handles_labels()
            for hh, ll in zip(h, l):
                if ll not in labels:
                    handles.append(hh)
                    labels.append(ll)
        if handles:
            fig.legend(handles, labels, loc="upper center", ncol=len(handles),
                       bbox_to_anchor=(0.5, 1.06))
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(outdir / f"{name}.{ext}")
    plt.close(fig)
    print(f"  wrote {outdir / name}.pdf")


def plot_by_rate(data, value, ylabel, outdir, name, width, height,
                 channels=None, transform=None, ylim=None, ylog=False, hlines=()):
    """Generic A-vs-B figure: mean and min-max of `value` against sample rate,
    one panel per channel count."""
    if not data:
        print(f"  skip {name}: no data")
        return
    frames = {}
    for fw, df in data.items():
        df = df.copy()
        if transform:
            df = transform(df)
        if len(df):
            frames[fw] = df
    if not frames:
        print(f"  skip {name}: no rows after filtering")
        return
    chs = channels or sorted(set().union(*(set(d["channels"]) for d in frames.values())))
    fig, axes = panels(chs, width, height)
    for ax, ch in zip(axes, chs):
        for fw, df in frames.items():
            sub = df[df["channels"] == ch]
            if len(sub):
                draw_series(ax, summarize(sub, value), fw)
        for y, text in hlines:
            ax.axhline(y, color=MUTED, lw=0.8, ls=":", zorder=1)
            ax.annotate(text, (0.01, y), xycoords=("axes fraction", "data"), ha="left",
                        va="bottom", fontsize=7, color=MUTED)
        if ylog:
            ax.set_yscale("log")
        if ylim:
            ax.set_ylim(*ylim)
    rates = pd.concat([d.loc[d["channels"].isin(chs), "rate_hz"] for d in frames.values()])
    span = rates.max() / rates.min()
    if span < 10:
        # Under one decade a log axis shows at most one labelled tick.
        for ax in axes:
            ax.set_xscale("linear")
            ax.xaxis.set_major_formatter(FuncFormatter(rate_formatter))
            ax.margins(x=0.06)
    elif span < 100:
        # One or two decades leave one or two labelled ticks, which is too few
        # to read a rate off the axis: label the 2/3/5 minor ticks as well.
        for ax in axes:
            ax.xaxis.set_minor_locator(LogLocator(base=10.0, subs=(2.0, 3.0, 5.0),
                                                  numticks=12))
            ax.xaxis.set_minor_formatter(FuncFormatter(rate_formatter))
            ax.tick_params(axis="x", which="minor", labelsize=7)
    finish(fig, axes, "Taxa de amostragem ($10^6$ Sa/s)", ylabel, outdir, name)


# --- per-metric figures -----------------------------------------------------

def fig_m1(outdir, width, height):
    data = load_pair("m1_A_edges.csv", "m1_B_edges.csv")

    def clean(df):
        g = df.groupby(["channels", "rate_hz"])["ok"].mean().mul(100).rename("clean_pct")
        df = df.merge(g.reset_index(), on=["channels", "rate_hz"])
        return df

    plot_by_rate(data, "clean_pct", "Repetições íntegras (%)", outdir, "m1_integras",
                 width, height, transform=clean, ylim=(-5, 105))

    def excess(df):
        # An aborted capture delivers only a few buffers: its edge count
        # measures the abort, not extra edges, so it stays out of this panel.
        df = df[df["delivered_fraction"] >= 0.99].copy()
        df["excess"] = (df["edges_min"] + df["edges_max"]) / 2 - df["ref_edges"]
        return df

    plot_by_rate(data, "excess", "Bordas DAQ $-$ referência", outdir, "m1_excesso_bordas",
                 width, height, transform=excess, hlines=((0, ""),))


def fig_m2(outdir, width, height):
    data = load_pair("m2_A_trig.csv", "m2_B_trig.csv")

    def above_nyquist(df):
        f_sig = df["ref_n_periods"] / df["window_s"]
        return df[df["rate_hz"] / f_sig > 2.05].copy()

    plot_by_rate(data, "period_std_ns", "Desvio-padrão do período (ns)", outdir,
                 "m2_jitter_std", width, height, transform=above_nyquist)

    def lost(df):
        df = above_nyquist(df)
        df["lost_pct"] = -df["period_count_diff"] / df["ref_n_periods"] * 100
        return df

    plot_by_rate(data, "lost_pct", "Períodos perdidos (%)", outdir, "m2_periodos_perdidos",
                 width, height, transform=lost, hlines=((0, ""),))


def sustained(df):
    """Completed captures, only at conditions where most repetitions completed:
    a lone survivor among aborted repetitions is not a sustained rate."""
    ok = (df["aborted"] == 0) & (df["stream_s"] > 1)
    share = ok.groupby([df["channels"], df["rate_hz"]]).transform("mean")
    return df[ok & (share >= 0.5)].copy()


def fig_m3(outdir, width, height):
    data = load_pair("m3_A.csv", "m3_B.csv")

    def per_min(df):
        df = sustained(df)
        df["gaps_per_min"] = df["n_gaps"] / df["stream_s"] * 60
        return df

    plot_by_rate(data, "gaps_per_min", "Lacunas por minuto", outdir, "m3_lacunas",
                 width, height, transform=per_min)

    def delivered(df):
        df = sustained(df)
        df["delivered_pct"] = df["delivered_fraction"] * 100
        return df

    plot_by_rate(data, "delivered_pct", "Amostras entregues (%)", outdir, "m3_entregues",
                 width, height, transform=delivered, hlines=((100, ""),))

    def aborted(df):
        g = df.groupby(["channels", "rate_hz"])["aborted"].mean().mul(100).rename("aborted_pct")
        return df.merge(g.reset_index(), on=["channels", "rate_hz"])

    plot_by_rate(data, "aborted_pct", "Capturas abortadas (%)", outdir, "m3_abortadas",
                 width, height, transform=aborted, ylim=(-5, 105))

    # Where gaps fall inside the firmware's 1M-sample continuous chunk.
    rows = []
    for fw, df in data.items():
        if "gap_index_mod_chunk" not in df:
            continue
        for v in df["gap_index_mod_chunk"].dropna().astype(str):
            rows += [(fw, int(x)) for x in v.split(";") if x]
    if rows:
        pos = pd.DataFrame(rows, columns=["fw", "idx"])
        fig, ax = plt.subplots(figsize=(width / 2, height))
        for fw, sub in pos.groupby("fw"):
            st = FIRMWARES[fw]
            ax.hist(sub["idx"], bins=np.logspace(0, 6, 31), color=st["color"],
                    edgecolor="white", linewidth=0.5, label=st["label"], alpha=0.9)
        ax.set_xscale("log")
        ax.set_xlabel("Posição da lacuna no bloco de $10^6$ amostras")
        ax.set_ylabel("Lacunas")
        ax.legend(loc="upper right")
        fig.tight_layout()
        for ext in ("pdf", "png"):
            fig.savefig(outdir / f"m3_posicao_lacunas.{ext}")
        plt.close(fig)
        print(f"  wrote {outdir / 'm3_posicao_lacunas'}.pdf")


def fig_m4(outdir, width, height):
    data = load_pair(("m4_A_valid.csv", "m4_A.csv"), "m4_B.csv")

    def pct(df):
        df = df[df["decode_errors"] == 0].copy()
        df["occ_pct"] = df["occupancy_fraction"] * 100
        return df

    plot_by_rate(data, "occ_pct", "Ocupação da CPU (%)", outdir, "m4_ocupacao",
                 width, height, transform=pct, ylim=(-3, 105))


def fig_usb(outdir, width, height):
    df = read("usb_throughput.csv")
    if df is None:
        print("  skip usb_vazao: no data")
        return
    df = df[(df["aborted"] == 0) & (df["elapsed_s"] > 1) & (df["bytes_per_sample"] > 0.5)]
    df = df.assign(fw=df["label"].str[0])
    data = {fw: g.drop(columns="fw") for fw, g in df.groupby("fw") if fw in FIRMWARES}
    plot_by_rate(data, "wire_MBps", "Vazão no barramento (MB/s)", outdir, "usb_vazao",
                 width, height, ylim=(0, 1.35),
                 hlines=((USB_FS_CEILING_MBPS, "teto teórico Full-Speed"),))


FIGURES = {"m1": fig_m1, "m2": fig_m2, "m3": fig_m3, "m4": fig_m4, "usb": fig_usb}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outdir", default=str(RESULTS_DIR / "figures"))
    ap.add_argument("--only", nargs="*", choices=sorted(FIGURES), default=None)
    ap.add_argument("--width", type=float, default=6.3,
                    help="figure width in inches (6.3 = A4 text width)")
    ap.add_argument("--height", type=float, default=2.4)
    args = ap.parse_args()

    setup_style()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for key in args.only or FIGURES:
        print(f"[{key}]")
        FIGURES[key](outdir, args.width, args.height)


if __name__ == "__main__":
    sys.exit(main())
