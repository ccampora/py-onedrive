"""
control.py — Unix socket control server for the OneDrive FUSE daemon.

The daemon runs a ControlServer trio task that:
  - Accepts connections from any local client (applet, CLI, scripts)
  - Pushes a status JSON line to every connected client on every state change
  - Accepts command JSON lines from clients and responds

Protocol (newline-delimited JSON):

  Status push  (daemon → client, on every state change and on connect):
    {"type":"status","state":"idle","pending":0,"last_sync":"2026-…Z","mounted":true,"error":""}

  Command      (client → daemon):
    {"type":"cmd","action":"sync_now"}
    {"type":"cmd","action":"pause"}
    {"type":"cmd","action":"resume"}
    {"type":"cmd","action":"status"}

  Response     (daemon → client, one per command):
    {"type":"response","action":"sync_now","ok":true}
    {"type":"response","action":"pause","ok":true,"error":""}

Socket path: ~/.py-onedrive/control.sock  (CONTROL_SOCKET in Globals.py)
"""

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Literal

import trio

from Globals import CONTROL_SOCKET, LOGGER as logger


# ---------------------------------------------------------------------------
# Status model
# ---------------------------------------------------------------------------

State = Literal["idle", "uploading", "downloading", "syncing", "paused", "error"]


@dataclass
class DaemonStatus:
    mounted: bool = False
    state: State = "idle"
    pending: int = 0            # files currently uploading or downloading
    last_sync: str = ""         # ISO-8601 UTC timestamp of last successful poll
    error: str = ""             # last error message, empty when healthy

    def to_json(self) -> str:
        d = asdict(self)
        d["type"] = "status"
        return json.dumps(d)


# ---------------------------------------------------------------------------
# Control server
# ---------------------------------------------------------------------------

class ControlServer:
    """
    Trio task that listens on a Unix domain socket and brokers status/commands
    between the FUSE daemon and any connected clients (applets, CLI tools).

    Usage:
        server = ControlServer()
        # wire into OneDriveFUSE so it can call server.update_state(...)
        async with trio.open_nursery() as nursery:
            nursery.start_soon(server.run)
            nursery.start_soon(pyfuse3.main)
            ...
    """

    def __init__(self):
        self.status = DaemonStatus()
        self._clients: set[trio.SocketStream] = set()
        self._send_channel, self._recv_channel = trio.open_memory_channel(64)
        # Callback the FUSE driver sets to trigger an immediate poll
        self.on_sync_now = None
        self.on_pause = None
        self.on_resume = None

    # ------------------------------------------------------------------
    # Public API — called by the FUSE driver to update state
    # ------------------------------------------------------------------

    def set_mounted(self, mounted: bool):
        self.status.mounted = mounted
        self._enqueue_broadcast()

    def set_state(self, state: State, error: str = ""):
        self.status.state = state
        self.status.error = error
        self._enqueue_broadcast()

    def inc_pending(self):
        self.status.pending += 1
        if self.status.state not in ("paused",):
            self.status.state = "uploading"
        self._enqueue_broadcast()

    def dec_pending(self):
        self.status.pending = max(0, self.status.pending - 1)
        if self.status.pending == 0 and self.status.state == "uploading":
            self.status.state = "idle"
        self._enqueue_broadcast()

    def set_last_sync(self, timestamp: str):
        self.status.last_sync = timestamp
        self._enqueue_broadcast()

    def set_error(self, message: str):
        self.status.state = "error"
        self.status.error = message
        self._enqueue_broadcast()

    def clear_error(self):
        if self.status.state == "error":
            self.status.state = "idle"
            self.status.error = ""
            self._enqueue_broadcast()

    # ------------------------------------------------------------------
    # Trio entry point
    # ------------------------------------------------------------------

    async def run(self):
        """Main trio task. Cleans up the socket file on exit."""
        _remove_socket()
        try:
            async with await trio.open_unix_listener(CONTROL_SOCKET) as listener:
                logger.info(f"Control socket: {CONTROL_SOCKET}")
                async with trio.open_nursery() as nursery:
                    nursery.start_soon(self._broadcaster)
                    nursery.start_soon(listener.serve, self._handle_client)
        finally:
            _remove_socket()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _enqueue_broadcast(self):
        """Non-blocking: queue a status broadcast to all clients."""
        try:
            self._send_channel.send_nowait(self.status.to_json())
        except trio.WouldBlock:
            pass  # channel full — drop; clients will get next update

    async def _broadcaster(self):
        """Read from the memory channel and fan-out to every connected client."""
        async with self._recv_channel:
            async for message in self._recv_channel:
                dead = set()
                for client in list(self._clients):
                    try:
                        await client.send_all((message + "\n").encode())
                    except Exception:
                        dead.add(client)
                self._clients -= dead

    async def _handle_client(self, stream: trio.SocketStream):
        """Serve a single connected client."""
        self._clients.add(stream)
        logger.debug("Control client connected")
        try:
            # Send current status immediately on connect
            await stream.send_all((self.status.to_json() + "\n").encode())
            # Read commands
            buf = b""
            while True:
                chunk = await stream.receive_some(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    await self._dispatch(stream, line.strip())
        except Exception:
            pass
        finally:
            self._clients.discard(stream)
            logger.debug("Control client disconnected")

    async def _dispatch(self, stream: trio.SocketStream, line: bytes):
        """Parse and execute a command from a client."""
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            await _send(stream, {"type": "response", "ok": False, "error": "invalid JSON"})
            return

        if msg.get("type") != "cmd":
            return

        action = msg.get("action", "")

        if action == "status":
            await _send(stream, json.loads(self.status.to_json()))

        elif action == "sync_now":
            if self.on_sync_now:
                self.on_sync_now()
            await _send(stream, {"type": "response", "action": action, "ok": True})

        elif action == "pause":
            if self.on_pause:
                self.on_pause()
            self.set_state("paused")
            await _send(stream, {"type": "response", "action": action, "ok": True})

        elif action == "resume":
            if self.on_resume:
                self.on_resume()
            self.set_state("idle")
            await _send(stream, {"type": "response", "action": action, "ok": True})

        else:
            await _send(stream, {"type": "response", "action": action, "ok": False,
                                 "error": f"unknown action: {action}"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _send(stream: trio.SocketStream, obj: dict):
    try:
        await stream.send_all((json.dumps(obj) + "\n").encode())
    except Exception:
        pass


def _remove_socket():
    try:
        os.unlink(CONTROL_SOCKET)
    except FileNotFoundError:
        pass
