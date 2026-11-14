"""session_paths.py: a relative folder or file in an analysis tool's arguments resolves against the session folder.

The measurement prompt names its series "folder\\scan", "folder\\grid" and "folder\\bestPoint", and the simulator
writes a series with a folder part into that folder under its session folder (sim_hardware.SimNanonis._next_path,
2026-11-13). The team's analysis tools (analyze_sxm_dynamics, analyze_twist_angle_disorder,
evaluate_fractional_gwc_points and the others that take a folder) resolve a relative path against the server's working
directory, mcpServer/, where no such folder is. On the microscope the operator writes an absolute path into the prompt.

wrap_path_args(mcp, session_dir) wraps every tool that takes one of PATH_ARGS: a relative value that names nothing
under the working directory but names a file or folder under the session folder is handed to the tool as that
session-folder path. So is an absolute path inside the agent's own working directory (agent_cwd, given by the console)
that names nothing there but does under the session folder: an agent that reads "measurement" as a folder next to
itself writes that path. Anything else (other absolute paths, paths that exist where they point, values naming
nothing) goes through unchanged, so a run that writes absolute paths of its own sees no difference. A relative folder
name then works for the writer and the reader alike, and the runs of a console, each with its own simulator, never
share a folder. (Added 2026-11-14.)
"""
import functools
import inspect
import os
from pathlib import Path
from typing import Callable, Optional

PATH_ARGS = {"folder_path", "dat_folder_path", "folder1", "folder2", "sxm_path", "sxm_file_path", "image_path"}


def resolve(value, session_dir: Optional[Path], agent_cwd: Optional[Path] = None):
    if session_dir is None or not isinstance(value, str) or not value.strip():
        return value
    p = value.strip().strip('"').replace("\\", "/")
    if os.path.exists(p):
        return value
    if os.path.isabs(p):
        if agent_cwd is None:
            return value
        try:
            p = str(Path(p).relative_to(agent_cwd))
        except ValueError:
            return value
    cand = Path(session_dir) / p
    return str(cand) if cand.exists() else value


def _wrapped(fn, keys, session_dir: Callable[[], Optional[Path]], is_async: bool, agent_cwd: Optional[Path]):
    def fix(kwargs):
        sd = session_dir()
        return {k: (resolve(v, sd, agent_cwd) if k in keys else v) for k, v in kwargs.items()}

    if is_async:
        @functools.wraps(fn)
        async def runner(**kwargs):
            return await fn(**fix(kwargs))
    else:
        @functools.wraps(fn)
        def runner(**kwargs):
            return fn(**fix(kwargs))
    return runner


def wrap_path_args(mcp, session_dir: Callable[[], Optional[Path]], agent_cwd: Optional[str] = None) -> int:
    """Wrap, in place, the tools of a FastMCP server that take a path in PATH_ARGS. Returns how many were wrapped.
    Call it before make_tools_threaded, which wraps whatever function a tool then has."""
    cwd = Path(agent_cwd).resolve() if agent_cwd else None
    n = 0
    for tool in getattr(mcp, "_tool_manager")._tools.values():
        keys = set(inspect.signature(tool.fn).parameters) & PATH_ARGS
        if keys:
            tool.fn = _wrapped(tool.fn, keys, session_dir, bool(tool.is_async), cwd)
            n += 1
    return n
