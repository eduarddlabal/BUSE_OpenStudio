"""A vehicle = buses + peripherals (busepanel/peripherals), driven by a SimEngine.

Vehicle config (YAML, vozidla/<name>.yaml) – see busepanel/peripherals/__init__.py:
  name: Solaris 12
  buses: {ibis: {output: serial, port: /dev/ttyUSB0, address: 10}}
  panels:
    - {name: vnitřní, role: interior, type: BS120, bus: ibis}
    - {name: čelní,   role: front,    type: BS210, variant: v1, bus: ibis}
    - {name: náhled,  role: interior, profile: BS120, output: virtual}     # old form: own output
  settings: {interior: {items: [next, time, zone], seconds: 4}}
"""
from __future__ import annotations

import threading
import time

from ..live.modes import text_payload
from ..panels.profile import profile as get_profile
from ..peripherals.interior import PanelSlot  # noqa: F401  (re-export, legacy API)

ROLES = {"interior": "vnitřní", "front": "čelní", "side": "boční", "rear": "zadní"}


def line_key(line: str, line_map: dict | None = None) -> str | None:
    """Key of the line record. BS120 uses only the last two digits of l<NNN>, so lines 1-99 use
    their own number and others need a slot from the database's line_map."""
    if not line:
        return None
    if line_map and line in line_map:
        return line_map[line]
    return f"{int(line):03d}" if line.isdigit() and 0 < int(line) < 100 else None


def free_text_layout(spec: dict, prof, fonts, line_map: dict | None = None):
    """Free text of a page as the panel gets it -> (payload, line key or None, room, width, runs).
    room = columns the text runs through (destination field next to the line number, else the whole panel)."""
    text = spec.get("text") or ""
    line = spec.get("line")
    lk = line_key(line, line_map) if line else None
    if line and lk is None:              # line not in the database -> put it into the text
        text, lk = f"{line}  {text}", None
        line = None
    payload, _ = text_payload(fonts, text, spec.get("font"), spec.get("spacing"))
    g = prof.geometry
    room = g.dest_width if (line and lk) else g.columns
    width = _width(payload, fonts)
    runs = bool(spec.get("run") or (spec.get("auto_run", True) and width > room))
    return payload, (lk if line else None), room, width, runs


def run_seconds(spec: dict, prof, fonts, line_map: dict | None = None) -> float:
    """Seconds a running text needs to pass through once – it enters at the right edge and leaves at
    the left one, i.e. (text width + field width) columns at the panel's scroll speed. 0 = static text."""
    try:
        _p, _lk, room, width, runs = free_text_layout(spec, prof, fonts, line_map)
    except Exception:      # noqa: BLE001
        return 0.0
    return (width + room) * prof.geometry.scroll_ms / 1000 if runs else 0.0


def spec_telegrams(spec: dict, prof, fonts, line_map: dict | None = None) -> list:
    """Display spec -> IBIS telegrams for the given panel profile."""
    if prof.free_text:
        payload, lk, _room, _w, runs = free_text_layout(spec, prof, fonts, line_map)
        if runs:
            payload = _with_run(payload)
        g = prof.geometry
        if spec.get("heading") and g.rows > 8:
            # two-line panel: heading on the upper line, text below (LF separator – NOT verified on hardware)
            head, _ = text_payload(fonts, spec["heading"], spec.get("heading_font"), spec.get("spacing"))
            payload = head + b"\n" + payload
        # The real BS120 shows the previously stored text right after a mode switch and takes
        # the new one only with the next v -> send v again after the switch (verified issue).
        v = b"v" + payload
        if lk:
            return [f"l{lk}", prof.mode("line_text") or "x9997", v]
        return [prof.mode("text") or "x9996", v]
    # panels without free text: only the line number from the database
    line = spec.get("line")
    lk = line_key(line, line_map) if line else None
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


def make_measure(fonts, prof, line_map: dict | None = None):
    """measure(page, line) -> seconds for one pass of a running text on this panel (0 = static)."""
    def measure(page: dict, line=None) -> float:
        return run_seconds({**page, "line": line}, prof, fonts, line_map)
    return measure


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
    def __init__(self, engine, fonts, panels: list, log=None, line_map: dict | None = None):
        self.engine, self.fonts, self.panels = engine, fonts, panels
        self.buses = {}
        self.line_map = line_map or {}
        self.log = log or (lambda m: None)
        self.running = False
        self.thread = None
        self._gen = 0                 # loop generation: a stopped loop never resumes (Stop -> Start)
        self._lock = threading.Lock() # one tick at a time (loop thread, state requests)

    @staticmethod
    def from_config(cfg: dict, engine, project, log=None, base_dir: str = ".") -> "Vehicle":
        from ..peripherals import build_peripherals
        line_map = project.data.get("line_map") or {}
        slots, buses = build_peripherals(cfg, project, base_dir=base_dir, line_map=line_map, log=log)
        veh = Vehicle(engine, project.fonts, slots, log, line_map=line_map)
        veh.buses = buses
        interior = [s for s in slots if getattr(s, "profile", None) is not None]
        ref = next((s.profile for s in interior if s.role == "interior"), interior[0].profile if interior else None)
        if ref is not None:
            engine.measure = make_measure(project.fonts, ref, line_map)
        return veh

    def tick(self, now: float | None = None, virtual_only: bool = False):
        """Send changed content to the panels. virtual_only=True refreshes only the virtual
        previews (used while the simulation is stopped – real panels must stay untouched)."""
        with self._lock:
            self._tick(now, virtual_only)

    def _tick(self, now, virtual_only):
        for p in self.panels:
            if p.kind == "lcd":                      # add-on LCD: the web page reads the state itself
                continue
            if virtual_only and p.real:
                continue
            try:
                p.update(self, now, virtual_only)
            except Exception as e:      # noqa: BLE001 – keep the other panels running
                p.error = str(e)
                p.last = None

    def resend(self):
        for b in self.buses.values():
            b.reset()
        for p in self.panels:
            p.resend()

    def run(self, period: float = 0.5, gen: int | None = None):
        gen = self._gen if gen is None else gen
        while self.running and self._gen == gen:
            self.tick()
            time.sleep(period)

    def start(self):
        if not self.running:
            self._gen += 1                       # an old loop still sleeping will end, not resume
            self.running = True
            self.thread = threading.Thread(target=self.run, kwargs={"gen": self._gen}, daemon=True)
            self.thread.start()

    def stop(self):
        self.running = False
        self._gen += 1
