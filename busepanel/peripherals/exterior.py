"""Exterior DOT-LED panel (BS210, gBUSE0 firmware) – emulator and simulation driver.

The emulator receives IBIS telegrams like the real panel and draws them with busepanel.gbuse0.program:

  l<NNN>     line record from LIN; a missing record shows the number itself (presets of the line field)
  z<NNN>     destination record from CIL (missing -> the panel keeps the previous picture)
  zC<NNNN>   4-digit destination (CIL4)
  zD<NNNN…>  via stops: codes of DRU records, shown one after another in the stop field (max. 10)
  zI<text>   open text into the destination field, autoformatted (FW 3.08+); LF splits two lines (3.18)
  zN<text>   open text of via stops into the stop field (presets); stops separated by "|" [ODHAD]
Everything else (v, x99xx of the interior panels, a…) is ignored. While a record with the control
command b is shown, other telegrams are ignored; w hides the stops (gBUSE0 manual).

Font / spacing codes (E0+n, B0+n) at the start of a zI text or of a zN item choose the font instead of
the automatic formatting [ODHAD – used by the simulation's text composition].

Without a gBUSE0 database the panel uses the variant geometry (exterior.yaml) and the original gBUSE0
font set (gbuse0_font.yaml).
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import yaml

from ..gbuse0.database import Database0, Field
from ..gbuse0.program import Layer, Program, run

_FILE = os.path.join(os.path.dirname(__file__), "exterior.yaml")
with open(_FILE, encoding="utf-8") as _f:
    VARIANTS: dict = yaml.safe_load(_f)

ROLE_VARIANT = {"front": "v1", "side": "v2", "rear": "v4"}
STOP_SEPARATOR = "|"            # zN: our convention for several stops in one telegram [ODHAD]
MAX_STOPS = 10                  # zD carries at most 10 stops (dBUSE manual)


# ------------------------------------------------------------------ database of a variant
_FONTS = None


def gbuse0_fonts() -> dict:
    """Original gBUSE0 fonts 0–12 (gbuse0_font.yaml) – id -> Font."""
    global _FONTS
    if _FONTS is None:
        from ..core.fonts import FontSet
        with open(os.path.join(os.path.dirname(__file__), "gbuse0_font.yaml"), encoding="utf-8") as f:
            _FONTS = {x.id: x for x in FontSet.from_yaml(yaml.safe_load(f)).fonts}
    return _FONTS


fallback_fonts = gbuse0_fonts          # old name


def _stop_position(stop_field) -> str | None:
    if stop_field in (None, False, "", "none", "ne"):
        return None
    return "bottom" if str(stop_field).lower() in ("bottom", "dole") else "top"


def variant_database(variant: str = "v1", stop_field=False, columns: int | None = None,
                     rows: int | None = None, stop_rows: int | None = None) -> Database0:
    """stop_field: False | "top" (True) | "bottom" – a via-stop line above or below the destination;
    stop_rows: its height (default 10 on top, 8 at the bottom)."""
    v = VARIANTS.get(variant) or VARIANTS["v1"]
    cols, rws = int(columns or v["columns"]), int(rows or v["rows"])
    db = Database0(cols, rws, fonts=gbuse0_fonts(), version="SIM " + variant, source=f"varianta {variant}")
    if v.get("line"):
        x0, x1 = v["line"]
        db.fields["line"] = Field(x0, min(x1, cols - 1), 0, rws - 1, font=8, spacing=1)
    if v.get("dest"):
        x0, x1 = v["dest"]
        x1 = cols - 1 if x1 >= v["columns"] - 1 else min(x1, cols - 1)
        pos = _stop_position(stop_field)
        if pos == "top":
            h = int(stop_rows or 10)
            db.fields["stop"] = Field(x0, x1, 0, h - 1, font=0, spacing=1, interval=3.0)
            db.fields["dest"] = Field(x0, x1, h, rws - 1, font=3, spacing=1)
        elif pos == "bottom":
            h = int(stop_rows or 8)
            db.fields["dest"] = Field(x0, x1, 0, rws - h - 1, font=3, spacing=1)
            db.fields["stop"] = Field(x0, x1, rws - h, rws - 1, font=11, spacing=1, interval=3.0)
        else:
            db.fields["dest"] = Field(x0, x1, 0, rws - 1, font=7, spacing=1)
    return db


# ------------------------------------------------------------------ text -> record
def _codetable():
    from ..core.gbuse_font import gbuse_fontset
    return gbuse_fontset().codetable


_CT = None


def encode_text(text: str) -> bytes:
    """Unicode -> panel codes (Kamenický order of the gBUSE code table); unknown characters are dropped."""
    global _CT
    if _CT is None:
        _CT = _codetable()
    out = bytearray()
    for ch in text:
        if ch == "\n":
            out.append(0x0A)
            continue
        c = _CT.encode_char(ch)
        if c is None:
            c = _CT.encode_char(ch.upper())
        if c is not None:
            out.append(c)
    return bytes(out)


_INK: dict = {}


def font_height(db, fid: int) -> int:
    """Rows the font really uses from the top of its cell (gBUSE0 cells are taller than the letters)."""
    f = db.fonts.get(fid)
    if f is None:
        return 0
    key = id(f)
    if key not in _INK:
        from ..gbuse0.program import glyph_dots
        # letters and digits incl. accents and descenders – not symbols / pictograms of the font
        letters = [g for c, g in f.glyphs.items() if 0x30 <= c <= 0x39 or 0x41 <= c <= 0x5A or 0x61 <= c <= 0x7A
                   or 0x80 <= c <= 0xA9] or list(f.glyphs.values())
        _INK[key] = max((r + 1 for g in letters for col in glyph_dots(g) for r in col), default=0)
    return _INK[key]


def text_width(db, fid: int, codes: bytes, spacing: int) -> int:
    w, first = 0, True
    for c in codes:
        g = db.glyph(fid, c)
        if not first:
            w += spacing
        first = False
        w += g.width if g is not None else (2 if c == 0x20 else 0)
    return w


_BASE: dict = {}
_DESC = set(b"gjpqy,;()")              # glyphs that may go below the baseline


def _baseline(g) -> int | None:
    from ..gbuse0.program import glyph_dots
    key = (id(g), g.code)
    if key not in _BASE:
        rows = [r for col in glyph_dots(g) for r in col]
        _BASE[key] = max(rows) if rows else None
    return _BASE[key]


def covers(db, fid: int, codes: bytes) -> bool:
    """The font has a glyph for every printable character (a symbol font must not 'fit' with width 0)
    and all letters / digits of the text sit on one baseline (a font with small superscript lower-case
    letters is only for capitals)."""
    base = set()
    for c in codes:
        if c <= 0x20:
            continue
        g = db.glyph(fid, c)
        if g is None:
            return False
        if c not in _DESC and (0x30 <= c <= 0x39 or 0x41 <= c <= 0x5A or 0x61 <= c <= 0x7A or c >= 0x80):
            b = _baseline(g)
            if b is not None:
                base.add(b)
    return not base or max(base) - min(base) <= 1


def _candidates(db, max_h: int, codes: bytes = b""):
    fonts = [(font_height(db, f), f) for f in db.fonts if covers(db, f, codes)]
    return [f for h, f in sorted(fonts, key=lambda x: (-x[0], x[1])) if 0 < h <= max_h]


def split_codes(codes: bytes) -> tuple[int | None, int | None, bytes]:
    """Leading font / spacing / shift codes of a text -> (font, spacing, rest)."""
    font = spacing = None
    i = 0
    while i < len(codes) and 0xB0 <= codes[i] <= 0xEF and not 0xD0 <= codes[i] <= 0xDF:
        c = codes[i]
        if c >= 0xE0:
            font = c - 0xE0
        elif c < 0xC0:
            spacing = c - 0xB0
        i += 1
    return font, spacing, codes[i:]


SPACINGS = (1, 0)                    # normal letter spacing first, tight only when needed


def autoformat(db, fld: Field, codes: bytes) -> bytes:
    """Open text -> record (FW 3.08 autoformat): the biggest font that fits (normal spacing first); two
    lines if the text has LF (FW 3.18) or does not fit on one line. Vertically centred. Leading font /
    spacing codes fix the font / spacing."""
    font, spacing, codes = split_codes(codes)
    sps = (spacing,) if spacing is not None else SPACINGS

    def cands(max_h, text):
        if font is not None:
            return [font] if font in db.fonts else [fld.font]
        return _candidates(db, max_h, text)

    if 0x0A not in codes:
        for f in cands(fld.height, codes):
            for sp in sps:
                if text_width(db, f, codes, sp) <= fld.width:
                    y = max(0, (fld.height - font_height(db, f)) // 2)
                    return bytes([0xE0 + f, 0xB0 + sp, 0xC0 + y]) + codes + b"\r"
        if font is not None:
            # the chosen font does not fit: a lower font that fits (the page font is the maximum size)
            limit = font_height(db, font)
            for f in _candidates(db, limit, codes):
                for sp in sps:
                    if font_height(db, f) <= limit and text_width(db, f, codes, sp) <= fld.width:
                        y = max(0, (fld.height - font_height(db, f)) // 2)
                        return bytes([0xE0 + f, 0xB0 + sp, 0xC0 + y]) + codes + b"\r"
        words = codes.split(b" ")
        if len(words) < 2:
            f = (cands(fld.height, codes) or [fld.font])[-1]
            return bytes([0xE0 + f, 0xB0 + sps[-1], 0xC0]) + codes + b"\r"
        best = min(range(1, len(words)), key=lambda i: abs(len(b" ".join(words[:i])) - len(b" ".join(words[i:]))))
        codes = b" ".join(words[:best]) + b"\n" + b" ".join(words[best:])
    top, bottom = codes.split(b"\n", 1)
    half = (fld.height - 1) // 2
    flat = codes.replace(b"\n", b"")
    if not cands(half, flat):
        # the field is too low for two lines: one line in the narrowest font that fits the height
        one = cands(fld.height, flat) or [fld.font]
        f = min(one, key=lambda x: text_width(db, x, flat.replace(b"\n", b" "), sps[-1]))
        y = max(0, (fld.height - font_height(db, f)) // 2)
        return bytes([0xE0 + f, 0xB0 + sps[-1], 0xC0 + y]) + codes.replace(b"\n", b" ") + b"\r"
    for f in cands(half, flat):
        for sp in sps:
            if max(text_width(db, f, top, sp), text_width(db, f, bottom, sp)) <= fld.width:
                h = font_height(db, f)
                gap = max(1, fld.height - 2 * h) // 2
                y1 = max(0, (fld.height - 2 * h - 1) // 2)
                return (bytes([0xE0 + f, 0xB0 + sp, 0xC0 + y1]) + top + b"\n"
                        + bytes([0xE0 + f, 0xB0 + sp, 0xC0 + min(31, y1 + h + gap)]) + bottom + b"\r")
    f = (cands(half, flat) or [fld.font])[-1]
    return bytes([0xE0 + f, 0xB0, 0xC0]) + top + b"\n" + bytes([0xE0 + f, 0xB0, 0xC0 + half + 1]) + bottom + b"\r"


def preset_record(fld: Field, codes: bytes) -> bytes:
    return bytes([0xE0 + fld.font, 0xB0 + fld.spacing, 0xC0 + fld.shift]) + codes + b"\r"


# ------------------------------------------------------------------ emulator
@dataclass
class _Shown:
    program: Program
    t0: float
    cmd: str
    what: str


class ExteriorPanel:
    """Emulated exterior panel – receives telegrams (send) and renders the current picture (frame)."""
    kind = "virtual"

    def __init__(self, name: str, db: Database0, line_map: dict | None = None, stop_seconds: float = 3.0,
                 clock=time.time, line_in_text: bool | None = None):
        self.name, self.db, self.clock = name, db, clock
        self.reverse_map = {v: k for k, v in (line_map or {}).items()}
        self.stop_seconds = stop_seconds
        # simulation aid [ODHAD]: a panel without a line field (v3) puts the line number in front of a
        # free-text destination. Default only for the built-in variants, never for a real database.
        self.line_in_text = (db.source.startswith("variant") if line_in_text is None else line_in_text) \
            and "line" not in db.fields
        self.sent = 0
        self.last_error = None
        self.fonts = None
        self.reset()

    def reset(self):
        self.shown: dict[str, _Shown] = {}
        self.values = {"l": None, "z": None, "zD": [], "zI": None, "zN": None}
        self.log: list[str] = []
        self.changed = self.clock()

    # ---- input -------------------------------------------------------
    def send(self, *telegrams):
        for t in telegrams:
            b = t if isinstance(t, bytes) else str(t).encode("cp1250", "replace")
            self.feed(b)
            self.sent += 1

    def _blocked(self, cmd: str) -> bool:
        for s in self.shown.values():
            if s.program.block and s.cmd != cmd:
                return True
        return False

    def _show(self, fname: str, rec: bytes | None, cmd: str, what: str, program: Program | None = None):
        fld = self.db.fields.get(fname)
        if fld is None or (rec is None and program is None):
            return
        prog = program or run(rec, self.db, fld)
        self.shown[fname] = _Shown(prog, self.clock(), cmd, what)
        self.changed = self.clock()

    def feed(self, b: bytes):
        from ..outputs.base import tel_text
        self.log = (self.log + [time.strftime("%H:%M:%S ") + tel_text(b)])[-40:]
        s = b.decode("latin1")
        recs = self.db.records
        for cmd in ("zC", "zD", "zI", "zA", "aA", "zN", "lA", "l", "z"):
            if s.startswith(cmd):
                arg = b[len(cmd):]
                break
        else:
            return                                   # not an exterior panel telegram (v, x…, a…)
        if self._blocked({"l": "l", "lA": "l", "zD": "zD", "zN": "zD"}.get(cmd, "z")):
            return
        if cmd == "l" and arg[:3].isdigit():
            key = arg[:3].decode()
            self.values["l"] = key
            rec = recs.get("LIN", {}).get(key)
            if rec is None and "line" in self.db.fields:
                rec = self._fit_line(encode_text(self._line_label()))
            self._show("line", rec, "l", f"linka {key}")
            if self.line_in_text and self.values["zI"] is not None and self.shown.get("dest", None) \
                    and self.shown["dest"].what == "text":
                self._dest_text()
        elif cmd == "z" and arg[:3].isdigit():
            key = arg[:3].decode()
            self.values["z"] = key
            rec = recs.get("CIL", {}).get(key)
            if rec is not None:
                self._show("dest", rec, "z", f"cíl {key}")
        elif cmd == "zC" and arg[:4].isdigit():
            rec = recs.get("CIL4", {}).get(arg[:4].decode())
            if rec is not None:
                self._show("dest", rec, "z", f"cíl {arg[:4].decode()}")
        elif cmd in ("zI", "zA", "aA"):
            self.values["zI"] = arg
            self._dest_text()
        elif cmd == "zD":
            codes = [arg[i:i + 4].decode() for i in range(0, len(arg) - 3, 4) if arg[i:i + 4].isdigit()][:MAX_STOPS]
            self.values["zD"] = codes
            dru = recs.get("DRU", {})
            self._stops([dru[c] for c in codes if c in dru], f"zastávky {' '.join(codes)}")
        elif cmd == "zN":
            fld = self.db.fields.get("stop")
            self.values["zN"] = arg
            if fld is not None:
                parts = [p for p in arg.split(STOP_SEPARATOR.encode()) if p.strip()][:MAX_STOPS]
                recs = [autoformat(self.db, fld, p) if split_codes(p)[0] is not None else preset_record(fld, p)
                        for p in parts]
                self._stops(recs, f"zastávky ({len(parts)})")

    def _line_label(self) -> str:
        key = self.values["l"]
        return self.reverse_map.get(key) or str(int(key)) if key else ""

    def _dest_text(self):
        fld = self.db.fields.get("dest")
        if fld is None:
            return
        arg = self.values["zI"]
        if self.line_in_text and self.values["l"]:
            arg = encode_text(self._line_label()) + b" " + arg
        self._show("dest", autoformat(self.db, fld, arg), "z", "text")

    def _fit_line(self, codes: bytes) -> bytes:
        fld = self.db.fields["line"]
        if text_width(self.db, fld.font, codes, fld.spacing) <= fld.width:
            return preset_record(fld, codes)
        return autoformat(self.db, fld, codes)

    def _stops(self, records: list, what: str):
        fld = self.db.fields.get("stop")
        if fld is None:
            return
        if not records:
            self._show("stop", None, "zD", what, Program([(Layer(self.db.columns, self.db.rows), None)], None))
            return
        frames = []
        for rec in records:
            p = run(rec, self.db, fld)
            if p.frames:
                frames.append((p.frames[-1][0], max(fld.interval, self.stop_seconds)))
        prog = Program(frames, 0 if len(frames) > 1 else None)
        if len(frames) == 1:
            prog.frames = [(frames[0][0], None)]
        self._show("stop", None, "zD", what, prog)

    # ---- output ------------------------------------------------------
    def bitmap(self, now: float | None = None) -> list[int]:
        now = self.clock() if now is None else now
        out = [0] * self.db.columns
        dest = self.shown.get("dest")
        hide_stops = bool(dest and dest.program.suppress_stops)
        for name, s in sorted(self.shown.items(), key=lambda kv: kv[1].t0):
            if name == "stop" and hide_stops:
                continue
            layer = s.program.at(now - s.t0)
            if layer is None:
                continue
            for x in range(min(self.db.columns, layer.columns)):
                m = layer.mask[x]
                out[x] = (out[x] & ~m) | (layer.bits[x] & m)
        return out

    def frame(self, now: float | None = None) -> dict:
        db = self.db
        parts = [s.what for _n, s in sorted(self.shown.items())]
        return {"name": self.name, "matrix": True, "bits": self.bitmap(now),
                "mode": "exterior", "mode_title": " · ".join(parts) or "prázdný",
                "geometry": {"columns": db.columns, "rows": db.rows,
                             "fields": {k: v.to_dict() for k, v in db.fields.items()}},
                "log": self.log[-8:], "changed": self.changed}

    def status(self) -> dict:
        return {"name": self.name, "kind": self.kind, "sent": self.sent, "error": self.last_error,
                "mode": "exterior"}

    def ascii(self, now: float | None = None) -> str:
        bits = self.bitmap(now)
        return "\n".join("".join("#" if bits[x] >> y & 1 else "." for x in range(self.db.columns))
                         for y in range(self.db.rows))


# ------------------------------------------------------------------ simulation driver
import re as _re

from .base import Bus, Peripheral

_ICON = _re.compile(r"\{icon:[^{}]*\}|\{/?run\}|\{[fm]\d+\}")


def _plain(text: str) -> str:
    return " ".join(_ICON.sub("", text or "").split())


class ExteriorSlot(Peripheral):
    """Exterior panel driven by the simulation (later: by the BS100).

    control: "text" – l + zI<destination> (+ zN<via stops>) – works with any database (default)
             "code" – l + z<dest_code> (+ zD<stop codes>) – needs a gBUSE0 database with these records
    """
    kind = "exterior"

    def __init__(self, name, role, db: Database0, bus: Bus, params: dict | None = None, line_map=None):
        params = params or {}
        emulator = ExteriorPanel(name, db, line_map=line_map, stop_seconds=float(params.get("stop_seconds", 3)),
                                 line_in_text=params.get("line_in_text"))
        super().__init__(name, role, bus, emulator, params)
        self.db = db
        self.control = params.get("control", "text")
        self.sent_by_cmd: dict = {}

    def resend(self):
        super().resend()
        self.sent_by_cmd = {}

    def telegrams(self, vehicle, now) -> list:
        from ..sim.vehicle import line_key
        eng = vehicle.engine
        spec = eng.display(self.role if self.role != "interior" else "front", now, cursor=self.cursor,
                           measure=None)          # the gBUSE0 panel formats (shrinks) texts itself, no running text
        route = eng.route
        tel = []
        fields = self.db.fields
        if route.line and ("line" in fields or self.emulator.line_in_text):
            key = line_key(route.line, vehicle.line_map)
            if key is None and route.line.isdigit():
                key = f"{int(route.line) % 1000:03d}"
            if key is not None:
                tel.append(f"l{key}")
        if "dest" in fields:
            code = getattr(route, "dest_code", "") or ""
            if self.control == "code" and code:
                tel.append(f"zC{int(code):04d}" if len(str(code)) == 4 else f"z{int(code):03d}")
            else:
                text = _plain(spec.get("text") or route.destination)
                tel.append(b"zI" + _font_prefix(spec) + encode_text(text))
        if "stop" in fields:
            pages = eng.via_pages(self.role, now)[:MAX_STOPS]
            if pages:
                self.emulator.stop_seconds = pages[0]["seconds"]
            if self.control == "code":
                tel.append("zD" + "".join(f"{int(p['code']):04d}" for p in pages if p.get("code")))
            else:
                tel.append(b"zN" + STOP_SEPARATOR.encode().join(
                    _font_prefix(p) + encode_text(_plain(p["text"])) for p in pages))
        return tel

    def update(self, vehicle, now, virtual_only):
        tel = self.telegrams(vehicle, now)
        # IBIS is a broadcast line: a telegram another exterior panel already put on the bus is not
        # sent again (all panels on the bus show the same line / destination, as on the real vehicle)
        changed = [t for t in tel if self.sent_by_cmd.get(_cmd_key(t)) != t
                   and self.bus.state.get(_cmd_key(t)) != t]
        for t in tel:
            self.sent_by_cmd[_cmd_key(t)] = t
        if not changed:
            self.last = tel
            return
        self.bus.send(*changed, virtual_only=virtual_only)
        for t in changed:
            self.bus.state[_cmd_key(t)] = t
        self.last = tel
        self.error = None
        self._sent_now(now)
        self._log(vehicle, changed)

    def status(self):
        st = super().status()
        st["profile"] = self.params.get("variant") or self.db.source
        st["control"] = self.control
        return st


def _cmd_key(t) -> str:
    """l / z / zC / zD / zI / zN – one slot per telegram kind (unchanged kinds are not resent)."""
    s = t.decode("latin1") if isinstance(t, bytes) else t
    return s[:2] if s[:2] in ("zC", "zD", "zI", "zN") else s[:1]


def _font_prefix(page: dict) -> bytes:
    """Font / spacing chosen in the text composition -> leading codes (else the panel formats itself)."""
    out = b""
    if page.get("font") is not None:
        out += bytes([0xE0 + int(page["font"]) % 16])
    if page.get("spacing") is not None:
        out += bytes([0xB0 + int(page["spacing"]) % 16])
    return out


def via_stops(engine) -> list:
    """Via stops still ahead (stops marked via: true) – see SimEngine.via_stops."""
    return engine.via_stops()
