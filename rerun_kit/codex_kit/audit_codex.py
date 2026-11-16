#!/usr/bin/env python3
"""audit_codex.py RUN_DIR [...]: audit a Codex-driven rerun and write what the aggregator needs.

Reads RUN_DIR/events.jsonl (Codex `exec --json`, either event format) and RUN_DIR/final_message.md,
and writes:
  audit.json / audit.txt   the same four checks as audit_run.py for the Claude runs:
                           network commands (pip, curl, wget, git, npm, apt), pip installs,
                           absolute paths outside the packet, mentions of the ablation or the arm
  transcript.json          {result, num_turns, usage, duration_ms, total_cost_usd: null, subtype}
                           so aggregate_runs.py reads a Codex run like a Claude run (cost is
                           null: a ChatGPT plan bills nothing per run; tokens are in usage)
The Codex JSON is traversed generically: any dict with a "command" is a shell command, any dict
with input/output token counts is usage, any "agent_message" is assistant text. Nothing here is
format-exact, so the audit lists what it found and how many events it could not parse.
"""
import datetime
import json
import re
import sys
from pathlib import Path

NET_RE = re.compile(r"(^|[\s;&|('\"])(pip3?|uv|conda|npm|npx|curl|wget|apt(-get)?|git\s+(clone|fetch|pull))\b")   # Codex wraps commands in bash -lc '...' 
PIP_RE = re.compile(r"\bpip3?\s+install\b|\buv\s+pip\s+install\b|python3?\s+-m\s+pip\s+install\b")
BLIND_RE = re.compile(r"ablation[- ]arm|this arm|arm\s+[A-E]\b|_asrun|nointernet|litheld|noprior|nowindows|withh[eo]ld|manifest\.json|literature_removed|papers? (that )?(were|was|been) removed", re.I)
PATH_RE = re.compile(r"(?<![\w./-])(/[\w.@][\w.@/+-]*)")
BENIGN = ("/tmp/", "/dev/", "/usr/", "/bin/", "/opt/", "/etc/", "/private/", "/System/", "/Library/", "/var/", "/proc/", "/sys/", "/Applications/")


def walk(o, found):
    """Collect commands, outputs, messages and usage from any nesting of the event JSON."""
    if isinstance(o, dict):
        cmd = o.get("command")
        if cmd and (isinstance(cmd, str) or isinstance(cmd, list)):
            found["commands"].append(" ".join(cmd) if isinstance(cmd, list) else cmd)
        for k in ("aggregated_output", "output", "stdout", "stderr"):
            if isinstance(o.get(k), str) and o[k]:
                found["outputs"].append(o[k])
        t = o.get("type")
        if t in ("agent_message", "message") or (t is None and isinstance(o.get("message"), str)):
            txt = o.get("text") or o.get("message")
            if isinstance(txt, str) and txt.strip():
                found["messages"].append(txt)
        if any(k in o for k in ("input_tokens", "output_tokens", "cached_input_tokens", "total_tokens")):
            found["usage"].append({k: o[k] for k in ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens", "reasoning_output_tokens") if isinstance(o.get(k), int)})
        if t in ("turn.completed", "turn.started", "task_complete", "task_started"):
            found["turn_events"] += 1
        if t == "error" or "error" in o and isinstance(o.get("error"), (str, dict)):
            msg = str(o.get("message") or o.get("error"))
            if not msg.startswith("Codex is ignoring"):       # a config-key warning, not a failure
                found["errors"].append(msg[:300])
        for v in o.values():
            walk(v, found)
    elif isinstance(o, list):
        for v in o:
            walk(v, found)


def audit(run: Path):
    found = {"commands": [], "outputs": [], "messages": [], "usage": [], "turn_events": 0, "errors": []}
    n_lines = n_bad = 0
    for line in (run / "events.jsonl").read_text(errors="replace").splitlines() if (run / "events.jsonl").exists() else []:
        if not line.strip():
            continue
        n_lines += 1
        try:
            walk(json.loads(line), found)
        except json.JSONDecodeError:
            n_bad += 1
            found["messages"].append(line)      # plain-text output when --json was unavailable
    packet_hint = "/packet"
    cmds = found["commands"]
    out = {"n_events": n_lines, "n_unparsed_lines": n_bad, "n_commands": len(cmds), "n_messages": len(found["messages"]),
           "network_commands": [c[:200] for c in cmds if NET_RE.search(c)],
           "pip_installs": [c[:200] for c in cmds if PIP_RE.search(c)]}
    outside = set()
    for c in cmds:
        for p in PATH_RE.findall(c):
            if packet_hint in p or p.startswith(BENIGN) or p in ("/", "/Users", "/Users/"):
                continue
            outside.add(p)
    out["outside_paths"] = sorted(outside)[:40]
    sweeps = [c[:200] for c in cmds if re.search(r"(^|[\s;&|(])(find|ls|grep|rg|du|tree|locate)\s+(-\S+\s+)*(/|~|/Users)(\s|$)", c)]
    out["root_sweeps"] = sweeps[:20]
    text = "\n".join(found["messages"])
    mentions = [m.group(0) for m in BLIND_RE.finditer(text)]
    out["blinding_mentions"] = mentions[:40]
    out["n_blinding_mentions"] = len(mentions)
    # network evidence from the agent's own command outputs: a sandbox denial or a resolver failure
    denied = [o[:160] for o in found["outputs"] if re.search(r"Could not resolve host|Network is unreachable|Temporary failure in name resolution|NameResolutionError|connection broken|sandbox|Operation not permitted|ConnectionError|Failed to establish|nodename nor servname", o)]
    out["network_denials_seen"] = denied[:10]
    out["errors"] = found["errors"][:10]
    # usage: Codex reports cumulative totals per turn in some versions and per-turn deltas in others;
    # keep the last record and the max, and say which was used
    usage = found["usage"]
    last = usage[-1] if usage else {}
    mx = {k: max(u.get(k, 0) for u in usage) for k in ("input_tokens", "cached_input_tokens", "output_tokens")} if usage else {}
    out["usage_last"], out["usage_max"] = last, mx
    return out, found


def transcript(run: Path, a: dict, found: dict):
    final = (run / "final_message.md").read_text() if (run / "final_message.md").exists() else (found["messages"][-1] if found["messages"] else "")
    meta = (run / "meta.txt").read_text() if (run / "meta.txt").exists() else ""
    t0 = re.search(r"start=(\S+)", meta); t1 = re.search(r"end=(\S+)", meta)
    dur = None
    try:
        if t0 and t1:
            dur = int((datetime.datetime.fromisoformat(t1.group(1)) - datetime.datetime.fromisoformat(t0.group(1))).total_seconds() * 1000)
    except ValueError:
        pass
    model = (re.search(r"model_reported=(\S+)", meta) or re.search(r"model=(\S+)", meta) or [None, None])[1]
    roll = run / "codex_session.jsonl"
    if roll.exists():
        m = re.search(r'"model":\s*"([^"]+)"', roll.read_text(errors="replace"))
        if m:
            model = m.group(1)
    u = a.get("usage_max") or {}
    api_error = any(re.search(r'"status":\s*[45]\d\d|model_not_found|invalid_request|unauthorized|insufficient_quota', e) for e in a["errors"])
    tr = {"type": "result", "subtype": "success" if final and not api_error else "error", "is_error": not bool(final),
          "result": final, "num_turns": a["n_commands"] + a["n_messages"], "duration_ms": dur, "total_cost_usd": None,
          "usage": {"input_tokens": u.get("input_tokens"), "cache_read_input_tokens": u.get("cached_input_tokens"),
                    "output_tokens": u.get("output_tokens")}, "driver": "codex", "model": model}
    (run / "transcript.json").write_text(json.dumps(tr, indent=1))
    return tr


def main(dirs):
    for d in dirs:
        run = Path(d)
        a, found = audit(run)
        tr = transcript(run, a, found)
        (run / "audit.json").write_text(json.dumps(a, indent=1))
        lines = [f"events: {a['n_events']} (unparsed lines: {a['n_unparsed_lines']}); commands: {a['n_commands']}; messages: {a['n_messages']}; turns (commands + messages): {tr['num_turns']}",
                 f"network commands: {len(a['network_commands'])}; pip installs: {len(a['pip_installs'])}; network denials seen in outputs: {len(a['network_denials_seen'])}",
                 f"paths outside the packet: {a['outside_paths'] or 'none'}",
                 f"root or home sweeps: {len(a['root_sweeps'])}",
                 f"blinding mentions: {a['n_blinding_mentions']}",
                 f"usage (max over records): {a['usage_max']}",
                 f"errors: {a['errors'] or 'none'}"]
        for c in a["network_commands"][:10]:
            lines.append(f"  net: {c}")
        for m in a["blinding_mentions"][:10]:
            lines.append(f"  mention: {m}")
        (run / "audit.txt").write_text("\n".join(lines) + "\n")
        print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1:] or ["."])
