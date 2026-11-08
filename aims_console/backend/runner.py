"""runner.py: one AIMS loop run, with every step streamed as events and written to a JSONL log.

Two drivers share one contract. ClaudeRun drives a Claude model through the Claude Agent SDK
(the same headless engine as the blinded reruns); OpenAIRun (openai_runner.py) drives a GPT
model through the OpenAI Responses API with the same MCP tools. For the navigation and
measurement loops the agent gets the team's MCP server (SSE) as its only tool surface; for the
discovery loop a Claude agent gets file and shell tools inside a packet directory, the same
construction as the blinded reruns. Tool calls on a configurable list (stage moves, tip
approach, gate ramps) can be held for operator approval; the operator can also stop a run, or
inject a message between turns.

Events (dicts with a "type") are the contract with the console page and the log:
  status            {state, detail}
  system            {subtype, tools, mcp_servers, model}
  assistant_text    {text}
  tool_call         {tool_use_id, name, input}
  tool_result       {tool_use_id, name, text, images: [data-uri], is_error}
  awaiting_approval {approval_id, name, input}
  approval          {approval_id, decision, note}
  operator_message  {text}
  result            {subtype, num_turns, duration_ms, total_cost_usd, result, is_error, usage}
  error             {message}

Credentials: a run may carry its own API key (bring your own key). It is placed in the agent
subprocess's environment or in the request headers for that run only; it is never written to
the log directory and never echoed in an event.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

IMAGE_RE = re.compile(r"data:image/(?:png|jpeg);base64,[A-Za-z0-9+/=]+")

# tools that change the instrument's state; held for approval when the run asks for it
INSTRUMENT_TOOLS = {"move_tip", "execute_movement", "approach_and_lift", "retract_tip", "bottom_gate_ramp",
                    "bottom_gate_ramp_and_scan", "top_gate_ramp", "generic_sweeper_start",
                    "grid_generic_sweeper_start", "nanonis_xy_set", "set_scan_frame", "scan"}


def short_name(tool: str) -> str:
    """mcp__aims__move_tip -> move_tip"""
    return tool.split("__")[-1] if tool.startswith("mcp__") else tool


def split_images(text: str):
    """Pull data-URI images out of a tool result so the page shows them and the log stays
    readable; the agent itself already received the full result."""
    images = IMAGE_RE.findall(text or "")
    cleaned = IMAGE_RE.sub("[image]", text or "")
    return cleaned, images


class BaseRun:
    """Event log, subscribers, approvals and the run directory; drivers add the model loop."""

    driver = "base"

    def __init__(self, *, loop: str, prompt: str, system_prompt: Optional[str], model: str,
                 mcp_url: Optional[str], cwd: Optional[str], max_turns: int, approve: bool,
                 approve_tools: Optional[List[str]], log_dir: Path, env: Optional[Dict[str, str]] = None,
                 tools_mode: str = "mcp", api_key: Optional[str] = None, skills_dir: Optional[Path] = None,
                 client: Optional[str] = None, wall_limit_s: Optional[float] = None, extra_meta: Optional[dict] = None):
        self.id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.loop, self.prompt, self.system_prompt, self.model = loop, prompt, system_prompt, model
        self.mcp_url, self.cwd, self.max_turns = mcp_url, cwd, max_turns
        self.approve, self.approve_tools = approve, set(approve_tools or INSTRUMENT_TOOLS)
        self.tools_mode = tools_mode
        self.env = dict(env or {})
        self.api_key = api_key or None
        self.skills_dir = skills_dir
        self.client_id = client
        self.wall_limit_s = wall_limit_s
        self.dir = log_dir / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events: List[dict] = []
        self.subscribers: List[asyncio.Queue] = []
        self.state = "created"
        self.task: Optional[asyncio.Task] = None
        self.pending: Dict[str, asyncio.Future] = {}
        self.tool_names: Dict[str, str] = {}      # tool_use_id -> name
        self.started = time.time()
        self.result: Optional[dict] = None
        self.on_finish = []                       # callables run when the run ends (simulator teardown)
        meta = {"id": self.id, "loop": loop, "driver": self.driver, "model": model, "mcp_url": mcp_url, "cwd": cwd,
                "max_turns": max_turns, "approve": approve, "approve_tools": sorted(self.approve_tools),
                "tools_mode": tools_mode, "own_key": bool(api_key), "skills": sorted(p.name for p in skills_dir.iterdir()) if skills_dir else [],
                "client": client, "prompt": prompt, "system_prompt": system_prompt, "started": datetime.now().isoformat()}
        meta.update(extra_meta or {})
        (self.dir / "meta.json").write_text(json.dumps(meta, indent=1))

    def update_meta(self, **kw):
        p = self.dir / "meta.json"
        meta = json.loads(p.read_text())
        meta.update(kw)
        p.write_text(json.dumps(meta, indent=1))

    # -- events ------------------------------------------------------------------------
    def emit(self, ev: dict):
        ev = {"t": round(time.time() - self.started, 2), "seq": len(self.events), **ev}
        self.events.append(ev)
        with open(self.dir / "events.jsonl", "a") as f:
            f.write(json.dumps(ev) + "\n")
        for q in list(self.subscribers):
            q.put_nowait(ev)

    def set_state(self, state: str, detail: str = ""):
        self.state = state
        self.emit({"type": "status", "state": state, "detail": detail})
        if state in ("finished", "error", "stopped"):
            for cb in self.on_finish:
                try:
                    cb()
                except Exception:
                    pass
            self.on_finish = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        for ev in self.events:          # replay history to a late joiner
            q.put_nowait(ev)
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self.subscribers:
            self.subscribers.remove(q)

    def finish(self, result: dict):
        self.result = result
        self.emit({"type": "result", **result})
        (self.dir / "result.json").write_text(json.dumps(result, indent=1))
        self.set_state("finished" if not result.get("is_error") else "error", str(result.get("subtype", "")))

    # -- approvals ---------------------------------------------------------------------
    async def ask_approval(self, name: str, tool_input: dict):
        """True when the operator allows the call (or no approval is needed); otherwise the note."""
        if not (self.approve and name in self.approve_tools):
            return True, ""
        approval_id = uuid.uuid4().hex[:8]
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self.pending[approval_id] = fut
        self.emit({"type": "awaiting_approval", "approval_id": approval_id, "name": name, "input": tool_input})
        self.set_state("awaiting_approval", name)
        decision, note = await fut
        self.pending.pop(approval_id, None)
        self.emit({"type": "approval", "approval_id": approval_id, "decision": decision, "note": note})
        self.set_state("running")
        return decision == "allow", note

    def decide(self, approval_id: str, decision: str, note: str = ""):
        fut = self.pending.get(approval_id)
        if fut and not fut.done():
            fut.set_result((decision, note))
            return True
        return False

    def deny_pending(self, note: str):
        for fut in self.pending.values():
            if not fut.done():
                fut.set_result(("deny", note))

    # -- the loop (drivers implement _main, inject, stop) --------------------------------
    async def start(self):
        self.task = asyncio.create_task(self._guarded())
        if self.wall_limit_s:
            asyncio.create_task(self._watchdog())

    async def _watchdog(self):
        await asyncio.sleep(self.wall_limit_s)
        if self.state not in ("finished", "error", "stopped"):
            self.emit({"type": "error", "message": f"wall-time limit of {self.wall_limit_s / 60:.0f} min reached; stopping the run"})
            await self.stop()

    async def _guarded(self):
        try:
            await self._main()
        except asyncio.CancelledError:
            if self.state not in ("finished", "error", "stopped"):
                self.set_state("stopped", "cancelled")
        except Exception as e:  # noqa: BLE001
            e = unwrap(e)
            self.emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
            self.set_state("error", str(e)[:200])

    async def _main(self):
        raise NotImplementedError

    async def inject(self, text: str) -> bool:
        raise NotImplementedError

    async def stop(self):
        raise NotImplementedError


def unwrap(e: BaseException) -> BaseException:
    """The innermost exception of an anyio/asyncio ExceptionGroup, for a readable error event."""
    seen = 0
    while isinstance(e, BaseExceptionGroup) and e.exceptions and seen < 10:
        e = e.exceptions[0]
        seen += 1
    return e


def make_private_home(run_dir: Path, source_home: Optional[str], keep_credentials: bool) -> str:
    """A HOME for the agent subprocess: nothing from the operator's account except, when the run
    carries no key of its own, the signed-in CLI credentials. Keeps the agent away from the
    server's settings, memory and other projects."""
    home = run_dir / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    if keep_credentials and source_home:
        cred = Path(source_home) / ".claude" / ".credentials.json"
        if cred.exists():
            shutil.copy2(cred, home / ".claude" / ".credentials.json")
    return str(home)


def install_skills(work: Path, skills_dir: Optional[Path]):
    """Copy the console's skills into <work>/.claude/skills so the project settings source finds them."""
    if not skills_dir or not skills_dir.is_dir():
        return []
    dst = work / ".claude" / "skills"
    dst.mkdir(parents=True, exist_ok=True)
    names = []
    for s in sorted(skills_dir.iterdir()):
        if (s / "SKILL.md").exists():
            if (dst / s.name).exists():
                shutil.rmtree(dst / s.name)
            shutil.copytree(s, dst / s.name)
            names.append(s.name)
    return names


class ClaudeRun(BaseRun):
    """A Claude model through the Claude Agent SDK."""

    driver = "claude"

    def __init__(self, **kw):
        super().__init__(**kw)
        self.client = None

    async def can_use_tool(self, tool_name: str, tool_input: dict, context):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
        ok, note = await self.ask_approval(short_name(tool_name), tool_input)
        if ok:
            return PermissionResultAllow()
        return PermissionResultDeny(message=note or "Denied by the operator. Explain what you would do instead and wait.")

    def options(self):
        from claude_agent_sdk import ClaudeAgentOptions
        env = dict(self.env)
        if self.api_key:                                   # the run's own key, nothing from the server
            for k in ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
                env.pop(k, None)
            env["ANTHROPIC_API_KEY"] = self.api_key
        env["HOME"] = make_private_home(self.dir, self.env.get("HOME") or os.environ.get("HOME"), keep_credentials=not self.api_key)
        kw: Dict[str, Any] = dict(model=self.model, max_turns=self.max_turns, can_use_tool=self.can_use_tool,
                                  env=env, permission_mode="default")
        if self.system_prompt:
            kw["system_prompt"] = self.system_prompt
        # Tools in allowed_tools never reach the permission callback, so with approvals on the
        # instrument tools stay out of that list and every call goes through can_use_tool, which
        # allows analysis tools at once and holds the instrument tools for the operator.
        if self.tools_mode == "mcp":
            kw["mcp_servers"] = {"aims": {"type": "sse", "url": self.mcp_url}}
            kw["strict_mcp_config"] = True
            kw["allowed_tools"] = ([] if self.approve else ["mcp__aims"]) + (["Skill"] if self.skills_dir else [])
            kw["disallowed_tools"] = ["Bash", "Write", "Edit", "WebSearch", "WebFetch", "Agent", "NotebookEdit"]
            kw["tools"] = ["Skill"] if self.skills_dir else []      # nothing but the instrument (and the skills)
        else:                         # discovery: file + shell tools inside the packet
            kw["tools"] = ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]
            kw["allowed_tools"] = ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]
            kw["disallowed_tools"] = ["WebSearch", "WebFetch"]
        # the project settings source is the run's own working directory: it holds the console's
        # skills and nothing else; the user source (the server account's settings) is never loaded
        kw["setting_sources"] = ["project"] if self.skills_dir else []
        if self.cwd:
            kw["cwd"] = self.cwd
        return ClaudeAgentOptions(**kw)

    async def _main(self):
        from claude_agent_sdk import ClaudeSDKClient
        self.set_state("connecting")
        self.client = ClaudeSDKClient(self.options())
        try:
            await self.client.connect()
            self.set_state("running")
            await self.client.query(self.prompt)
            await self._consume()
        finally:
            try:
                await self.client.disconnect()
            except Exception:
                pass

    async def _consume(self):
        from claude_agent_sdk import (AssistantMessage, ResultMessage, SystemMessage, TextBlock, ToolResultBlock,
                                      ToolUseBlock, UserMessage)
        async for msg in self.client.receive_messages():
            if isinstance(msg, SystemMessage):
                d = msg.data or {}
                if msg.subtype == "init":
                    self.emit({"type": "system", "subtype": msg.subtype, "tools": d.get("tools"),
                               "mcp_servers": d.get("mcp_servers"), "model": d.get("model"),
                               "skills": d.get("skills") or d.get("slash_commands")})
            elif isinstance(msg, AssistantMessage):
                for b in msg.content:
                    if isinstance(b, TextBlock) and b.text.strip():
                        self.emit({"type": "assistant_text", "text": b.text})
                    elif isinstance(b, ToolUseBlock):
                        self.tool_names[b.id] = short_name(b.name)
                        self.emit({"type": "tool_call", "tool_use_id": b.id, "name": short_name(b.name), "input": b.input})
                if getattr(msg, "error", None):
                    self.emit({"type": "error", "message": f"model error: {msg.error}"})
            elif isinstance(msg, UserMessage):
                blocks = msg.content if isinstance(msg.content, list) else []
                for b in blocks:
                    if isinstance(b, ToolResultBlock):
                        text = b.content if isinstance(b.content, str) else " ".join(
                            c.get("text", "") for c in (b.content or []) if isinstance(c, dict))
                        images = []
                        if isinstance(b.content, list):
                            for c in b.content:
                                if isinstance(c, dict) and c.get("type") == "image":
                                    src = c.get("source", {})
                                    if src.get("data"):
                                        images.append(f"data:{src.get('media_type', 'image/png')};base64,{src['data']}")
                        cleaned, more = split_images(text)
                        images += self._image_files(cleaned)
                        self.emit({"type": "tool_result", "tool_use_id": b.tool_use_id,
                                   "name": self.tool_names.get(b.tool_use_id, "?"), "text": cleaned,
                                   "images": images + more, "is_error": bool(b.is_error)})
            elif isinstance(msg, ResultMessage):
                usage = getattr(msg, "usage", None)
                self.finish({"subtype": msg.subtype, "num_turns": msg.num_turns, "duration_ms": msg.duration_ms,
                             "total_cost_usd": msg.total_cost_usd, "result": msg.result, "is_error": msg.is_error,
                             "session_id": msg.session_id, "usage": usage if isinstance(usage, dict) else None})
                return

    def _image_files(self, text: str):
        """The CLI keeps a large image result on disk and tells the model '[Image: source: <path>]';
        read it back so the page and the log carry the picture too (only files under the run's own HOME)."""
        out = []
        import base64
        for m in re.finditer(r"\[Image: source: ([^\]\s]+)\]", text or ""):
            p = Path(m.group(1))
            try:
                if p.is_file() and str(p.resolve()).startswith(str(self.dir.resolve())):
                    mime = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
                    out.append(f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode())
            except Exception:
                pass
        return out

    async def inject(self, text: str):
        """A message from the operator, delivered as the next user turn (after the current
        response ends)."""
        if not self.client or self.state in ("finished", "error", "stopped"):
            return False
        self.emit({"type": "operator_message", "text": text})
        await self.client.query(text)
        self.set_state("running")
        asyncio.create_task(self._consume())
        return True

    async def stop(self):
        self.deny_pending("Run stopped by the operator.")
        try:
            if self.client:
                await self.client.interrupt()
        except Exception:
            pass
        if self.task and not self.task.done():
            self.task.cancel()
        if self.state not in ("finished", "error"):
            self.set_state("stopped", "operator")


# kept for callers that import the old name
Run = ClaudeRun
