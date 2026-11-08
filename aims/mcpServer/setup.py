#!/usr/bin/env python3
"""
Setup script for MCP Server
Automatically installs dependencies and tests the server
"""

import subprocess
import sys
import os
from pathlib import Path

def run_command(command, description):
    """Run a command and handle errors"""
    print(f"\n🔄 {description}...")
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(f"✅ {description} completed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ {description} failed:")
        print(f"Error: {e.stderr}")
        return False

def check_python_version():
    """Check if Python version is compatible"""
    print("🐍 Checking Python version...")
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 8):
        print(f"❌ Python 3.8+ required. Current version: {version.major}.{version.minor}")
        return False
    print(f"✅ Python {version.major}.{version.minor}.{version.micro} is compatible")
    return True

def install_dependencies():
    """Install required dependencies"""
    # requirements.txt lives at the repository root, one level above mcpServer/.
    req = Path(__file__).resolve().parent.parent / "requirements.txt"
    if not req.exists():
        req = Path(__file__).resolve().parent / "requirements.txt"
    return run_command(f'"{sys.executable}" -m pip install -r "{req}"', "Installing dependencies")

def test_server():
    """Test the MCP server (optional - server can be tested manually)"""
    print("⚠️  Skipping automated server test (no test suite available)")
    print("   You can test the server by starting it and connecting a client")
    return True

def create_directories():
    """Create necessary directories"""
    print("\n📁 Creating directories...")
    directories = ["sessions", "temp"]
    for directory in directories:
        Path(directory).mkdir(exist_ok=True)
        print(f"✅ Created directory: {directory}")

def main():
    """Main setup function"""
    print("🚀 MCP Server Setup")
    print("=" * 50)
    
    # Check Python version
    if not check_python_version():
        sys.exit(1)
    
    # Create directories
    create_directories()
    
    # Install dependencies
    if not install_dependencies():
        print("\n❌ Setup failed at dependency installation")
        sys.exit(1)
    
    # Test server
    if not test_server():
        print("\n❌ Setup failed at server testing")
        sys.exit(1)
    
    print("\n🎉 Setup completed successfully!")
    print("\n📋 Next steps:")
    print("1. Run 'python start_server_sse.py' to start the SSE server")
    print("2. Connect MCP client to use the centering tools")
    print("3. Check 'sessions/' folder for session data")
    
    print("\n📖 For more information, see README.md")

if __name__ == "__main__":
    main()


