"""simpool.py: one simulated MCP server per run.

In the public console every run gets its own instance of the team's MCP server on the
simulator (start_server_sim.py), on a free local port, with its sessions directory inside the
run's log folder, so runs never share instrument state and the simulator's ground truth
(truth.jsonl) is archived with the run. The instance is stopped when the run ends.
"""
from __future__ import annotations

import asyncio
import os
import random
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SimServer:
    def __init__(self, *, sim_script: Path, mcp_dir: Path, sessions_dir: Path, log_path: Path,
                 python: Optional[str] = None, seed: Optional[int] = None, p_defect: float = 0.3,
                 extra_args: Optional[List[str]] = None, bind: str = "127.0.0.1"):
        self.sim_script, self.mcp_dir, self.sessions_dir, self.log_path = sim_script, mcp_dir, sessions_dir, log_path
        self.python = python or sys.executable
        self.seed = random.randrange(1_000_000) if seed is None else seed
        self.p_defect = p_defect
        self.extra_args = extra_args or []
        self.bind = bind
        self.port = free_port()
        self.proc: Optional[subprocess.Popen] = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/sse"

    async def start(self, timeout: float = 120.0):
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        cmd = [self.python, str(self.sim_script), "--mcp-dir", str(self.mcp_dir), "--port", str(self.port),
               "--sessions", str(self.sessions_dir), "--seed", str(self.seed), "--p-defect", str(self.p_defect),
               "--bind", self.bind] + self.extra_args
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "TERM", "MPLBACKEND", "OMP_NUM_THREADS")}
        env.setdefault("MPLBACKEND", "Agg")
        self.proc = subprocess.Popen(cmd, stdout=open(self.log_path, "ab"), stderr=subprocess.STDOUT, env=env,
                                     start_new_session=True)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(f"simulator server exited early (code {self.proc.returncode}); see {self.log_path}")
            if await self.ready():
                return self
            await asyncio.sleep(1.0)
        self.stop()
        raise RuntimeError(f"simulator server did not come up within {timeout:.0f} s; see {self.log_path}")

    async def ready(self) -> bool:
        from mcp import ClientSession
        from mcp.client.sse import sse_client
        try:
            async with asyncio.timeout(5):
                async with sse_client(self.url) as (r, w):
                    async with ClientSession(r, w) as s:
                        await s.initialize()
                        return True
        except Exception:
            return False

    async def list_tools(self):
        from mcp import ClientSession
        from mcp.client.sse import sse_client
        async with sse_client(self.url) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return (await s.list_tools()).tools

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), 15)
            except Exception:
                try:
                    self.proc.terminate()
                except Exception:
                    pass
        self.proc = None
