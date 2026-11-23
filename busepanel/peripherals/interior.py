"""Interior LED panel with free text (BS120 measured, BS220 estimated) – the existing simulation driver.

The telegrams are exactly those of BUSE Studio 0.2 (v + x9996 / x9997, mode switch first, never the
same switch twice) – verified on a real BS120, so this module must not change what is sent.
"""
from __future__ import annotations

from .base import Bus, Peripheral


class PanelSlot(Peripheral):
    kind = "bs120"

    def __init__(self, name, role, prof, output=None, shadow=None, bus: Bus | None = None, params=None):
        """Legacy form PanelSlot(name, role, profile, output[, shadow]) gets a private bus for `output`;
        new form passes bus= (shared) and shadow= (the emulator)."""
        if bus is None:
            bus = Bus(name, output)
            if bus.real and shadow is None:
                from ..outputs.virtual import VirtualPanel
                shadow = VirtualPanel(name + " (náhled)", None, prof)
        emulator = shadow if shadow is not None else (bus.devices[0] if bus.devices else None)
        super().__init__(name, role, bus, emulator, params)
        self.profile = prof
        self.mode = None              # last x99xx switch sent to this panel

    @property
    def shadow(self):
        return self.emulator if self.real else None

    def resend(self):
        super().resend()
        self.mode = None

    def update(self, vehicle, now, virtual_only):
        from ..live.modes import order_for_panel
        from ..sim.vehicle import spec_telegrams
        spec = vehicle.engine.display(self.role, now, two_line=self.profile.geometry.rows > 8, cursor=self.cursor)
        tel = spec_telegrams(spec, self.profile, vehicle.fonts, vehicle.line_map)
        if tel == self.last:
            return
        out, new_mode = order_for_panel(tel, self.mode)
        self.bus.send(*out, virtual_only=virtual_only)
        self.mode = new_mode
        self.last = tel
        self.error = None
        self._sent_now(now)
        self._log(vehicle, tel)

    def frame(self, now=None):
        fr = super().frame(now)
        if fr is not None and "geometry" not in fr:
            fr["geometry"] = self.profile.geometry.__dict__
        return fr

    def status(self):
        st = super().status()
        st["profile"] = self.profile.name
        return st
