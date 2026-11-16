#!/usr/bin/env python3
"""fig_reruns.py: the rerun figure for the SI, from results/rerun_summary.csv.

Two panels, one point per run, grouped by arm (as-run values as dashed references):
  left   closed-model posterior P(quantum | data), the quantity every run computes from the
         two engines' evidences and the only one defined the same way in every run
  right  the run's own mechanism-support P_mech(quantum), whose formula (severity weights,
         alternatives list) each run authors itself, hence the wider spread
Usage: python fig_reruns.py --summary results/rerun_summary.csv --out results/rerun_distribution
"""
import argparse, csv, math
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

STEEL, TEAL, TERRA, GREY = "#3D6B9E", "#1FA89C", "#C65A2E", "#9a9a9a"
ARMS = [("B_nointernet", "B\nno internet"), ("C_litheld", "C\n+ literature\nwithheld"),
        ("D_litheld_noprior", "D\n+ prior\nassignment\nremoved"), ("E_nowindows", "E\n+ tool\nwindows\nremoved")]
ASRUN = {"P_H2_given_D": 0.95006, "P_mech_quantum": 0.6818}


def load(path):
    rows = list(csv.DictReader(open(path)))
    out = {}
    for r in rows:
        if r.get("ok") != "True":
            continue
        out.setdefault(r["arm"], []).append(r)
    return out


def num(r, k):
    try:
        v = float(r.get(k, ""))
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def main(summary, out, style):
    if style and Path(style).exists():
        plt.style.use(style)
    runs = load(summary)
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 4.0), sharey=True)
    panels = [("P_H2_given_D", "closed-model posterior P(quantum | data)"),
              ("P_mech_quantum", "run-defined mechanism support P_mech(quantum)")]
    for ax, (key, title) in zip(axes, panels):
        for i, (arm, label) in enumerate(ARMS):
            rs = runs.get(arm, [])
            ys = [num(r, key) for r in rs]
            ys = [y for y in ys if y is not None]
            n_q = sum(1 for r in rs if r.get("quantum_favoured") == "True")
            xs = [i + 0.12 * (j - (len(ys) - 1) / 2) for j in range(len(ys))]
            ax.plot(xs, ys, "o", ms=7, mfc=STEEL, mec="white", mew=0.8, alpha=0.9, zorder=3)
            note = f"{n_q}/{len(rs)} quantum" + (f"\n{len(ys)} of {len(rs)} report it" if len(ys) < len(rs) else "")
            ax.text(i, -0.06, note, ha="center", va="top", fontsize=7.5, color=GREY, linespacing=1.1)
        ax.axhline(ASRUN[key], color=TERRA, ls="--", lw=1.2, zorder=2)
        ax.text(-0.55, ASRUN[key] - 0.02, f"as-run {ASRUN[key]:.2f}", color=TERRA, fontsize=8, ha="left", va="top")
        ax.axhline(0.5, color=GREY, ls=":", lw=1.0, zorder=1)
        ax.set_xticks(range(len(ARMS)))
        ax.set_xticklabels([l for _, l in ARMS], fontsize=8)
        ax.set_xlim(-0.6, len(ARMS) - 0.4)
        ax.set_ylim(-0.22, 1.08)
        ax.set_title(title, fontsize=10, fontweight="normal", loc="center")
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.tick_params(top=False, right=False, which="both")
    axes[0].set_ylabel("probability")
    fig.suptitle("Discovery-loop first pass, five reruns per ablation arm (Opus 4.8, headless, blinded)",
                 x=0.01, ha="left", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    for ext in ("png", "pdf"):
        fig.savefig(f"{out}.{ext}", dpi=220, bbox_inches="tight")
    print("wrote", out + ".png/.pdf")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", default="results/rerun_summary.csv")
    ap.add_argument("--out", default="results/rerun_distribution")
    ap.add_argument("--style", default=None, help="an optional matplotlib style file")
    a = ap.parse_args()
    main(a.summary, a.out, a.style)
