"""Universal panel database: fonts + pictograms + line numbers; all other texts are sent
dynamically (free text v + x9996/x9997). Upload once, then drive the panel from the app.

BS120 firmware uses only the last two digits of the l telegram (measured: l607 shows record 007,
l123 -> 023, l100 -> 000). So only 100 line slots exist (keys 000-099). Lines 1-99 keep their own
slot; other lines (607, X7 ...) get a free slot from the top (099, 098 ...) and are written to
`line_map` in the project so the simulation sends the right number.
"""
from __future__ import annotations

import copy

from ..core.bundle import PanelEntry, ProjectBundle, load_builtin_fonts
from ..core.fonts import CodeTable, _ct_index
from ..core.icons import add_icon_font
from ..core.project import Project

COMMANDS = ["l", "z", "x", "v", "zI", "zM", "zN", "a", "aV"]

# Codes the BS120 cannot take in free text (v): the firmware ends the text at them (measured:
# "HLAVNÍ NÁDRAŽÍ" shows only "HLAVNÍ" – Í = 0x8B, i.e. VT 0x0B with the high bit). The universal
# database moves such characters to free codes; the code table makes the app send the new code.
# Tested OK on hardware: ČĎŤĚÁÉŽÓÚŠÝŘ ďčěžůýť (0x80–0x9F apart from 0x8B).
# Target 0x40 ('@'): plain ASCII, empty and "undefined" in the original gBUSE font – users of gBUSE
# typed '@' to get Í. Verified on the BS120. (Firmware details: docs/PROTOKOL.md 9.6.)
RELOCATE = {0x8B: 0x40}          # Í
SLOTS = 100

_DEFAULT_EXTERIOR_ADDR = {"front": 1, "side": 2, "rear": 3}
_DEFAULT_BUNDLE_PANELS = [
    {"id": "celni", "name": "Čelní BS210", "role": "front", "type": "BS210", "variant": "v1", "ibis_address": 1},
    {"id": "bocni", "name": "Boční BS210", "role": "side", "type": "BS210", "variant": "v2", "stop_field": "bottom", "ibis_address": 2},
    {"id": "vnitrni", "name": "Vnitřní BS120", "role": "interior", "type": "BS120", "profile": "BS120", "columns": 135, "ibis_address": 10},
]


def relocate_codes(fonts, moves: dict = RELOCATE):
    """Move characters from codes the firmware cannot display in free text to free codes
    (glyph in every font + code table entry)."""
    raw = bytearray(fonts.codetable.raw)
    for old, new in moves.items():
        oi, ni = _ct_index(old), _ct_index(new)
        if oi is None or ni is None or not raw[oi]:
            continue                              # nothing to move
        raw[ni], raw[oi] = raw[oi], 0             # replaces the char at `new`
        for f in fonts.fonts:
            g = f.glyphs.get(old)
            if g is None:
                continue
            glyphs = {c: x for c, x in f.glyphs.items() if c != old}
            glyphs[new] = type(g)(new, g.height, g.data)
            f.glyphs = dict(sorted(glyphs.items()))
    fonts.codetable = CodeTable(bytes(raw))


def plan_lines(extra_lines=()) -> tuple[dict, dict]:
    """-> (records {key: text}, line_map {label: key})"""
    texts = {f"{n:03d}": str(n) for n in range(1, SLOTS)}
    texts["000"] = ""
    line_map = {}
    free = [f"{n:03d}" for n in range(SLOTS - 1, 0, -1)]   # take from the top
    for label in extra_lines:
        label = str(label).strip()
        if not label or label in line_map:
            continue
        if label.isdigit() and 0 < int(label) < SLOTS:
            continue                                         # own slot
        key = free.pop(0)
        texts[key] = label
        line_map[label] = key
    return texts, line_map


def _resolve_columns(panel_type: str, base: Project | None, columns: int | None) -> int:
    if columns is not None:
        return int(columns)
    if base is not None:
        cfg_cols = (base.data.get("config") or {}).get("columns")
        if cfg_cols:
            return int(cfg_cols)
    from ..panels.profile import profile
    return int(profile(panel_type).geometry.columns)


def build_universal(base: Project | None = None, version: str = "UNIV", extra_lines=(), line_font: int = 3,
                    panel_type: str = "BS120", ibis_address: int = 10, font: str = "gbuse",
                    columns: int | None = None, panel_kind: int = 1, fw: str = "",
                    name: str = "Univerzální databáze (gBUSE1)",
                    description: str = "") -> Project:
    """base = project/HEX whose fonts to use; None = font set `font`:
    'gbuse' (original gBUSE fonts, default) or 'builtin' (own design)."""
    if base is None:
        from ..core.gbuse_font import fontset
        fonts = fontset(font)
    else:
        fonts = copy.deepcopy(base.fonts)
    relocate_codes(fonts)
    add_icon_font(fonts)
    texts, line_map = plan_lines(extra_lines)
    line_recs = {}
    for key in sorted(texts):
        t = texts[key]
        line_recs[key] = {"text": t, "font": line_font, "spacing": 2 if len(t) < 3 else 1} if t else ""
    cols = _resolve_columns(panel_type, base, columns)
    cfg: dict = {
        "version": version,
        "columns": cols,
        "commands": list(COMMANDS),
        "defaults": {"font": 3, "spacing": 1, "interval": 1.0},
        "tariff_digits": 1,
        "cycle_delay": 10,
        "panel_kind": int(panel_kind),
    }
    if fw:
        cfg["fw"] = fw
    meta: dict = {"name": name}
    if description:
        meta["description"] = description
    data = {
        "meta": meta,
        "panel": {"format": "gbuse1", "type": panel_type, "ibis_address": int(ibis_address)},
        "config": cfg,
        "fonts": None,
        "line_map": line_map,
        "lines": line_recs,
        "destinations": {"000": ""},
    }
    return Project(data, fonts)


def _exterior_universal_project(spec: dict, texts: dict[str, str], line_map: dict[str, str],
                                version: str, ibis_address: int) -> Project:
    from ..peripherals.exterior import ROLE_VARIANT, variant_database
    role = str(spec.get("role") or "front")
    stop_field = spec.get("stop_field")
    variant = spec.get("variant") or ROLE_VARIANT.get(role, "v1")
    preset = "rear" if role == "rear" else ("side_via" if (stop_field or variant in ("v2", "v3")) else ("side" if role == "side" else "front"))
    fs0 = load_builtin_fonts("gbuse0")
    proj = Project.new_gbuse0(fs0, preset=preset, version=version, fw="3.08", ibis_address=ibis_address)
    if any(spec.get(k) is not None for k in ("variant", "columns", "rows", "stop_field", "stop_rows")):
        db = variant_database(variant, stop_field, spec.get("columns"), spec.get("rows"), spec.get("stop_rows"))
        cfg = proj.data["config"]
        cfg["columns"] = db.columns
        cfg["rows"] = db.rows
        cfg["fields"] = {k: f.to_bottom_dict(db.rows) for k, f in db.fields.items()}
        cmds = list(cfg.get("commands") or [])
        if "stop" in db.fields:
            for c in ("zD", "zN"):
                if c not in cmds:
                    cmds.append(c)
        else:
            cmds = [c for c in cmds if c not in ("zD", "zN")]
        cfg["commands"] = cmds
    lf = (proj.data["config"]["fields"].get("line") or {}).get("font", 8)
    proj.data["lines"] = {
        key: (f"f{lf} m1 '{t.replace(chr(39), '')}' CR" if t else "")
        for key, t in sorted(texts.items())
    }
    proj.data["destinations"] = {"000": ""}
    if "stop" in proj.data["config"]["fields"]:
        proj.data["stops"] = {"0000": ""}
    if line_map:
        proj.data["line_map"] = dict(line_map)
    return proj


def build_universal_bundle(
    base: Project | None = None,
    version: str = "UNIV",
    extra_lines=(),
    font: str = "gbuse",
    name: str = "Univerzální autobus (simulace)",
    description: str = "Vícepanelový univerzální projekt pro simulátor (gBUSE0 + gBUSE1)",
    vehicle_cfg: dict | None = None,
    panels: list[dict] | None = None,
) -> ProjectBundle:
    """Build a multi-panel ProjectBundle (gBUSE0 exterior + gBUSE1 interior) for simulation."""
    from ..peripherals import TYPES, peripheral_type
    specs = list(panels) if panels is not None else list((vehicle_cfg or {}).get("panels") or _DEFAULT_BUNDLE_PANELS)
    if not specs:
        specs = list(_DEFAULT_BUNDLE_PANELS)

    texts, line_map = plan_lines(extra_lines)
    entries: list[PanelEntry] = []
    sim_panels: list[dict] = []
    used_ids: set[str] = set()

    for idx, raw in enumerate(specs, start=1):
        p = dict(raw)
        ptype = peripheral_type(p)
        cls = TYPES.get(ptype, {}).get("class", "interior")
        if cls == "lcd":
            sim_panels.append(p)
            continue

        role = str(p.get("role") or ("interior" if cls == "interior" else "front"))
        pname = str(p.get("name") or f"Panel {idx}")
        pid = str(p.get("id") or "")
        if not pid:
            base_slug = {"front": "celni", "side": "bocni", "rear": "zadni", "interior": "vnitrni"}.get(role, f"panel_{idx}")
            pid = base_slug if base_slug not in used_ids else f"{base_slug}_{idx}"
        while pid in used_ids:
            pid = f"{pid}_{idx}"
        used_ids.add(pid)

        default_addr = _DEFAULT_EXTERIOR_ADDR.get(role, idx) if cls == "exterior" else 10
        addr = int(p.get("address", p.get("ibis_address", default_addr)))

        if cls == "exterior":
            proj = _exterior_universal_project(p, texts, line_map, version=version, ibis_address=addr)
            proj.data["meta"] = {"name": pname}
            db_rel = f"panely/{pid}/projekt.yaml"
            entries.append(PanelEntry(id=pid, name=pname, role=role, db=db_rel, ibis_address=addr, project=proj))
            sim_item = {
                "name": pname,
                "type": ptype,
                **({"variant": p["variant"]} if p.get("variant") else {}),
                **({"stop_field": p["stop_field"]} if p.get("stop_field") else {}),
                **({"columns": int(p["columns"])} if p.get("columns") else {}),
                **({"rows": int(p["rows"])} if p.get("rows") else {}),
                "role": role,
                "output": p.get("output", "virtual"),
                "database": db_rel,
                "panel_id": pid,
            }
        else:
            prof_name = str(p.get("profile") or ptype or "BS120")
            proj = build_universal(
                base=base, version=version, extra_lines=extra_lines,
                panel_type=prof_name, ibis_address=addr, font=font,
                columns=p.get("columns"),
                panel_kind=int(p.get("panel_kind", 2 if prof_name == "BS220" else 1)),
                name=pname,
            )
            db_rel = f"panely/{pid}/projekt.yaml"
            entries.append(PanelEntry(id=pid, name=pname, role=role, db=db_rel, ibis_address=addr, project=proj))
            sim_item = {
                "name": pname,
                "type": ptype,
                "profile": prof_name,
                **({"columns": int(proj.geometry.columns)} if p.get("columns") else {}),
                "role": role,
                "output": p.get("output", "virtual"),
                "database": db_rel,
                "panel_id": pid,
            }
        if sim_item["output"] == "serial":
            sim_item["port"] = p.get("port", "")
            sim_item["address"] = addr
            if "enabled" in p:
                sim_item["enabled"] = bool(p["enabled"])
        elif sim_item["output"] == "esp" and p.get("device"):
            sim_item["device"] = p["device"]
        sim_panels.append(sim_item)

    if not entries:
        return build_universal_bundle(base=base, version=version, extra_lines=extra_lines, font=font,
                                      name=name, description=description, panels=_DEFAULT_BUNDLE_PANELS)

    sim_cfg = {
        "system": str((vehicle_cfg or {}).get("system") or "default"),
        "vehicle_data": {"name": name, "panels": sim_panels},
    }
    return ProjectBundle(meta={"name": name, "description": description}, panels=entries, simulation=sim_cfg)

