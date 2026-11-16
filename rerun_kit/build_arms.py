#!/usr/bin/env python3
"""build_arms.py: construct the four ablation arms for the discovery-loop rerun.

Arms (each a self-contained working directory the agent runs inside):
  A_asrun            packet exactly as released; internet allowed by the runner
  B_nointernet       packet exactly as released; runner disables web tools and egress
  C_litheld          B + the answer-bearing literature removed
  D_litheld_noprior  C + the filling-assignment sentences removed from Physics_prior.md
  E_nowindows        D + the device-specific voltage windows removed from the supplied
                     melting tool, so the voltage-to-filling map must come from the data

Answer-bearing literature (default WITHHOLD list; override with --withhold):
  * Quantum melting of generalized electron crystal in twisted bilayer MoSe2 (Zong et al.,
    Nat. Commun. 2025): the prior quantum-melting result for this system
  * MOESM1_ESM_supplementary.pdf: its Supplementary Information
  * Melting temperature shifts from quantum fluctuations in generalized Wigner crystals
    (Kumar et al. 2026): the theory predicting the hierarchy shift
Borderline, kept by default (add with --withhold-borderline): Origin and stability of
generalized Wigner crystallinity (Kumar, Lewandowski, Changlani), which discusses quantum
melting qualitatively but does not state the hierarchy inversion.

Every arm gets a manifest (sha256 of every file) written to OUT/manifests/<arm>.json,
outside the tree the agent sees, and the build writes a unified diff of Physics_prior.md
(released vs arm D). The build refuses to finish if any text file inside an arm mentions
the ablation (arm names, "withheld", the removed-literature list): the arm D pilot read an
in-tree manifest and learned the titles of the withheld papers from it.
"""
import argparse, difflib, hashlib, json, re, shutil, sys
from pathlib import Path

DEFAULT_WITHHOLD = [
    "Quantum melting of generalized electron crystal in twisted bilayer MoSe2.pdf",
    "MOESM1_ESM_supplementary.pdf",
    "Melting temperature shifts from quantum fluctuations in generalized Wigner crystals.pdf",
]
BORDERLINE = [
    "Origin and stability of generalized Wigner crystallinity in triangular moir#U00b4e systems.pdf",
]

# Sentences in Physics_prior.md that assign fillings to features; arm D removes them.
PRIOR_STRIP_PATTERNS = [
    r"\s*For bottom gate voltage greater than 0, the first four correlated dips should "
    r"correspond to filling factors[^.]*\.\s*The last dip should correspond to filling factor[^.]*\.",
    r"\s*The \\\(\\nu=1\\\) feature observed at each grid point can be used to calculate local twist angle\.",
]

TOOL_FILES = ("mcp_server_sse.py", "utils.py", "plotter.py", "melting_temperature_mcp_tool.py")


TOOL_WINDOWS_OLD = 'FILLING_WINDOWS = {"1/3": (0.75, 1.45), "1/2": (1.50, 1.92), "2/3": (1.92, 2.45)}'
TOOL_WINDOWS_NEW = '''# The gate-voltage window of each fractional state is device-specific and is NOT
# supplied with this tool. Determine the voltage-to-filling map from the data (for
# example from the linear V_bg(nu) ladder anchored on the charge-neutrality point and
# the nu = 1 Mott state) and write it to analysis_results/filling_windows.json as
# {"1/3": [lo, hi], "1/2": [lo, hi], "2/3": [lo, hi], "1": [lo, hi]} (volts) before
# calling the melting or twist tools.
import os as _os
def _load_windows():
    _p = _os.path.join("analysis_results", "filling_windows.json")
    if not _os.path.exists(_p):
        raise RuntimeError("filling_windows.json not found: determine the filling windows "
                           "from the data and write analysis_results/filling_windows.json")
    _w = json.load(open(_p))
    return {k: tuple(v) for k, v in _w.items()}
class _LazyWindows(dict):
    def _ensure(self):
        if not dict.__len__(self):
            self.update(_load_windows())
    def __getitem__(self, k):
        self._ensure(); return dict.__getitem__(self, k)
    def __contains__(self, k):
        self._ensure(); return dict.__contains__(self, k)
    def __iter__(self):
        self._ensure(); return dict.__iter__(self)
    def __len__(self):
        self._ensure(); return dict.__len__(self)
    def keys(self):
        self._ensure(); return dict.keys(self)
FILLING_WINDOWS = _LazyWindows()'''
NU1_OLD = "NU1_WINDOW = (2.50, 3.25)                   # the strong nu=1 Mott dip (long lever arm)"
NU1_NEW = "NU1_WINDOW = None                           # supplied by analysis_results/filling_windows.json"
TWIST_OLD = ('    return {"1/3": FILLING_WINDOWS["1/3"], "1/2": FILLING_WINDOWS["1/2"],\n'
             '            "2/3": FILLING_WINDOWS["2/3"], "1": NU1_WINDOW}')
TWIST_NEW = ('    return {"1/3": FILLING_WINDOWS["1/3"], "1/2": FILLING_WINDOWS["1/2"],\n'
             '            "2/3": FILLING_WINDOWS["2/3"], "1": FILLING_WINDOWS["1"]}')


def strip_tool_windows(tool: Path):
    """Arm E: remove the device-specific voltage windows from the supplied melting tool."""
    t = tool.read_text(encoding="utf-8")
    for old in (TOOL_WINDOWS_OLD, NU1_OLD, TWIST_OLD):
        if old not in t:
            sys.exit(f"tool anchor not found for arm E: {old[:50]}")
    t = t.replace(TOOL_WINDOWS_OLD, TOOL_WINDOWS_NEW).replace(NU1_OLD, NU1_NEW).replace(TWIST_OLD, TWIST_NEW)
    if "\nimport json" not in t:
        t = t.replace("import numpy as np", "import json\nimport numpy as np", 1)
    tool.write_text(t, encoding="utf-8")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def first_pass_prompt(text: str) -> str:
    """The loop prompt cut at '## Second pass': the as-run first pass could not have seen a section
    written after it, and the section names the follow-ups (AFM registration, ED and FTLM)."""
    i = text.find("## Second pass")
    return text if i < 0 else text[:i].rstrip() + "\n"


def build(packet: Path, prompts: Path, tools: Path, skill: Path, out: Path,
          withhold: list, arms: list, first_pass_only: bool = False, suffix: str = "") -> dict:
    prior_txt = (prompts / "Physics_prior.md").read_text(encoding="utf-8")
    stripped, n_removed = prior_txt, 0
    for pat in PRIOR_STRIP_PATTERNS:
        stripped, k = re.subn(pat, "", stripped)
        n_removed += k
    if n_removed == 0:
        sys.exit("prior strip patterns matched nothing; refusing to build arm D blind")

    report = {}
    for base in arms:
        arm = base + suffix
        d = out / arm
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        # SupplementaryData holds only second-pass material (the registered RT-AFM images and their
        # registration, the fold tables, the ED/FTLM sweep code), all of it produced after the
        # as-run first pass proposed those follow-ups; a first-pass-only arm leaves it out
        subs = ("data", "meltingAnalysis") if first_pass_only else ("data", "meltingAnalysis", "SupplementaryData")
        for sub in subs:
            shutil.copytree(packet / sub, d / sub)
        lit_dst = d / "literature"
        lit_dst.mkdir()
        removed = []
        for f in sorted((packet / "literature").iterdir()):
            if base in ("C_litheld", "D_litheld_noprior", "E_nowindows") and f.name in withhold:
                removed.append(f.name)
                continue
            shutil.copy2(f, lit_dst / f.name)
        for f in TOOL_FILES:
            if (tools / f).exists():
                shutil.copy2(tools / f, d / f)
        if base == "E_nowindows":
            strip_tool_windows(d / "melting_temperature_mcp_tool.py")
        shutil.copy2(prompts / "AIMS_generic_bayesian_mechanism_protocol.md",
                     d / "AIMS_generic_bayesian_mechanism_protocol.md")
        loop_prompt = (prompts / "Discovery loop prompt.md").read_text(encoding="utf-8")
        (d / "Discovery loop prompt.md").write_text(first_pass_prompt(loop_prompt) if first_pass_only else loop_prompt, encoding="utf-8")
        shutil.copytree(skill, d / "mim-discovery-skill")
        (d / "Physics_prior.md").write_text(
            stripped if base in ("D_litheld_noprior", "E_nowindows") else prior_txt, encoding="utf-8")
        files = sorted(p for p in d.rglob("*") if p.is_file())
        manifest = {
            "arm": arm,
            "base_arm": base,
            "first_pass_only": first_pass_only,
            "internet": base == "A_asrun",
            "web_tools": base == "A_asrun",
            "literature_removed": removed,
            "prior_sentences_removed": n_removed if base in ("D_litheld_noprior", "E_nowindows") else 0,
            "tool_windows_removed": base == "E_nowindows",
            "n_files": len(files),
            "sha256": {str(p.relative_to(d)): sha256(p) for p in files},
        }
        # The manifest lives OUTSIDE the arm tree. The pilot showed that an in-tree
        # manifest.json is read by the agent, and its literature_removed list names the
        # withheld papers by title ("Quantum melting of ... twisted bilayer MoSe2"), which
        # leaks the conclusion the arm is meant to test. Nothing in the tree may identify
        # the arm or what was removed.
        (out / "manifests").mkdir(exist_ok=True)
        (out / "manifests" / f"{arm}.json").write_text(json.dumps(manifest, indent=1))
        report[arm] = {k: manifest[k] for k in
                       ("base_arm", "first_pass_only", "internet", "web_tools", "literature_removed", "prior_sentences_removed", "tool_windows_removed", "n_files")}
        leak = [str(p.relative_to(d)) for p in files
                if p.suffix in (".json", ".md", ".txt", ".py")
                and re.search(r"litheld|noprior|nowindows|withh[eo]ld|literature_removed", p.read_text(errors="ignore"), re.I)]
        if leak:
            sys.exit(f"arm {arm}: files in the tree mention the ablation itself: {leak}")
    (out / ("arms_report" + suffix + ".json")).write_text(json.dumps(report, indent=1))
    (out / "Physics_prior.D.diff.txt").write_text("\n".join(difflib.unified_diff(
        prior_txt.splitlines(), stripped.splitlines(),
        "Physics_prior.md (released)", "Physics_prior.md (arm D)", lineterm="")), encoding="utf-8")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--packet", required=True, help="discoveryLoopPacket directory")
    ap.add_argument("--prompts", required=True, help="prompts directory (prior, protocol, loop prompt)")
    ap.add_argument("--tools", required=True, help="directory with the released analysis tools")
    ap.add_argument("--skill", required=True, help="mim-discovery-skill directory (contains SKILL.md)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--withhold", nargs="*", default=None)
    ap.add_argument("--withhold-borderline", action="store_true")
    ap.add_argument("--arms", nargs="*", default=["A_asrun", "B_nointernet", "C_litheld", "D_litheld_noprior", "E_nowindows"])
    ap.add_argument("--first-pass-only", action="store_true",
                    help="leave out SupplementaryData and the '## Second pass' prompt section (material that postdates the as-run first pass)")
    ap.add_argument("--suffix", default="", help="appended to each arm name, e.g. _p1only")
    a = ap.parse_args()
    wh = list(a.withhold) if a.withhold is not None else list(DEFAULT_WITHHOLD)
    if a.withhold_borderline:
        wh += BORDERLINE
    rep = build(Path(a.packet), Path(a.prompts), Path(a.tools), Path(a.skill), Path(a.out), wh, a.arms,
                first_pass_only=a.first_pass_only, suffix=a.suffix)
    print(json.dumps(rep, indent=1))
