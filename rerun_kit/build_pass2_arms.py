#!/usr/bin/env python3
"""build_pass2_arms.py: pass-2 arms = a pass-1 arm tree + the pass-1 results the second pass starts from
+ the ED/FTLM cache the released pass-2 run produced.

    python build_pass2_arms.py --asrun /path/to/claudeMIMComparisonTest51 --out arms \
        --bases B_nointernet C_litheld D_litheld_noprior [--pass1 asrun | <arm>/run_NN]

Two designs, chosen with --pass1:
  asrun           the as-run pass-1 outputs (analysis_results/) are the input, as in the published pass 2;
                  the as-run ED/FTLM cache (eps_perp 5.987, the as-run HF MAP) is supplied, so the sweep
                  resolves from cache and the run is the inference update (registration, fold correlation,
                  ED synthesis, posterior update). Comparable to the published pass 2 directly.
  <arm>/run_NN    chained: that rerun's own pass-1 outputs are the input. The ED script then reads no HF
                  MAP it recognises and falls back to eps_perp 4.5, so the cache for 4.5 must exist
                  (precompute it once with the team's script; pass it with --cache) or the sweep is
                  recomputed in the run.
--cache PATH      the ED/FTLM cache to place in SupplementaryData/ (default for asrun: the released
                  eps_perp 5.987 cache). The builder resolves eps_perp exactly as the team's script does
                  (analysis_results/tables/evidence_hf.json MAP_eps, else model_comparison.json, else 4.5)
                  and refuses the arm if the cache's eps_perp (from its file name) differs.
The arm name says which base and which pass-1 input it used; the name never enters the tree. Manifests
are written outside the tree, as for pass 1, and the tree is refused if any file mentions the ablation.
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEAK = re.compile(r"litheld|noprior|nowindows|withh[eo]ld|literature_removed|ablation[- ]arm|this arm\b", re.I)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_eps_perp(analysis_results: Path, default=4.5):
    """The team's ED_FTLM_epsweep.py `_hf_optimized_eps_perp`, line for line: which eps_perp the script
    will compute at (and so which cache file name it will look for) when run beside this analysis_results/."""
    cands = [analysis_results / "tables" / "evidence_hf.json", analysis_results / "tables" / "model_comparison.json"]
    for p in cands:
        if not p.exists():
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
            if isinstance(d.get("MAP_eps"), (list, tuple)) and len(d["MAP_eps"]) >= 2:
                return float(d["MAP_eps"][1]), f"HF MAP eps_perp <- {p.name}"
            if "eps_perp_med_ci" in d:
                return float(d["eps_perp_med_ci"][1]), f"HF median eps_perp <- {p.name}"
            fav = d.get("favored_fit_HF", {})
            if isinstance(fav.get("eps_perp_med_ci"), (list, tuple)):
                return float(fav["eps_perp_med_ci"][1]), f"HF median eps_perp <- {p.name}:favored_fit_HF"
        except Exception:
            pass
    return default, f"fallback default {default} (no HF fit found)"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--asrun", required=True, help="the as-run packet (holds analysis_results/ and the ED cache)")
    ap.add_argument("--out", default=str(HERE / "arms"))
    ap.add_argument("--bases", nargs="+", default=["B_nointernet", "C_litheld", "D_litheld_noprior"])
    ap.add_argument("--pass1", default="asrun", help="'asrun' or '<arm>/run_NN' under runs/")
    ap.add_argument("--suffix", default=None, help="arm name suffix; default p2asrun or p2<run>")
    ap.add_argument("--cache", default=None, help="ED/FTLM cache JSON to supply (default for asrun: the released 5.987 cache)")
    a = ap.parse_args()

    asrun = Path(a.asrun).resolve()
    out = Path(a.out).resolve()
    cache = Path(a.cache).resolve() if a.cache else asrun / "SupplementaryData" / "ED_FTLM_epsweep_N18-20-18_epsperp5.987.json"
    if a.pass1 == "asrun":
        p1 = asrun / "analysis_results"
        suffix = a.suffix or "p2asrun"
    else:
        p1 = HERE / "runs" / a.pass1 / "work" / "analysis_results"
        suffix = a.suffix or ("p2" + a.pass1.replace("/", "_").replace("run_", "r"))
    if not p1.is_dir():
        sys.exit(f"no pass-1 results at {p1}")
    eps_perp, eps_src = resolve_eps_perp(p1)
    supply_cache = cache.exists() and (a.pass1 == "asrun" or a.cache is not None)
    if supply_cache:
        m = re.search(r"epsperp([0-9.]+)\.json$", cache.name)
        if not m or abs(float(m.group(1)) - eps_perp) > 1e-6:
            sys.exit(f"cache {cache.name} is not at the eps_perp the script will resolve beside these results: "
                     f"{eps_perp:g} ({eps_src})")
    report = {}
    for base in a.bases:
        src = out / base
        if not src.is_dir():
            sys.exit(f"no base arm {src}")
        arm = f"{base.split('_')[0]}_{suffix}"           # e.g. C_p2asrun; the arm letter keeps the condition
        d = out / arm
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(src, d, symlinks=False)
        shutil.copytree(p1, d / "analysis_results")
        for junk in list((d / "analysis_results").rglob("__pycache__")):
            shutil.rmtree(junk)
        if supply_cache:
            shutil.copy2(cache, d / "SupplementaryData" / cache.name)
        files = sorted(p for p in d.rglob("*") if p.is_file())
        leak = [str(p.relative_to(d)) for p in files
                if p.suffix in (".json", ".md", ".txt", ".py", ".csv") and LEAK.search(p.read_text(errors="ignore"))]
        if leak:
            sys.exit(f"arm {arm}: files in the tree mention the ablation itself: {leak[:10]}")
        base_man = json.load(open(out / "manifests" / f"{base}.json"))
        manifest = dict(base_man)
        manifest.update(arm=arm, pass_=2, base_arm=base, pass1_input=a.pass1,
                        ed_cache_supplied=supply_cache, ed_cache_name=(cache.name if supply_cache else None),
                        ed_cache_sha256=(sha256(cache) if supply_cache else None),
                        eps_perp_resolved=eps_perp, eps_perp_source=eps_src, n_files=len(files),
                        sha256={str(p.relative_to(d)): sha256(p) for p in files})
        (out / "manifests" / f"{arm}.json").write_text(json.dumps(manifest, indent=1))
        report[arm] = {k: manifest[k] for k in ("base_arm", "pass1_input", "ed_cache_supplied", "ed_cache_name",
                                                "eps_perp_resolved", "eps_perp_source", "literature_removed",
                                                "prior_sentences_removed", "tool_windows_removed", "n_files")}
        print(arm, report[arm])
    rep_path = out / "arms_report_pass2.json"
    prev = json.load(open(rep_path)) if rep_path.exists() else {}
    prev.update(report)
    rep_path.write_text(json.dumps(prev, indent=1))


if __name__ == "__main__":
    main()
