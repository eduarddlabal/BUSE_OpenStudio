"""Peripherals of a simulated vehicle – reusable, any number, each with its own parameters.

Vehicle YAML (vozidla/*.yaml):

  panels:
    - {name: Vnitřní, type: BS120, role: interior, output: serial, port: "", address: 10}
    - {name: Čelní, type: BS210, variant: v1, role: front, output: serial, port: ""}
    - {name: Boční, type: BS210, variant: v2, stop_field: bottom, role: side, output: virtual}
    - {name: Zadní, type: BS210, variant: v4, role: rear, output: esp, device: zadni,
       database: databaze/vnejsi/zadni.hex, control: code}
    - {name: LCD, type: LCD, role: interior, http_port: 8091}

output: serial (physical panel on a COM port, IBIS address) | esp (on the network) | virtual (preview).
Peripherals on the same COM port or ESP device share one IBIS line (an internal bus). Optional
`buses: {name: {output…}}` + `bus: name` name such a line explicitly (not shown in the UI).
Types: see TYPES; new types register there.
"""
from __future__ import annotations

import os

from .base import Bus, Peripheral
from .exterior import VARIANTS, ExteriorSlot, ROLE_VARIANT, variant_database
from .interior import PanelSlot
from .lcd import LcdSlot

TYPES = {
    "BS120": {"title": "BS 120 – vnitřní jednořádkový (LED, volný text)", "class": "interior"},
    "BS220": {"title": "BS 220 – vnitřní dvouřádkový (neověřeno)", "class": "interior"},
    "BS210": {"title": "BS 210 – vnější DOT-LED (gBUSE0, odhad)", "class": "exterior"},
    "LCD": {"title": "LCD panel – webová stránka (doplněk)", "class": "lcd"},
}


def peripheral_type(p: dict) -> str:
    t = str(p.get("type") or "").upper().replace(" ", "")
    if t in TYPES:
        return t
    if p.get("output") == "lcd":
        return "LCD"
    prof = str(p.get("profile") or "BS120").upper().replace(" ", "")
    if prof.startswith(("BS210", "BS110")):
        return "BS210"
    return prof if prof in TYPES else "BS120"


def _output(cfg: dict, name: str, project):
    from ..sim.vehicle import make_output
    return make_output({**cfg, "name": name}, project)


def _bus_key(p: dict) -> str | None:
    out = p.get("output", "virtual")
    if out == "serial":
        return "serial:" + (p.get("port") or "auto")
    if out == "esp":
        return "esp:" + str(p.get("device") or p.get("name", ""))
    return None


def build_peripherals(cfg: dict, project, base_dir: str = ".", line_map: dict | None = None,
                      log=None) -> tuple[list[Peripheral], dict]:
    """-> (peripherals, buses by name)"""
    from ..outputs.virtual import VirtualPanel
    from ..panels.profile import profile as get_profile
    buses: dict[str, Bus] = {}
    for bname, bcfg in (cfg.get("buses") or {}).items():
        bcfg = dict(bcfg or {})
        out = None if bcfg.get("output", "virtual") == "virtual" else _output(bcfg, bname, project)
        if out is not None:
            out.fonts = project.fonts
        buses[bname] = Bus(bname, out)
    items = cfg.get("panels") or cfg.get("peripherals") or [{"name": "náhled", "role": "interior", "output": "virtual"}]
    result = []
    for p in items:
        p = dict(p)
        name = p.get("name") or p.get("role", "panel")
        role = p.get("role", "interior")
        ptype = peripheral_type(p)
        kind = TYPES[ptype]["class"]
        if kind == "lcd":
            out = _output(p, name, project)
            out.fonts = project.fonts
            result.append(LcdSlot(name, role, out, p))
            continue
        bus = buses.get(p.get("bus") or "")
        if bus is None:
            key = _bus_key(p)
            if key and key in buses:
                bus = buses[key]
            elif key:
                out = _output(p, name, project)
                out.fonts = project.fonts
                bus = buses[key] = Bus(key, out)
            else:
                bus = buses[f"náhled:{name}"] = Bus(f"náhled:{name}")
        if kind == "exterior":
            db = _exterior_db(p, role, base_dir)
            result.append(ExteriorSlot(name, role, db, bus, p, line_map=line_map))
        else:
            prof = get_profile(p.get("profile") or ptype)
            emu = VirtualPanel(name if not bus.real else name + " (náhled)", project, prof)
            emu.fonts = project.fonts
            result.append(PanelSlot(name, role, prof, shadow=emu, bus=bus, params=p))
    return result, buses


def _exterior_db(p: dict, role: str, base_dir: str):
    path = p.get("database")
    if path:
        full = path if os.path.isabs(path) else os.path.join(base_dir, path)
        from ..gbuse0.database import load_hex
        return load_hex(full)
    variant = p.get("variant") or ROLE_VARIANT.get(role, "v1")
    return variant_database(variant, p.get("stop_field"), p.get("columns"), p.get("rows"), p.get("stop_rows"))


def describe() -> dict:
    """Types and variants for the UI."""
    return {"types": {k: v["title"] for k, v in TYPES.items()},
            "variants": {k: v.get("title", k) for k, v in VARIANTS.items()}}


__all__ = ["Bus", "Peripheral", "PanelSlot", "ExteriorSlot", "LcdSlot", "TYPES", "build_peripherals", "describe"]
