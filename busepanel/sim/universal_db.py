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

# Codes the BS120 cannot take in free text (v): the firmware ends the text at them (measured:
# "HLAVNÍ NÁDRAŽÍ" shows only "HLAVNÍ" – Í = 0x8B, i.e. VT 0x0B with the high bit). The universal
# database moves such characters to free codes; the code table makes the app send the new code.
# Tested OK on hardware: ČĎŤĚÁÉŽÓÚŠÝŘ ďčěžůýť (0x80–0x9F apart from 0x8B).
# Target 0x40 ('@'): plain ASCII, empty and "undefined" in the original gBUSE font – users of gBUSE
# typed '@' to get Í. Verified on the BS120. (Firmware details: docs/PROTOKOL.md 9.6.)
RELOCATE = {0x8B: 0x40}          # Í
SLOTS = 100


def relocate_codes(fonts, moves: dict = RELOCATE):
    """Move characters from codes the firmware cannot display in free text to free codes
    (glyph in every font + code table entry)."""
    raw = bytearray(fonts.codetable.raw)
    for old, new in moves.items():
        if not raw[old - 0x21]:
            continue                              # nothing to move
        raw[new - 0x21], raw[old - 0x21] = raw[old - 0x21], 0     # replaces the char at `new`
        for f in fonts.fonts:
            g = f.glyphs.get(old)
            if g is None:
                continue
            glyphs = {c: x for c, x in f.glyphs.items() if c != old}
            glyphs[new] = type(g)(new, g.height, g.data)
            f.glyphs = dict(sorted(glyphs.items()))
    from ..core.fonts import CodeTable
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


def build_universal(base: Project | None = None, version: str = "UNIV", extra_lines=(), line_font: int = 3,
                    panel_type: str = "BS120", ibis_address: int = 10, font: str = "gbuse") -> Project:
    """base = project/HEX whose fonts to use; None = font set `font`:
    'gbuse' (original gBUSE fonts, default) or 'builtin' (own design)."""
    if base is None:
        from ..core.gbuse_font import fontset
        fonts = fontset(font)
    else:
        fonts = base.fonts
    relocate_codes(fonts)
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
