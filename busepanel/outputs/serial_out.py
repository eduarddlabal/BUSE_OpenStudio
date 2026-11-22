"""Local serial port (IBIS converter) output."""
from __future__ import annotations

import threading

from .base import Output

_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()


def port_lock(port: str) -> threading.Lock:
    """One lock per serial port – shared by live modes, simulation and the loader."""
    with _GUARD:
        return _LOCKS.setdefault(port.upper(), threading.Lock())


class SerialOutput(Output):
    kind = "serial"

    def __init__(self, name: str, port: str, address: int = 10, pause: float = 0.2):
        super().__init__(name)
        self.port, self.address, self.pause = port, address, pause

    def send(self, *telegrams):
        from ..loader.device import Panel
        with port_lock(self.port):
            try:
                Panel(self.port, self.address).send(*telegrams, pause=self.pause)
                self.sent += len(telegrams)
                self.last_error = None
            except Exception as e:      # noqa: BLE001
                self.last_error = str(e)
                raise

    def status(self) -> dict:
        return {**super().status(), "port": self.port, "address": self.address}
