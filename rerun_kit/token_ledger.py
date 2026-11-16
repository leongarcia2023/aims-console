#!/usr/bin/env python3
"""token_ledger.py RUN_DIR [...]: the full token and activity accounting of a Claude Code rerun.

Reads RUN_DIR/agent_log.jsonl (the CLI's own session log), RUN_DIR/transcript.json (the CLI's
final result JSON) and RUN_DIR/meta.txt (the runner's clock) and writes into RUN_DIR:

  tokens_per_call.csv      one row per API call (one assistant message id), in order:
                           call, timestamp, seconds since the first call, model, stop reason,
                           input_tokens (uncached), cache_write_1h, cache_write_5m, cache_read,
                           output_tokens, thinking_tokens, context_in (= input + cache writes +
                           cache read: the context the model processed on that call), tools called
                           in that message, characters of assistant text, cumulative output tokens
  tokens_summary.json      the CLI's cost-state totals (per model: input, output, thinking, cache
                           read, cache creation split 5m/1h, cost USD; API time, tool time, total
                           time, lines added and removed), call and tool counts (per tool name),
                           tool_result count, compaction and background re-entry counts, request
                           configuration (model, max_tokens, thinking, effort, betas,
                           context_management), the tools the agent had, the system prompt's size
                           and sha256, the CLI version, the runner's start/end and wall minutes,
                           and the transcript's own result fields
  agent_system_prompt.txt  the system prompt blocks the agent saw, verbatim (from api-request-shape)
  tools_available.json     name and description of every tool the agent could call

Nothing here is estimated: every number is read from the log. Cost is the CLI's own figure
(cost-state.totalCostUSD, list prices at run time); no rate card is applied here.
"""
import csv
import datetime as dt
import hashlib
import json
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path


def load_events(path):
    out = []
    bad = 0
    with path.open(errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    return out, bad


def ledger(run: Path):
    events, bad = load_events(run / "agent_log.jsonl")
    calls = OrderedDict()          # message id -> row
    tool_counter = Counter()
    n_tool_results = 0
    n_compactions = 0
    n_bg_reentries = 0
    n_user_text = 0
    cost_state = None
    shape = None
    req = None
    versions = set()
    session_ids = set()
    first_ts = None
    for e in events:
        t = e.get("type")
        if e.get("sessionId"):
            session_ids.add(e["sessionId"])
        if e.get("version"):
            versions.add(e["version"])
        if t == "assistant":
            m = e.get("message") or {}
            mid = m.get("id") or e.get("requestId") or e.get("uuid")
            u = m.get("usage") or {}
            ts = e.get("timestamp")
            row = calls.get(mid)
            if row is None:
                row = {"call": len(calls) + 1, "timestamp": ts, "model": m.get("model"), "stop_reason": None,
                       "input_tokens": 0, "cache_write_1h": 0, "cache_write_5m": 0, "cache_read": 0,
                       "output_tokens": 0, "thinking_tokens": 0, "tools": [], "text_chars": 0, "requested_model": e.get("requestedModel")}
                calls[mid] = row
            cc = u.get("cache_creation") or {}
            row["input_tokens"] = max(row["input_tokens"], u.get("input_tokens") or 0)
            row["cache_write_1h"] = max(row["cache_write_1h"], cc.get("ephemeral_1h_input_tokens") or 0)
            row["cache_write_5m"] = max(row["cache_write_5m"], cc.get("ephemeral_5m_input_tokens") or 0)
            if not cc and u.get("cache_creation_input_tokens"):
                row["cache_write_5m"] = max(row["cache_write_5m"], u.get("cache_creation_input_tokens") or 0)
            row["cache_read"] = max(row["cache_read"], u.get("cache_read_input_tokens") or 0)
            row["output_tokens"] = max(row["output_tokens"], u.get("output_tokens") or 0)
            row["thinking_tokens"] = max(row["thinking_tokens"], ((u.get("output_tokens_details") or {}).get("thinking_tokens") or 0))
            if m.get("stop_reason"):
                row["stop_reason"] = m.get("stop_reason")
            for b in m.get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    row["tools"].append(b.get("name"))
                    tool_counter[b.get("name")] += 1
                elif b.get("type") == "text":
                    row["text_chars"] += len(b.get("text") or "")
        elif t == "user":
            c = (e.get("message") or {}).get("content")
            if isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        n_tool_results += 1
                    elif isinstance(b, dict) and b.get("type") == "text":
                        txt = b.get("text") or ""
                        n_user_text += 1
                        if "continued from a previous conversation" in txt or "ran out of context" in txt:
                            n_compactions += 1
                        if "<task-notification>" in txt or "task-notification" in txt:
                            n_bg_reentries += 1
            elif isinstance(c, str):
                n_user_text += 1
                if "continued from a previous conversation" in c:
                    n_compactions += 1
                if "task-notification" in c:
                    n_bg_reentries += 1
        elif t == "cost-state":
            cost_state = e
        elif t == "api-request-shape" and shape is None:
            shape = e.get("shape") or {}
        elif t == "api-request" and req is None:
            req = e.get("params") or {}
    rows = list(calls.values())
    t0 = None
    cum_out = 0
    for r in rows:
        try:
            ts = dt.datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")) if r["timestamp"] else None
        except Exception:
            ts = None
        if ts and t0 is None:
            t0 = ts
        r["seconds"] = round((ts - t0).total_seconds(), 1) if (ts and t0) else None
        r["context_in"] = r["input_tokens"] + r["cache_write_1h"] + r["cache_write_5m"] + r["cache_read"]
        cum_out += r["output_tokens"]
        r["cumulative_output"] = cum_out
        r["tools"] = ";".join(x for x in r["tools"] if x)
    # write per-call CSV
    fields = ["call", "timestamp", "seconds", "model", "stop_reason", "input_tokens", "cache_write_1h", "cache_write_5m", "cache_read",
              "output_tokens", "thinking_tokens", "context_in", "tools", "text_chars", "cumulative_output"]
    with (run / "tokens_per_call.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    # system prompt and tools
    sys_text = ""
    tools_avail = []
    if shape:
        blocks = shape.get("system") or []
        sys_text = "\n\n==== system block boundary ====\n\n".join((b.get("text") or "") for b in blocks if isinstance(b, dict))
        (run / "agent_system_prompt.txt").write_text(sys_text)
        for tdef in shape.get("tools") or []:
            if isinstance(tdef, dict):
                tools_avail.append({"name": tdef.get("name"), "description": (tdef.get("description") or "")[:4000]})
        (run / "tools_available.json").write_text(json.dumps(tools_avail, indent=1))
    # runner clock
    meta = (run / "meta.txt").read_text() if (run / "meta.txt").exists() else ""
    ms = re.search(r"start=(\S+)", meta)
    me = re.search(r"end=(\S+)", meta)
    wall_min = None
    try:
        if ms and me:
            wall_min = round((dt.datetime.fromisoformat(me.group(1)) - dt.datetime.fromisoformat(ms.group(1))).total_seconds() / 60, 1)
    except ValueError:
        pass
    tr = {}
    if (run / "transcript.json").exists():
        try:
            tr = json.loads((run / "transcript.json").read_text())
        except Exception:
            tr = {}
    summary = {
        "run": str(run),
        "n_log_events": len(events), "n_unparsed_lines": bad,
        "n_api_calls_by_message_id": len(rows),
        "n_api_request_events": sum(1 for e in events if e.get("type") == "api-request"),
        "n_assistant_events": sum(1 for e in events if e.get("type") == "assistant"),
        "n_tool_calls": sum(tool_counter.values()), "tool_calls_by_name": dict(tool_counter.most_common()),
        "n_tool_results": n_tool_results, "n_user_text_messages": n_user_text,
        "n_compactions_seen": n_compactions, "n_background_task_reentries": n_bg_reentries,
        "sum_output_tokens": sum(r["output_tokens"] for r in rows),
        "sum_thinking_tokens": sum(r["thinking_tokens"] for r in rows),
        "sum_input_tokens_uncached": sum(r["input_tokens"] for r in rows),
        "sum_cache_write_1h": sum(r["cache_write_1h"] for r in rows),
        "sum_cache_write_5m": sum(r["cache_write_5m"] for r in rows),
        "sum_cache_read": sum(r["cache_read"] for r in rows),
        "sum_context_in": sum(r["context_in"] for r in rows),
        "max_context_in_one_call": max((r["context_in"] for r in rows), default=0),
        "models_seen": sorted({r["model"] for r in rows if r["model"]}),
        "requested_models_seen": sorted({r["requested_model"] for r in rows if r.get("requested_model")}),
        "stop_reasons": dict(Counter(r["stop_reason"] for r in rows)),
        "cli_versions": sorted(versions), "session_ids": sorted(session_ids),
        "request_config": {k: req.get(k) for k in ("model", "max_tokens", "thinking", "output_config", "context_management", "betas")} if req else None,
        "system_prompt_chars": len(sys_text), "system_prompt_sha256": hashlib.sha256(sys_text.encode()).hexdigest() if sys_text else None,
        "tools_available": [t["name"] for t in tools_avail],
        "cost_state": {k: cost_state.get(k) for k in ("totalCostUSD", "totalAPIDuration", "totalAPIDurationWithoutRetries", "totalToolDuration", "totalDuration", "totalLinesAdded", "totalLinesRemoved", "startTime", "modelUsage", "hasUnknownModelCost")} if cost_state else None,
        "runner_clock": {"start": ms.group(1) if ms else None, "end": me.group(1) if me else None, "wall_min": wall_min},
        "transcript_result": {k: tr.get(k) for k in ("subtype", "is_error", "num_turns", "duration_ms", "duration_api_ms", "total_cost_usd", "usage", "session_id")} if tr else None,
        "first_call": rows[0]["timestamp"] if rows else None, "last_call": rows[-1]["timestamp"] if rows else None,
    }
    (run / "tokens_summary.json").write_text(json.dumps(summary, indent=1))
    return summary


def main(dirs):
    for d in dirs:
        s = ledger(Path(d))
        cs = s.get("cost_state") or {}
        print(f"{d}: calls {s['n_api_calls_by_message_id']}, tools {s['n_tool_calls']}, out {s['sum_output_tokens']}, "
              f"cache read {s['sum_cache_read']}, cost {cs.get('totalCostUSD')}, wall {s['runner_clock']['wall_min']} min, "
              f"compactions {s['n_compactions_seen']}, bg re-entries {s['n_background_task_reentries']}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["."])
