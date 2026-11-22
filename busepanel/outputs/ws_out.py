"""ESP over WebSocket output. Protocol: docs/esp-protokol.md

The ESP connects to ws://<server>/ws/device/<id>. The server sends
    {"type": "ibis", "data": "<hex of the complete IBIS telegram incl. CR + checksum>", "text": "<readable>"}
and the ESP writes the bytes 1:1 to its UART (1200 Bd 7E2, DTR low). The ESP may send
    {"type": "hello", "id": "...", "fw": "..."}  and  {"type": "rx", "data": "<hex>"} (panel answers).
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

from ..core import ibis
from .base import Output, tel_text


class DeviceHub:
    """Registry of connected ESP devices (lives in the FastAPI event loop)."""

    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.devices: dict[str, dict] = {}     # id -> {"ws", "hello", "since", "rx"}
        self.lock = threading.Lock()

    async def attach(self, dev_id: str, ws):
        self.loop = asyncio.get_running_loop()
        with self.lock:
            self.devices[dev_id] = {"ws": ws, "hello": None, "since": time.time(), "rx": []}

    def detach(self, dev_id: str, ws):
        with self.lock:
            d = self.devices.get(dev_id)
            if d and d["ws"] is ws:
                del self.devices[dev_id]

    def on_message(self, dev_id: str, msg: dict):
        d = self.devices.get(dev_id)
        if not d:
            return
        if msg.get("type") == "hello":
            d["hello"] = msg
        elif msg.get("type") == "rx":
            d["rx"] = (d["rx"] + [msg.get("data", "")])[-20:]

    def list(self) -> list[dict]:
        return [{"id": k, "hello": v["hello"], "since": v["since"]} for k, v in self.devices.items()]

    def send(self, dev_id: str, payloads: list[dict], timeout: float = 3.0):
        d = self.devices.get(dev_id)
        if not d or not self.loop:
            raise ConnectionError(f"zařízení {dev_id!r} není připojené")
        ws = d["ws"]

        async def go():
            for p in payloads:
                await ws.send_text(json.dumps(p))
        asyncio.run_coroutine_threadsafe(go(), self.loop).result(timeout)


HUB = DeviceHub()


def payload(t, fonts=None) -> dict:
    return {"type": "ibis", "data": ibis.encode(t).hex(), "text": tel_text(t, fonts)}


class WebSocketOutput(Output):
    kind = "esp"

    def __init__(self, name: str, device_id: str, hub: DeviceHub = HUB):
        super().__init__(name)
        self.device_id, self.hub = device_id, hub

    def send(self, *telegrams):
        try:
            self.hub.send(self.device_id, [payload(t, self.fonts) for t in telegrams])
            self.sent += len(telegrams)
            self.last_error = None
        except Exception as e:      # noqa: BLE001
            self.last_error = str(e)
            raise

    def status(self) -> dict:
        return {**super().status(), "device": self.device_id,
                "connected": self.device_id in self.hub.devices}
