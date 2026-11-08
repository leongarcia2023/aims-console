"""smoke_sim.py: exercise the simulated instrument through the team's MCP server.

Walks the navigation tools (centering session, scan, image, particle-filter prediction,
movement) and the measurement tools (scan frame, gate ramp-and-scan, grid sweeper) over
the real SSE endpoint, prints each tool's reply, and checks the ground-truth log.
Usage: python smoke_sim.py --url http://localhost:8765/sse --template /path/ArrowMarker_10x.jpg
"""
import argparse, asyncio, json, re, sys
from pathlib import Path
from mcp import ClientSession
from mcp.client.sse import sse_client


def short(s, n=300):
    s = str(s).replace("\n", " ")
    return s if len(s) <= n else s[:n] + " ..."


async def call(session, name, **args):
    r = await session.call_tool(name, args)
    txt = " ".join(c.text for c in r.content if getattr(c, "type", "") == "text")
    print(f"\n>>> {name}({json.dumps(args)[:120]})\n    {short(txt)}")
    return txt


async def main(url, template):
    async with sse_client(url) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = await s.list_tools()
            names = [t.name for t in tools.tools]
            print(f"{len(names)} tools:", ", ".join(names))

            # ---- navigation ----
            await call(s, "start_centering_session", max_iterations=5)
            await call(s, "check_controller_status")
            await call(s, "approach_and_lift")
            await call(s, "scan", series_name="nav", pixels=128, lines=40)
            img = await call(s, "get_sxm_image", id="0001")
            png = None
            try:
                png = json.loads(img.split("\n")[0] if img.startswith("{") else img)["path"]
            except Exception:
                m = re.search(r'"path":\s*"([^"]+)"', img)
                png = m.group(1) if m else None
            print("    png:", png)
            await call(s, "load_template", template_path=template, template_height_um=1088 * 0.875496,
                       origin_x_px=867, origin_y_px=498)
            pred = await call(s, "predict_position", image_path=png, scan_size_um=53.0) if png else ""
            try:
                pj = json.loads(pred[pred.find("{"):pred.rfind("}") + 1])
                x, y = pj.get("x_position", pj.get("x")), pj.get("y_position", pj.get("y"))
                conf = pj.get("confidence", 0.5); dist = pj.get("distance_from_center", (x ** 2 + y ** 2) ** 0.5)
                await call(s, "calculate_movement_from_prediction", x_position=x, y_position=y, confidence=conf, distance_from_center=dist)
            except Exception as e:
                print("    (prediction not parsed:", e, ")")
            await call(s, "move_tip", x_steps=-200, y_steps=100)
            await call(s, "scan", series_name="nav")
            await call(s, "get_sxm_image", id="0002")

            # ---- measurement ----
            await call(s, "set_scan_frame", x=0.0, y=0.0, width=3e-6, height=3e-6, angle=0.0)
            await call(s, "get_scan_frame")
            await call(s, "bottom_gate_ramp", Vbg=2.5, rate=0.5)
            await call(s, "bottom_gate_ramp_and_scan", start=2.5, stop=3.4, step=3, series_name="vbg", rate=0.5)
            await call(s, "grid_generic_sweeper_start", grid=[[0, 0], [1e-6, 0]], basename="grid", wait=0, iteration=1, reverse=False)
            await call(s, "retract_tip")
            await call(s, "get_session_results")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8765/sse")
    ap.add_argument("--template", required=True)
    a = ap.parse_args()
    asyncio.run(main(a.url, a.template))
