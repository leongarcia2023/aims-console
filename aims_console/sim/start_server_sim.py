#!/usr/bin/env python3
"""start_server_sim.py: run the team's MCP server (mcp_server_sse.py) on a simulated instrument.

    python start_server_sim.py --mcp-dir /path/to/AIMS/mcpServer [--port 8000]
        [--template /path/to/templates/ArrowMarker_10x.jpg] [--examples /path/to/examples]
        [--seed 0] [--p-defect 0.30] [--init-r 500 | --start-xy X,Y] [--scan-seconds 0.5]

Nothing in the team's code is modified. The server does
``from hardware_connections import HardwareManager`` inside each tool and
``centering_controller`` imports the same name at module level; both are pointed at
``sim_hardware.SimHardwareManager`` before ``mcp_server_sse`` is imported, so every tool
runs against the virtual stage, the synthetic navigation scans and the replayed
measurement data. The SSE endpoint is the same (http://localhost:PORT/sse), so LibreChat,
the console, or any MCP client connects exactly as it would to the real instrument.
"""
import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mcp-dir", required=True, help="the repository's mcpServer directory")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--template", default=None, help="default: <mcp-dir>/../templates/ArrowMarker_10x.jpg")
    ap.add_argument("--examples", default=None, help="default: <mcp-dir>/../examples")
    ap.add_argument("--sessions", default=None, help="default: <mcp-dir>/sessions/sim")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--p-defect", type=float, default=0.30)
    ap.add_argument("--init-r", type=float, default=500.0, help="initial tip distance from the target, um")
    ap.add_argument("--start-xy", default=None, help="initial true position 'x,y' in um (overrides --init-r)")
    ap.add_argument("--scan-seconds", type=float, default=0.5, help="pause per simulated scan")
    ap.add_argument("--bind", default="0.0.0.0", help="interface to listen on (127.0.0.1 for per-run servers behind the console)")
    a = ap.parse_args()

    mcp_dir = Path(a.mcp_dir).resolve()
    if not (mcp_dir / "mcp_server_sse.py").exists():
        sys.exit(f"{mcp_dir} does not contain mcp_server_sse.py")
    os.environ["AIMS_SIM_TEMPLATE"] = str(Path(a.template or mcp_dir.parent / "templates" / "ArrowMarker_10x.jpg").resolve())
    os.environ["AIMS_SIM_EXAMPLES"] = str(Path(a.examples or mcp_dir.parent / "examples").resolve())
    os.environ["AIMS_SIM_SESSIONS"] = str(Path(a.sessions or mcp_dir / "sessions" / "sim").resolve())
    os.environ["AIMS_SIM_SEED"] = str(a.seed)
    os.environ["AIMS_SIM_P_DEFECT"] = str(a.p_defect)
    os.environ["AIMS_SIM_INIT_R_UM"] = str(a.init_r)
    if a.start_xy:
        os.environ["AIMS_SIM_START_XY"] = a.start_xy
    os.environ["AIMS_SIM_SCAN_SECONDS"] = str(a.scan_seconds)
    os.environ["AIMS_SIM"] = "1"

    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here))
    sys.path.insert(0, str(mcp_dir))
    os.chdir(mcp_dir)                       # the server resolves config/ and sessions/ relative to its directory
    # the loop prompts name templates\... and examples\... ; make both reachable from here
    for name, target in (("templates", Path(os.environ["AIMS_SIM_TEMPLATE"]).parent),
                         ("examples", Path(os.environ["AIMS_SIM_EXAMPLES"]))):
        link = mcp_dir / name
        if not link.exists():
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError:
                pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    import sim_hardware
    import hardware_connections
    hardware_connections.HardwareManager = sim_hardware.SimHardwareManager
    import centering_controller
    centering_controller.HardwareManager = sim_hardware.SimHardwareManager
    import mcp_server_sse
    mcp_server_sse.HardwareManager = sim_hardware.SimHardwareManager  # in case a tool binds the name at import

    import uvicorn
    from mcp_server_sse import mcp, create_starlette_app
    n_tools = len(getattr(mcp, "_tool_manager")._tools)
    print(f"AIMS MCP server on a SIMULATED instrument: {n_tools} tools, SSE at http://localhost:{a.port}/sse")
    print(f"  template {os.environ['AIMS_SIM_TEMPLATE']}\n  examples {os.environ['AIMS_SIM_EXAMPLES']}\n"
          f"  sessions {os.environ['AIMS_SIM_SESSIONS']}\n  seed {a.seed}, p_defect {a.p_defect}, "
          f"start {'xy=' + a.start_xy if a.start_xy else f'r={a.init_r} um'}")
    app = create_starlette_app(mcp._mcp_server, debug=False)
    config = uvicorn.Config(app, host=a.bind, port=a.port, log_level="info")
    asyncio.run(uvicorn.Server(config).serve())


if __name__ == "__main__":
    main()
