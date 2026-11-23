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
        self.control = (params or {}).get("control") or "auto"
        self.note: str | None = None
        self.mode = None              # last x99xx switch sent to this panel
        self._measure = None          # (vehicle, running-text timing of this panel)

    @property
    def shadow(self):
        return self.emulator if self.real else None

    def resend(self):
        super().resend()
        self.mode = None

    @staticmethod
    def _parse_code(raw) -> tuple[int, str] | None:
        s = str(raw or "").strip()
        return (int(s), s) if s and s.isdigit() else None

    def _code_telegrams(self, vehicle) -> tuple[list, bool] | None:
        """Return (telegrams, is_stop_code) if database code mode applies to the current phase."""
        self.note = None
        if self.control == "text":
            return None
        if self.control == "auto" and self.profile.geometry.rows > 8:
            # two-line panel: the page composition (heading + text, PID) is sent as text; database records
            # are single-line texts without headings -> only with control: code
            r = vehicle.engine.route
            if any(x.code for x in r.stops) or getattr(r, "dest_code", ""):
                self.note = "dvouřádek: text (stránky PID), kódy jen při ovládání „kódy“"
            return None
        from ..sim.vehicle import line_key
        eng = vehicle.engine
        if eng.message and (eng.settings.get("interior") or {}).get("message_mode") == "override":
            return None
        proj = getattr(self.emulator, "project", None)
        data = proj.data if proj is not None else {}
        stops_map = data.get("stops") or {}
        dests = data.get("destinations") or {}
        dests4 = data.get("destinations4") or {}
        at_stop_mode = (eng.settings.get("interior") or {}).get("at_stop", "line_dest")

        cur_stop = eng.route.stops[eng.pos] if eng.route.stops and 0 <= eng.pos < len(eng.route.stops) else None
        sp = self._parse_code(cur_stop.code if cur_stop else "")
        if sp is not None and (eng.phase == "driving" or (eng.phase == "at_stop" and at_stop_mode == "stop_name")):
            sk = f"{sp[0]:04d}"
            in_stops = sk in stops_map
            if self.control == "code":
                self.note = (f"zastávka kódem {sk} ✓" if in_stops
                             else f"⚠ zastávka {sk} není v databázi panelu")
                return [f"x{sk}"], True
            if self.control == "auto":
                if in_stops:
                    self.note = f"zastávka kódem {sk} ✓"
                    return [f"x{sk}"], True
                self.note = f"zastávka {sk} není v databázi → posílá se text"

        dp = self._parse_code(getattr(eng.route, "dest_code", ""))
        if dp is not None and eng.phase in ("idle", "at_stop"):
            dn, ddig = dp
            k3, k4 = f"{dn:03d}", f"{dn:04d}"
            in_d3, in_d4 = k3 in dests, k4 in dests4
            code_label = k4 if len(ddig) == 4 else k3
            if self.control == "code" or (self.control == "auto" and (in_d3 or in_d4)):
                self.note = (f"cíl kódem {code_label} ✓" if (in_d3 or in_d4)
                             else f"⚠ cíl {code_label} není v databázi panelu")
                lk = line_key(eng.route.line, vehicle.line_map) if eng.route.line else None
                if in_d4 or (len(ddig) == 4 and not in_d3):
                    tel = ([f"l{lk}", f"zC{k4}", "x9993"] if lk else [f"zC{k4}", "x9992"])
                else:
                    tel = ([f"l{lk}", f"z{k3}", "x9999"] if lk else [f"z{k3}", "x9995"])
                return tel, False
            self.note = f"cíl {code_label} není v databázi → posílá se text"
        return None

    def update(self, vehicle, now, virtual_only):
        from ..live.modes import order_for_panel
        from ..sim.vehicle import spec_telegrams
        code_hit = self._code_telegrams(vehicle)
        if code_hit is not None:
            tel, is_stop = code_hit
            if tel == self.last:
                return
            if is_stop:
                self.bus.send(*tel, virtual_only=virtual_only)
                self.mode = tel[-1]
            else:
                out, new_mode = order_for_panel(tel, self.mode)
                self.bus.send(*out, virtual_only=virtual_only)
                self.mode = new_mode
            self.last = tel
            self.error = None
            self._sent_now(now)
            self._log(vehicle, tel)
            return

        if self._measure is None or self._measure[0] is not vehicle:
            from ..sim.vehicle import make_measure
            self._measure = (vehicle, make_measure(vehicle.fonts, self.profile, vehicle.line_map))
        spec = vehicle.engine.display(self.role, now, two_line=self.profile.geometry.rows > 8, cursor=self.cursor,
                                      measure=self._measure[1])
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
        st["control"] = self.control
        if self.note:
            st["note"] = self.note
        return st
