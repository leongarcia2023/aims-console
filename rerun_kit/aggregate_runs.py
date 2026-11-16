#!/usr/bin/env python3
"""aggregate_runs.py: compile the discovery-loop reruns into one table and a figure.

The protocol fixes the questions, not the file names or the synthesis formula: each
run's agent writes its own scripts, so the mechanism-comparison JSON is named and
structured differently from run to run (the as-run pass wrote
tables/model_comparison.json; the arm D pilot wrote tables/q4_mechanism.json with
different keys). This reader therefore searches every JSON under analysis_results/
for the quantities, matching keys by meaning:

  P_mech per engine     any dict key matching /p_?mech/i whose children are keyed by
                        engine (hf/HF/quantum -> quantum; mc/MC/classical -> classical)
  closed-model posterior any key matching /closed.*posterior|p_closed/i, same engine keys
  Bayes factor          any key matching /bayes_?factor/i (ln or log10 variants are
                        converted to B21 = BF(quantum over classical))
  P_alt, U_closure      keys matching /p_?alt/i and /u_?closure/i
  favoured mechanism    key /favou?red/i if present, else argmax of P_mech
  Tm per filling        melting_temperatures.json (dict keyed by filling, values either
                        numbers or dicts with Tm/Tm_K)
  hypotheses            hypothesis_scores.csv at analysis_results/ or tables/

Each run's row records which file and which keys were used (source_json, keys_used),
so the mapping is auditable. Writes rerun_summary.csv, rerun_stats.json and
rerun_distribution.png. Usage: python aggregate_runs.py --runs runs --out results
"""
import argparse, csv, json, re, statistics as st
from pathlib import Path

QUANTUM = re.compile(r"^(hf|quantum|h2|t12|t=t12.*|hf.*quantum.*)$", re.I)
CLASSICAL = re.compile(r"^(mc|classical|h1|t0|t=0.*|mc.*classical.*)$", re.I)


Q_TOKENS = {"hf", "quantum", "h2", "t12", "hartree", "fock"}
C_TOKENS = {"mc", "classical", "h1", "t0", "montecarlo", "monte"}


def engine_key(k: str):
    """Map an engine-ish key or label ('HF', 'MC_classical', 'p_hf', 'H2_quantum_HF',
    'HF (quantum, t=t12)') to 'quantum' or 'classical'; None when ambiguous or neither."""
    k = str(k).strip()
    if QUANTUM.match(k) or k.lower().startswith("hf"):
        return "quantum"
    if CLASSICAL.match(k) or k.lower().startswith("mc"):
        return "classical"
    toks = set(re.split(r"[^a-z0-9]+", k.lower())) - {""}
    q = bool(toks & Q_TOKENS) or "t=t12" in k.lower().replace(" ", "")
    c = bool(toks & C_TOKENS) or "t=0" in k.lower().replace(" ", "")
    if q and not c:
        return "quantum"
    if c and not q:
        return "classical"
    return None


NOT_ENGINE = re.compile(r"closure|alt|sever|adequac|note|basis|weight|ess|chi2|loss|eps|tm|trial", re.I)


def by_engine(d):
    """Map a dict keyed by engine names to {'quantum': x, 'classical': y} (numbers only).
    Keys that carry another quantity's name (U_closure_favored_HF, A_closure_mc) are not
    engine entries even though they mention an engine."""
    if not isinstance(d, dict):
        return {}
    out = {}
    for k, v in d.items():
        if NOT_ENGINE.search(str(k)) and not re.fullmatch(r"(?i)(hf|mc|quantum|classical|h1|h2|p_hf|p_mc)", str(k)):
            continue
        e = engine_key(k)
        if e and isinstance(v, (int, float)) and not isinstance(v, bool):
            out[e] = float(v)
    return out


def walk(obj, path=""):
    """Yield (path, key, value) for every dict entry, recursively."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}/{k}"
            yield p, str(k), v
            yield from walk(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")


def extract_mechanism(T: Path):
    """Search every JSON under analysis_results for the mechanism-comparison quantities."""
    best, best_score = None, 0
    for p in sorted(T.rglob("*.json")):
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        found, keys = {}, {}
        flat_pm, flat_post, bf_cands = {}, {}, []
        for path, k, v in walk(d):
            kl = k.lower()
            num = isinstance(v, (int, float)) and not isinstance(v, bool)
            # P_mech as a dict keyed by engine, or as flat keys P_mech_<engine>
            m = re.match(r"^p_?mech(?:anism)?_(.+)$", kl)
            mp = re.match(r"^(?:closed_model_p|closed_p|posterior|p_closed)_(.+)$", kl) or re.match(r"^p_(.+)_given_d$", kl)
            if m and num and engine_key(m.group(1)):
                flat_pm[engine_key(m.group(1))] = float(v); keys.setdefault("P_mech", path)
            elif mp and num and engine_key(mp.group(1)):
                flat_post[engine_key(mp.group(1))] = float(v); keys.setdefault("posterior", path)
            elif re.search(r"p_?mech|mech(anism)?_support", kl) and isinstance(v, dict):
                be = by_engine(v)
                explicit = bool(re.search(r"p_?mech", kl))
                if be and ("P_mech" not in found or (explicit and not keys.get("P_mech_explicit"))):
                    found["P_mech"], keys["P_mech"] = be, path
                    if explicit:
                        keys["P_mech_explicit"] = True
            elif re.search(r"(closed.*posterior|p_closed|posterior_closed|closed_model_support|closed.*support|^mechanism_posterior$|^posterior$|^p_h\d?_given_d)", kl) and isinstance(v, dict) and "posterior" not in found:
                be = by_engine(v)
                if be:
                    found["posterior"], keys["posterior"] = be, path
            elif re.search(r"bayes_?factor|ln_?bf|log(10)?_?bf|^bf_|_bf$|^lnb21|^b21|^lnb_|^lnb$|^log_?b_", kl) and num:
                import math
                val = float(v)
                if re.search(r"^ln|^log_?bf|lnbayes|^log(?!10)|^lnb", kl):
                    val = math.exp(min(val, 700.0))
                elif "log10" in kl:
                    val = 10 ** min(val, 300.0)
                if re.search(r"mc_over_hf|classical_over|b12", kl):
                    val = 1 / val if val else float("inf")
                bf_cands.append((path, val))
                if "B21" not in found:
                    found["B21"], keys["B21"] = val, path
            elif re.search(r"^p_?alt", kl) and num and "P_alt" not in found:
                found["P_alt"], keys["P_alt"] = float(v), path
            elif re.search(r"u_?closure", kl) and num and "U_cl" not in found \
                    and not re.search(r"classical|/mc\b|_mc\b|/mc_|h1\b", path.lower()):
                # U_closure of the favoured (quantum) model; a classical record's U_closure is not it
                found["U_cl"], keys["U_cl"] = float(v), path
            elif re.search(r"^favou?red|^favou?rite|^decision|^verdict|^mechanism_(label|decision|verdict)|^winner|^selected", kl) \
                    and isinstance(v, str) and "favoured" not in found:
                e = engine_key(v) or ("quantum" if re.search(r"quantum|hf|hopping", v, re.I) else
                                      "classical" if re.search(r"classical|mc\b|t\s*=\s*0", v, re.I) else None)
                if e:
                    found["favoured"], keys["favoured"] = e, path
            elif kl == "a_class" and isinstance(v, dict) and "A_class" not in found:
                be = by_engine(v)
                if be:
                    found["A_class"], keys["A_class"] = be, path
        if "P_mech" not in found and flat_pm:
            found["P_mech"] = flat_pm
        if "posterior" not in found and flat_post:
            found["posterior"] = flat_post
        if bf_cands:
            import math
            keys["B21_candidates"] = [(pth, val if not math.isfinite(val) else round(val, 4)) for pth, val in bf_cands]
            finite = [(pth, val) for pth, val in bf_cands if math.isfinite(val)]
            if finite and not math.isfinite(found.get("B21", float("nan"))):
                found["B21"], keys["B21"] = finite[0][1], finite[0][0]
        score = len(found) + (3 if "P_mech" in found else 0) + (2 if "posterior" in found else 0)
        if score > best_score:
            best, best_score = (p, found, keys), score
    return best


def _tm_from(m):
    """{'1/3': x, ...} from a dict keyed by filling (values numbers or dicts with Tm_K/Tm/tm),
    or from the columnar form {'fillings': [...], 'Tm_K': [...]}."""
    out = {}
    if isinstance(m, dict):
        for nu in ("1/3", "1/2", "2/3"):
            v = m.get(nu)
            if isinstance(v, dict):
                v = v.get("Tm_K", v.get("Tm", v.get("tm", v.get("T_m"))))
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[nu] = float(v)
        if not out:
            names = m.get("fillings") or m.get("filling") or m.get("nu_labels") or m.get("states")
            vals = (m.get("Tm_K") or m.get("Tm") or m.get("tm") or m.get("T_m") or m.get("Tm_mean_K")
                    or m.get("EXP") or m.get("exp_Tm") or m.get("Tm_exp_K") or m.get("values"))
            if isinstance(names, list) and isinstance(vals, list) and len(names) == len(vals):
                for n, v in zip(names, vals):
                    if str(n) in ("1/3", "1/2", "2/3") and isinstance(v, (int, float)) and not isinstance(v, bool):
                        out[str(n)] = float(v)
    return out


def extract_from_report(T: Path, extra_text: str = ""):
    """Fallback when a run wrote its model comparison only into prose (report.md, or the agent's
    final message in transcript.json): the mechanism label, ln Bayes factor, P_mech, P_alt and
    U_closure are read from the text with conservative patterns. Values found this way are
    marked source_json = 'report.md (text)'."""
    p = T / "report.md"
    if not p.exists() and not extra_text:
        return None
    txt = (p.read_text(errors="ignore") if p.exists() else "") + "\n" + (extra_text or "")
    found, keys = {}, {}
    m = re.search(r"Mechanism label:?\**\s*\**\s*(FAVOU?RED|DISFAVOU?RED|RESOLVED|OPEN|UNDETERMINED)[^\n]{0,160}", txt, re.I)
    if m:
        line = m.group(0)
        if re.search(r"quantum|hopping|HF", line, re.I) and re.search(r"FAVOU?RED|RESOLVED", m.group(1), re.I) \
                and not re.search(r"DISFAVOU?RED\W*[^.]{0,40}quantum", line, re.I):
            found["favoured"], keys["favoured"] = "quantum", "report.md: Mechanism label"
        elif re.search(r"classical", line, re.I) and re.search(r"FAVOU?RED|RESOLVED", m.group(1), re.I):
            found["favoured"], keys["favoured"] = "classical", "report.md: Mechanism label"
    m = re.search(r"ln\s*(?:Bayes factor|BF)[^\n]*", txt, re.I)
    if m:
        import math
        line = m.group(0).split("→")[0]          # the value stated before any "-> BF ~ 10^..."
        nums = re.findall(r"[=≈~]\s*\+?([+-]?\d[\d,.]*)", line)
        if nums:
            try:
                v = float(nums[-1].replace(",", "").rstrip("."))   # the last stated value is the result
                found["B21"], keys["B21"] = math.exp(min(v, 700.0)), f"report.md: {line.strip()[:80]}"
            except ValueError:
                pass
    for key, pat in (("P_mech", r"(?:P_mech\s*\(?(?:quantum|HF|H2)?\)?|mechanism posterior P\((?:quantum|HF|H2)\)|P_mech\((?:quantum|HF)\))\s*[=≈:]\s*([01](?:\.\d+)?)"),
                     ("P_alt", r"P_alt\s*[=≈:]\s*([01](?:\.\d+)?)"),
                     ("U_cl", r"U_closure\w*\s*[=≈:]\s*([01](?:\.\d+)?)")):
        m = re.search(pat, txt)
        if m:
            found[key], keys[key] = float(m.group(1)), f"report.md: {m.group(0)[:40]}"
    if "P_mech" in found:
        found["P_mech"] = {"quantum": found["P_mech"]}
    return (p, found, keys) if found else None


def read_tm(T: Path):
    """Melting temperatures per filling: top level of melting_temperatures.json, else the first
    nested dict that carries all three fillings (runs nest them under EXP, measured, data...)."""
    cands = [T / "tables" / "melting_temperatures.json", T / "melting_temperatures.json",
             T / "tables" / "melting_full.json", T / "tables" / "melting_temperatures_full.json",
             T / "tables" / "q3_melting.json"]
    cands += sorted(q for q in T.rglob("*.json") if re.search(r"melt|tm", q.name, re.I) and q not in cands)
    for p in cands:
        if not p.exists():
            continue
        try:
            m = json.loads(p.read_text())
        except Exception:
            continue
        out = _tm_from(m)
        if len(out) == 3:
            return out
        for _, _, v in walk(m):
            cand = _tm_from(v)
            if len(cand) == 3:
                return cand
        if out:
            return out
    return {}


def read_hypotheses(T: Path):
    """Return (n, names, top): the hypothesis with the highest score-like column, if any."""
    for p in [T / "hypothesis_scores.csv", T / "tables" / "hypothesis_scores.csv"]:
        if p.exists():
            with open(p) as f:
                rows = list(csv.DictReader(f))
            names, scored = [], []
            # when the table classifies rows, rank only the primary-mechanism rows
            cls_col = next((c for c in (rows[0].keys() if rows else []) if re.search(r"class|type|role|category", c, re.I)), None)
            if cls_col and any(re.search(r"primary|mechanism", r.get(cls_col) or "", re.I) for r in rows):
                rows_for_rank = [r for r in rows if re.search(r"primary", r.get(cls_col) or "", re.I)] or rows
            else:
                rows_for_rank = rows
            for r in rows:
                name = None
                for key in ("hypothesis", "name", "id", "label", "hypothesis_id"):
                    if key in r:
                        name = r[key]; break
                if name is None:
                    name = next(iter(r.values()), "?")
                names.append(name)
                for key in r:
                    if r not in rows_for_rank:
                        break
                    if re.search(r"weighted_score|total_score|^score$|overall|posterior|weighted|^total$|^s_weighted$", key, re.I):
                        try:
                            scored.append((float(r[key]), name)); break
                        except (TypeError, ValueError):
                            pass
            top = max(scored)[1] if scored else None
            return len(rows), " ; ".join(names), top
    return None, None, None


def read_run(run: Path) -> dict:
    T = run / "work" / "analysis_results"
    row = {"run": run.name, "ok": False}
    for f in ("canary.txt", "meta.txt"):
        p = run / f
        row[f.split(".")[0]] = p.read_text().strip().replace("\n", " | ") if p.exists() else ""
    # blinding evidence written by run_arm.sh and audit_run.py
    for f, key, good in (("probe.txt", "probe_clean", "probe clean"), ("hidden.txt", "host_hidden", "run visible at")):
        p = run / f
        row[key] = (good in p.read_text() and "VISIBLE" not in p.read_text()) if p.exists() else None
    au = run / "audit.json"
    if au.exists():
        try:
            a = json.loads(au.read_text())
            row["n_blinding_mentions"] = a.get("n_blinding_mentions")
            row["unexpected_tools"] = ";".join(a.get("unexpected_tools") or [])
            row["n_pip_installs"] = len(a.get("pip_installs") or [])
            row["n_host_sweeps"] = len(a.get("host_sweeps") or [])
            row["n_outside_paths"] = len(a.get("outside_paths") or [])
        except Exception:
            pass
    # wall time from the runner's own clock (the CLI's duration_ms has been seen to cover only
    # the last internal segment of a long run)
    meta = (run / "meta.txt").read_text() if (run / "meta.txt").exists() else ""
    ms = re.search(r"start=(\S+)", meta); me = re.search(r"end=(\S+)", meta)
    if ms and me:
        try:
            from datetime import datetime
            row["wall_min"] = round((datetime.fromisoformat(me.group(1)) - datetime.fromisoformat(ms.group(1))).total_seconds() / 60, 1)
        except Exception:
            pass
    tr = run / "transcript.json"
    if tr.exists() and tr.stat().st_size > 0:
        try:
            t = json.loads(tr.read_text())
            if isinstance(t, dict):
                row["cost_usd"] = t.get("total_cost_usd")
                row["num_turns"] = t.get("num_turns")
                row["duration_min"] = round(t.get("duration_ms", 0) / 60000, 1) if t.get("duration_ms") else None
                row["is_error"] = t.get("is_error")
        except Exception:
            pass
    # API requests from the CLI's own session log: num_turns above restarts when a background
    # tool's completion re-enters the agent (seen in D3 and E3 of the clean campaign)
    al = run / "agent_log.jsonl"
    if al.exists():
        try:
            row["api_requests"] = sum(1 for line in al.open(errors="replace") if '"type":"api-request"' in line)
        except Exception:
            pass
    if not T.exists():
        return row
    tm = read_tm(T)
    for nu, v in tm.items():
        row[f"Tm_{nu}"] = v
    row["fillings_assigned"] = all(nu in tm for nu in ("1/3", "1/2", "2/3"))
    row["n_hypotheses"], row["hypotheses"], row["top_hypothesis"] = read_hypotheses(T)
    result_text = ""
    if tr.exists() and tr.stat().st_size > 0:
        try:
            result_text = str(json.loads(tr.read_text()).get("result", ""))
        except Exception:
            pass
    mech = extract_mechanism(T)
    if mech:
        # fill what the JSON lacks from the prose (P_mech and the label are often prose-only)
        rep = extract_from_report(T, result_text)
        if rep:
            _, rfound, rkeys = rep
            for k in ("P_mech", "favoured", "P_alt", "U_cl"):
                if k in rfound and k not in mech[1]:
                    mech[1][k], mech[2][k] = rfound[k], rkeys[k]
    if not mech:
        mech = extract_from_report(T, result_text)
    if not mech:
        th = row.get("top_hypothesis") or ""
        fav = "quantum" if re.search(r"quantum|hopping|hf", th, re.I) else "classical" if re.search(r"classical|thermal_only|mc\b", th, re.I) else None
        row["favoured"], row["favoured_from"] = fav, "top_hypothesis" if fav else None
        row["quantum_favoured"], row["ok"] = fav == "quantum", fav is not None
        return row
    p, found, keys = mech
    row["source_json"] = str(p.relative_to(T)) + (" (text)" if p.name == "report.md" else "")
    row["keys_used"] = json.dumps(keys)
    pm = found.get("P_mech", {})
    row["P_mech_quantum"], row["P_mech_classical"] = pm.get("quantum"), pm.get("classical")
    post = found.get("posterior", {})
    row["P_H2_given_D"], row["P_H1_given_D"] = post.get("quantum"), post.get("classical")
    row["B21"] = found.get("B21")
    if isinstance(row["B21"], (int, float)):
        import math
        row["lnB21"] = math.log(row["B21"]) if row["B21"] > 0 else float("-inf")
    row["P_alt"], row["U_cl"] = found.get("P_alt"), found.get("U_cl")
    ac = found.get("A_class", {})
    row["A_class_quantum"], row["A_class_classical"] = ac.get("quantum"), ac.get("classical")
    # which mechanism the run favoured, in order of reliability: an explicit label in the
    # JSON; the closed-model posterior; the top-scored hypothesis; the P_mech argmax
    fav, how = found.get("favoured"), "label"
    if fav is None and post:
        fav, how = max(post, key=post.get), "closed_posterior"
    if fav is None and row.get("top_hypothesis"):
        th = row["top_hypothesis"]
        fav = "quantum" if re.search(r"quantum|hopping|hf", th, re.I) else "classical" if re.search(r"classical|thermal_only|mc\b", th, re.I) else None
        how = "top_hypothesis"
    if fav is None and pm:
        fav, how = max(pm, key=pm.get), "P_mech_argmax"
    row["favoured"], row["favoured_from"] = fav, how if fav else None
    row["quantum_favoured"] = fav == "quantum"
    row["ok"] = fav is not None
    return row


def main(runs: Path, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    table, stats = [], {}
    for armdir in sorted(p for p in runs.iterdir() if p.is_dir() and any(p.glob("run_*"))):
        rows = [read_run(r) for r in sorted(armdir.glob("run_*"))]
        for r in rows:
            r["arm"] = armdir.name
        table += rows
        ok = [r for r in rows if r["ok"]]

        import math

        def col(k):
            return [r[k] for r in ok if isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool)
                    and math.isfinite(r[k])]
        s = {"n_runs": len(rows), "n_ok": len(ok),
             "n_quantum_favoured": sum(1 for r in ok if r.get("quantum_favoured")),
             "n_fillings_assigned": sum(1 for r in rows if r.get("fillings_assigned")),
             "n_internet_reachable": sum(1 for r in rows if "reachable" in r.get("canary", "")),
             "n_probe_clean": sum(1 for r in rows if r.get("probe_clean")),
             "n_host_hidden": sum(1 for r in rows if r.get("host_hidden")),
             "n_with_blinding_mentions": sum(1 for r in rows if (r.get("n_blinding_mentions") or 0) > 0),
             "n_with_unexpected_tools": sum(1 for r in rows if r.get("unexpected_tools")),
             "n_with_pip_installs": sum(1 for r in rows if (r.get("n_pip_installs") or 0) > 0),
             "n_with_host_sweeps": sum(1 for r in rows if (r.get("n_host_sweeps") or 0) > 0)}
        for k in ("P_mech_quantum", "P_H2_given_D", "lnB21", "P_alt", "U_cl", "Tm_1/3", "Tm_1/2", "Tm_2/3", "cost_usd", "wall_min", "num_turns"):
            v = col(k) if k not in ("cost_usd", "wall_min", "num_turns") else \
                [r[k] for r in rows if isinstance(r.get(k), (int, float)) and math.isfinite(r[k])]
            if v:
                s[k] = {"mean": st.mean(v), "median": st.median(v), "sd": st.pstdev(v) if len(v) > 1 else 0.0,
                        "min": min(v), "max": max(v), "n": len(v)}
        s["n_lnB21_infinite"] = sum(1 for r in ok if isinstance(r.get("lnB21"), float) and math.isinf(r["lnB21"]))
        stats[armdir.name] = s
    keys = sorted({k for r in table for k in r})
    with open(out / "rerun_summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(table)
    (out / "rerun_stats.json").write_text(json.dumps(stats, indent=1))
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        arms = list(stats)
        fig, ax = plt.subplots(figsize=(9, 5))
        for i, a in enumerate(arms):
            ys = [r["P_mech_quantum"] for r in table if r["arm"] == a and isinstance(r.get("P_mech_quantum"), (int, float))]
            ax.plot([i] * len(ys), ys, "o", ms=9, mfc="#3D6B9E", mec="k", alpha=0.8)
        ax.axhline(0.5, color="k", ls=":", lw=1.2)
        ax.axhline(0.68, color="#9a9a9a", ls="--", lw=1.2)
        ax.set_xticks(range(len(arms)), arms, fontsize=10)
        ax.set_ylim(0, 1)
        ax.set_ylabel("P_mech(quantum), pass 1")
        ax.set_title("Discovery-loop reruns by ablation arm")
        fig.savefig(out / "rerun_distribution.png", dpi=200, bbox_inches="tight")
    except Exception as e:  # matplotlib optional
        print("figure skipped:", e)
    print(json.dumps(stats, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    main(Path(a.runs), Path(a.out))
