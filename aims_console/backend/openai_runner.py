"""openai_runner.py: a GPT model driving the same MCP tools, through the OpenAI Responses API.

Same event contract as runner.ClaudeRun. The loop is explicit here: list the MCP server's
tools, hand them to the model as function tools, call the ones it asks for over MCP, feed the
results back, repeat until the model answers without tool calls or the turn cap is reached.
Approvals, operator messages and stop work the same way as for the Claude driver. The run's
API key travels only in the Authorization header of its own requests.

Only the MCP tool surface is supported (navigation and measurement); the discovery loop's file
and shell tools are a Claude Code construction and are not offered through this driver.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from .runner import BaseRun, split_images

import os
OPENAI_URL = os.environ.get("AIMS_OPENAI_URL", "https://api.openai.com/v1/responses")   # override for tests


def _schema(tool) -> dict:
    s = dict(tool.inputSchema or {"type": "object", "properties": {}})
    s.setdefault("type", "object")
    s.setdefault("properties", {})
    return s


class OpenAIRun(BaseRun):
    driver = "openai"

    def __init__(self, *, base_url: str = OPENAI_URL, skills_text: Optional[str] = None, **kw):
        super().__init__(**kw)
        self.base_url = base_url
        self.skills_text = skills_text
        self.pending_messages: List[str] = []
        self.session = None
        self.turns = 0
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def instructions(self) -> str:
        parts = [self.system_prompt or ""]
        if self.skills_text:
            parts.append("The following skills are in effect. When the task says to use one of them, follow its "
                         "procedure exactly.\n\n" + self.skills_text)
        return "\n\n".join(p for p in parts if p)

    async def _main(self):
        from mcp import ClientSession
        from mcp.client.sse import sse_client
        if not self.api_key:
            raise RuntimeError("this driver needs an OpenAI API key for the run")
        self.set_state("connecting")
        async with sse_client(self.mcp_url) as (r, w):
            async with ClientSession(r, w) as session:
                self.session = session
                await session.initialize()
                tools = (await session.list_tools()).tools
                fn_tools = [{"type": "function", "name": t.name, "description": (t.description or "")[:1024],
                             "parameters": _schema(t)} for t in tools]
                self.emit({"type": "system", "subtype": "init", "tools": [t.name for t in tools],
                           "mcp_servers": [{"name": "aims", "status": "connected"}], "model": self.model,
                           "skills": ["(in system prompt)"] if self.skills_text else []})
                self.set_state("running")
                await self._loop(fn_tools)

    async def _call_model(self, client: httpx.AsyncClient, body: dict) -> dict:
        for attempt in range(5):
            resp = await client.post(self.base_url, json=body,
                                     headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
            if resp.status_code in (429, 500, 502, 503, 529) and attempt < 4:
                await asyncio.sleep(2 ** attempt * 3)
                continue
            if resp.status_code >= 400:
                try:
                    msg = resp.json().get("error", {}).get("message", resp.text[:300])
                except Exception:
                    msg = resp.text[:300]
                raise RuntimeError(f"OpenAI API {resp.status_code}: {msg}")
            return resp.json()
        raise RuntimeError("OpenAI API: repeated transient failures")

    async def _loop(self, fn_tools: List[dict]):
        t0 = time.time()
        input_items: List[Any] = [{"role": "user", "content": self.prompt}]
        previous_id = None
        final_text = ""
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0)) as client:
            while self.turns < self.max_turns:
                if self.pending_messages:
                    for m in self.pending_messages:
                        input_items.append({"role": "user", "content": m})
                    self.pending_messages = []
                body = {"model": self.model, "input": input_items, "tools": fn_tools, "tool_choice": "auto",
                        "instructions": self.instructions(), "parallel_tool_calls": False, "store": True}
                if previous_id:
                    body["previous_response_id"] = previous_id
                data = await self._call_model(client, body)
                self.turns += 1
                previous_id = data.get("id")
                u = data.get("usage") or {}
                self.usage["input_tokens"] += int(u.get("input_tokens", 0) or 0)
                self.usage["output_tokens"] += int(u.get("output_tokens", 0) or 0)
                calls = []
                final_text = ""
                for item in data.get("output", []):
                    if item.get("type") == "message":
                        text = "".join(c.get("text", "") for c in item.get("content", []) if c.get("type") == "output_text")
                        if text.strip():
                            final_text = text
                            self.emit({"type": "assistant_text", "text": text})
                    elif item.get("type") == "function_call":
                        calls.append(item)
                if not calls:
                    break
                input_items = []
                for c in calls:
                    name, call_id = c["name"], c["call_id"]
                    try:
                        args = json.loads(c.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    self.tool_names[call_id] = name
                    self.emit({"type": "tool_call", "tool_use_id": call_id, "name": name, "input": args})
                    ok, note = await self.ask_approval(name, args)
                    if not ok:
                        text = note or "Denied by the operator. Explain what you would do instead and wait."
                        self.emit({"type": "tool_result", "tool_use_id": call_id, "name": name, "text": text, "images": [], "is_error": True})
                        input_items.append({"type": "function_call_output", "call_id": call_id, "output": text})
                        continue
                    text, images, is_error = await self._call_tool(name, args)
                    cleaned, more = split_images(text)
                    self.emit({"type": "tool_result", "tool_use_id": call_id, "name": name, "text": cleaned,
                               "images": images + more, "is_error": is_error})
                    input_items.append({"type": "function_call_output", "call_id": call_id, "output": cleaned[:60000]})
                    if images:   # the Responses API takes images only in a user item
                        input_items.append({"role": "user", "content": [{"type": "input_text", "text": f"Image returned by {name}:"}] +
                                            [{"type": "input_image", "image_url": src} for src in images[:4]]})
            else:
                self.emit({"type": "error", "message": f"turn cap of {self.max_turns} reached"})
        self.finish({"subtype": "success" if self.turns < self.max_turns else "error_max_turns", "num_turns": self.turns,
                     "duration_ms": int((time.time() - t0) * 1000), "total_cost_usd": None, "result": final_text,
                     "is_error": False, "usage": dict(self.usage)})

    async def _call_tool(self, name: str, args: dict):
        try:
            res = await self.session.call_tool(name, args)
        except Exception as e:  # noqa: BLE001
            return f"tool error: {type(e).__name__}: {e}", [], True
        texts, images = [], []
        for c in res.content or []:
            ctype = getattr(c, "type", None)
            if ctype == "text":
                texts.append(c.text)
            elif ctype == "image":
                images.append(f"data:{getattr(c, 'mimeType', 'image/png')};base64,{c.data}")
        return "\n".join(texts), images, bool(getattr(res, "isError", False))

    async def inject(self, text: str):
        if self.state in ("finished", "error", "stopped"):
            return False
        self.emit({"type": "operator_message", "text": text})
        self.pending_messages.append(text)
        return True

    async def stop(self):
        self.deny_pending("Run stopped by the operator.")
        if self.task and not self.task.done():
            self.task.cancel()
        if self.state not in ("finished", "error"):
            self.set_state("stopped", "operator")
