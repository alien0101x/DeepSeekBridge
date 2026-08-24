#!/usr/bin/env python3
"""
DeepSeekBridge Status Checker
Works with ANY AI agent (OpenCode, Cursor, Cline, Continue, etc.)

Usage:
    python bridge-status.py          # Check status
    python bridge-status.py --start  # Start if not running
    python bridge-status.py --wait   # Wait until ready
"""
import sys
import urllib.request
import subprocess
import time
import os

BRIDGE_URL = "http://localhost:8084"
BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BRIDGE_DIR, "bridge.log")


def check_bridge():
    """Check if bridge is running."""
    try:
        req = urllib.request.Request(f"{BRIDGE_URL}/v1/models")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def start_bridge():
    """Start the bridge in background."""
    if check_bridge():
        return True

    subprocess.Popen(
        ["python", os.path.join(BRIDGE_DIR, "main.py")],
        stdout=open(LOG_FILE, "w"),
        stderr=subprocess.STDOUT,
        cwd=BRIDGE_DIR,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    return True


def wait_bridge(timeout=30):
    """Wait for bridge to be ready."""
    start = time.time()
    while time.time() - start < timeout:
        if check_bridge():
            return True
        time.sleep(1)
    return False


def main():
    args = sys.argv[1:]

    if "--start" in args:
        if check_bridge():
            print("DeepSeekBridge: Already running")
            return 0
        print("DeepSeekBridge: Starting...")
        start_bridge()
        if wait_bridge(15):
            print("DeepSeekBridge: Ready")
            return 0
        print("DeepSeekBridge: Failed to start")
        return 1

    if "--wait" in args:
        if wait_bridge(int(args[args.index("--wait") + 1]) if len(args) > args.index("--wait") + 1 else 30):
            print("DeepSeekBridge: Ready")
            return 0
        print("DeepSeekBridge: Timeout")
        return 1

    # Default: check status
    if check_bridge():
        print("DeepSeekBridge: Running")
        return 0
    print("DeepSeekBridge: Not running (use --start to start)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
