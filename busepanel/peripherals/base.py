"""IBIS bus and the common peripheral interface.

A vehicle has one or more buses. A bus is a real connection (serial IBIS converter, ESP gateway) or a
purely virtual one. Every peripheral sits on a bus; what any peripheral driver sends goes to the real
connection once and is heard by the emulators of ALL peripherals on that bus – as on a real IBIS line,
where line / destination telegrams are broadcast and each device picks what it understands.
"""
from __future__ import annotations

import time


class Bus:
    def __init__(self, name: str, output=None):
        self.name = name
        self.output = output if output is not None and getattr(output, "kind", "virtual") != "virtual" else None
        self.devices: list = []           # emulators (VirtualPanel, ExteriorPanel …) listening on the bus
        self.state: dict = {}             # last telegram per kind put on the bus by an exterior driver
        if output is not None and self.output is None:
            self.devices.append(output)   # a virtual output is itself an emulator

    @property
    def real(self) -> bool:
        return self.output is not None

    def attach(self, device):
        if device is not None and device not in self.devices:
            self.devices.append(device)

    def reset(self):
        self.state = {}

    def send(self, *telegrams, virtual_only: bool = False):
        if self.output is not None and not virtual_only:
            self.output.send(*telegrams)
        for d in self.devices:
            d.send(*telegrams)

    def status(self) -> dict:
        st = {"name": self.name, "real": self.real, "devices": len(self.devices)}
        if self.output is not None:
            st.update(self.output.status())
        return st


class Peripheral:
    """One device of the vehicle. Subclasses: interior LED (BS120), exterior LED (BS210), LCD."""
    kind = "base"
    has_bus = True

    def __init__(self, name: str, role: str, bus: Bus | None = None, emulator=None, params: dict | None = None):
        self.name, self.role, self.bus, self.emulator = name, role, bus, emulator
        self.params = params or {}
        self.last = None
        self.error = None
        self.cursor = {}                  # page pacing (SimEngine._pick)
        if bus is not None and emulator is not None:
            bus.attach(emulator)

    # legacy name used by the API / LCD add-on: the real output, else the emulator
    @property
    def output(self):
        if self.bus is not None and self.bus.output is not None:
            return self.bus.output
        return self.emulator

    @property
    def real(self) -> bool:
        return bool(self.bus and self.bus.real)

    def update(self, vehicle, now: float | None, virtual_only: bool):
        """Compute what this peripheral should show and send what changed."""

    def resend(self):
        self.last = None
        self.cursor.clear()

    def frame(self, now: float | None = None) -> dict | None:
        em = self.emulator
        if em is None or not hasattr(em, "frame"):
            return None
        if self.real and not getattr(em, "sent", 0):
            return None
        fr = em.frame(now) if self.kind == "exterior" else em.frame()
        if self.real:
            fr["mirror"] = True
        return fr

    def status(self) -> dict:
        out = self.output
        st = {"name": self.name, "role": self.role, "type": self.kind}
        if out is not None:
            st.update(out.status())
        st["name"] = self.name
        if self.bus is not None:
            st["bus"] = self.bus.name
        st["error"] = self.error or st.get("error")
        return st

    def _log(self, vehicle, tel):
        from ..outputs.base import tel_text
        vehicle.log(f"{self.name}: " + "  ".join(tel_text(t, vehicle.fonts) for t in tel))

    def _sent_now(self, now):
        if now is None:
            self.cursor["t"] = time.time()          # the page is shown from now on
