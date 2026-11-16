#!/usr/bin/env python3
"""aggregate_pass2.py: one table for the second-pass reruns.

    python aggregate_pass2.py --runs runs --out results [--asrun /path/to/pass2_model_comparison.json]

Each pass-2 run gets a row: the posterior update (P_mech, P_alt, U_closure, A_closure: first
pass -> second pass), whether the FTLM hierarchy at the physical hopping was reproduced (the six
Tm values at eps_r = 12 and 3 that the released cache fixes), the registered-fold correlations
(fractional and Mott GWC in the high-twist domain, r and p), the decision sentence, cost, turns
and minutes. The runs lay their files out differently (tables_pass2/, second_pass/, ...), so
numbers are collected from every JSON written after the run started plus the agent's final
message, and disagreements between the two sources are flagged rather than resolved.

Reference: the as-run pass 2 of the paper (discovery_record/tables/pass2_model_comparison.json):
P_mech 0.6818 -> 0.7143, P_alt 0.3182 -> 0.2857, U_closure 0.7752 -> 0.5671, A_closure 0.2248 ->
0.4329, fold r = -0.409 (p 0.0248) fractional, +0.582 (p 0.00074) Mott, ED Tm at eps_r 3:
18.19 / 30.45 / 14.47 K for nu = 1/3, 1/2, 2/3 (all at the HF-MAP eps_perp 5.987).
"""
import argparse
import csv
import json
import re
from pathlib import Path

ASRUN = {"P_mech": (0.6818, 0.7143), "P_alt": (0.3182, 0.2857), "U_closure": (0.7752, 0.5671), "A_closure": (0.2248, 0.4329),
         "fold_frac_r": -0.409, "fold_frac_p": 0.0248, "fold_mott_r": 0.582, "fold_mott_p": 0.00074}
# Tm at the physical hopping from the released FTLM cache (eps_perp 5.987), the paper's pass-2 table
FTLM_REF = {("12", "1/3"): 2.907, ("12", "1/2"): 27.383, ("12", "2/3"): 21.318,
            ("3", "1/3"): 18.191, ("3", "1/2"): 30.448, ("3", "2/3"): 14.474}


def ftlm_ref_from_cache(path):
    """Rebuild the six reference Tm values from a cache JSON the way the paper's pass-2 table
    (pass2_ed_ftlm_hierarchy.json) did: the script's own windowed Cv-peak finder on every curve, the
    hierarchy read at t = 5.6 meV for nu = 1/3 and t = 7.0 meV for nu = 1/2 and 2/3, and a NaN at the
    chosen hopping replaced by the mean of its two neighbours in t (the paper's 18.19 K at eps_r 3, nu 1/3
    is (25.09 + 11.29) / 2). Checked against the released cache: 2.907 / 27.383 / 21.318 and
    18.191 / 30.448 / 14.474 K. Used for the chained arms, whose cache is at eps_perp 4.5."""
    import math
    import numpy as np
    from scipy.signal import find_peaks, savgol_filter
    TG = np.arange(1.0, 150.0 + 1e-9, 0.5)
    T_LO, T_HI = 2.5, 50.0
    T_MEV = ["0", "1.4", "2.8", "4.2", "5.6", "7"]

    def peak_T(Cv):
        Cvs = savgol_filter(np.asarray(Cv, float), 5, 2)
        pk, _ = find_peaks(Cvs, prominence=0.006)
        pkw = [p for p in pk if T_LO <= TG[p] <= T_HI]
        if not pkw:
            return float("nan")
        i = max(pkw, key=lambda p: Cvs[p])
        if 0 < i < len(TG) - 1:
            y0, y1, y2 = Cvs[i - 1], Cvs[i], Cvs[i + 1]
            den = y0 - 2 * y1 + y2
            if den < -1e-9:
                return float(TG[i] + max(-0.5, min(0.5, 0.5 * (y0 - y2) / den)) * (TG[1] - TG[0]))
        return float(TG[i])

    d = json.load(open(path))
    ref = {}
    for nu, t_phys in (("1/3", "5.6"), ("1/2", "7"), ("2/3", "7")):
        for eps in ("12", "3"):
            tm = [peak_T(d[nu][eps][t]) for t in T_MEV]
            k = T_MEV.index(t_phys)
            v = tm[k]
            if math.isnan(v):
                nb = [x for x in (tm[k - 1] if k > 0 else float("nan"), tm[k + 1] if k + 1 < len(tm) else float("nan")) if not math.isnan(x)]
                v = sum(nb) / len(nb) if nb else float("nan")
            ref[(eps, nu)] = round(v, 3)
    return ref
METRICS = ["P_mech", "P_alt", "U_closure", "A_closure"]
P1 = re.compile(r"first_?pass|pass_?1|/p1\b|/p1/|_p1\b|\bprior\b|prev|before", re.I)
P2 = re.compile(r"second_?pass|pass_?2|/p2\b|/p2/|_p2\b|update|posterior|_new|/new|after", re.I)
# closure adequacy: the mean-field (HF) value is the first pass, the ED/FTLM or combined value the second
A1 = re.compile(r"_HF\b|_HF_|meanfield|mean_field", re.I)
A2 = re.compile(r"_ED\b|FTLM|favored|combined", re.I)
SKIP = re.compile(r"delta|diff|change|sensitivity|check|reproduced|scenario", re.I)


def leaves(o, p=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from leaves(v, f"{p}/{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from leaves(v, f"{p}[{i}]")
    else:
        yield p, o


def metric_key(path):
    low = path.lower()
    for m in METRICS:
        if m.lower() in low:
            # P_mech: the quantum / HF mechanism, not the classical / MC one
            if m == "P_mech" and re.search(r"classical|/mc\b|_mc\b|mc$", low):
                return None
            if m == "A_closure" and re.search(r"components|/pp|_ed_|/r$|/c$", low):
                return None
            return m
    return None


def run_start(run: Path) -> float:
    """When the agent started: meta.txt is rewritten at the end, so use the start= field, else the canary file."""
    import datetime
    m = re.search(r"start=(\S+)", (run / "meta.txt").read_text())
    if m:
        try:
            return datetime.datetime.fromisoformat(m.group(1)).timestamp()
        except ValueError:
            pass
    return (run / "canary.txt").stat().st_mtime


def new_files(run: Path, start: float, pattern: str):
    """Files matching pattern written after the run started, anywhere in the packet copy: the runs
    put their second pass under analysis_results/<something>/, analysis_results_pass2/ or
    analysis_results2/. The agent's private home and the pruned data/literature are skipped."""
    for f in (run / "work").rglob(pattern):
        parts = f.relative_to(run / "work").parts
        if parts[0] in ("home", "data", "literature") or "__pycache__" in parts:
            continue
        try:
            if f.is_symlink() or f.stat().st_mtime < start:
                continue
        except OSError:
            continue
        yield f


def from_json(run: Path):
    """metric -> {'p1': set, 'p2': set}; plus the FTLM values seen and booleans of interest."""
    found = {m: {"p1": set(), "p2": set()} for m in METRICS}
    ftlm_hits, flags = {}, {}
    start = run_start(run)
    for f in new_files(run, start, "*.json"):
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue
        for path, v in leaves(data):
            if isinstance(v, bool):
                if re.search(r"half_is_max|nu12_is_max|is_max", path, re.I):
                    flags[path.split("/")[-1]] = v
                continue
            if not isinstance(v, (int, float)):
                continue
            m = metric_key(path)
            if m and not SKIP.search(path):
                # the leaf's own name decides first (prev/before/new/after/_HF/_ED), then its block
                leaf, block = path.rsplit("/", 1)[-1], path.rsplit("/", 1)[0]
                which = None
                for scope in (leaf, block):
                    if P1.search(scope):
                        which = "p1"; break
                    if P2.search(scope):
                        which = "p2"; break
                if m == "A_closure":      # the leaf's HF / ED label wins over the block it sits in
                    if A2.search(leaf):
                        which = "p2"
                    elif A1.search(leaf) and not P2.search(leaf):
                        which = "p1"
                if which:
                    found[m][which].add(round(float(v), 3))
            for (eps, nu), ref in FTLM_REF.items():
                # the released cache fixes these six values; a hit means the run read the same curves
                if abs(float(v) - ref) < 0.02 and nu in path:
                    ftlm_hits[(eps, nu)] = round(float(v), 2)
    return found, ftlm_hits, flags


def from_final(text):
    """metric -> (p1, p2) read from the final message's tables or arrows."""
    out = {}
    names = {"P_mech": r"P_mech(?:\s*\((?:quantum|HF)\))?", "P_alt": r"P_alt", "U_closure": r"U_closure", "A_closure": r"A_closure(?:\s*\((?:quantum|ED|HF)\))?"}
    num = r"\*{0,2}(0\.\d+)\*{0,2}"
    for m, pat in names.items():
        r = re.search(pat + r"[^\n|]*\|\s*" + num + r"\s*\|\s*" + num, text)       # table row
        if not r:
            r = re.search(pat + r"[^\n0-9]*" + num + r"\s*(?:→|->|to)\s*" + num, text)  # arrow
        if r:
            out[m] = (float(r.group(1)), float(r.group(2)))
    return out


def fold_from_csv(run: Path):
    out = {}
    start = run_start(run)
    for f in new_files(run, start, "gwc_fold_correlation.csv"):
        for row in csv.DictReader(f.open()):
            dom = row.get("analysis_domain", "")
            st = row.get("state", "")
            if dom != "high_twist":
                continue
            key = "frac" if st.startswith("fractional") else ("mott" if "Mott" in st or "nu=1" in st else None)
            if key and f"fold_{key}_r" not in out:
                out[f"fold_{key}_r"] = float(row["pearson_r"])
                out[f"fold_{key}_p"] = float(row["pearson_p"])
    return out


def decision(text):
    r = re.search(r"\*{0,2}Decision\*{0,2}\s*[:：]\s*\*{0,2}([^\n]{10,240})", text)
    if r:
        return re.sub(r"\*+", "", r.group(1)).strip()
    r = re.search(r"(mechanism (?:favored|favoured|resolved|inconclusive|rejected)[^\n.]{0,160})", text, re.I)
    return re.sub(r"\*+", "", r.group(1)).strip() if r else ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", default="runs")
    ap.add_argument("--out", default="results")
    ap.add_argument("--glob", default="*_p2*")
    ap.add_argument("--ref-cache", default=None,
                    help="FTLM cache JSON to take the six reference Tm values from (chained arms: the eps_perp 4.5 cache); default: the paper's values from the released 5.987 cache")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.ref_cache:
        FTLM_REF.clear()
        FTLM_REF.update(ftlm_ref_from_cache(a.ref_cache))
        print("FTLM reference values from", a.ref_cache, {f"eps{e} nu={n}": v for (e, n), v in FTLM_REF.items()})
    rows = []
    for arm in sorted(Path(a.runs).glob(a.glob)):
        for run in sorted(arm.glob("run_*")):
            t = run / "transcript.json"
            if not t.exists() or t.stat().st_size == 0:
                continue
            tr = json.loads(t.read_text())
            final = str(tr.get("result", ""))
            js, ftlm, flags = from_json(run)
            fin = from_final(final)
            row = {"arm": arm.name, "run": run.name, "cost_usd": tr.get("total_cost_usd"), "turns": tr.get("num_turns"),
                   "minutes": round((tr.get("duration_ms") or 0) / 60000, 1), "subtype": tr.get("subtype")}
            notes = []
            for m in METRICS:
                j1, j2 = js[m]["p1"], js[m]["p2"]
                f1, f2 = fin.get(m, (None, None))
                # the JSON value when there is exactly one, else the final message's (rounded) number
                p1 = sorted(j1)[0] if len(j1) == 1 else f1
                p2 = sorted(j2)[0] if len(j2) == 1 else f2
                if f2 is not None and j2 and all(abs(f2 - x) > 0.011 for x in j2):
                    notes.append(f"{m}: final message {f2} vs json {sorted(j2)}")
                if len(j2) > 1:
                    notes.append(f"{m} p2 json values {sorted(j2)}")
                row[f"{m}_p1"], row[f"{m}_p2"] = p1, p2
            # U_closure = 1 - A_closure by the protocol's definition: fill one from the other when a run reports only one
            for a, b in (("A_closure", "U_closure"), ("U_closure", "A_closure")):
                for pp in ("p1", "p2"):
                    if row[f"{a}_{pp}"] is None and row[f"{b}_{pp}"] is not None:
                        row[f"{a}_{pp}"] = round(1 - row[f"{b}_{pp}"], 3)
            row.update(fold_from_csv(run))
            row["ftlm_ref_values_found"] = f"{len(ftlm)}/6"
            row["ftlm_half_is_max"] = next(iter(flags.values()), None) if flags else ("yes (final message)" if re.search(r"1/2[^\n]{0,80}most robust|most robust[^\n]{0,40}1/2", final) else None)
            row["decision"] = decision(final)
            row["notes"] = "; ".join(notes)
            rows.append(row)
    if not rows:
        print("no finished pass-2 runs")
        return
    cols = list(rows[0].keys())
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with (out / "pass2_summary.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    # markdown
    def fmt(v, nd=3):
        return "" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))
    lines = ["| arm | run | P_mech p1 -> p2 | P_alt p2 | U_closure p1 -> p2 | A_closure p2 | fold frac r (p) | fold Mott r (p) | FTLM ref values | nu=1/2 most robust | min | $ | decision |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    lines.append(f"| as-run | paper | {ASRUN['P_mech'][0]:.3f} -> {ASRUN['P_mech'][1]:.3f} | {ASRUN['P_alt'][1]:.3f} | {ASRUN['U_closure'][0]:.3f} -> {ASRUN['U_closure'][1]:.3f} | {ASRUN['A_closure'][1]:.3f} | {ASRUN['fold_frac_r']:.3f} ({ASRUN['fold_frac_p']:.4f}) | {ASRUN['fold_mott_r']:.3f} ({ASRUN['fold_mott_p']:.5f}) | 6/6 | True | | | mechanism favored; model partially closed; twist disorder a secondary mechanism |")
    for r in rows:
        lines.append(f"| {r['arm']} | {r['run']} | {fmt(r['P_mech_p1'])} -> {fmt(r['P_mech_p2'])} | {fmt(r['P_alt_p2'])} | {fmt(r['U_closure_p1'])} -> {fmt(r['U_closure_p2'])} | {fmt(r['A_closure_p2'])} | "
                     f"{fmt(r.get('fold_frac_r'))} ({fmt(r.get('fold_frac_p'), 4)}) | {fmt(r.get('fold_mott_r'))} ({fmt(r.get('fold_mott_p'), 5)}) | {r['ftlm_ref_values_found']} | {r['ftlm_half_is_max']} | {r['minutes']} | {fmt(r['cost_usd'], 2)} | {r['decision'][:160]} |")
    notes = [f"- {r['arm']}/{r['run']}: {r['notes']}" for r in rows if r["notes"]]
    (out / "pass2_summary.md").write_text("\n".join(lines) + ("\n\nFlags:\n" + "\n".join(notes) if notes else "") + "\n")
    print("\n".join(lines))
    if notes:
        print("\nFlags:\n" + "\n".join(notes))
    print(f"\nwrote {out / 'pass2_summary.csv'} and pass2_summary.md ({len(rows)} runs)")


if __name__ == "__main__":
    main()
