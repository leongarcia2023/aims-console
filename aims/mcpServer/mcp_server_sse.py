"""MIM MCP Server (``mim-centering``) — SSE transport built on FastMCP.

This is the entry point of the Microwave Impedance Microscopy (MIM) automation
stack. It exposes the full experiment workflow as Model Context Protocol (MCP)
tools that an LLM agent (e.g. LibreChat) can call over Server-Sent Events:

* Hardware control      — Nanonis SPM tip/scan/gate control and the Attocube XY
                          coarse stage (``hardware_connections``, ``Nanonis``).
* Image acquisition     — capture ``.sxm`` scans and convert them to PNG, with
                          stripe removal, Gaussian-process infill and contrast
                          enhancement (``gp_regression``, ``gp_enhanced``,
                          ``stripe_removal``, ``image_enhancement``).
* Stage centering       — particle-filter template localization that drives the
                          stage toward a target (``particle_filter``,
                          ``centering_controller``, ``session_manager``).
* Data analysis         — dip/correlation detection, twist-angle extraction and
                          SSIM comparison of gate sweeps (``utils``, ``plotter``).

Run with ``python start_server_sse.py`` (or ``python mcp_server_sse.py --port N``).
See README.md for the full tool catalog and setup instructions.

Author : Philip David Suh and Siyuan Qiu — Stanford University, Department of Physics
"""

from __future__ import annotations
import logging
from typing import Dict, Any, List, Optional, Union, Tuple, Text
from pathlib import Path
import yaml
import json
import base64

import plotter as _plt
from mcp.server.fastmcp import FastMCP, Context, Image
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, Mount
from mcp.server.sse import SseServerTransport
import uvicorn
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import utils as _utils
import os
import io
import re
import time
from stripe_removal import remove_stripes_row_poly

# Ensure high-resolution output globally
plt.rcParams["savefig.dpi"] = 300

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Create the MCP server
mcp = FastMCP("mim-centering")

# Global state
centering_controller = None
shared_hardware_manager = None
stage_config = None
cached_template_context: Optional[Any] = None
cached_template_path: Optional[str] = None
experiment_complete: bool = False

def _result(fig, save_path: Optional[str] = None, extra: Optional[Dict[str, Any]] = None):
    """Encode a Matplotlib figure as an MCP Image, optionally saving it to disk.

    If ``extra`` is provided, its contents are returned alongside the image as a
    JSON text block, with any NumPy arrays converted to lists for serialization.
    """
    res = _utils.save_or_encode(fig, save_path)
    img_obj = Image(data=res["image_bytes"], format="png")
    if extra:
        # Convert numpy arrays to lists for JSON serialization if needed
        serializable_extra = {}
        for k, v in extra.items():
            if isinstance(v, np.ndarray):
                serializable_extra[k] = v.tolist()
            else:
                serializable_extra[k] = v
        return [img_obj, Text(json.dumps(serializable_extra, indent=2))]
    return img_obj

# Load stage configuration
def load_stage_config() -> Dict:
    """Load the stage configuration from ``config/stage_config.yaml`` (``{}`` if absent)."""
    stage_config_path = Path("config/stage_config.yaml")
    if stage_config_path.exists():
        with open(stage_config_path, 'r') as f:
            return yaml.safe_load(f)
    return {}


# Initialize stage config
stage_config = load_stage_config()


# Helper function to get hardware manager
def get_hardware_manager():
    """Return the shared hardware manager, creating and connecting it on first use."""
    global shared_hardware_manager
    if shared_hardware_manager is None:
        try:
            from hardware_connections import HardwareManager
            shared_hardware_manager = HardwareManager()
            shared_hardware_manager.connect_all()
        except Exception as e:
            logger.error(f"Failed to create hardware manager: {e}")
            return None
    return shared_hardware_manager


# Helper function to format tool returns with experiment_complete
def format_tool_response(response: str, experiment_complete_value: bool = False) -> str:
    """Format tool response to include experiment_complete field. Pass True only from predict_position when distance < threshold; all other tools use default False."""
    # Try to parse as JSON (for image tools that return JSON)
    try:
        result = json.loads(response)
        if isinstance(result, dict):
            result["experiment_complete"] = experiment_complete_value
            return json.dumps(result)
    except (json.JSONDecodeError, TypeError):
        pass

    # If not JSON, wrap in JSON object
    result = {
        "message": response,
        "experiment_complete": experiment_complete_value
    }
    return json.dumps(result)


# Tool definitions using @mcp.tool() decorator

@mcp.tool()
def start_centering_session(
    max_iterations: int = 10,
    convergence_threshold: float = 10.0,
    confidence_threshold: float = 0.7,
    nanonis_address: str = "TCPIP0::localhost::6501::SOCKET",
    attocube_address: str = "COM6",
    ctx: Context = None
) -> str:
    """Start a new centering session with configurable parameters"""
    global centering_controller, shared_hardware_manager, stage_config
    
    try:
        from centering_controller import CenteringController
    except ImportError as e:
        return format_tool_response(f"Centering controller not available: {e}")

    try:
        centering_controller = CenteringController(stage_config=stage_config)
        hardware_initialized = centering_controller.initialize_hardware(
            nanonis_address=nanonis_address,
            attocube_address=attocube_address
        )
        shared_hardware_manager = centering_controller.hardware_manager if hardware_initialized else None
        session_id = centering_controller.start_session(
            max_iterations=max_iterations,
            convergence_threshold=convergence_threshold,
            confidence_threshold=confidence_threshold
        )
        logger.info(f"Centering session started (hardware_initialized={hardware_initialized}, session_id={session_id})")
        return format_tool_response(f"Session started: {session_id}, Hardware initialized: {hardware_initialized}")
    except Exception as e:
        centering_controller = None
        shared_hardware_manager = None
        return format_tool_response(f"Failed to start centering session: {e}")


@mcp.tool()
def get_centering_status(ctx: Context = None) -> str:
    """Get current status of the centering session"""
    global centering_controller
    if centering_controller is None:
        return format_tool_response("No active centering session")
    status = centering_controller.get_status()
    return format_tool_response(str(status))


@mcp.tool()
def execute_movement(x_steps: float, y_steps: float, ctx: Context = None) -> str:
    """Execute stage movement based on position prediction"""
    global centering_controller
    if centering_controller is None:
        return format_tool_response("No active centering session")
    result = centering_controller.execute_movement(x_steps, y_steps)
    return format_tool_response(str(result))


@mcp.tool()
def get_session_results(ctx: Context = None) -> str:
    """Get final results of the centering session"""
    global centering_controller
    if centering_controller is None:
        return format_tool_response("No active centering session")
    results = centering_controller.get_session_results()
    return format_tool_response(str(results))


@mcp.tool()
def run_gp_regression(
    sxm_path: str,
    output_path: str,
    factor: int = 4,
    output_width: int = 256,
    output_height: int = 256,
    channel: str = "MIM-Im_AC-X",
    p_lo: float = 2.0,
    p_hi: float = 99.0,
    clip_limit: float = 0.01,
    ctx: Context = None
) -> str:
    """Run GP regression on an SXM scan, then enhance contrast (robust percentile stretch + CLAHE) to sharpen faint markers and suppress bright dirt. Outputs a single processed PNG ready for predict_position. p_lo/p_hi set the robust-stretch percentiles; clip_limit sets CLAHE strength."""
    if not sxm_path or not output_path:
        return format_tool_response(json.dumps({"success": False, "error": "sxm_path and output_path are required"}))
    try:
        from gp_enhanced import run_gp_regression_enhanced as _run_gp_enhanced
        result = _run_gp_enhanced(
            sxm_path=sxm_path,
            output_path=output_path,
            factor=factor,
            output_width=output_width,
            output_height=output_height,
            channel=channel,
            p_lo=p_lo,
            p_hi=p_hi,
            clip_limit=clip_limit,
        )
        return format_tool_response(json.dumps(result))
    except Exception as e:
        return format_tool_response(json.dumps({"success": False, "error": str(e)}))

@mcp.tool()
def move_tip(
    x_steps: float,
    y_steps: float,
    temperature: str = "cold",
    x_voltage: Optional[float] = None,
    y_voltage: Optional[float] = None,
    x_frequency: float = 100,
    y_frequency: float = 100,
    ctx: Context = None
) -> str:
    """Move the tip using Attocube stage controller (direct hardware control)"""
    try:
        from hardware_connections import HardwareManager
    except ImportError as e:
        return format_tool_response(f"Hardware connections not available: {e}")

    # Set temperature-dependent voltage defaults
    if temperature == 'room':
        default_voltage = 30
    else:  # 'cold'
        default_voltage = 60
    
    x_volt = x_voltage if x_voltage is not None else default_voltage
    y_volt = y_voltage if y_voltage is not None else default_voltage

    try:
        hw = get_hardware_manager()
        if hw is None or hw.attocube is None:
            return format_tool_response("Attocube not connected")
        result = hw.move_tip(x_steps, y_steps, x_volt=x_volt, y_volt=y_volt, x_freq=x_frequency, y_freq=y_frequency)
        return format_tool_response(str(result))
    except Exception as e:
        return format_tool_response(f"Move tip failed: {e}")


@mcp.tool()
def approach_and_lift(
    temperature: str = "cold",
    tip_lift: float = 5e-7,
    high_limit: Optional[float] = None,
    low_limit: Optional[float] = None,
    ctx: Context = None
) -> str:
    """Approach and lift the tip using Nanonis controller"""
    try:
        from hardware_connections import HardwareManager
    except ImportError as e:
        return format_tool_response(f"Hardware connections not available: {e}")

    # Set temperature-dependent defaults
    if temperature == 'room':
        default_high_limit = 12e-6
        default_low_limit = -12e-6
    else:  # 'cold'
        default_high_limit = 3e-6
        default_low_limit = -3e-6
    
    high_lim = high_limit if high_limit is not None else default_high_limit
    low_lim = low_limit if low_limit is not None else default_low_limit

    if low_lim >= high_lim:
        return format_tool_response(f"low_limit ({low_lim}) must be less than high_limit ({high_lim})")

    try:
        hw = get_hardware_manager()
        if hw is None or hw.nanonis is None:
            return format_tool_response("Nanonis not connected")
        result = hw.approach_and_lift(tip_lift=tip_lift, high_limit=high_lim, low_limit=low_lim)
        return format_tool_response(str(result))
    except Exception as e:
        return format_tool_response(f"Approach and lift failed: {e}")


@mcp.tool()
def scan(
    series_name: str = "scan",
    direction: str = "up",
    scan_speed: float = 1.9e-6,
    pixels: int = 128,
    lines: int = 40,
    timeout_ms: int = -1,
    ctx: Context = None
) -> str:
    """Start a scan on Nanonis and wait for completion before returning"""
    try:
        from hardware_connections import HardwareManager
    except ImportError as e:
        return format_tool_response(f"Hardware connections not available: {e}")

    try:
        hw = get_hardware_manager()
        if hw is None or hw.nanonis is None:
            return format_tool_response("Nanonis not connected")
        
        nanonis = hw.nanonis
        nanonis.SpeedSet(fwd_speed=scan_speed, bwd_speed=scan_speed)
        nanonis.BufferSet(pixels=pixels, lines=lines)
        nanonis.PropsSet(series_name=series_name)
        nanonis.Action("start", direction)
        
        # Wait for scan to complete
        timeout_status, file_path_size, file_path = nanonis.WaitEndOfScan(timeout=timeout_ms)
        
        if timeout_status:
            return format_tool_response(f"Scan timed out after {timeout_ms}ms. Series: {series_name}, direction={direction}, pixels={pixels}, lines={lines}")
        
        # Check controller status to determine if scan was successful
        controller_status = nanonis.z_controller_OnOffGet()
        successful_scan = (controller_status == 1)
        status_msg = "successful" if successful_scan else "failed"
        
        return format_tool_response(f"Scan completed ({status_msg}): {series_name}, direction={direction}, pixels={pixels}, lines={lines}. File: {file_path}")
    except Exception as e:
        import traceback
        return format_tool_response(f"Scan failed: {e}\n{traceback.format_exc()}")


@mcp.tool()
def check_controller_status(ctx: Context = None) -> str:
    """Check the Z controller status (ON/OFF)"""
    try:
        from hardware_connections import HardwareManager
    except ImportError as e:
        return format_tool_response(f"Hardware connections not available: {e}")

    try:
        hw = get_hardware_manager()
        if hw is None or hw.nanonis is None:
            return format_tool_response("Nanonis not connected")
        
        nanonis = hw.nanonis
        controller_status = nanonis.z_controller_OnOffGet()
        status_text = "ON" if controller_status == 1 else "OFF"
        return format_tool_response(f"Controller status: {status_text}")
    except Exception as e:
        import traceback
        return format_tool_response(f"Check controller status failed: {e}\n{traceback.format_exc()}")


@mcp.tool()
def retract_tip(ctx: Context = None) -> str:
    """Retract the tip after scanning"""
    try:
        from hardware_connections import HardwareManager
    except ImportError as e:
        return format_tool_response(f"Hardware connections not available: {e}")

    try:
        hw = get_hardware_manager()
        if hw is None or hw.nanonis is None:
            return format_tool_response("Nanonis not connected")
        
        nanonis = hw.nanonis
        nanonis.z_withdraw()
        return format_tool_response("Tip retracted successfully.")
    except Exception as e:
        return format_tool_response(f"Retract tip failed: {e}")


@mcp.tool()
def bottom_gate_ramp(Vbg: float, rate: float = 0.01, ctx: Context = None) -> str:
    """Ramp bottom gate voltage to Vbg (V) at given rate (V/step). Uses Nanonis AO8 (sig index 31)."""
    import time
    import numpy as np
    try:
        hw = get_hardware_manager()
        if hw is None or hw.nanonis is None:
            return format_tool_response("Nanonis not connected")
        nanonis = hw.nanonis
        current_value = nanonis.sig_val_get_by_index(31)
        n_steps = int(max([2, np.ceil(np.abs(current_value - Vbg) / rate)]))
        for V in np.linspace(current_value, Vbg, n_steps, endpoint=True):
            nanonis.AO_volt_set_by_index(8, V)
            time.sleep(0.03)
        return format_tool_response(f"Bottom gate ramped to {Vbg} V")
    except Exception as e:
        return format_tool_response(f"Bottom gate ramp failed: {e}")


@mcp.tool()
def bottom_gate_ramp_and_scan(
    start: float,
    stop: float,
    step: int,
    series_name: str = "scan",
    direction: str = "up",
    scan_speed: float = 1.9e-6,
    pixels: int = 128,
    lines: int = 40,
    timeout_ms: int = -1,
    rate: float = 0.01,
    ctx: Context = None,
) -> str:
    """Ramp bottom gate from start to stop in step points; at each Vbg run a Nanonis scan. Uses AO8 (sig index 31)."""
    import time
    import numpy as np
    try:
        from hardware_connections import HardwareManager
    except ImportError as e:
        return format_tool_response(f"Hardware connections not available: {e}")

    try:
        hw = get_hardware_manager()
        if hw is None or hw.nanonis is None:
            return format_tool_response("Nanonis not connected")
        nanonis = hw.nanonis
        step = max(2, int(step))
        Vbg_steps = np.linspace(start, stop, step, endpoint=True)
        completed = []
        for Vbg in Vbg_steps:
            raw = nanonis.sig_val_get_by_index(31)
            current_value = raw[0] if isinstance(raw, (list, tuple)) else raw
            n_ramp = int(max([2, np.ceil(np.abs(current_value - float(Vbg)) / rate)]))
            for V in np.linspace(current_value, Vbg, n_ramp, endpoint=True):
                nanonis.AO_volt_set_by_index(8, float(V))
                time.sleep(0.03)
            nanonis.SpeedSet(fwd_speed=scan_speed, bwd_speed=scan_speed)
            nanonis.BufferSet(pixels=pixels, lines=lines)
            nanonis.PropsSet(series_name=series_name)
            nanonis.Action("start", direction)
            timeout_status, file_path_size, file_path = nanonis.WaitEndOfScan(timeout=timeout_ms)
            if timeout_status:
                return format_tool_response(
                    json.dumps({
                        "error": f"Scan timed out at Vbg={Vbg:.4f} V after {timeout_ms}ms.",
                        "completed_vbg": completed,
                        "series_name": series_name,
                    })
                )
            completed.append(float(Vbg))
        return format_tool_response(
            json.dumps({
                "message": f"Bottom gate ramp-and-scan completed: {len(completed)} scans from {start} V to {stop} V.",
                "completed_vbg": completed,
                "series_name": series_name,
            })
        )
    except Exception as e:
        import traceback
        return format_tool_response(json.dumps({"error": f"bottom_gate_ramp_and_scan failed: {e}", "traceback": traceback.format_exc()}))


@mcp.tool()
def top_gate_ramp(Vtg: float, rate: float = 0.01, ctx: Context = None) -> str:
    """Ramp top gate voltage to Vtg (V) at given rate (V/step). Uses Nanonis AO4 (sig index 27)."""
    import time
    import numpy as np
    try:
        hw = get_hardware_manager()
        if hw is None or hw.nanonis is None:
            return format_tool_response("Nanonis not connected")
        nanonis = hw.nanonis
        current_value = nanonis.sig_val_get_by_index(27)
        n_steps = int(max([2, np.ceil(np.abs(current_value - Vtg) / rate)]))
        for V in np.linspace(current_value, Vtg, n_steps, endpoint=True):
            nanonis.AO_volt_set_by_index(4, V)
            time.sleep(0.03)
        return format_tool_response(f"Top gate ramped to {Vtg} V")
    except Exception as e:
        return format_tool_response(f"Top gate ramp failed: {e}")


@mcp.tool()
def generic_sweeper_start(
    basename: str,
    get_data: int = 1,
    sweep_direction: int = 1,
    reset_signal: int = 0,
    ctx: Context = None
) -> str:
    """Start Nanonis Generic Sweeper. basename: run name; get_data, sweep_direction, reset_signal: 0/1."""
    try:
        hw = get_hardware_manager()
        if hw is None or hw.nanonis is None:
            return format_tool_response("Nanonis not connected")
        nanonis = hw.nanonis
        nanonis.GS_start(basename, get_data, sweep_direction, reset_signal)
        return format_tool_response(f"Generic Sweeper started: {basename}")
    except Exception as e:
        return format_tool_response(f"Generic sweeper start failed: {e}")

@mcp.tool()
def grid_generic_sweeper_start(grid: list,
    basename: str,
    get_data: int = 1,
    sweep_direction: int = 1,
    reset_signal: int = 0,
    wait: int = 10,
    iteration: int = 1,
    reverse: bool = True,
    ctx: Context = None
) -> str:
    """Start Nanonis Generic Sweeper. basename: run name; get_data, sweep_direction, reset_signal: 0/1."""
    try:
        hw = get_hardware_manager()
        if hw is None or hw.nanonis is None:
            return format_tool_response("Nanonis not connected")
        nanonis = hw.nanonis
        for pos in grid:
            # Go to new sample position
            if isinstance(pos, dict):
                nanonis.XY_pos_set(pos["x"], pos["y"], wait_end=1)
            elif isinstance(pos, (list, tuple)) and len(pos) == 2:
                nanonis.XY_pos_set(pos[0], pos[1], wait_end=1)
            time.sleep(wait)  # s
            for i in range(iteration):
                nanonis.GS_start(basename, get_data, sweep_direction, reset_signal)
                if reverse:
                    nanonis.GS_start(basename, get_data, int(1-sweep_direction), reset_signal)
        return format_tool_response(f"Generic Sweeper started: {basename}")
    except Exception as e:
        return format_tool_response(f"Generic sweeper start failed: {e}")


@mcp.tool()
def nanonis_xy_set(x: float, y: float, ctx: Context = None) -> str:
    """Set Nanonis XY tip position in meters. Uses Nanonis XY_pos_set (wait_end=1)."""
    try:
        hw = get_hardware_manager()
        if hw is None or hw.nanonis is None:
            return format_tool_response("Nanonis not connected")
        nanonis = hw.nanonis
        nanonis.XY_pos_set(x, y, wait_end=1)
        return format_tool_response(f"XY position set to ({x}, {y}) m")
    except Exception as e:
        return format_tool_response(f"Set XY position failed: {e}")


@mcp.tool()
def nanonis_stop_tip(ctx: Context = None) -> str:
    """Stop Nanonis tip movement (tip_stop)."""
    try:
        hw = get_hardware_manager()
        if hw is None or hw.nanonis is None:
            return format_tool_response("Nanonis not connected")
        nanonis = hw.nanonis
        nanonis.tip_stop()
        return format_tool_response("Tip stop sent.")
    except Exception as e:
        return format_tool_response(f"Stop tip failed: {e}")


@mcp.tool()
def set_scan_frame(
    x: float,
    y: float,
    width: float,
    height: float,
    angle: float = 0,
    ctx: Context = None
) -> str:
    """Set Nanonis scan frame: center (x,y), width, height, angle (FrameSet)."""
    try:
        hw = get_hardware_manager()
        if hw is None or hw.nanonis is None:
            return format_tool_response("Nanonis not connected")
        nanonis = hw.nanonis
        nanonis.FrameSet(x, y, width, height, angle)
        return format_tool_response(f"Scan frame set: center=({x},{y}), size={width}x{height}, angle={angle}")
    except Exception as e:
        return format_tool_response(f"Set scan frame failed: {e}")


@mcp.tool()
def get_scan_frame(ctx: Context = None) -> str:
    """Get current Nanonis scan frame (FrameGet). Returns center x, y, width, height, angle."""
    try:
        hw = get_hardware_manager()
        if hw is None or hw.nanonis is None:
            return format_tool_response("Nanonis not connected")
        nanonis = hw.nanonis
        result = nanonis.FrameGet()
        return format_tool_response(json.dumps({"success": True, "frame": result}))
    except Exception as e:
        return format_tool_response(f"Get scan frame failed: {e}")


@mcp.tool()
def get_sxm_image(
    id: str = "0001",
    channel: str = "MIM-Im_AC-X",
    direction: str = "forward",
    output_path: Optional[str] = None,
    ctx: Context = None
) -> str:
    """Get SXM image from Nanonis and save as PNG (no processing applied)"""
    try:
        from hardware_connections import HardwareManager
        from pySPM import SXM
        import cv2
        import numpy as np
    except ImportError as e:
        return format_tool_response(f"Required dependencies not available: {e}")

    try:
        hw = get_hardware_manager()
        if hw is None or hw.nanonis is None:
            # Try to get from centering controller
            global centering_controller
            if centering_controller and hasattr(centering_controller, "hardware_manager") and centering_controller.hardware_manager and centering_controller.hardware_manager.nanonis:
                hw = centering_controller.hardware_manager
            else:
                return format_tool_response("Nanonis not connected. Run start_centering_session first.")
        
        nanonis = hw.nanonis
        session_path = nanonis.session_path_get()
        series_name = nanonis.PropsGet()[3]
        sxm_filepath = session_path + f"\\{series_name}{id}.sxm"
        
        sxm = SXM(sxm_filepath)
        scan = sxm.get_channel(channel, direction=direction)
        img_array = scan.pixels
        img_array = np.flipud(img_array)
        
        if not output_path:
            sessions_dir = Path("sessions")
            if centering_controller and hasattr(centering_controller, 'session_dir'):
                output_dir = centering_controller.session_dir / "images"
            else:
                output_dir = sessions_dir / "temp_images"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(output_dir / f"raw_image_{id}.png")
        
        norm_img = cv2.normalize(img_array, None, 0, 255, cv2.NORM_MINMAX)
        norm_img = norm_img.astype('uint8')
        success = cv2.imwrite(output_path, norm_img)
        if not success:
            return format_tool_response(f"Failed to write PNG to {output_path}")
        
        # Encode image as base64
        _, buffer = cv2.imencode('.png', norm_img)
        image_base64 = base64.b64encode(buffer).decode('utf-8')
        image_data_uri = f"data:image/png;base64,{image_base64}"
        
        # Return JSON with both path and image data
        result = {
            "path": output_path,
            "shape": f"{norm_img.shape[0]}x{norm_img.shape[1]}",
            "image": image_data_uri
        }
        return format_tool_response(json.dumps(result))
    except Exception as e:
        return format_tool_response(f"Get SXM image failed: {e}")


@mcp.tool()
def convert_sxm_to_png(
    sxm_path: str,
    output_path: str,
    channel: str = "MIM-Im_AC-X",
    direction: str = "forward",
    output_width: Optional[int] = None,
    output_height: Optional[int] = None,
    ctx: Context = None
):
    """Convert an SXM file to a PNG image with optional resizing.

    Returns the converted image as an MCP Image (so it renders inline in MCP
    clients) together with a short JSON note giving the saved path and shapes.
    """
    if not sxm_path or not output_path:
        return format_tool_response("sxm_path and output_path are required")

    try:
        from pySPM import SXM
        import cv2
        import numpy as np
    except ImportError as e:
        return format_tool_response(f"Required dependency missing: {e}")

    try:
        sxm = SXM(sxm_path)
        scan = sxm.get_channel(channel, direction=direction)

        img = scan.pixels
        img = remove_stripes_row_poly(img, deg=1)
        img = np.flipud(img)
        norm_img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX)
        norm_img = norm_img.astype('uint8')
        
        if output_width is not None and output_height is not None:
            norm_img = cv2.resize(norm_img, (output_width, output_height), interpolation=cv2.INTER_LINEAR)
        elif output_width is not None or output_height is not None:
            return format_tool_response("Both output_width and output_height must be specified together")

        success = cv2.imwrite(output_path, norm_img)
        if not success:
            return format_tool_response(f"Failed to write PNG to {output_path}")

        # Return an MCP Image (renders inline in MCP clients) plus small metadata text.
        ok, buffer = cv2.imencode('.png', norm_img)
        if not ok:
            return format_tool_response(f"Failed to encode PNG for {output_path}")
        meta = {
            "path": output_path,
            "original_shape": f"{img.shape[0]}x{img.shape[1]}",
            "output_shape": f"{norm_img.shape[0]}x{norm_img.shape[1]}",
        }
        return [Image(data=buffer.tobytes(), format="png"), Text(json.dumps(meta, indent=2))]
    except Exception as e:
        return format_tool_response(f"Conversion failed: {e}")


@mcp.tool()
def load_template(
    template_path: str,
    template_height_um: float,
    template_width_um: Optional[float] = None,
    origin_x_px: Optional[float] = None,
    origin_y_px: Optional[float] = None,
    ctx: Context = None
) -> str:
    """Load and preprocess a template image for particle filter predictions. This must be called before using predict_position."""
    global cached_template_context, cached_template_path
    
    try:
        from particle_filter import Config, build_big_context
    except ImportError as e:
        return format_tool_response(f"Required dependency missing: {e}")

    try:
        cfg = Config()
        cfg.large_image_path = template_path
        cfg.Z_HEIGHT = float(template_height_um)
        cfg.VERBOSE = False
        ctx = build_big_context(cfg, origin_x_px=origin_x_px, origin_y_px=origin_y_px)
        cached_template_context = ctx
        cached_template_path = template_path
        
        return format_tool_response(f"Template loaded: {template_path}, height: {template_height_um}μm, origin: ({ctx.origin_x_px}, {ctx.origin_y_px})")
    except Exception as e:
        import traceback
        return format_tool_response(f"Load template failed: {e}\n{traceback.format_exc()}")


@mcp.tool()
def predict_position(
    image_path: str,
    scan_size_um: float,
    ctx: Context = None
) -> str:
    """Run the particle filter on an image and return predicted position. Requires load_template to be called first."""
    global cached_template_context

    if cached_template_context is None:
        return format_tool_response("Template not loaded. Please call load_template first.")

    try:
        import numpy as np
        from particle_filter import Config, predict_single_image
    except ImportError as e:
        return format_tool_response(f"Required dependency missing: {e}")

    try:
        cfg = Config()
        cfg.VERBOSE = False
        ctx = cached_template_context
        meas, viz_info = predict_single_image(cfg, ctx, image_path, float(scan_size_um))

        position = meas.mean_xy
        confidence = meas.conf
        uncertainty = np.array([np.sqrt(meas.cov_xy[0,0]), np.sqrt(meas.cov_xy[1,1])])
        distance = float(np.linalg.norm(position))
        exp_complete = (distance < 50.0)

        response = f"Position: ({position[0]:.3f}, {position[1]:.3f}) μm, Confidence: {confidence:.3f}, Uncertainty: ({uncertainty[0]:.3f}, {uncertainty[1]:.3f}) μm, Distance: {distance:.3f} μm"
        return format_tool_response(response, experiment_complete_value=exp_complete)
    except Exception as e:
        import traceback
        return format_tool_response(f"Prediction failed: {e}\n{traceback.format_exc()}")


@mcp.tool()
def calculate_movement_from_prediction(
    x_position: float,
    y_position: float,
    confidence: float,
    distance_from_center: float,
    ctx: Context = None
) -> str:
    """Convert particle filter output into stage movement steps"""
    global centering_controller, stage_config

    try:
        import numpy as np
        from centering_controller import CenteringController
    except ImportError as e:
        return format_tool_response(f"Required dependency missing: {e}")

    try:
        if centering_controller is None:
            centering_controller = CenteringController(stage_config=stage_config)

        position = np.array([x_position, y_position], dtype=np.float64)
        movement = centering_controller._calculate_movement(position, confidence, distance_from_center)
        return format_tool_response(str(movement))
    except Exception as e:
        return format_tool_response(f"Calculation failed: {e}")


@mcp.tool()
def plot_predicted_positions(
    positions: List[List[float]],
    box_size_um: float = 50.0,
    labels: Optional[List[str]] = None,
    output_path: Optional[str] = None,
    template_path: Optional[str] = None,
    template_height_um: Optional[float] = None,
    ctx: Context = None
):
    """Overlay one or more predicted positions as squares on the template image.

    Each position is ``[x_um, y_um]`` in the same Cartesian frame that
    predict_position returns (x to the right, y up, relative to the template
    origin). A single position may be passed as ``[[x, y]]`` (a bare ``[x, y]`` is
    also accepted).

    Args:
        positions: list of ``[x_um, y_um]`` pairs to mark.
        box_size_um: side length of each square in micrometers (default 50, i.e. a
            typical scan field of view).
        labels: optional label per position (defaults to its coordinates).
        output_path: where to save the annotated PNG. If omitted, the image is only
            returned inline (base64), not written to disk.
        template_path: build the template context from this image instead of the one
            cached by load_template; template_height_um sets its physical height (um)
            and the origin defaults to the image center.

    Returns an annotated image plus JSON listing the pixel coordinate of each square.
    """
    global cached_template_context

    try:
        import numpy as np
        from matplotlib.patches import Rectangle
    except ImportError as e:
        return format_tool_response(f"Required dependency missing: {e}")

    # --- Normalize `positions` into a list of (x, y) pairs ---
    if not positions:
        return format_tool_response("No positions provided.")
    if len(positions) == 2 and all(isinstance(v, (int, float)) for v in positions):
        positions = [positions]  # a bare [x, y] -> single position
    norm = []
    for p in positions:
        if not (isinstance(p, (list, tuple)) and len(p) == 2):
            return format_tool_response(f"Each position must be [x_um, y_um]; got: {p}")
        norm.append((float(p[0]), float(p[1])))

    # --- Resolve the template context (image + origin + px/um scale) ---
    if template_path:
        try:
            from particle_filter import Config, build_big_context
            cfg = Config()
            cfg.large_image_path = template_path
            if template_height_um is not None:
                cfg.Z_HEIGHT = float(template_height_um)
            cfg.VERBOSE = False
            tctx = build_big_context(cfg)
        except Exception as e:
            import traceback
            return format_tool_response(f"Failed to load template: {e}\n{traceback.format_exc()}")
    elif cached_template_context is not None:
        tctx = cached_template_context
    else:
        return format_tool_response("No template loaded. Call load_template first, or pass template_path.")

    try:
        px_per_um = float(tctx.px_per_unit_big)
        ox, oy = float(tctx.origin_x_px), float(tctx.origin_y_px)
        side_px = box_size_um * px_per_um

        # Keep the rendered figure a sensible size regardless of template resolution.
        aspect = tctx.W / float(tctx.H)
        if aspect >= 1.0:
            fig_w, fig_h = 10.0, 10.0 / aspect
        else:
            fig_w, fig_h = 10.0 * aspect, 10.0
        fig, ax = plt.subplots(figsize=(fig_w, fig_h))
        ax.imshow(tctx.big_rgb, interpolation='nearest')
        ax.axis('off')

        # Template origin = (0, 0) um
        ax.plot(ox, oy, '+', color='cyan', markersize=14, markeredgewidth=2)

        cmap = plt.get_cmap('tab10')
        marked = []
        for i, (x_um, y_um) in enumerate(norm):
            # Invert the particle-filter px->um mapping (y is up in um, down in rows).
            cx = ox + x_um * px_per_um
            cy = oy - y_um * px_per_um
            color = cmap(i % 10)
            ax.add_patch(Rectangle((cx - side_px / 2.0, cy - side_px / 2.0), side_px, side_px,
                                   fill=False, edgecolor=color, linewidth=2))
            ax.plot(cx, cy, 'o', color=color, markersize=5,
                    markeredgecolor='black', markeredgewidth=1)
            text = labels[i] if (labels and i < len(labels)) else f"({x_um:.1f}, {y_um:.1f}) µm"
            ax.text(cx, cy - side_px / 2.0 - 6, text, color=color, fontsize=9,
                    ha='center', va='bottom',
                    bbox=dict(boxstyle='round,pad=0.2', facecolor='white', alpha=0.7, edgecolor='none'))
            marked.append({"x_um": x_um, "y_um": y_um, "x_px": cx, "y_px": cy})

        ax.set_title(f"Predicted position(s) on template — {box_size_um:g} µm boxes", fontsize=12)
        fig.tight_layout()

        return _result(fig, output_path, extra={
            "num_positions": len(norm),
            "box_size_um": box_size_um,
            "px_per_um": px_per_um,
            "origin_px": [ox, oy],
            "positions": marked,
        })
    except Exception as e:
        import traceback
        return format_tool_response(f"Plotting failed: {e}\n{traceback.format_exc()}")


@mcp.tool()
def average_measurements_in_folder(folder_path: str) -> str:
    """
    Scan a folder for .dat files, average consecutive pairs (1&2, 3&4, ...),
    and return a list of averaged .dat formatted strings.

    This is a data processing tool. The returned strings can be passed to
    plotting tools like 'plot_combined_measurement'.
    """
    if not os.path.isdir(folder_path):
        return format_tool_response(json.dumps({"error": f"'{folder_path}' is not a valid directory.", "averaged_contents": []}))

    files = sorted([f for f in os.listdir(folder_path) if f.lower().endswith('.dat')])
    if not files:
        return format_tool_response(json.dumps({"averaged_contents": [], "message": "No .dat files found"}))

    averaged_contents = []
    for i in range(0, len(files) - 1, 2):
        path1 = os.path.join(folder_path, files[i])
        path2 = os.path.join(folder_path, files[i + 1])
        try:
            with open(path1, 'r', encoding='utf-8', errors='ignore') as in1:
                c1 = in1.read()
            with open(path2, 'r', encoding='utf-8', errors='ignore') as in2:
                c2 = in2.read()
            avg_str = _utils.average_dat_contents(c1, c2)
            averaged_contents.append(avg_str)
        except Exception:
            continue

    return format_tool_response(json.dumps({"averaged_contents": averaged_contents, "count": len(averaged_contents)}))


@mcp.tool()
def normalize_measurements_in_folder(
    folder_path: str,
    x_col: str = "BottomGate Setp. (V)",
    y_col: str = "MIM-Im_AC-X (V)",
    target_val1: float = 0.0,
    target_val2: float = 1.0,
) -> str:
    """
    Scan a folder for .dat files and normalize a specific channel (y_col)
    such that its values at min(x_col) and max(x_col) are target_val1 and target_val2.

    Returns a list of 'virtual' .dat strings.
    """
    if not os.path.isdir(folder_path):
        return format_tool_response(json.dumps({"error": f"'{folder_path}' is not a valid directory.", "normalized_contents": []}))

    files = sorted([f for f in os.listdir(folder_path) if f.lower().endswith('.dat')])
    if not files:
        return format_tool_response(json.dumps({"normalized_contents": [], "message": "No .dat files found"}))

    normalized_contents = []
    for f in files:
        full_path = os.path.join(folder_path, f)
        try:
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f_in:
                content = f_in.read()
            processed = _utils.normalize_dat_content(content, x_col, y_col, target_val1, target_val2)
            normalized_contents.append(processed)
        except Exception:
            continue

    return format_tool_response(json.dumps({"normalized_contents": normalized_contents, "count": len(normalized_contents)}))


@mcp.tool()
def analyze_correlation(
        folder_path: str,
        x_col: str = "BottomGate Setp. (V)",
        z_col: str = "MIM-Im_AC-X (V)",
        normalize: bool = False,
        target_val1: float = 0.0,
        target_val2: float = 1.0,
        window_size: int = 21,
        x_min: Optional[float] = None,
        x_max: Optional[float] = None,
        slope_threshold: float = 0.0,
        slope_offset: int = 10,
        neighborhood_window: int = 2,
        font: str = "Arial",
        fontsize: int = 15,
):
    """
    Analyze a folder of data by automatically averaging ALL files into a single curve.
    Applies sharpness-based feature detection and provides detailed validation plots.

    Args:
        folder_path: Path containing .dat files.
        normalize: Whether to apply range normalization to the averaged curve.
    """
    import os
    import matplotlib.pyplot as plt
    _utils._set_global_font(font, fontsize)

    # 1. Data Gathering
    if not os.path.isdir(folder_path):
        return f"Error: '{folder_path}' is not a directory."

    files = sorted([f for f in os.listdir(folder_path) if
                    f.lower().endswith('.dat') and os.path.isfile(os.path.join(folder_path, f))])
    if not files:
        return "No .dat files found in the specified folder."

    raw_contents = []
    for f in files:
        with open(os.path.join(folder_path, f), 'r', encoding='utf-8', errors='ignore') as fin:
            raw_contents.append(fin.read())

    # 2. Total Averaging
    try:
        avg_content = _utils.average_multiple_dat_contents(raw_contents, x_col=x_col)
    except Exception as e:
        return f"Error during averaging: {str(e)}"

    # 3. Normalization
    if normalize:
        try:
            avg_content = _utils.normalize_dat_content(
                avg_content, x_col, z_col, target_val1, target_val2
            )
        except Exception:
            pass

    # 4. Feature Detection
    try:
        m, df = _utils.parse_instrument_data(avg_content)
        bx = df[x_col].to_numpy(dtype=float)
        bz = df[z_col].to_numpy(dtype=float)

        # Mask
        mask = ~(np.isnan(bx) | np.isnan(bz))
        if x_min is not None: mask &= (bx >= x_min)
        if x_max is not None: mask &= (bx <= x_max)
        bx, bz = bx[mask], bz[mask]

        if len(bx) < 2:
            return "Insufficient data points after filtering for analysis."

        bz_clean = _utils.suppress_outliers(bz)
        stats = _utils.detect_minima_from_derivatives(
            bx, bz_clean,
            window_size=window_size,
            slope_threshold=slope_threshold,
            slope_offset=slope_offset,
            neighborhood_window=neighborhood_window
        )
    except Exception as e:
        return f"Analysis failed: {str(e)}"

    # 5. Output Generation
    summary = f"### Total Correlation Analysis: {folder_path}\n"
    summary += f"- **Files Averaged**: {len(files)}\n"
    summary += f"- **Normalization**: {'Enabled' if normalize else 'Disabled'}\n"
    summary += f"- **Detected Features**: {stats['num_features']}\n"
    summary += f"- **Sharpness Score**: {stats['sharpness']:.4e}\n\n"

    # List the exact voltages for easy copying
    if stats['num_features'] > 0:
        summary += "#### Detected Dip Voltages (V):\n"
        # Sort dip positions for better readability
        dips_v = sorted(stats['x_minima'].tolist())
        summary += ", ".join([f"{v:.4f}" for v in dips_v]) + "\n\n"
    else:
        dips_v = []
        summary += "*No dips detected in the specified range.*\n\n"

    # 4-Panel Validation Plot: 2x2 Layout
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    ax1, ax2 = axes[0]
    ax3, ax4 = axes[1]

    # Panel 1: Aggregated Curve
    ax1.plot(bx, bz, color='gray', alpha=0.5, label="Averaged Raw")
    ax1.plot(stats['x_smooth'], stats['y_smooth'], color='blue', linewidth=2, label="Filtered")
    ax1.set_title("Aggregated Data Curve", fontsize=fontsize)
    ax1.set_xlabel(x_col, fontsize=fontsize)
    ax1.set_ylabel(z_col, fontsize=fontsize)
    ax1.legend(fontsize=fontsize - 2)

    # Panel 2: Feature Detection
    ax2.plot(stats['x_smooth'], stats['y_smooth'], color='blue', alpha=0.3)
    if len(stats['x_minima']) > 0:
        ax2.scatter(stats['x_minima'], stats['y_minima'], color='red', s=40, zorder=5, label="Dips")
    ax2.set_title(f"Features Detected: {stats['num_features']}", fontsize=fontsize)
    ax2.set_xlabel(x_col, fontsize=fontsize)
    ax2.set_ylabel(z_col, fontsize=fontsize)

    # Panel 3: First Derivative
    ax3.plot(stats['x_smooth'], stats['grad1'], color='green', linewidth=1.5, label="dZ/dVg")
    ax3.axhline(0, color='black', alpha=0.4, linestyle='--')
    if len(stats['x_minima']) > 0:
        # Use interpolated x and exact zero for g1 plot
        ax3.scatter(stats['x_minima'], np.zeros_like(stats['x_minima']), color='red', s=40, zorder=5)
    ax3.set_title("First Derivative (Zero-Crossing)", fontsize=fontsize)
    ax3.set_xlabel(x_col, fontsize=fontsize)
    ax3.set_ylabel("dZ/dVg", fontsize=fontsize)

    # Panel 4: Sharpness Analysis (Second Derivative)
    ax4.plot(stats['x_smooth'], stats['grad2'], color='purple', linewidth=1.5, label="d2Z/dVg2")
    ax4.axhline(0, color='black', alpha=0.4)
    if len(stats['x_minima']) > 0:
        # Use interpolated x and interpolated g2 for g2 plot
        ax4.scatter(stats['x_minima'], stats['grad2_minima'], color='red', s=40, zorder=5)
    ax4.set_title(f"Sharpness Profile: {stats['sharpness']:.2e}", fontsize=fontsize)
    ax4.set_xlabel(x_col, fontsize=fontsize)
    ax4.set_ylabel("d2Z/dVg2", fontsize=fontsize)

    fig.subplots_adjust(hspace=0.3, wspace=0.3)
    fig.set_tight_layout(True)
    res_bytes = _utils.figure_to_bytes(fig)
    plt.close(fig)

    # Return summary table, machine-readable voltages, and validation plot image
    return [
        summary,
        json.dumps({"dip_voltages": dips_v}, indent=2),
        Image(data=res_bytes, format="png")
    ]

@mcp.tool()
def analyze_twist_angle(
        dip_positions: List[float],
        filling_factors: List[float],
        # Gate geometry (defaulting to typical graphene/hBN setup)
        has_top_gate: bool = True,
        has_bottom_gate: bool = True,
        top_hbn_thickness_nm: float = 30.0,
        bottom_hbn_thickness_nm: float = 30.0,
        epsilon_hbn: float = 3.5,
        lattice_constant_angstrom: float = 2.46,
):
    """
    Extract the twist angle and Charge Neutrality Point (CNP) from a list of gate voltage dips
    and their corresponding filling factors.

    This tool performs a linear regression (V vs. filling factor) to handle discrepancies
    between points and extracts the moiré physics parameters.

    Args:
        dip_positions: List of gate voltage positions (V) for identified dips.
        filling_factors: List of expected filling factors (nu) for those dips.
        lattice_constant_angstrom: Lattice constant in Å (default 2.46 for graphene).
    """
    if len(dip_positions) != len(filling_factors) or len(dip_positions) < 2:
        raise ValueError("Must provide at least two corresponding dip positions and filling factors.")

    # 1. Physics Constants
    e = 1.602176634e-19  # Elementary charge [C]
    eps0 = 8.854187817e-12  # Vacuum permittivity [F/m]
    a0_m = lattice_constant_angstrom * 1e-10  # Lattice constant [m]

    # Gate capacitance per unit area [F/m²]
    C_tg = (eps0 * epsilon_hbn / (top_hbn_thickness_nm * 1e-9)) if has_top_gate else 0.0
    C_bg = (eps0 * epsilon_hbn / (bottom_hbn_thickness_nm * 1e-9)) if has_bottom_gate else 0.0
    C_total = C_tg + C_bg

    if C_total <= 0:
        raise ValueError("Total capacitance must be greater than zero. Check gate settings.")

    # 2. Linear Regression (V = m * nu + CNP)
    # Using np.polyfit for a robust linear fit
    from numpy import polyfit
    slope, intercept = polyfit(filling_factors, dip_positions, 1)

    v_cnp = intercept

    # 3. Moiré Density (n_s)
    # Based on the logic in analyze_twist_angle_disorder:
    # delta_n = C/e * delta_V
    # n_s = delta_n / delta_nu = (C/e * slope * delta_nu) / delta_nu = C/e * slope
    n_s = abs(slope * C_total / e)

    # 4. Moiré Lattice Constant (a_M)
    # From existing tool: a_M = 2.0 / sqrt(sqrt(3.0) * n_s)
    
    a_M = np.sqrt(2.0 / (np.sqrt(3.0) * n_s))

    # 5. Twist Angle (theta)
    # From existing tool: theta = 2 * arcsin(a0 / (2 * a_M))
    sin_val = a0_m / (2.0 * a_M)
    if abs(sin_val) > 1.0:
        return {
            "twist_angle": 0.0,
            "charge_neutrality_point": v_cnp,
            "error": "Geometry invalid for the given slope (sin(theta/2) > 1)"
        }

    theta_rad = 2.0 * np.arcsin(sin_val)
    theta_deg = np.degrees(theta_rad)

    return {
        "twist_angle": float(theta_deg),
        "charge_neutrality_point": float(v_cnp)
    }

@mcp.tool()
def analyze_sxm_dynamics(
        folder_path: str,
        channel: str = "MIM-Im_AC-X",
        direction: str = "forward",
        window_width_um: float = 1.0,
        window_height_um: float = 1.0,
        analysis_mode: str = "sad",
        norm_ref_col: int = -1,
        show_grid: bool = False,
        grid_points_x: int = 4,
        grid_points_y: int = 4,
):
    """
    Analyzes a sequence of SXM images to find the region with the most physical change.
    Automatically scans all rotation angles (0-180°) to find the optimal window orientation.

    Args:
        folder_path: Path to the folder containing sequential .sxm files.
        channel: The data channel to load (default 'Z').
        direction: Scan direction to load ('forward' or 'backward').
        window_width_um: The width of the search window in micrometers (default 1.0).
        window_height_um: The height of the search window in micrometers (default 1.0).
        analysis_mode: 'sad' for Sum of Absolute Differences, 'variance' for pixel variance.
        norm_ref_col: The pixel column index to use for row-by-row normalization (default -1).
    """
    import os
    import numpy as np
    import matplotlib.pyplot as plt

    if not os.path.isdir(folder_path):
        return f"Error: '{folder_path}' is not a valid directory."

    files = sorted([f for f in os.listdir(folder_path) if f.lower().endswith('.sxm')])
    if len(files) < 2:
        return f"Error: Found {len(files)} .sxm files. Need at least 2 for dynamics analysis."

    print(f"Loading {len(files)} SXM files from {folder_path}...")

    images = []
    meta = None
    skipped_files = []

    for f in files:
        full_path = os.path.join(folder_path, f)
        try:
            pixels, m = _utils.load_sxm_data(full_path, channel, direction=direction)

            # Normalize row-by-row
            norm_pixels = _utils.normalize_image_rows(pixels, ref_col_idx=norm_ref_col)

            images.append(norm_pixels)
            if meta is None:
                meta = m
        except Exception as e:
            print(f"Warning: Failed to load {f} - {e}")

    if len(images) < 2:
        return "Failed to load enough valid images for analysis."

    # Stack dynamics
    if analysis_mode.lower() == "variance":
        diff_img = np.var(images, axis=0)
        mode_label = "Pixel Variance"
    else:
        # Default: Sum of absolute differences
        diff_img = np.zeros_like(images[0])
        for i in range(len(images) - 1):
            diff_img += np.abs(images[i + 1] - images[i])
        mode_label = "Cumulative Absolute Change"

    # Find hotspot
    width_um = meta["width"] * 1e6  # assuming base unit is m, typical for pySPM
    height_um = meta["height"] * 1e6
    if "unit" in meta and meta["unit"] == "m":
        unit = "µm"
    else:
        width_um = meta["width"]
        height_um = meta["height"]
        unit = meta.get("unit", "px")

    px_x = meta["pixels_x"]
    px_y = meta["pixels_y"]

    um_per_px_x = width_um / px_x
    um_per_px_y = height_um / px_y
    # Auto-scan 180 angles (every 1°) to find optimal orientation
    center_y_px, center_x_px, max_score, best_angle = _utils.find_max_change_window(
        diff_img,
        um_per_px_y,
        um_per_px_x,
        window_height_um,
        window_width_um,
        angle_deg=0.0,
        angle_steps=180
    )

    # Results from find_max_change_window are centers

    center_x_um = center_x_px * um_per_px_x
    center_y_um_from_top = center_y_px * um_per_px_y
    center_y_um = (px_y - center_y_px) * um_per_px_y  # physical y from bottom

    # ─── Global (physical) coordinates — kept for navigation/reporting ───
    # Nanonis scan_offset is typically the center of the image.
    scan_offset_x = meta.get("scan_offset_x", 0.0) * 1e6
    scan_offset_y = meta.get("scan_offset_y", 0.0) * 1e6
    if unit != "µm":
        scan_offset_x = meta.get("scan_offset_x", 0.0)
        scan_offset_y = meta.get("scan_offset_y", 0.0)

    scan_angle = meta.get("scan_angle", 0.0)
    rad_scan = np.deg2rad(-scan_angle)

    # True physical hotspot center = local offset from image center, rotated by scan_angle.
    dx_local = (center_x_px - px_x / 2.0) * um_per_px_x
    dy_local = (center_y_px - px_y / 2.0) * um_per_px_y
    dx_global = dx_local * np.cos(rad_scan) - dy_local * np.sin(rad_scan)
    dy_global = dx_local * np.sin(rad_scan) + dy_local * np.cos(rad_scan)
    center_x_global = scan_offset_x + dx_global
    center_y_global = scan_offset_y + dy_global
    corrected_angle = (best_angle + scan_angle) % 360.0

    # ─── Display in the image's own (upright) frame ───
    # The raw scan may carry a physical scan_angle; we intentionally do NOT rotate
    # the displayed image by it, so the scan lines stay horizontal (upright view).
    # Only the detected window is drawn rotated, by best_angle relative to the image.
    hotspot_x_img = center_x_px * um_per_px_x
    hotspot_y_img = center_y_px * um_per_px_y
    extent = [0.0, width_um, 0.0, height_um]

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    ax1, ax2 = axes

    # Panel 1: First Frame
    im1 = ax1.imshow(images[0], cmap='viridis', extent=extent, origin='lower')
    ax1.set_aspect('equal')
    plt.colorbar(im1, ax=ax1, fraction=0.046, pad=0.04).set_label(f"Amplitude ({channel})")

    # Panel 2: Stacked Dynamics Heatmap
    im2 = ax2.imshow(diff_img, cmap='inferno', extent=extent, origin='lower')
    ax2.set_aspect('equal')
    plt.colorbar(im2, ax=ax2, fraction=0.046, pad=0.04).set_label(f"{mode_label} ({channel})")

    # Hotspot square + optional grid, drawn in the upright image frame.
    import matplotlib.patches as patches
    from matplotlib.ticker import FuncFormatter

    def _rot_img(px, py, deg):
        rad = np.deg2rad(deg)
        return (hotspot_x_img + px * np.cos(rad) - py * np.sin(rad),
                hotspot_y_img + px * np.sin(rad) + py * np.cos(rad))

    grid_positions_um = []
    for ax in axes:
        rect = patches.Rectangle(
            (hotspot_x_img - window_width_um / 2.0, hotspot_y_img - window_height_um / 2.0),
            window_width_um, window_height_um,
            angle=-best_angle,
            rotation_point='center',
            linewidth=2, edgecolor='cyan', facecolor='none'
        )
        ax.add_patch(rect)

        if show_grid and grid_points_x > 0 and grid_points_y > 0:
            xs = np.linspace(-window_width_um / 2.0, window_width_um / 2.0, grid_points_x)
            ys = np.linspace(-window_height_um / 2.0, window_height_um / 2.0, grid_points_y)

            # Vertical grid lines (image frame, rotated by best_angle about the hotspot)
            for x_val in xs:
                p1 = _rot_img(x_val, -window_height_um / 2.0, -best_angle)
                p2 = _rot_img(x_val, window_height_um / 2.0, -best_angle)
                ax.plot([p1[0], p2[0]], [p1[1], p2[1]], color='cyan', linestyle='--', linewidth=1, alpha=0.7)

            # Horizontal grid lines
            for y_val in ys:
                p1 = _rot_img(-window_width_um / 2.0, y_val, -best_angle)
                p2 = _rot_img(window_width_um / 2.0, y_val, -best_angle)
                ax.plot([p1[0], p2[0]], [p1[1], p2[1]], color='cyan', linestyle='--', linewidth=1, alpha=0.7)

            # Global (physical) grid-point coordinates for the navigation table, computed once.
            if not grid_positions_um:
                rad_g = np.deg2rad(-corrected_angle)
                for j, y_val in enumerate(reversed(ys)):
                    for i, x_val in enumerate(xs):
                        gx = center_x_global + x_val * np.cos(rad_g) - y_val * np.sin(rad_g)
                        gy = center_y_global + x_val * np.sin(rad_g) + y_val * np.cos(rad_g)
                        grid_positions_um.append((i, j, gx, gy))

        ax.xaxis.set_major_formatter(FuncFormatter(lambda val, pos: f"{val:.1f}"))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda val, pos: f"{val:.1f}"))

    _utils.apply_style(ax1, title="First Frame (t=0)", xlabel=f"Image X ({unit})", ylabel=f"Image Y ({unit})")
    _utils.apply_style(ax2, title="SXM Dynamics Hotspot", xlabel=f"Image X ({unit})", ylabel=f"Image Y ({unit})")

    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=60)
    buf.seek(0)
    png_data = buf.read()
    img = Image(data=png_data, format="png")
    plt.close(fig)

    summary = f"### SXM Dynamics Analysis: {folder_path}\n"
    summary += f"- **Files Analyzed**: {len(images)} (Channel: `{channel}`, Dir: `{direction}`)\n"
    summary += f"- **Metric**: {mode_label}\n"
    summary += "- **Normalization**: Row-by-row (Reference: Rightmost Column)\n"
    summary += f"\n**Detected Hotspot Square**:\n"
    summary += f"- Center (image): X = `{hotspot_x_img:.3f}` {unit}, Y = `{hotspot_y_img:.3f}` {unit}\n"
    summary += f"- Center (global/physical): X = `{center_x_global:.3f}` {unit}, Y = `{center_y_global:.3f}` {unit}\n"
    summary += f"- Size: `{window_width_um}` × `{window_height_um}` {unit}\n"
    summary += f"- Window angle (image): `{best_angle:.1f}`°\n"
    summary += f"- Corrected Global Angle: `{corrected_angle:.1f}`°\n"
    summary += f"- Cumulative Change Score: `{max_score:.4e}`\n\n"

    if show_grid and grid_points_x > 0 and grid_points_y > 0 and grid_positions_um:
        summary += f"**Grid Point Positions ({grid_points_x}x{grid_points_y})**\n"
        summary += "| Grid (X, Y) | Global X (m) | Global Y (m) |\n"
        summary += "|---|---|---|\n"
        for i, j, gx, gy in grid_positions_um:
            summary += f"| ({i}, {j}) | {gx * 1e-6:.4e} | {gy * 1e-6:.4e} |\n"
        summary += "\n"

    return [summary, img]


@mcp.tool()
def find_best_region_ssim(
        folder_path: str,
        channel: str = "MIM-Im_AC-X",
        direction: str = "forward",
        window_width_um: float = 1.0,
        window_height_um: float = 1.0,
        norm_ref_col: int = -1,
        angle_steps: int = 180,
        save_path: Optional[str] = None,
):
    """
    SSIM-based best-measurement-region finder for a sequence of SXM images.

    The structural-change analogue of analyze_sxm_dynamics: instead of summing raw
    absolute differences, it measures STRUCTURAL change via SSIM, which is insensitive
    to uniform intensity offsets between frames. Pipeline:
      1. Load the sorted .sxm sequence (one channel), normalize each frame row-by-row.
      2. Crop all frames to a common shape and compute the SSIM map of every consecutive
         pair (skimage), then average them -> mean SSIM map over the sequence.
      3. Build the change map = (1 - mean SSIM): high where structure changes most.
      4. Slide a rotated window of the requested physical size over the change map,
         scanning 0-180 deg, and return the window MAXIMIZING the (1 - SSIM) sum --
         i.e. the region with the LOWEST mean SSIM (most structural change).

    Lower SSIM = more change = the better measurement region.

    Parameters
    ----------
    folder_path       : Folder of sequential .sxm files.
    channel           : Data channel (default 'MIM-Im_AC-X').
    direction         : 'forward' or 'backward'.
    window_width_um   : Search-window width (um).
    window_height_um  : Search-window height (um).
    norm_ref_col      : Pixel column for row-by-row normalization (default -1, rightmost).
    angle_steps       : Number of rotation angles scanned over 0-180 deg (default 180).
    save_path         : Optional absolute path to save the figure.
    """
    import os
    import numpy as np
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches

    if not os.path.isdir(folder_path):
        return f"Error: '{folder_path}' is not a valid directory."
    files = sorted([f for f in os.listdir(folder_path) if f.lower().endswith('.sxm')])
    if len(files) < 2:
        return f"Error: Found {len(files)} .sxm files. Need at least 2 for SSIM analysis."

    images, meta = [], None
    for f in files:
        try:
            pixels, m = _utils.load_sxm_data(os.path.join(folder_path, f), channel, direction=direction)
            images.append(_utils.normalize_image_rows(pixels, ref_col_idx=norm_ref_col))
            if meta is None:
                meta = m
        except Exception as e:
            print(f"Warning: Failed to load {f} - {e}")
    if len(images) < 2:
        return "Failed to load enough valid images for SSIM analysis."

    # SSIM requires identical shapes -> crop all to the common minimum
    ny = min(im.shape[0] for im in images)
    nx = min(im.shape[1] for im in images)
    images = [im[:ny, :nx] for im in images]

    # Mean SSIM map over consecutive pairs; change = 1 - SSIM (high = most change)
    ssim_sum = None
    for i in range(len(images) - 1):
        sm = _utils.calculate_ssim_map(images[i], images[i + 1])
        ssim_sum = sm if ssim_sum is None else ssim_sum + sm
    ssim_mean = ssim_sum / (len(images) - 1)
    change_map = np.nan_to_num(1.0 - ssim_mean, nan=0.0)

    # Physical units
    width_um = meta["width"] * 1e6
    height_um = meta["height"] * 1e6
    if meta.get("unit") == "m":
        unit = "µm"
    else:
        width_um = meta["width"]
        height_um = meta["height"]
        unit = meta.get("unit", "px")
    px_y, px_x = change_map.shape
    um_per_px_x = width_um / px_x
    um_per_px_y = height_um / px_y

    # Window of MIN mean SSIM == MAX sum of (1 - SSIM)
    center_y_px, center_x_px, max_change, best_angle = _utils.find_max_change_window(
        change_map, um_per_px_y, um_per_px_x,
        window_height_um, window_width_um, angle_deg=0.0, angle_steps=angle_steps,
    )
    win_area_px = max((window_width_um / um_per_px_x) * (window_height_um / um_per_px_y), 1.0)
    mean_ssim_region = float(np.clip(1.0 - max_change / win_area_px, -1.0, 1.0))

    # Global coordinates for tip positioning (account for scan rotation)
    scale = 1e6 if unit == "µm" else 1.0
    scan_offset_x = meta.get("scan_offset_x", 0.0) * scale
    scan_offset_y = meta.get("scan_offset_y", 0.0) * scale
    scan_angle = meta.get("scan_angle", 0.0)
    rad_scan = np.deg2rad(-scan_angle)
    dx_local = (center_x_px - px_x / 2.0) * um_per_px_x
    dy_local = (center_y_px - px_y / 2.0) * um_per_px_y
    center_x_global = scan_offset_x + dx_local * np.cos(rad_scan) - dy_local * np.sin(rad_scan)
    center_y_global = scan_offset_y + dx_local * np.sin(rad_scan) + dy_local * np.cos(rad_scan)
    corrected_angle = (best_angle + scan_angle) % 360.0

    # Figure: first frame + mean-SSIM map, both marked with the lowest-SSIM window
    extent = [0, width_um, 0, height_um]
    rcx = center_x_px * um_per_px_x
    rcy = center_y_px * um_per_px_y
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    im1 = ax1.imshow(images[0], cmap="viridis", extent=extent, origin="lower")
    plt.colorbar(im1, ax=ax1, fraction=0.046, pad=0.04).set_label(f"{channel} (first frame)")
    im2 = ax2.imshow(ssim_mean, cmap="inferno_r", extent=extent, origin="lower")
    plt.colorbar(im2, ax=ax2, fraction=0.046, pad=0.04).set_label("Mean SSIM (low = most change)")
    for ax in (ax1, ax2):
        ax.add_patch(patches.Rectangle(
            (rcx - window_width_um / 2.0, rcy - window_height_um / 2.0),
            window_width_um, window_height_um, angle=-best_angle, rotation_point="center",
            linewidth=2, edgecolor="cyan", facecolor="none"))
        ax.set_aspect("equal")
        ax.set_xlabel(f"Image X ({unit})")
        ax.set_ylabel(f"Image Y ({unit})")
    ax1.set_title("First frame (t=0)")
    ax2.set_title("Mean SSIM + lowest-SSIM window")
    fig.suptitle("SSIM best-region finder (cyan = lowest mean SSIM = most structural change)", fontsize=13)
    plt.tight_layout(rect=[0, 0, 1, 0.96])

    return _result(fig, save_path, extra={
        "metric": "1 - mean SSIM over consecutive pairs",
        "n_frames": len(images),
        "center_x_um": float(center_x_global),
        "center_y_um": float(center_y_global),
        "center_row_px": int(center_y_px),
        "center_col_px": int(center_x_px),
        "window_um": [float(window_width_um), float(window_height_um)],
        "angle_deg": float(corrected_angle),
        "mean_ssim_region": mean_ssim_region,
        "change_score_sum_1_minus_ssim": float(max_change),
    })


@mcp.tool()
def plot_sxm_combined(
        sxm_file_path: str,
        dat_file_contents: Optional[List[str]] = None,
        dat_folder_path: Optional[str] = None,
        x_col: str = "BottomGate Setp. (V)",
        y_cols: List[str] = ["MIM-Im_AC-X (V)"],
        sxm_channel: str = "MIM-Im_AC-X",
        sxm_direction: str = "forward",
        sxm_poly_deg: Optional[int] = 1,
        line_styles: Optional[List[Dict[str, str]]] = None,
        title: str = "Grid measurement result",
        cmap: str = "bone",
        font: str = "Arial",
        fontsize: int = 15,
        smooth_window: Optional[int] = None,
        smooth_order: Optional[int] = None,
        save_path: Optional[str] = None,
        x_min: Optional[float] = None,
        x_max: Optional[float] = None,
        y_min: Optional[float] = None,
        y_max: Optional[float] = None,
        show_legend: bool = False,
        color_by_column: bool = True,
        use_subplots: bool = True,
        max_subplot_rows: int = 4,
        angle_offset: float = 0.0,
        figsize: Tuple[float, float] = (20, 10),
        normalize: bool = True,
        norm_vmin: float = 0.0,
        norm_vmax: float = 7.0,
        pts_per_row: Optional[int] = None,
        average_n: int = 1,
):
    """
    Generate a 1x2 plot: [SXM Heatmap + Point Overlays] | [1D Data Curves].

    Parameters
    ----------
    sxm_file_path     : Absolute path to the .sxm file.
    dat_file_contents : List of raw .dat file contents.
    dat_folder_path   : Path to a folder containing .dat files.
    color_by_column   : If True, uses 'bwr' colormap for points and curves based on column index.
    use_subplots      : If True, splits the 1D curves into a grid of subplots by row.
    max_subplot_rows  : Maximum rows of subplots to display (default 4).
    angle_offset      : Optional rotation offset in degrees (e.g. 180 to flip).
    figsize           : Tuple of (width, height) in inches.
    pts_per_row       : Exact number of points in each row of the spatial measurement grid.
    average_n         : Number of consecutive files to average together per point (default 1).
    """
    # 1. Load SXM
    pix, meta = _utils.load_sxm_data(sxm_file_path, sxm_channel, direction=sxm_direction)
    if sxm_direction.lower() == "backward":
        pix = np.fliplr(pix)
    if sxm_poly_deg is not None:
        pix = _utils.remove_stripes_row_poly(pix, deg=sxm_poly_deg)

    # 2. Gather raw .dat info
    raw_dat_info = []  # (label, content)
    if dat_file_contents:
        for i, c in enumerate(dat_file_contents):
            raw_dat_info.append((f"Input_{i + 1}", c))
    if dat_folder_path:
        files = sorted([f for f in os.listdir(dat_folder_path)
                        if f.lower().endswith('.dat') and os.path.isfile(os.path.join(dat_folder_path, f))])
        for f in files:
            with open(os.path.join(dat_folder_path, f), 'r', encoding='utf-8', errors='ignore') as fin:
                raw_dat_info.append((f, fin.read()))

    if not raw_dat_info:
        raise ValueError("No .dat data found.")

    # 3. Apply Averaging
    processed_records = []  # (label, content)
    if average_n > 1:
        for i in range(0, len(raw_dat_info) - average_n + 1, average_n):
            group = raw_dat_info[i: i + average_n]
            group_contents = [g[1] for g in group]
            label = f"Avg({group[0][0]}...{group[-1][0]})"
            try:
                avg_content = _utils.average_multiple_dat_contents(group_contents, x_col=x_col)
                processed_records.append((label, avg_content))
            except Exception:
                continue
    else:
        processed_records = raw_dat_info

    # 4. Load .dat data
    data_list = []
    for i, (p_label, content) in enumerate(processed_records):
        d_meta, df = _utils.parse_instrument_data(content)

        # Apply smoothing if requested
        if smooth_window is not None and smooth_order is not None:
            for col in y_cols:
                if col in df.columns:
                    y_data = df[col].to_numpy(dtype=float)
                    mask = ~np.isnan(y_data)
                    if np.sum(mask) > smooth_window:
                        y_smooth = _utils.savitzky_golay(y_data[mask], smooth_window, smooth_order)
                        df.loc[mask, col] = y_smooth

        x_m = float(d_meta.get("X (m)", 0))
        y_m = float(d_meta.get("Y (m)", 0))
        label = p_label if average_n > 1 else f"Point {i + 1}"
        data_list.append((x_m, y_m, df, label))

    # 5. Plot
    fig = _plt.sxm_combined_plot(
        pix, meta, data_list, x_col, y_cols,
        title=title, cmap=cmap, font=font, fontsize=fontsize,
        line_styles=line_styles,
        x_min=x_min, x_max=x_max,
        y_min=y_min, y_max=y_max,
        show_legend=show_legend,
        color_by_column=color_by_column,
        use_subplots=use_subplots,
        max_subplot_rows=max_subplot_rows,
        angle_offset=angle_offset,
        figsize=figsize,
        normalize=normalize,
        norm_vmin=norm_vmin,
        norm_vmax=norm_vmax,
        pts_per_row=pts_per_row
    )
    return _result(fig, save_path)


@mcp.tool()
def analyze_twist_angle_disorder(
        dat_file_contents: Optional[List[str]] = None,
        dat_folder_path: Optional[str] = None,
        x_col: str = "BottomGate Setp. (V)",
        y_col: str = "MIM-Im_AC-X (V)",
        pts_per_row: Optional[int] = None,
        # Gate geometry (defaults: MoSe2 device, single bottom gate, 15 nm hBN)
        has_top_gate: bool = False,
        has_bottom_gate: bool = True,
        top_hbn_thickness_nm: float = 15.0,
        bottom_hbn_thickness_nm: float = 15.0,
        epsilon_hbn: float = 3.5,
        lattice_constant_angstrom: float = 3.29,
        # nu=1 search window is DERIVED from a twist-angle range (not raw voltages):
        # with CNP assumed 0, V(nu=1)=n_s(theta)*e/C, giving [V(theta_min), V(theta_max)].
        theta_min: float = 2.0,
        theta_max: float = 4.0,
        nu0_vmin: float = -0.98,
        nu0_vmax: float = 0.20,
        nu1_vmin: Optional[float] = None,   # optional explicit override of the theta-derived window
        nu1_vmax: Optional[float] = None,
        nu0_val: float = 0.0,
        nu1_val: float = 1.0,
        input_v0: Optional[Union[float, List[float]]] = None,
        cnp_top_n_nu2: Optional[int] = None,
        cnp_nu2_weight: float = 0.5,
        nu2_lo_factor: float = 1.8,
        nu2_hi_factor: float = 2.2,
        nu2_use_shoulder: bool = True,
        smooth_window: int = 15,
        hampel_k: float = 3.5,
        hampel_floor_v: float = 0.5,
        # Optional SXM
        sxm_file_path: Optional[str] = None,
        sxm_channel: str = "MIM-Im_AC-X",
        sxm_direction: str = "forward",
        sxm_poly_deg: Optional[int] = 1,
        sxm_cmap: str = "bone",
        angle_offset: float = 0.0,
        # Display
        title: str = "Twist Angle Disorder Map",
        figsize_w: float = 12.0,
        figsize_h: float = 5.0,
        cmap: str = "plasma",
        average_n: int = 2,
        # Normalization
        normalize: bool = False,
        target_val1: float = 0.0,
        target_val2: float = 1.0,
        x1_norm: Optional[float] = None,
        x2_norm: Optional[float] = None,
        show_values: bool = True,
        save_path: Optional[str] = None,
):
    """
    Characterize the twist-angle disorder from a 2D grid of gate-sweep measurements.

    Detection method (fixed; not a switchable parameter). For each measurement point:
    1. Detect significant dips on the lightly smoothed signal (shoulder-baseline depth
       gated by noise-scaled significance).
    2. ν=1 (Mott) = the strongest, sharpest dip in [nu1_vmin, nu1_vmax], with two
       corrections that make it robust on this data:
         - plateau/step override: when the Mott is a flat step on the rising edge (no
           local minimum) the dip-finder would skip it; a slope-minimum at meaningfully
           lower voltage that is more prominent is adopted instead.
         - spatial-consistency override: a lone twist-domain outlier (disagreeing with a
           strict majority of its 8 grid neighbours) adopts a dip in the neighbours'
           domain range, since twist domains are contiguous.
    3. ν=2 = deepest dip above ν=1 (confidence = shoulder-baseline depth / noise).
    4. Charge-neutrality point CNP = confidence-weighted average of the per-point
       2·V(ν=1) − V(ν=2) intercepts of the ``cnp_top_n_nu2`` best-resolved ν=2 curves.
       ``cnp_top_n_nu2`` defaults to None = auto (total points // 12, e.g. 36->3, 64->5);
       a positive int fixes the count; 0 uses the legacy conf-threshold rule. This single
       CNP anchors every point; falls back to the ν=0 dip window if no ν=2 is resolved.
       Override with `input_v0` (scalar or per-point list).
    5. Local twist from the spacing s = V(ν=1) − CNP: n_s = (C/A)·s/e, a_M =
       sqrt(2/(√3·n_s)), θ = 2·arcsin(a₀/(2·a_M)). Invalid points → 0 (shown as blank).

    Gate geometry parameters (defaults are the MoSe2 device: single bottom gate, 15 nm)
    ----------------------------------------------------------------------------------
    has_top_gate / has_bottom_gate    : Which gates are present (default: bottom only).
    top_hbn_thickness_nm              : Top hBN spacer thickness (nm).
    bottom_hbn_thickness_nm           : Bottom hBN spacer thickness (nm, default 15).
    epsilon_hbn                       : Relative permittivity of hBN (default 3.5).
    lattice_constant_angstrom         : Lattice constant in Å (default MoSe2 3.29 Å).
    theta_min / theta_max             : twist-angle range (deg, default 2-4) that SETS the
                                        ν=1 search window via V(ν=1)=n_s(θ)·e/C at CNP=0.
    nu1_vmin / nu1_vmax               : optional explicit ν=1 window override (else θ-derived).
    nu2_lo_factor / nu2_hi_factor     : ν=2 searched in [lo,hi]·V1 (default 1.8-2.2, ~2·V1).
    nu2_use_shoulder                  : also accept a flatness-gated slope-shoulder as ν=2.
    cnp_nu2_weight                    : anchor quality = conf1^(1-w)·conf2^w (default 0.5).
    cnp_top_n_nu2                     : # anchor curves (None -> auto = total points//12).
    smooth_window                     : Savitzky-Golay dip-detection window (default 15).
    nu0_vmin / nu0_vmax               : ν=0 dip window (CNP fallback only).
    input_v0                          : Optional fixed CNP (scalar or per-point list);
                                        overrides the ν=1/ν=2 CNP fit.
    average_n                         : Consecutive files averaged per point (default 2).
    normalize                         : If True, scales y_col values linearly.
    target_val1 / target_val2         : Target Y values at x1_norm / x2_norm respectively.
    x1_norm / x2_norm                 : X-axis values for normalization endpoints (None = file range).
    """
    # 1. Gather raw data
    raw_dat_info = []  # (label, content)
    if dat_file_contents:
        for i, c in enumerate(dat_file_contents):
            raw_dat_info.append((f"Input_{i + 1}", c))
    if dat_folder_path:
        files = sorted([f for f in os.listdir(dat_folder_path)
                        if f.lower().endswith('.dat') and os.path.isfile(os.path.join(dat_folder_path, f))])
        for f in files:
            with open(os.path.join(dat_folder_path, f), 'r', encoding='utf-8', errors='ignore') as fin:
                raw_dat_info.append((f, fin.read()))

    if not raw_dat_info:
        raise ValueError("No .dat data found.")

    # 2. Preprocess (Average & Normalize)
    processed_records = []  # (label, content)

    # A. Averaging
    if average_n > 1:
        for i in range(0, len(raw_dat_info) - average_n + 1, average_n):
            group = raw_dat_info[i: i + average_n]
            group_contents = [g[1] for g in group]
            label = f"Avg({group[0][0]}...{group[-1][0]})"
            try:
                avg_c = _utils.average_multiple_dat_contents(group_contents, x_col=x_col)
                processed_records.append((label, avg_c))
            except Exception:
                continue
    else:
        processed_records = raw_dat_info

    # B. Normalization
    if normalize:
        norm_records = []
        for label, content in processed_records:
            norm_c = _utils.normalize_dat_content(
                content, x_col=x_col, y_col=y_col,
                target_val1=target_val1, target_val2=target_val2,
                x1_norm=x1_norm, x2_norm=x2_norm
            )
            norm_records.append((label, norm_c))
        processed_records = norm_records

    # 3. Load processed data
    data_list = []
    for i, (label, content) in enumerate(processed_records):
        d_meta, df = _utils.parse_instrument_data(content)
        x_m = float(d_meta.get("X (m)", 0))
        y_m = float(d_meta.get("Y (m)", 0))
        # Coordinates are available: average_multiple_dat_contents preserves the
        # numeric metadata (X, Y) from the first file of each averaged group.
        data_list.append((x_m, y_m, df, label))

    # 4. Optional SXM
    sxm_pix, sxm_meta = None, None
    if sxm_file_path:
        sxm_pix, sxm_meta = _utils.load_sxm_data(sxm_file_path, sxm_channel, direction=sxm_direction)
        if sxm_direction.lower() == "backward":
            sxm_pix = np.fliplr(sxm_pix)
        if sxm_poly_deg is not None:
            sxm_pix = _utils.remove_stripes_row_poly(sxm_pix, deg=sxm_poly_deg)

    # 5. Run twist angle analysis
    fig, theta_grid, v0_grid = _plt.twist_angle_map_plot(
        data_list=data_list,
        x_col=x_col,
        y_col=y_col,
        pts_per_row=pts_per_row,
        has_top_gate=has_top_gate,
        has_bottom_gate=has_bottom_gate,
        top_hbn_thickness_nm=top_hbn_thickness_nm,
        bottom_hbn_thickness_nm=bottom_hbn_thickness_nm,
        epsilon_hbn=epsilon_hbn,
        lattice_constant_angstrom=lattice_constant_angstrom,
        nu0_range=(nu0_vmin, nu0_vmax),
        nu1_range=((nu1_vmin, nu1_vmax) if (nu1_vmin is not None and nu1_vmax is not None) else None),
        theta_min=theta_min, theta_max=theta_max, smooth_window=smooth_window,
        cnp_nu2_weight=cnp_nu2_weight, nu2_lo_factor=nu2_lo_factor,
        nu2_hi_factor=nu2_hi_factor, nu2_use_shoulder=nu2_use_shoulder,
        hampel_k=hampel_k, hampel_floor_v=hampel_floor_v,
        nu0_val=nu0_val,
        nu1_val=nu1_val,
        input_v0=input_v0,
        cnp_top_n_nu2=cnp_top_n_nu2,
        sxm_data=sxm_pix,
        sxm_meta=sxm_meta,
        sxm_cmap=sxm_cmap,
        angle_offset=angle_offset,
        title=title,
        figsize=(figsize_w, figsize_h),
        cmap=cmap,
        show_values=show_values,
    )

    return _result(fig, save_path, extra={
        "theta_array": theta_grid,
        "v0_array": v0_grid
    })


@mcp.tool()
def evaluate_fractional_gwc_points(
        dat_file_contents: Optional[List[str]] = None,
        dat_folder_path: Optional[str] = None,
        x_col: str = "BottomGate Setp. (V)",
        y_col: str = "MIM-Im_AC-X (V)",
        pts_per_row: Optional[int] = None,
        average_n: int = 2,
        # Gate geometry (MoSe2 device: single bottom gate, 15 nm hBN)
        has_top_gate: bool = False,
        has_bottom_gate: bool = True,
        top_hbn_thickness_nm: float = 15.0,
        bottom_hbn_thickness_nm: float = 15.0,
        epsilon_hbn: float = 3.5,
        lattice_constant_angstrom: float = 3.29,
        # Dip search windows / CNP
        nu0_vmin: float = -0.98,
        nu0_vmax: float = 0.20,
        theta_min: float = 2.0,
        theta_max: float = 4.0,
        smooth_window: int = 15,
        hampel_k: float = 3.5,
        hampel_floor_v: float = 0.5,
        nu1_vmin: Optional[float] = None,
        nu1_vmax: Optional[float] = None,
        input_v0: Optional[Union[float, List[float]]] = None,
        cnp_top_n_nu2: Optional[int] = None,
        # Fractional-GWC scoring
        frac_win_lo: float = 0.10,
        frac_win_hi: float = 0.90,
        w_ndips: float = 1.0,
        w_depth: float = 0.3,
        w_sharpness: float = 0.0,
        w_noise: float = 1.0,
        w_screening: float = 0.0,
        w_fillacc: float = 1.0,   # reward GWC dips landing at canonical nu = 1/3, 1/2, 2/3
        ndips_dominant: bool = True,
        max_gwc_dips: int = 3,
        frac_merge_v: float = 0.18,
        n_best: int = 3,
        # Normalization (preprocessing, identical to the other grid tools)
        normalize: bool = False,
        target_val1: float = 0.0,
        target_val2: float = 1.0,
        x1_norm: Optional[float] = None,
        x2_norm: Optional[float] = None,
        # Display
        title: str = "Fractional-GWC Measurement Score",
        figsize_w: float = 15.0,
        figsize_h: float = 6.4,
        cmap: str = "inferno",
        save_path: Optional[str] = None,
):
    """
    Rank a 2D grid of gate-sweeps by fractional generalized-Wigner-crystal (GWC) richness
    and return the best measurement positions.

    Method (same dip detection + CNP anchoring as analyze_twist_angle_disorder). Per point,
    on the filling axis nu = (V - CNP) / (V1 - CNP), it looks ONLY inside the fractional
    window nu in [frac_win_lo, frac_win_hi] -- strictly between the CNP and the nu=1 Mott,
    where the GWC states live -- and measures: the number of significance-gated dips (the
    GWC states themselves; nu=0/1/2 are excluded), their mean depth and sharpness, the
    per-curve noise, and a contact/screening proxy (mean |signal| level). Each term is
    min-max normalized across the grid and combined as
        w_ndips*N + w_depth*depth + w_sharpness*sharp - w_noise*noise - w_screening*screening.

    ndips_dominant (default True) makes the ranking strictly dip-count-first: a point with
    more GWC dips ALWAYS ranks above one with fewer, and the weighted terms only break ties
    within a dip-count group. Set False to rank by the raw weighted sum.

    CNP: pass ``input_v0`` to fix it, or ``cnp_top_n_nu2=N`` to set it from the confidence-
    weighted 2*V1-V2 intercept of the N best-resolved nu=2 curves; otherwise the method's
    global CNP is used. Returns the top ``n_best`` points (with their fractional dip
    fillings) plus a 2-panel figure (dip-count map + score map). Each best point's
    ``row``/``col`` are 1-based and match the score-map axes (row 1 = top, column 1 = left).
    """
    # 1. Gather raw data
    raw_dat_info = []
    if dat_file_contents:
        for i, c in enumerate(dat_file_contents):
            raw_dat_info.append((f"Input_{i + 1}", c))
    if dat_folder_path:
        files = sorted([f for f in os.listdir(dat_folder_path)
                        if f.lower().endswith('.dat') and os.path.isfile(os.path.join(dat_folder_path, f))])
        for f in files:
            with open(os.path.join(dat_folder_path, f), 'r', encoding='utf-8', errors='ignore') as fin:
                raw_dat_info.append((f, fin.read()))
    if not raw_dat_info:
        raise ValueError("No .dat data found.")

    # 2. Average & normalize (identical preprocessing to the other grid tools)
    if average_n > 1:
        processed_records = []
        for i in range(0, len(raw_dat_info) - average_n + 1, average_n):
            group_contents = [g[1] for g in raw_dat_info[i: i + average_n]]
            try:
                processed_records.append(("avg", _utils.average_multiple_dat_contents(group_contents, x_col=x_col)))
            except Exception:
                continue
    else:
        processed_records = raw_dat_info
    if normalize:
        processed_records = [(label, _utils.normalize_dat_content(
            content, x_col=x_col, y_col=y_col, target_val1=target_val1, target_val2=target_val2,
            x1_norm=x1_norm, x2_norm=x2_norm)) for label, content in processed_records]

    # 3. Parse into (x, y) point list
    points = []
    for label, content in processed_records:
        _meta, df = _utils.parse_instrument_data(content)
        if x_col in df.columns and y_col in df.columns:
            points.append((df[x_col].to_numpy(dtype=float), df[y_col].to_numpy(dtype=float)))
        else:
            points.append((np.zeros(1), np.zeros(1)))

    n_pts = len(points)
    if pts_per_row is None:
        pts_per_row = int(round(np.sqrt(n_pts)))

    # 4. Fractional-GWC scoring (fixed method; lives in utils)
    result = _utils.compute_fractional_gwc_score(
        points, pts_per_row,
        has_top_gate=has_top_gate, has_bottom_gate=has_bottom_gate,
        top_hbn_thickness_nm=top_hbn_thickness_nm, bottom_hbn_thickness_nm=bottom_hbn_thickness_nm,
        epsilon_hbn=epsilon_hbn, lattice_constant_angstrom=lattice_constant_angstrom,
        nu0_range=(nu0_vmin, nu0_vmax),
        nu1_range=((nu1_vmin, nu1_vmax) if (nu1_vmin is not None and nu1_vmax is not None) else None),
        theta_min=theta_min, theta_max=theta_max, smooth_window=smooth_window,
        hampel_k=hampel_k, hampel_floor_v=hampel_floor_v,
        input_v0=input_v0, cnp_top_n_nu2=cnp_top_n_nu2,
        frac_window=(frac_win_lo, frac_win_hi),
        weights=(w_ndips, w_depth, w_sharpness, w_noise, w_screening, w_fillacc),
        ndips_dominant=ndips_dominant, max_gwc_dips=max_gwc_dips, frac_merge_v=frac_merge_v,
        n_best=n_best,
    )

    fig = _plt.fractional_gwc_score_map_plot(
        result, pts_per_row, n_best=n_best, title=title,
        figsize=(figsize_w, figsize_h), cmap=cmap,
    )

    # Report grid positions 1-based so they match the score-map axis labels
    # ("row 1..N" top->bottom, column "1..M" left->right). result["best"] keeps the
    # 0-based row/col that the plot needs for marker placement, so we return a copy.
    n_grid_rows = int(max(result["rows"]) + 1) if result["rows"] else 0
    best_points = []
    for b in result["best"]:
        bd = dict(b)
        bd["row"] = int(b["row"]) + 1
        bd["col"] = int(b["col"]) + 1
        best_points.append(bd)

    return _result(fig, save_path, extra={
        "best_points": best_points,
        "grid_shape": {"rows": n_grid_rows, "cols": int(pts_per_row)},
        "position_convention": "row/col are 1-based and match the score-map axes: row 1 = top, column 1 = left.",
        "cnp_volts": float(result["cnp_global"]),
        "n_dips": [int(v) for v in result["n_dips"]],
        "score_array": [None if not np.isfinite(s) else float(s) for s in result["score"]],
    })


@mcp.tool()
def plot_averaged_ssim_map(
        folder_path: str,
        channel: str = "MIM-Im_AC-X",
        direction: str = "forward",
        poly_deg: Optional[int] = 1,
        normalize_col: Optional[int] = -1,
        x_min: Optional[float] = None,
        x_max: Optional[float] = None,
        cmap: str = "magma",
        font: str = "Arial",
        fontsize: int = 18,
        cbar_shrink: float = 0.75,
        show_title: bool = False,
        save_path: Optional[str] = None,
):
    """
    Calculate and plot the MEAN 2D SSIM similarity map for a scan series.
    Optimized for publication-quality output (600 DPI, trimmed axis).
    """
    import os
    if not os.path.isdir(folder_path):
        raise ValueError(f"'{folder_path}' is not a valid directory.")

    files = [f for f in os.listdir(folder_path) if f.lower().endswith('.sxm')]
    if len(files) < 2:
        raise ValueError("At least two .sxm files are required for SSIM calculation.")

    file_info = []
    for f in files:
        path = os.path.join(folder_path, f)
        v = _utils.get_sxm_gate_voltage(path)
        if v is not None:
            file_info.append({"path": path, "voltage": v})

    if len(file_info) < 2:
        raise ValueError("Insufficient files with valid gate voltage metadata.")

    file_info.sort(key=lambda x: x["voltage"])
    n_pairs = len(file_info) - 1

    summed_map = None
    meta_ref = None

    for i in range(n_pairs):
        f1, f2 = file_info[i], file_info[i + 1]
        pix1, m1 = _utils.load_sxm_data(f1["path"], channel, direction=direction)
        pix2, _ = _utils.load_sxm_data(f2["path"], channel, direction=direction)

        if direction.lower() == "backward":
            pix1, pix2 = np.fliplr(pix1), np.fliplr(pix2)
        if poly_deg is not None:
            pix1, pix2 = _utils.remove_stripes_row_poly(pix1, deg=poly_deg), _utils.remove_stripes_row_poly(pix2,
                                                                                                            deg=poly_deg)
        if normalize_col is not None:
            pix1 = _utils.normalize_image_rows(pix1, ref_col_idx=normalize_col)
            pix2 = _utils.normalize_image_rows(pix2, ref_col_idx=normalize_col)

        ny, nx = min(pix1.shape[0], pix2.shape[0]), min(pix1.shape[1], pix2.shape[1])
        pix1, pix2 = pix1[:ny, :nx], pix2[:ny, :nx]

        ssim_map = _utils.calculate_ssim_map(pix1, pix2)

        if summed_map is None:
            summed_map = ssim_map
            meta_ref = m1
        else:
            s_ny, s_nx = min(summed_map.shape[0], ssim_map.shape[0]), min(summed_map.shape[1], ssim_map.shape[1])
            summed_map = summed_map[:s_ny, :s_nx] + ssim_map[:s_ny, :s_nx]

    if summed_map is None:
        raise ValueError("SSIM map calculation failed.")

    # Calculate MEAN
    mean_map = summed_map / n_pairs

    fig = _plt.image_plot(
        mean_map,
        width_um=meta_ref["width"] * 1e6,
        height_um=meta_ref["height"] * 1e6,
        title="Mean SSIM Map",
        cmap=cmap,
        font=font,
        fontsize=fontsize,
        xlim=(x_min, x_max) if (x_min is not None and x_max is not None) else None,
        cbar_shrink=cbar_shrink,
        cbar_label="Averaged SSIM",
        show_title=show_title
    )
    return _result(fig, save_path)

@mcp.tool()
def show_sxm_ssim_pairs(
        folder_path: str,
        channel: str = "MIM-Im_AC-X",
        direction: str = "forward",
        poly_deg: Optional[int] = None,
        normalize_col: Optional[int] = -1,
        cmap: str = "magma",
        save_path: Optional[str] = None,
        title: str = "Pairwise SSIM Maps",
        font: str = "Arial",
        fontsize: int = 15,
):
    """
    Generate a grid of SSIM maps for each consecutive pair of SXM images in a folder.

    Parameters
    ----------
    folder_path   : Path to folder containing .sxm files.
    channel       : Channel name to analyze (e.g. 'MIM-Im_AC-X').
    direction     : 'forward' or 'backward'.
    poly_deg      : Degree of polynomial for row-wise background subtraction.
    normalize_col : Column index to normalize each row to zero (e.g. -1 for rightmost).
    cmap          : Matplotlib colormap.
    save_path     : Optional absolute path to save the resulting image.
    """
    import os
    import math
    import matplotlib.pyplot as plt
    _utils._set_global_font(font, fontsize)

    files = [os.path.join(folder_path, f) for f in os.listdir(folder_path) if f.endswith('.sxm')]
    if not files:
        raise ValueError(f"No .sxm files found in {folder_path}")

    file_info = []
    for full_path in files:
        v = _utils.get_sxm_gate_voltage(full_path)
        if v is not None:
            file_info.append({"path": full_path, "voltage": v})

    if len(file_info) < 2:
        raise ValueError("Insufficient files with valid gate voltage metadata.")

    file_info.sort(key=lambda x: x["voltage"])

    ssim_results = []
    meta_ref = None

    for i in range(len(file_info) - 1):
        f1, f2 = file_info[i], file_info[i + 1]
        pix1, meta1 = _utils.load_sxm_data(f1["path"], channel, direction=direction)
        pix2, _ = _utils.load_sxm_data(f2["path"], channel, direction=direction)

        if direction.lower() == "backward":
            pix1, pix2 = np.fliplr(pix1), np.fliplr(pix2)
        if poly_deg is not None:
            pix1, pix2 = _utils.remove_stripes_row_poly(pix1, deg=poly_deg), _utils.remove_stripes_row_poly(pix2,
                                                                                                            deg=poly_deg)

        # Row normalization
        if normalize_col is not None:
            pix1 = _utils.normalize_image_rows(pix1, ref_col_idx=normalize_col)
            pix2 = _utils.normalize_image_rows(pix2, ref_col_idx=normalize_col)

        ny, nx = min(pix1.shape[0], pix2.shape[0]), min(pix1.shape[1], pix2.shape[1])
        pix1, pix2 = pix1[:ny, :nx], pix2[:ny, :nx]

        ssim_map = _utils.calculate_ssim_map(pix1, pix2)
        ssim_results.append({
            "map": ssim_map,
            "v1": f1["voltage"],
            "v2": f2["voltage"]
        })
        if meta_ref is None:
            meta_ref = meta1

    n_pairs = len(ssim_results)
    cols = 3
    rows = math.ceil(n_pairs / cols)

    fig, axes = plt.subplots(rows, cols, figsize=(5 * cols, 4 * rows), squeeze=False)
    width_um = meta_ref["width"] * 1e6
    height_um = meta_ref["height"] * 1e6

    for idx, res in enumerate(ssim_results):
        r, c = divmod(idx, cols)
        ax = axes[r, c]
        im = ax.imshow(res["map"], extent=[0, width_um, 0, height_um], cmap=cmap, origin='lower')
        ax.set_title(f"{res['v1']:.2f}V \u2192 {res['v2']:.2f}V", fontsize=fontsize)
        cbar = fig.colorbar(im, ax=ax)
        cbar.ax.tick_params(labelsize=fontsize)
        ax.set_xlabel("X (\u03BCm)", fontsize=fontsize)
        ax.set_ylabel("Y (\u03BCm)", fontsize=fontsize)
        ax.tick_params(labelsize=fontsize)

    # Hide unused subplots
    for idx in range(n_pairs, rows * cols):
        r, c = divmod(idx, cols)
        axes[r, c].axis('off')

    fig.suptitle(title, fontsize=fontsize + 2)
    fig.tight_layout(rect=[0, 0, 1, 0.95])

    return _result(fig, save_path)


@mcp.tool()
def compare_folders_sxm_ssim(
        folder1: str,
        folder2: str,
        channel: str = "MIM-Im_AC-X",
        direction: str = "forward",
        parameter_name: str = "BottomGate",
        poly_deg: Optional[int] = 1,
        normalize_col: Optional[int] = -1,
        cmap: str = "magma",
        save_path: Optional[str] = None,
        title: str = "Cross-Folder SSIM Comparison",
        font: str = "Arial",
        fontsize: int = 15,
):
    """
    Compare SXM images between two folders by matching a parameter (e.g., Gate Voltage).
    Calculates 2D SSIM maps for each matched pair.

    Args:
        folder1, folder2: Paths to the directories to compare.
        channel: SXM channel name (default 'MIM-Im_AC-X').
        direction: Scan direction ('forward' or 'backward').
        parameter_name: Header parameter to match (default 'BottomGate').
        poly_deg: Degree of polynomial for background subtraction.
        normalize_col: Column index for row-wise normalization (default -1).
    """
    import os
    import math
    import matplotlib.pyplot as plt
    _utils._set_global_font(font, fontsize)

    def get_folder_info(folder):
        if not os.path.exists(folder):
            return {}
        files = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith('.sxm')]
        info = {}
        for f in files:
            val = _utils.get_sxm_gate_voltage(f, target_name=parameter_name)
            if val is not None:
                # Use rounded value as key to handle potential float precision issues
                key = round(val, 4)
                info[key] = f
        return info

    info1 = get_folder_info(folder1)
    info2 = get_folder_info(folder2)

    common_params = sorted(list(set(info1.keys()) & set(info2.keys())))
    if not common_params:
        raise ValueError(f"No common {parameter_name} values found between {folder1} and {folder2}.")

    ssim_results = []
    meta_ref = None

    for val in common_params:
        f1 = info1[val]
        f2 = info2[val]

        pix1, meta1 = _utils.load_sxm_data(f1, channel, direction=direction)
        pix2, _ = _utils.load_sxm_data(f2, channel, direction=direction)

        if direction.lower() == "backward":
            pix1, pix2 = np.fliplr(pix1), np.fliplr(pix2)
        if poly_deg is not None:
            pix1, pix2 = _utils.remove_stripes_row_poly(pix1, deg=poly_deg), _utils.remove_stripes_row_poly(pix2,
                                                                                                            deg=poly_deg)
        if normalize_col is not None:
            pix1 = _utils.normalize_image_rows(pix1, ref_col_idx=normalize_col)
            pix2 = _utils.normalize_image_rows(pix2, ref_col_idx=normalize_col)

        ny, nx = min(pix1.shape[0], pix2.shape[0]), min(pix1.shape[1], pix2.shape[1])
        pix1, pix2 = pix1[:ny, :nx], pix2[:ny, :nx]

        # SSIM calculation (Cross-folder at this voltage)
        ssim_map = _utils.calculate_ssim_map(pix1, pix2)

        ssim_results.append({
            "map": ssim_map,
            "param": val,
            "f1": os.path.basename(f1),
            "f2": os.path.basename(f2),
            "score": float(np.mean(ssim_map))
        })
        if meta_ref is None:
            meta_ref = meta1

    n_pairs = len(ssim_results)
    cols = 3
    rows = math.ceil(n_pairs / cols)
    width_um = meta_ref["width"] * 1e6
    height_um = meta_ref["height"] * 1e6

    # --- 1. Cross-Folder SSIM Grid Plot ---
    fig_ssim, axes_ssim = plt.subplots(rows, cols, figsize=(5 * cols, 4 * rows), squeeze=False)
    for idx, res in enumerate(ssim_results):
        r, c = divmod(idx, cols)
        ax = axes_ssim[r, c]
        im = ax.imshow(res["map"], extent=[0, width_um, 0, height_um], cmap=cmap, origin='lower', vmin=0, vmax=1)
        ax.set_title(f"{parameter_name}={res['param']}\nSSIM: {res['score']:.4f}", fontsize=fontsize)
        cbar = fig_ssim.colorbar(im, ax=ax)
        cbar.ax.tick_params(labelsize=fontsize)
        ax.set_xlabel("X (\u03BCm)", fontsize=fontsize)
        ax.set_ylabel("Y (\u03BCm)", fontsize=fontsize)
        ax.tick_params(labelsize=fontsize)

    for idx in range(n_pairs, rows * cols):
        r, c = divmod(idx, cols)
        axes_ssim[r, c].axis('off')

    fig_ssim.suptitle(f"{title} (Cross-Folder)\n{os.path.basename(folder1)} vs {os.path.basename(folder2)}",
                      fontsize=fontsize + 2)
    fig_ssim.tight_layout(rect=[0, 0, 1, 0.94])

    # Add summary table
    summary_rows = []
    for r in ssim_results:
        summary_rows.append({
            parameter_name: r["param"],
            "SSIM Score": f"{r['score']:.4f}",
            "File 1": r["f1"],
            "File 2": r["f2"]
        })

    return _result(fig_ssim, save_path)

# Create Starlette app with SSE support
def create_starlette_app(mcp_server, *, debug: bool = False) -> Starlette:
    """Create a Starlette application that can serve the provided mcp server with SSE."""
    # No trailing slash: SSE "endpoint" event tells clients to POST /messages?session_id=...
    # (LibreChat uses /messages without a slash; trailing-slash redirects break clients that do not follow 307.)
    sse = SseServerTransport("/messages")

    class _PostMessageAsgi:
        """ASGI wrapper forwarding POST /messages requests to the SSE transport."""
        __slots__ = ("_app",)

        def __init__(self, app):
            self._app = app

        async def __call__(self, scope, receive, send):
            await self._app(scope, receive, send)

    post_message_asgi = _PostMessageAsgi(sse.handle_post_message)

    # Pure ASGI (not Request + request_response): EventSourceResponse inside connect_sse
    # already completes the HTTP response. Returning a second PlainTextResponse after the
    # context exits violates ASGI and can reset the connection — LibreChat then logs
    # "SSE connection closed" / "SSE error: undefined".
    class _SseMcpAsgi:
        """ASGI endpoint bridging an SSE connection to the MCP server run loop."""
        __slots__ = ("_mcp_server", "_sse")

        def __init__(self, mcp_server, sse_transport):
            self._mcp_server = mcp_server
            self._sse = sse_transport

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return
            logger.info("SSE connection request received")
            try:
                async with self._sse.connect_sse(scope, receive, send) as (
                    read_stream,
                    write_stream,
                ):
                    logger.info("SSE connection established, running MCP server")
                    logger.info("Waiting for MCP protocol messages...")
                    try:
                        await self._mcp_server.run(
                            read_stream,
                            write_stream,
                            self._mcp_server.create_initialization_options(),
                        )
                    except Exception as run_error:
                        logger.error(f"Error during MCP server run: {run_error}")
                        import traceback

                        traceback.print_exc()
                    logger.info("MCP server run completed")
            except ValueError as e:
                logger.warning("SSE connect_sse failed (response may already be sent): %s", e)
            except Exception as e:
                logger.error(f"Error in SSE handler: {e}")
                import traceback

                traceback.print_exc()
                try:
                    await PlainTextResponse("SSE connection failed", status_code=500)(
                        scope, receive, send
                    )
                except Exception:
                    pass

    sse_mcp = _SseMcpAsgi(mcp_server, sse)

    async def root_handler(request):
        """Return a plain-text status banner for the server root ("/")."""
        return PlainTextResponse("MCP Server (mim-centering) is running. Connect to /sse for SSE endpoint.")

    app = Starlette(
        debug=debug,
        routes=[
            Route("/", endpoint=root_handler),
            Route("/sse", endpoint=sse_mcp, methods=["GET"]),
            Route("/sse/", endpoint=sse_mcp, methods=["GET"]),
            Route("/messages", endpoint=post_message_asgi, methods=["POST"]),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )
    # Same effect as FastAPI(..., redirect_slashes=False): no HTTP 307 between /path and /path/
    app.router.redirect_slashes = False
    return app


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="MCP Server for Microscopy Image Matching Centering (SSE)")
    parser.add_argument("--port", type=int, default=8000, help="Port to use for SSE mode (default: 8000)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind to for SSE mode (default: 0.0.0.0)")
    
    args = parser.parse_args()
    
    logger.info(f"Starting MCP server in SSE mode on {args.host}:{args.port}")
    # Get the internal MCP server from FastMCP
    mcp_server = mcp._mcp_server
    
    # Create the Starlette app with SSE support
    starlette_app = create_starlette_app(mcp_server, debug=True)
    
    logger.info(f"SSE endpoint available at http://{args.host}:{args.port}/sse")
    logger.info(f"Messages endpoint available at http://{args.host}:{args.port}/messages")
    uvicorn.run(starlette_app, host=args.host, port=args.port, log_level="info")
