"""threaded_tools.py: keep the MCP server's event loop free while a tool computes.

FastMCP calls a synchronous tool function directly on the event loop. While the function runs,
the server can send nothing, not even the SSE keep-alive comment that goes out every 15 seconds.
A client that sees a silent stream for long enough drops it and reconnects, and the tool's answer,
when it finally comes, is written to a session that no longer exists: the call never returns.
Seen on 2026-11-12 with run_gp_regression (10 minutes on a 2-core machine): the PNG was written,
the client had reconnected, and the agent waited for an answer until its own tool timeout.

make_tools_threaded(mcp) wraps every synchronous tool so that it runs in a worker thread. The tool
functions themselves are untouched and still run one at a time (a lock), so the instrument sees
the same sequence of calls as before. Only the transport changes: the loop stays free, the
keep-alive keeps flowing, and a long call returns.

Memory. One GP regression of the team's tool peaks at about 5.4 GB of resident memory (measured
2026-11-12: 128 x 40 scan, factor 4, 406 s on two cores). A console that serves several runs
starts one simulator per run, and two regressions at once need twice that (seen the same day: the
kernel killed a simulator). With heavy_lock set to a file that the
simulators of one console share, the tools named in HEAVY take an exclusive lock on it, so only
one of them computes at a time across all the runs and the others wait their turn with the
connection alive. The kernel releases the lock if the holder is killed.
"""
import functools
import threading

import anyio

try:
    import fcntl
except ImportError:                      # not on this platform: no cross-process lock
    fcntl = None

HEAVY = {"run_gp_regression"}
_ONE_AT_A_TIME = threading.Lock()


def _threaded(fn, lock_path=None):
    @functools.wraps(fn)
    async def runner(**kwargs):
        def call():
            with _ONE_AT_A_TIME:
                lf = None
                if lock_path and fcntl is not None:
                    try:
                        lf = open(lock_path, "a")
                    except OSError:              # the lock file cannot be opened: run without it
                        lf = None
                if lf is None:
                    return fn(**kwargs)
                with lf:
                    fcntl.flock(lf, fcntl.LOCK_EX)
                    try:
                        return fn(**kwargs)
                    finally:
                        fcntl.flock(lf, fcntl.LOCK_UN)
        return await anyio.to_thread.run_sync(call)
    return runner


def make_tools_threaded(mcp, heavy_lock=None) -> int:
    """Wrap the synchronous tools of a FastMCP server in place. Returns how many were wrapped."""
    n = 0
    for name, tool in getattr(mcp, "_tool_manager")._tools.items():
        if not tool.is_async:
            tool.fn = _threaded(tool.fn, heavy_lock if name in HEAVY else None)
            tool.is_async = True
            n += 1
    return n
