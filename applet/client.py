#!/usr/bin/env python3
"""
applet/client.py — Reference client for the OneDrive FUSE daemon control socket.

Demonstrates the full API contract. Use this as the starting point for
writing a COSMIC applet (Rust), a pystray tray icon, or any other frontend.

Usage:
    python applet/client.py               # stream live status updates
    python applet/client.py status        # print current status and exit
    python applet/client.py sync_now      # trigger immediate metadata sync
    python applet/client.py pause         # pause the metadata poller
    python applet/client.py resume        # resume the metadata poller
"""

import json
import socket
import sys
import os

SOCKET_PATH = os.path.expanduser("~/.py-onedrive/control.sock")


def connect() -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(SOCKET_PATH)
    except FileNotFoundError:
        print("error: daemon is not running (socket not found)", file=sys.stderr)
        sys.exit(1)
    except ConnectionRefusedError:
        print("error: daemon socket exists but is not accepting connections", file=sys.stderr)
        sys.exit(1)
    return sock


def send_cmd(sock: socket.socket, action: str) -> dict:
    # Drain the initial status push the server sends on every new connection,
    # then send our command and wait for the matching response.
    buf = b""
    buf = _drain_initial_push(sock, buf)
    msg = json.dumps({"type": "cmd", "action": action}) + "\n"
    sock.sendall(msg.encode())
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("type") == "response" and obj.get("action") == action:
                return obj
    return {}


def _drain_initial_push(sock: socket.socket, buf: bytes) -> bytes:
    """Read the status line the server sends immediately on connect."""
    sock.setblocking(False)
    import select
    while True:
        ready, _, _ = select.select([sock], [], [], 0.5)
        if not ready:
            break
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
        if b"\n" in buf:
            break
    sock.setblocking(True)
    # Consume the first complete line (the initial status push) and discard it
    if b"\n" in buf:
        _, buf = buf.split(b"\n", 1)
    return buf


def print_status(obj: dict):
    state = obj.get("state", "?")
    mounted = obj.get("mounted", False)
    pending = obj.get("pending", 0)
    last_sync = obj.get("last_sync", "never")
    error = obj.get("error", "")

    icon = {
        "idle":        "✓",
        "uploading":   "↑",
        "downloading": "↓",
        "syncing":     "↻",
        "paused":      "⏸",
        "error":       "✗",
    }.get(state, "?")

    mounted_str = "mounted" if mounted else "unmounted"
    line = f"{icon} {state:<12} [{mounted_str}]  pending={pending}  last_sync={last_sync}"
    if error:
        line += f"  error={error!r}"
    print(line)


def stream(sock: socket.socket):
    """Print every status update until Ctrl+C."""
    buf = b""
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                print("connection closed by daemon")
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if obj.get("type") == "status":
                    print_status(obj)
    except KeyboardInterrupt:
        pass


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "stream"

    sock = connect()

    if action == "stream":
        stream(sock)

    elif action in ("status", "sync_now", "pause", "resume"):
        resp = send_cmd(sock, action)
        if action == "status":
            print_status(resp)
        else:
            ok = resp.get("ok", False)
            err = resp.get("error", "")
            print(f"{'ok' if ok else 'error'}" + (f": {err}" if err else ""))

    else:
        print(f"unknown action: {action}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    sock.close()


if __name__ == "__main__":
    main()
