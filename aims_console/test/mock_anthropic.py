#!/usr/bin/env python3
"""mock_anthropic.py: a scripted stand-in for the Anthropic Messages API, for testing the console's Claude driver
(the Claude Agent SDK and its CLI) end to end with no model and no key.

    python test/mock_anthropic.py --port 9100 --script basic --log /tmp/mock.jsonl
    # then start the console with ANTHROPIC_BASE_URL=http://127.0.0.1:9100 ANTHROPIC_API_KEY=mock

The agent's conversation (a request that offers tools) is answered from a script: the n-th reply of a conversation is
step n, n being the number of assistant turns already in the request. A step asks for one tool (by its short name; the
full mcp__server__name is taken from the request's tool list), says a text, waits first, or fails with an HTTP status.
After the last step the agent says "Done." and ends its turn. Requests without tools (titles, summaries) get a short
text. Every request is logged (model, number of messages, the tools offered, the skills the Skill tool lists, the
system prompt's head), so a test can check what the agent was given.
"""
import argparse
import asyncio
import json
import re
import uuid

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

SCRIPTS = {
    # two reads of the instrument's state
    "basic": [("tool", "get_scan_frame", {}), ("tool", "check_controller_status", {}),
              ("text", "Read the scan frame and the controller.")],
    # a slow first step, for operator messages and Stop while a turn is under way
    "slow": [("wait", 12), ("tool", "get_scan_frame", {}), ("wait", 12), ("tool", "get_scan_frame", {}),
             ("text", "Read the scan frame twice.")],
    # more steps than a small turn cap allows
    "long": [("tool", "get_scan_frame", {})] * 8,
    # the navigation skills: read one skill, then load the template
    "skills": [("tool", "Skill", {"skill": "aims-gp-recovery"}), ("tool", "get_scan_frame", {}),
               ("text", "Read the skill and the scan frame.")],
    # the model API fails on the first request
    "error": [("fail", 529)],
}


def tools_of(body):
    return [t.get("name", "") for t in body.get("tools") or []]


def skills_listed(body):
    """The skills named in the Skill tool's description, as the agent sees them."""
    for t in body.get("tools") or []:
        if t.get("name") == "Skill":
            return sorted(set(re.findall(r"^- ([\w:.-]+)", t.get("description", ""), re.M)))
    return None


def full_name(body, short):
    for n in tools_of(body):
        if n == short or n.endswith("__" + short):
            return n
    return short


class Mock:
    def __init__(self, script, log):
        self.log = log
        self.steps, pending = [], 0         # (seconds to wait first, step); a "wait" belongs to the step after it
        for s in SCRIPTS[script]:
            if s[0] == "wait":
                pending += s[1]
                continue
            self.steps.append((pending, s))
            pending = 0

    def record(self, body, step):
        sys_text = body.get("system")
        if isinstance(sys_text, list):
            sys_text = " ".join(b.get("text", "") for b in sys_text if isinstance(b, dict))
        msgs = body.get("messages") or []
        last = msgs[-1] if msgs else {}
        content = last.get("content")
        kinds = [c.get("type") for c in content] if isinstance(content, list) else ["text"]
        texts = []
        for c in content if isinstance(content, list) else [{"type": "text", "text": str(content)}]:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text":
                texts.append(c.get("text", "")[:400])
            elif c.get("type") == "tool_result":       # context attached to a tool result travels inside it
                inner = c.get("content")
                for x in inner if isinstance(inner, list) else [{"type": "text", "text": str(inner or "")}]:
                    if isinstance(x, dict) and x.get("type") == "text":
                        texts.append(x.get("text", "")[:400])
        entry = {"model": body.get("model"), "stream": bool(body.get("stream")), "n_messages": len(msgs), "step": step,
                 "tools": tools_of(body), "skills": skills_listed(body), "system_head": (sys_text or "")[:300],
                 "last_kinds": kinds, "last_texts": texts}
        with open(self.log, "a") as f:
            f.write(json.dumps(entry) + "\n")
        if step == 0:                          # the whole first request, for a closer look at what the agent is given
            with open(self.log + ".first.json", "w") as f:
                json.dump(body, f)

    def blocks_for(self, body):
        """(blocks, stop_reason, fail_status, wait_s) for this request."""
        if not body.get("tools"):
            return [{"type": "text", "text": "ok"}], "end_turn", None, 0
        step = sum(1 for m in body.get("messages") or [] if m.get("role") == "assistant")
        self.record(body, step)
        if step >= len(self.steps):
            return [{"type": "text", "text": "Done."}], "end_turn", None, 0
        wait, s = self.steps[step]
        if s[0] == "fail":
            return None, None, s[1], wait
        if s[0] == "text":
            return [{"type": "text", "text": s[1]}], "end_turn", None, wait
        _, short, inp = s
        return ([{"type": "text", "text": f"Calling {short}."},
                 {"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:20], "name": full_name(body, short), "input": inp}],
                "tool_use", None, wait)

    async def messages(self, request: Request):
        body = await request.json()
        blocks, stop, fail, wait = self.blocks_for(body)
        if wait:
            await asyncio.sleep(wait)
        if fail:
            return JSONResponse({"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded (mock)"}},
                                status_code=fail)
        msg_id = "msg_" + uuid.uuid4().hex[:20]
        usage = {"input_tokens": 100, "output_tokens": 20, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        if not body.get("stream"):
            return JSONResponse({"id": msg_id, "type": "message", "role": "assistant", "model": body.get("model"),
                                 "content": blocks, "stop_reason": stop, "stop_sequence": None, "usage": usage})

        def ev(d):
            return f"event: {d['type']}\ndata: {json.dumps(d)}\n\n"

        async def gen():
            yield ev({"type": "message_start", "message": {"id": msg_id, "type": "message", "role": "assistant",
                      "model": body.get("model"), "content": [], "stop_reason": None, "stop_sequence": None,
                      "usage": dict(usage, output_tokens=1)}})
            for i, b in enumerate(blocks):
                if b["type"] == "text":
                    yield ev({"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}})
                    yield ev({"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": b["text"]}})
                else:
                    yield ev({"type": "content_block_start", "index": i,
                              "content_block": {"type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}})
                    yield ev({"type": "content_block_delta", "index": i,
                              "delta": {"type": "input_json_delta", "partial_json": json.dumps(b["input"])}})
                yield ev({"type": "content_block_stop", "index": i})
            yield ev({"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                      "usage": {"output_tokens": 20}})
            yield ev({"type": "message_stop"})

        return StreamingResponse(gen(), media_type="text/event-stream")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--script", choices=sorted(SCRIPTS), default="basic")
    ap.add_argument("--log", default="/tmp/mock_anthropic.jsonl")
    a = ap.parse_args()
    m = Mock(a.script, a.log)

    async def count_tokens(request: Request):
        return JSONResponse({"input_tokens": 100})

    async def other(request: Request):
        return JSONResponse({"type": "error", "error": {"type": "not_found_error", "message": "mock"}}, status_code=404)

    app = Starlette(routes=[Route("/v1/messages", m.messages, methods=["POST"]),
                            Route("/v1/messages/count_tokens", count_tokens, methods=["POST"]),
                            Route("/{path:path}", other, methods=["GET", "POST"])])
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
