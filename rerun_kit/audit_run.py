#!/usr/bin/env python3
"""audit_run.py RUN_DIR [RUN_DIR ...]: audit one discovery-loop run from the agent's own log.

The headless CLI writes a JSONL log of every assistant message, tool call and tool result
under the run's private HOME (.claude/projects/<cwd-slug>/<session>.jsonl); run_arm.sh
copies it to RUN_DIR/agent_log.jsonl. This script reads that log and writes
RUN_DIR/audit.json and RUN_DIR/audit.txt with:

  tools_used          count of calls per tool name
  unexpected_tools    tools outside the file/shell/task set (web tools, mcp__*, file delivery)
  network_commands    shell commands that could reach the network (pip, curl, wget, git, npm, apt)
  outside_paths       absolute paths used by Read/Write/Edit/Bash that lie outside the packet
  blinding_mentions   assistant text mentioning the ablation, arm names, withheld material or
                      a manifest (the pilot's leak surfaced exactly this way)
  skill_call          what happened to the "use mim_discovery_skill" instruction

Exit status is 0 whether or not anything is flagged; the flags are data for the SI, and
aggregate_runs.py copies the counts into rerun_summary.csv.
"""
import collections, json, re, sys
from pathlib import Path

# file/shell tools, the task list, subagents, and the CLI's own wait/schedule tools (Monitor,
# ScheduleWakeup: they pause the agent inside its own session and reach nothing outside it).
# Anything else (web tools, mcp__*, file delivery into another chat) is unexpected.
EXPECTED_TOOLS = {"Bash", "Read", "Write", "Edit", "Glob", "Grep", "Skill", "ToolSearch",
                  "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TaskStop", "Agent",
                  "NotebookEdit", "Monitor", "ScheduleWakeup", "Workflow", "ListAgents"}
NET_RE = re.compile(r"(^|[\s;&|(])(pip3?|uv|conda|npm|npx|curl|wget|apt(-get)?|git\s+(clone|fetch|pull))\b")
PIP_RE = re.compile(r"\bpip3?\s+install\b|\buv\s+pip\s+install\b|python3?\s+-m\s+pip\s+install\b")
# "ablation" alone is protocol vocabulary (the protocol asks for ablations), so the pattern
# needs the arm framing, an arm name, or the withheld material
BLIND_RE = re.compile(r"ablation[- ]arm|this arm|arm\s+[A-E]\b|_asrun|nointernet|litheld|noprior|nowindows|withh[eo]ld|manifest\.json|literature_removed|papers? (that )?(were|was|been) removed", re.I)
# commands that look around the host rather than the packet: a sweep from /, a look into home
# directories or the parent session's files, or an attempt to undo the sandbox mounts
HOME_PREFIXES = ("/home", "/root", "/mnt", "/Users")
ROOT_SWEEP_RE = re.compile(r"(^|[\s;&|(])(find|ls|grep|rg|du|tree|locate)\s+(-\S+\s+)*/(\s|$)|(^|[\s;&|(])(mount|umount|nsenter|unshare)\b|(^|[\s;&|(=])~/")


def is_sweep(cmd: str, packet) -> bool:
    if ROOT_SWEEP_RE.search(cmd):
        return True
    for p in paths_in(cmd):
        if packet and p.startswith(packet):
            continue
        if p.startswith(HOME_PREFIXES):
            return True
    return False
SAFE_PREFIXES = ("/usr", "/bin", "/sbin", "/lib", "/opt", "/dev", "/proc", "/sys", "/etc", "/var/tmp", "/tmp")


def paths_in(text: str):
    """Absolute paths in a command: at least two components, or a home-style prefix."""
    out = []
    for p in re.findall(r"(?<![\w./-])(/[\w.@][\w.@/+-]*)", text or ""):
        if p.startswith(HOME_PREFIXES) or p.count("/") >= 2:
            out.append(p)
    return out


def audit(run: Path) -> dict:
    log = run / "agent_log.jsonl"
    out = {"run": str(run), "log_present": log.exists()}
    if not log.exists():
        return out
    packet = None
    meta = run / "meta.txt"
    if meta.exists():
        m = re.search(r"packet=(\S+)", meta.read_text())
        if m:
            packet = m.group(1)
    tools = collections.Counter()
    bash, reads, writes, texts, skill = [], [], [], [], []
    ids = {}
    with open(log) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            c = (d.get("message") or {}).get("content")
            if not isinstance(c, list):
                continue
            for b in c:
                if not isinstance(b, dict):
                    continue
                t = b.get("type")
                if t == "tool_use":
                    name = b.get("name", "?"); tools[name] += 1
                    inp = b.get("input") or {}
                    if name == "Bash":
                        bash.append(inp.get("command") or "")
                    elif name == "Read":
                        reads.append(inp.get("file_path") or "")
                    elif name in ("Write", "Edit", "NotebookEdit"):
                        writes.append(inp.get("file_path") or inp.get("notebook_path") or "")
                    elif name == "Skill":
                        ids[b.get("id")] = inp.get("skill")
                elif t == "tool_result" and b.get("tool_use_id") in ids:
                    cc = b.get("content"); s = cc if isinstance(cc, str) else json.dumps(cc)
                    skill.append({"skill": ids[b["tool_use_id"]], "result": s[:200]})
                elif t == "text" and d.get("type") == "assistant":
                    texts.append(b.get("text") or "")
    out["tools_used"] = dict(tools)
    out["unexpected_tools"] = sorted(k for k in tools if k not in EXPECTED_TOOLS)
    out["network_commands"] = [c[:200] for c in bash if NET_RE.search(c)]
    out["pip_installs"] = [c[:200] for c in bash if PIP_RE.search(c)]
    out["host_sweeps"] = [c[:200] for c in bash if is_sweep(c, packet)]
    outside = []
    for p in reads + writes + [q for c in bash for q in paths_in(c)]:
        if not p or not p.startswith("/"):
            continue
        if packet and p.startswith(packet):
            continue
        if p.startswith(SAFE_PREFIXES):
            continue
        outside.append(p)
    out["outside_paths"] = sorted(set(outside))[:40]
    mentions = []
    for t in texts:
        for m in BLIND_RE.finditer(t):
            s, e = max(0, m.start() - 80), min(len(t), m.end() + 80)
            mentions.append(t[s:e].replace("\n", " "))
    out["blinding_mentions"] = mentions[:40]
    out["n_blinding_mentions"] = len(mentions)
    out["skill_call"] = skill
    out["n_assistant_messages"] = len(texts)
    return out


def main(dirs):
    for d in dirs:
        run = Path(d)
        a = audit(run)
        (run / "audit.json").write_text(json.dumps(a, indent=1))
        lines = [f"run: {run}", f"log present: {a.get('log_present')}"]
        if a.get("log_present"):
            lines += [f"tools used: {a['tools_used']}",
                      f"unexpected tools: {a['unexpected_tools'] or 'none'}",
                      f"pip installs: {len(a['pip_installs'])}",
                      f"network-capable commands: {len(a['network_commands'])}",
                      f"host sweeps: {len(a['host_sweeps'])}",
                      f"paths outside the packet: {a['outside_paths'] or 'none'}",
                      f"blinding mentions: {a['n_blinding_mentions']}",
                      f"skill call: {a['skill_call'] or 'none'}"]
            for mnt in a["blinding_mentions"][:10]:
                lines.append(f"  ... {mnt} ...")
        (run / "audit.txt").write_text("\n".join(lines) + "\n")
        print("\n".join(lines))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
