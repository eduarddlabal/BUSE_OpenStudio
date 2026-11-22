"""A vehicle = several panels (profile + output + role), driven by a SimEngine.

Vehicle config (YAML, vozidla/<name>.yaml):
  name: Solaris 12
  panels:
    - {name: vnitřní, role: interior, profile: BS120, output: serial, port: /dev/ttyUSB0, address: 10}
    - {name: čelní,   role: front,    profile: BS210, output: esp, device: celni}
    - {name: náhled,  role: interior, profile: BS120, output: virtual}
  settings: {interior: {items: [next, time, zone], seconds: 4}}
"""
from __future__ import annotations

import threading
import time

from ..live.modes import text_payload
from ..panels.profile import profile as get_profile

ROLES = {"interior": "vnitřní", "front": "čelní", "side": "boční", "rear": "zadní"}


def line_key(line: str, line_map: dict | None = None) -> str | None:
    """Key of the line record. BS120 uses only the last two digits of l<NNN>, so lines 1-99 use
    their own number and others need a slot from the database's line_map."""
    if not line:
        return None
    if line_map and line in line_map:
        return line_map[line]
    return f"{int(line):03d}" if line.isdigit() and 0 < int(line) < 100 else None


def spec_telegrams(spec: dict, prof, fonts, line_map: dict | None = None) -> list:
    """Display spec -> IBIS telegrams for the given panel profile."""
    text = spec.get("text") or ""
    line = spec.get("line")
    lk = line_key(line, line_map) if line else None
    if prof.free_text:
        if line and lk is None:              # line not in the database -> put it into the text
            text, lk = f"{line}  {text}", None
            line = None
        payload, _ = text_payload(fonts, text, spec.get("font"), spec.get("spacing"))
        g = prof.geometry
        room = g.dest_width if (line and lk) else g.columns
        if spec.get("run") or (spec.get("auto_run", True) and _width(payload, fonts) > room):
            payload = _with_run(payload)
        if spec.get("heading") and g.rows > 8:
            # two-line panel: heading on the upper line, text below (LF separator – NOT verified on hardware)
            head, _ = text_payload(fonts, spec["heading"], spec.get("heading_font"), spec.get("spacing"))
            payload = head + b"\n" + payload
        # The real BS120 shows the previously stored text right after a mode switch and takes
        # the new one only with the next v -> send v again after the switch (verified issue).
        v = b"v" + payload
        if line and lk:
            return [f"l{lk}", prof.mode("line_text") or "x9997", v]
        return [prof.mode("text") or "x9996", v]
    # panels without free text: only the line number from the database
    return [f"l{lk or '000'}"]


def _width(payload: bytes, fonts) -> int:
    from ..core.render import text_bitmap
    cols, _inv, _info = text_bitmap(payload + bytes([13]), fonts)
    return len(cols)


def _with_run(payload: bytes) -> bytes:
    """Insert ESC t 11h after the font/spacing prefix and VT at the end (as gBUSE does)."""
    i = 0
    while i < len(payload) and (0xE0 <= payload[i] <= 0xEF or 0xB0 <= payload[i] <= 0xBF):
        i += 1
    return payload[:i] + bytes([0x1B, 0x74, 0x11]) + payload[i:] + bytes([0x0B])


def make_measure(fonts, prof):
    """Seconds a page needs so that a running text scrolls through once (0 = fits, static)."""
    g = prof.geometry

    def measure(text, font, spacing, two_line=False, run=False):
        try:
            payload, _ = text_payload(fonts, text, font, spacing)
        except Exception:      # noqa: BLE001
            return 0
        w = _width(payload, fonts)
        room = g.columns
        if w <= room and not run:
            return 0
        return (w + room) * g.scroll_ms / 1000 + 0.5
    return measure


class PanelSlot:
    def __init__(self, name, role, prof, output, shadow=None):
        self.name, self.role, self.profile, self.output = name, role, prof, output
        self.shadow = shadow          # VirtualPanel mirroring a real panel (preview in the UI)
        self.mode = None              # last x99xx switch sent to this panel
        self.last = None
        self.error = None

    def status(self) -> dict:
        st = {"name": self.name, "role": self.role, "profile": self.profile.name, **self.output.status()}
        st["error"] = self.error or st.get("error")
        return st


def make_output(cfg: dict, project=None):
    from ..outputs.serial_out import SerialOutput
    from ..outputs.virtual import VirtualPanel
    from ..outputs.ws_out import WebSocketOutput
    kind = cfg.get("output", "virtual")
    name = cfg.get("name", kind)
    if kind == "serial":
        from ..loader.device import default_port
        return SerialOutput(name, cfg.get("port") or default_port(), int(cfg.get("address", 10)))
    if kind == "esp":
        return WebSocketOutput(name, cfg.get("device", name))
    if kind == "lcd":                     # add-on: LCD web page (busepanel/extras/lcd)
        from ..extras.lcd.output import DEFAULT_PORT, LcdOutput
        return LcdOutput(name, int(cfg.get("http_port") or DEFAULT_PORT), cfg.get("host") or "127.0.0.1")
    return VirtualPanel(name, project, get_profile(cfg.get("profile")))


class Vehicle:
    def __init__(self, engine, fonts, panels: list[PanelSlot], log=None, line_map: dict | None = None):
        self.engine, self.fonts, self.panels = engine, fonts, panels
        self.line_map = line_map or {}
        self.log = log or (lambda m: None)
        self.running = False
        self.thread = None

    @staticmethod
    def from_config(cfg: dict, engine, project, log=None) -> "Vehicle":
        slots = []
        for p in cfg.get("panels") or [{"name": "náhled", "role": "interior", "output": "virtual"}]:
            prof = get_profile(p.get("profile"))
            out = make_output(p, project)
            shadow = None
            if out.kind not in ("virtual", "lcd"):
                from ..outputs.virtual import VirtualPanel
                shadow = VirtualPanel(out.name + " (náhled)", project, prof)
            slots.append(PanelSlot(p.get("name", p.get("role", "panel")), p.get("role", "interior"), prof,
                                   out, shadow))
        for s in slots:
            s.output.fonts = project.fonts
        veh = Vehicle(engine, project.fonts, slots, log, line_map=project.data.get("line_map") or {})
        ref = next((s.profile for s in slots if s.role == "interior"), slots[0].profile if slots else None)
        if ref is not None:
            engine.measure = make_measure(project.fonts, ref)
        return veh

    def tick(self, now: float | None = None, virtual_only: bool = False):
        """Send changed content to the panels. virtual_only=True refreshes only the virtual
        previews (used while the simulation is stopped – real panels must stay untouched)."""
        for slot in self.panels:
            if virtual_only and slot.output.kind != "virtual":
                continue
            if slot.output.kind == "lcd":            # add-on LCD: the web page reads the state itself
                continue
            try:
                spec = self.engine.display(slot.role, now, two_line=slot.profile.geometry.rows > 8)
                tel = spec_telegrams(spec, slot.profile, self.fonts, self.line_map)
                if tel != slot.last:
                    from ..live.modes import order_for_panel
                    out, new_mode = order_for_panel(tel, slot.mode)
                    slot.output.send(*out)
                    slot.mode = new_mode
                    if slot.shadow is not None:
                        slot.shadow.send(*out)
                    slot.last = tel
                    slot.error = None
                    from ..outputs.base import tel_text
                    self.log(f"{slot.name}: " + "  ".join(tel_text(t, self.fonts) for t in tel))
            except Exception as e:      # noqa: BLE001 – keep the other panels running
                slot.error = str(e)
                slot.last = None

    def resend(self):
        for s in self.panels:
            s.last = None
            s.mode = None

    def run(self, period: float = 0.5):
        self.running = True
        while self.running:
            self.tick()
            time.sleep(period)

    def start(self):
        if not self.running:
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def stop(self):
        self.running = False
