#!/usr/bin/env python3
"""
Start the MCP Server with SSE support
This script starts the SSE MCP server and handles any startup issues
"""

import asyncio
import sys
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def check_requirements():
    """Check if all requirements are met"""
    print("🔍 Checking requirements...")
    
    # Resolve paths relative to this file so the check does not depend on
    # the working directory. requirements.txt lives at the repository root,
    # one level above mcpServer/, so it is checked there (and here, for
    # installs that copy it alongside) and only produces a warning: a
    # missing requirements file should not block an already-installed server.
    here = Path(__file__).resolve().parent
    required_files = [
        "mcp_server_sse.py",
        "centering_controller.py",
        "session_manager.py",
        "particle_filter.py",
    ]
    
    missing_files = [f for f in required_files if not (here / f).exists()]
    
    if missing_files:
        print(f"❌ Missing required files: {missing_files}")
        return False
    
    req_candidates = [here.parent / "requirements.txt", here / "requirements.txt"]
    if not any(p.exists() for p in req_candidates):
        print("⚠️  requirements.txt not found at the repository root; "
              "continuing (install dependencies with: pip install -r requirements.txt from the repo root)")
    
    # Check for MCP SDK, Starlette and uvicorn
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.server.sse import SseServerTransport
        import starlette
        import uvicorn
        print("✅ MCP SDK, Starlette and uvicorn found")
    except ImportError as e:
        print(f"⚠️  Required packages not found: {e}")
        print("Install with: pip install mcp starlette uvicorn")
        return False
    
    print("✅ All required files found")
    return True

def create_session_directory():
    """Create sessions directory if it doesn't exist"""
    sessions_dir = Path("sessions")
    sessions_dir.mkdir(exist_ok=True)
    print(f"✅ Sessions directory ready: {sessions_dir.absolute()}")

async def start_sse_server(port: int = 8000):
    """Start the SSE MCP server"""
    try:
        print("🚀 Starting MCP Server with SSE support (FastMCP)...")
        print("=" * 50)
        
        import uvicorn
        from mcp_server_sse import mcp, create_starlette_app
        
        print("✅ SSE MCP Server created successfully")
        
        # Display server info. Tool categories are summarized here; the full,
        # authoritative catalog lives in README.md (kept in sync there).
        try:
            n_tools = len(getattr(mcp, "_tool_manager")._tools)
            tool_count = str(n_tools)
        except Exception:
            tool_count = "45"
        print(f"📋 Server Name: mim-centering")
        print(f"📋 Server Version: 1.0.0")
        print(f"📋 Protocol: MCP over SSE (Server-Sent Events)")
        print(f"📋 Registered Tools: {tool_count}")
        print("   • Centering session  : start/status/execute_movement/results")
        print("   • Hardware control   : move_tip, approach_and_lift, scan, gate ramps,")
        print("                          sweepers, scan-frame & XY positioning, retract")
        print("   • Image acquisition  : get_sxm_image, convert_sxm_to_png, run_gp_regression")
        print("   • Particle filter    : load_template, predict_position, calc_movement,")
        print("                          plot_predicted_positions")
        print("   • Data analysis      : correlation, twist-angle, SSIM, dynamics, GWC")
        print("   See README.md for the full tool reference.")
        
        print(f"\n🌐 Server will start on:")
        print(f"   Root: http://localhost:{port}/")
        print(f"   SSE Endpoint: http://localhost:{port}/sse")
        print(f"   Messages: http://localhost:{port}/messages")
        print(f"\n📝 For LibreChat, configure:")
        print(f"   url: http://localhost:{port}/sse")
        
        print("\n🛑 Press Ctrl+C to stop the server")
        print("=" * 50)
        
        # Get the internal MCP server from FastMCP
        mcp_server = mcp._mcp_server
        
        # Create the Starlette app with SSE support
        starlette_app = create_starlette_app(mcp_server, debug=True)
        
        # Start the server
        config = uvicorn.Config(starlette_app, host="0.0.0.0", port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
        
    except KeyboardInterrupt:
        print("\n🛑 Server stopped by user")
    except Exception as e:
        print(f"❌ Server failed to start: {e}")
        logger.error(f"Server startup error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

async def main():
    """Main function"""
    print("🎯 MCP Server for Microscopy Image Matching Centering (SSE)")
    print("=" * 60)
    
    # Check requirements
    if not check_requirements():
        print("❌ Requirements check failed")
        sys.exit(1)
    
    # Create session directory
    create_session_directory()
    
    # Get port from command line or use default
    port = 8000
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print(f"⚠️  Invalid port number: {sys.argv[1]}. Using default port 8000")
    
    # Start the server
    success = await start_sse_server(port)
    
    if success:
        print("✅ Server shutdown completed")
    else:
        print("❌ Server shutdown with errors")
        sys.exit(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Server stopped")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        sys.exit(1)
