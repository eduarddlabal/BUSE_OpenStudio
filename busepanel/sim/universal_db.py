"""Universal panel database: fonts + pictograms + line numbers; all other texts are sent
dynamically (free text v + x9996/x9997). Upload once, then drive the panel from the app.

BS120 firmware uses only the last two digits of the l telegram (measured: l607 shows record 007,
l123 -> 023, l100 -> 000). So only 100 line slots exist (keys 000-099). Lines 1-99 keep their own
slot; other lines (607, X7 ...) get a free slot from the top (099, 098 ...) and are written to
`line_map` in the project so the simulation sends the right number.
"""
from __future__ import annotations

from ..core.icons import add_icon_font
from ..core.project import Project

COMMANDS = ["l", "z", "x", "v", "zI", "zM", "zN", "a", "aV"]
SLOTS = 100


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


def build_universal(base: Project | None = None, version: str = "UNIV", extra_lines=(), line_font: int = 3,
                    panel_type: str = "BS120", ibis_address: int = 10, font: str = "gbuse") -> Project:
    """base = project/HEX whose fonts to use; None = font set `font`:
    'gbuse' (original gBUSE fonts, default) or 'builtin' (own design)."""
    if base is None:
        from ..core.gbuse_font import fontset
        fonts = fontset(font)
    else:
        fonts = base.fonts
    add_icon_font(fonts)
    texts, line_map = plan_lines(extra_lines)
    line_recs = {}
    for key in sorted(texts):
        t = texts[key]
        line_recs[key] = {"text": t, "font": line_font, "spacing": 2 if len(t) < 3 else 1} if t else ""
    data = {
        "panel": {"type": panel_type, "ibis_address": ibis_address},
        "config": {"version": version, "columns": 135, "commands": list(COMMANDS),
                   "defaults": {"font": 3, "spacing": 1, "interval": 1.0},
                   "tariff_digits": 1, "cycle_delay": 10, "panel_kind": 1},
        "fonts": None,
        "line_map": line_map,
        "lines": line_recs,
        "destinations": {"000": ""},
    }
    return Project(data, fonts)
