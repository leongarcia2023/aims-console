#!/usr/bin/env python3
"""run_claude_mock.py: the console's Claude driver end to end, with a scripted stand-in for the model and no key.

    .venv/bin/python test/run_claude_mock.py [--repo ../aims] [--only basic,message]

Starts test/mock_anthropic.py (a stand-in for the Anthropic Messages API) and the console in lab mode with per-run
simulators, pointed at it through ANTHROPIC_BASE_URL. Each scenario starts a run through the console's HTTP API, as the
page does, and checks the run's record, its end state and what the agent was offered:

  basic    a measurement-loop run that reads the instrument twice: the agent is offered the team's 37 tools and no
           skill, no browser tool
  skills   objective-only navigation with the skills: the agent is offered exactly the console's two skills
  message  an operator message sent during a run reaches the agent with the next tool result
  cap      a run that reaches its turn cap ends as stopped, "turn cap of N reached"
  error    the model API failing ends the run as an error that says so
  stop     Stop during a response ends the run as stopped by the operator

After every scenario: no agent process is left running and the run's folder holds no copy of the CLI sign-in.
Nothing leaves the machine. Exit 0 when every check passes.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONSOLE = HERE.parent


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http(method, url, body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")


class Harness:
    def __init__(self, repo: Path, work: Path):
        self.repo, self.work = repo, work
        self.mport, self.cport = free_port(), free_port()
        self.mock = None
        self.base = f"http://127.0.0.1:{self.cport}"
        self.log = work / "mock.jsonl"
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "TERM", "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS")}
        # a HOME with a stand-in CLI sign-in, which each run's private HOME copies and must not keep (runner.drop_credentials)
        home = work / "home"
        (home / ".claude").mkdir(parents=True, exist_ok=True)
        (home / ".claude" / ".credentials.json").write_text("{}")
        env["HOME"] = str(home)
        env.update(ANTHROPIC_BASE_URL=f"http://127.0.0.1:{self.mport}", ANTHROPIC_API_KEY="mock-test-key", NO_PROXY="127.0.0.1,localhost")
        self.console = subprocess.Popen([sys.executable, "-m", "backend.app", "--repo", str(repo), "--runs", str(work / "runs"),
                                         "--sim-per-run", "--host", "127.0.0.1", "--port", str(self.cport)],
                                        cwd=CONSOLE, env=env, stdout=open(work / "console.log", "w"), stderr=subprocess.STDOUT)
        for _ in range(60):
            try:
                self.cfg = http("GET", self.base + "/api/config")
                break
            except Exception:
                time.sleep(1)
        else:
            raise SystemExit("the console did not start (see console.log)")

    def script(self, name):
        if self.mock:
            self.mock.terminate()
            self.mock.wait()
        self.log.unlink(missing_ok=True)
        self.mock = subprocess.Popen([sys.executable, str(HERE / "mock_anthropic.py"), "--port", str(self.mport), "--script", name,
                                      "--log", str(self.log)], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        time.sleep(2)

    def start(self, **body):
        r = http("POST", self.base + "/api/runs", body)
        assert "id" in r, r
        return r["id"]

    def wait(self, rid, limit=240):
        t0 = time.time()
        while time.time() - t0 < limit:
            st = http("GET", f"{self.base}/api/runs/{rid}")["state"]
            if st in ("finished", "error", "stopped"):
                time.sleep(2)                       # the driver's cleanup after the last event
                return st
            time.sleep(1.5)
        return "timeout"

    def events(self, rid):
        return [json.loads(l) for l in (self.work / "runs" / rid / "events.jsonl").read_text().splitlines()]

    def offered(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def close(self):
        for p in (self.mock, self.console):
            if p:
                p.terminate()


def agents_running():
    out = subprocess.run(["ps", "-eo", "cmd"], capture_output=True, text=True).stdout
    return sum(1 for l in out.splitlines() if "_bundled/claude" in l and "stream-json" in l)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=str(CONSOLE.parent / "aims"), help="the AIMS code (default: ../aims, as install.sh lays it out)")
    ap.add_argument("--only", default="", help="comma-separated scenarios")
    ap.add_argument("--work", default=None)
    a = ap.parse_args()
    work = Path(a.work or f"/tmp/aims_claude_mock_{os.getpid()}")
    work.mkdir(parents=True, exist_ok=True)
    h = Harness(Path(a.repo).resolve(), work)
    fails = []

    def check(name, ok, detail=""):
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail and not ok else ""))
        if not ok:
            fails.append(name)

    def after(rid):
        check("no agent process left", agents_running() == 0, f"{agents_running()} running")
        check("no sign-in copy in the run folder", not (work / "runs" / rid / "home" / ".claude" / ".credentials.json").exists())

    want = set(filter(None, a.only.split(","))) or {"basic", "skills", "message", "cap", "error", "stop"}
    P = h.cfg["prompts"]
    try:
        if "basic" in want:
            print("basic")
            h.script("basic")
            rid = h.start(loop="measurement", prompt=P["measurement"], model="claude-opus-4-5", max_turns=30)
            st = h.wait(rid)
            ev = h.events(rid)
            calls = [e["name"] for e in ev if e["type"] == "tool_call"]
            check("finished", st == "finished", st)
            check("the two reads were made", calls == ["get_scan_frame", "check_controller_status"], calls)
            check("every result ok", all(not e["is_error"] for e in ev if e["type"] == "tool_result"))
            sysev = next(e for e in ev if e["type"] == "system")
            check("only the aims server", [x["name"] for x in sysev["mcp_servers"]] == ["aims"], sysev["mcp_servers"])
            first = h.offered()[0]
            check("offered the team's 37 tools and nothing else", len(first["tools"]) == 37 and all(t.startswith("mcp__aims__") for t in first["tools"]),
                  [t for t in first["tools"] if not t.startswith("mcp__aims__")])
            after(rid)
        if "skills" in want:
            print("skills")
            h.script("skills")
            rid = h.start(loop="navigation", prompt=h.cfg["prompts_objective"]["navigation"], model="claude-opus-4-5", max_turns=10,
                          use_skills=True, seed=9)
            st = h.wait(rid)
            ev = h.events(rid)
            check("finished", st == "finished", st)
            body = json.loads((work / "mock.jsonl.first.json").read_text())
            listed = []
            for m in body["messages"]:
                for c in (m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": m["content"]}]):
                    t = c.get("text", "")
                    if "skills are available" in t:
                        listed = [l[2:].split(":")[0] for l in t.splitlines() if l.startswith("- ")]
            check("exactly the console's two skills offered", sorted(listed) == ["aims-controller-recovery", "aims-gp-recovery"], listed)
            tools = h.offered()[0]["tools"]
            check("the team's 37 tools and Skill, nothing else", len([t for t in tools if t.startswith("mcp__aims__")]) == 37
                  and [t for t in tools if not t.startswith("mcp__aims__")] == ["Skill"], [t for t in tools if not t.startswith("mcp__aims__")])
            check("the skill was read", any(e["type"] == "tool_result" and e["name"] == "Skill" and not e["is_error"] for e in ev))
            after(rid)
        if "message" in want:
            print("message")
            h.script("slow")
            rid = h.start(loop="measurement", prompt=P["measurement"], model="claude-opus-4-5", max_turns=20)
            time.sleep(6)
            r = http("POST", f"{h.base}/api/runs/{rid}/message", {"text": "Operator: also report the scan frame's angle."})
            check("message accepted", r.get("ok") is True, r)
            st = h.wait(rid)
            check("finished", st == "finished", st)
            steps = [e["step"] for e in h.offered() if any("also report the scan frame's angle" in t for t in e["last_texts"])]
            check("the agent received the message", bool(steps))
            check("during the run, with the next tool result", bool(steps) and min(steps) <= 1, steps)
            after(rid)
        if "cap" in want:
            print("cap")
            h.script("long")
            rid = h.start(loop="measurement", prompt=P["measurement"], model="claude-opus-4-5", max_turns=3)
            st = h.wait(rid)
            last = [e for e in h.events(rid) if e["type"] == "status"][-1]
            check("stopped at the cap", st == "stopped" and "turn cap of 3" in last["detail"], (st, last["detail"]))
            after(rid)
        if "error" in want:
            print("error")
            h.script("error")
            rid = h.start(loop="measurement", prompt=P["measurement"], model="claude-opus-4-5", max_turns=5)
            st = h.wait(rid, limit=420)
            last = [e for e in h.events(rid) if e["type"] == "status"][-1]
            check("ended as an error that says why", st == "error" and last["detail"] not in ("", "success"), (st, last["detail"]))
            after(rid)
        if "stop" in want:
            print("stop")
            h.script("slow")
            rid = h.start(loop="measurement", prompt=P["measurement"], model="claude-opus-4-5", max_turns=20)
            time.sleep(6)
            http("POST", f"{h.base}/api/runs/{rid}/stop")
            st = h.wait(rid)
            last = [e for e in h.events(rid) if e["type"] == "status"][-1]
            check("stopped by the operator", st == "stopped" and last["detail"] == "operator", (st, last["detail"]))
            check("one stop recorded", sum(1 for e in h.events(rid) if e["type"] == "status" and e["state"] == "stopped") == 1)
            after(rid)
    finally:
        h.close()
    print("PASS" if not fails else f"FAIL: {', '.join(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
