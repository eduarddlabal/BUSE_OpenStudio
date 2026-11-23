"""gBUSE0 MNEMO language compiler/decompiler, autoformat engine, and 1-line/2-line/alternating composer.

MNEMO language syntax (gBUSE0.doc "MNEMO JAZYK"):
  'text'      text literal (single apostrophe escaped via #39)
  #code       decimal character code in <32..175> or <240..255>
  f0..f15     font (0xE0+n)
  m0..m15     spacing (0xB0+n)
  y0..y31     vertical shift down from window top (0xC0+n)
  l1..l224    left window column (1B 6C n+0x0F)
  p1..p224    right window column (1B 70 n+0x0F)
  h1..h28     top window row from bottom (1B 68 n+0x0F)
  d1..d28     bottom window row from bottom (1B 64 n+0x0F)
  s1..s224    start column (1B 73 n+0x0F)
  z0..z239    frame display time in tenths of second (1B 7A n+0x0F)
  c           reset window to configuration field (1B 63)
  i           invert active window (1B 69)
  o           clear active window (1B 6F)
  x           overlay mode until next FF/LF/VT/CR (1B 78)
  b           block other IBIS telegrams while shown (1B 62)
  w           suppress zD stop field (1B 77)
  n           rotate active window up (1B 6E)
  r           rotate active window left (1B 72)
  FF          format into active window (0x0C)
  LF          format & end line / frame (0x0A)
  VT          format, show frame & loop from after first LF (0x0B)
  CR          format, show & stop (0x0D)
"""
from __future__ import annotations

import re

from ..core.fonts import CodeTable
from .program import glyph_dots

ESC, LF, VT, FF, CR = 0x1B, 0x0A, 0x0B, 0x0C, 0x0D

# Sensible default autoformat tables when project AFC/AFD are empty (gBUSE0 fonts 0–12)
DEFAULT_AFC = [
    (8, 2, 0), (8, 1, 0), (8, 0, 0),
    (7, 2, 0), (7, 1, 0), (7, 0, 0),
    (6, 2, 0), (6, 1, 0), (6, 0, 0),
    (5, 2, 0), (5, 1, 0), (5, 0, 0),
    (4, 1, 0), (3, 1, 0), (2, 1, 0), (0, 1, 0),
]
DEFAULT_AFD = [
    (3, 1, 0), (2, 1, 0), (1, 1, 0), (0, 1, 0),
    (3, 0, 0), (2, 0, 0), (1, 0, 0), (0, 0, 0),
]


def to_mnemo(rec: bytes, ct: CodeTable) -> str:
    """Binary gBUSE0 record -> official MNEMO text representation."""
    tokens: list[str] = []
    buf: list[str] = []

    def flush_buf():
        if buf:
            tokens.append("'" + "".join(buf) + "'")
            buf.clear()

    i, n = 0, len(rec)
    while i < n:
        c = rec[i]
        if c == ESC and i + 1 < n:
            flush_buf()
            k = chr(rec[i + 1])
            if k in "lphdsz" and i + 2 < n:
                v = rec[i + 2] - 0x0F
                tokens.append(f"{k}{v}")
                i += 3
            elif k in "cioxwb nr".replace(" ", ""):
                tokens.append(k)
                i += 2
            else:
                tokens.append(f"#{c} #{rec[i + 1]}")
                i += 2
            continue
        if 0xE0 <= c <= 0xEF:
            flush_buf(); tokens.append(f"f{c - 0xE0}")
        elif 0xB0 <= c <= 0xBF:
            flush_buf(); tokens.append(f"m{c - 0xB0}")
        elif 0xC0 <= c <= 0xDF:
            flush_buf(); tokens.append(f"y{c - 0xC0}")
        elif c == FF:
            flush_buf(); tokens.append("FF")
        elif c == LF:
            flush_buf(); tokens.append("LF")
        elif c == VT:
            flush_buf(); tokens.append("VT")
        elif c == CR:
            flush_buf(); tokens.append("CR")
        elif c >= 0x20:
            ch = ct.decode_code(c)
            if ch and ch != "'":
                buf.append(ch)
            else:
                flush_buf()
                tokens.append(f"#{c}")
        else:
            flush_buf()
            tokens.append(f"#{c}")
        i += 1
    flush_buf()
    return " ".join(tokens)


_MNEMO_TOK = re.compile(
    r"'([^']*)'"
    r"|#(\d+)"
    r"|\b(FF|LF|VT|CR)\b"
    r"|\b([fmylphdsz])(\d+)\b"
    r"|\b([cioxwb nr])\b"
)


def from_mnemo(text: str, ct: CodeTable) -> tuple[bytes, list[str]]:
    """Compile MNEMO text -> binary gBUSE0 record bytes."""
    out = bytearray()
    problems: list[str] = []
    pos = 0
    s = text.strip()
    while pos < len(s):
        if s[pos].isspace():
            pos += 1
            continue
        if s[pos] == "'":
            end = s.find("'", pos + 1)
            if end == -1:
                problems.append("neuzavřený řetězec v apostrofech")
                lit = s[pos + 1:]
                pos = len(s)
            else:
                lit = s[pos + 1:end]
                pos = end + 1
            for ch in lit:
                c = ct.encode_char(ch)
                if c is None:
                    problems.append(f"znak {ch!r} není v kódové tabulce")
                else:
                    out.append(c)
            continue
        if s[pos] == "#":
            m = re.match(r"#(\d+)", s[pos:])
            if not m:
                problems.append(f"chybný kód znaku na pozici {pos}")
                pos += 1
                continue
            code = int(m.group(1))
            if not (0 <= code <= 255):
                problems.append(f"kód #{code} mimo rozsah 0–255")
            else:
                out.append(code)
            pos += m.end()
            continue
        m = re.match(r"[A-Za-z0-9]+", s[pos:])
        if not m:
            problems.append(f"neznámý znak {s[pos]!r} v MNEMO")
            pos += 1
            continue
        tok = m.group(0)
        pos += m.end()
        if tok == "FF":
            out.append(FF)
        elif tok == "LF":
            out.append(LF)
        elif tok == "VT":
            out.append(VT)
        elif tok == "CR":
            out.append(CR)
        elif tok in ("c", "i", "o", "x", "w", "b", "n", "r"):
            out += bytes([ESC, ord(tok)])
        elif len(tok) >= 2 and tok[0] in "fmylphdsz" and tok[1:].isdigit():
            cmd, val = tok[0], int(tok[1:])
            if cmd == "f":
                if not 0 <= val <= 15:
                    problems.append(f"font f{val} mimo 0–15")
                out.append(0xE0 + (val & 0x0F))
            elif cmd == "m":
                if not 0 <= val <= 15:
                    problems.append(f"mezera m{val} mimo 0–15")
                out.append(0xB0 + (val & 0x0F))
            elif cmd == "y":
                if not 0 <= val <= 31:
                    problems.append(f"posun y{val} mimo 0–31")
                out.append(0xC0 + (val & 0x1F))
            else:
                if cmd in "lps" and not 1 <= val <= 224:
                    problems.append(f"{tok}: sloupec mimo 1–224")
                elif cmd in "hd" and not 1 <= val <= 28:
                    problems.append(f"{tok}: řádek mimo 1–28")
                elif cmd == "z" and not 0 <= val <= 239:
                    problems.append(f"{tok}: doba mimo 0–239")
                out += bytes([ESC, ord(cmd), max(0, min(255, val + 0x0F))])
        else:
            problems.append(f"neznámý příkaz MNEMO {tok!r}")
    return bytes(out), problems


def _font_ink_height(db, fid: int) -> int:
    f = db.fonts.get(fid)
    if f is None:
        return 0
    letters = [g for c, g in f.glyphs.items() if 0x30 <= c <= 0x39 or 0x41 <= c <= 0x5A
               or 0x61 <= c <= 0x7A or 0x80 <= c <= 0xA9] or list(f.glyphs.values())
    return max((r + 1 for g in letters for col in glyph_dots(g) for r in col), default=0)


def _measure_codes(db, fid: int, sp: int, codes: bytes) -> tuple[int, bool]:
    w, first, ok = 0, True, True
    for c in codes:
        if not first:
            w += sp
        first = False
        if c == 0x20:
            w += 2
            continue
        g = db.glyph(fid, c)
        if g is None:
            ok = False
        else:
            w += g.width
    return w, ok


def autoformat_line(text: str, db, ct: CodeTable, width: int, height: int,
                    two_line: bool = False, vertical_center: bool = True) -> tuple[int, int, int]:
    """Choose (font, spacing, shift) from db's AFC/AFD table (or built-in table) fitting width & height."""
    raw_table = (db.afd if two_line else db.afc) or (DEFAULT_AFD if two_line else DEFAULT_AFC)
    # Filter to fonts actually present in db
    table = [(f, sp, sh) for f, sp, sh in raw_table if f in db.fonts]
    if not table:
        # Build sorted candidate list from available fonts
        fids = sorted(db.fonts, key=lambda fid: (-_font_ink_height(db, fid), fid))
        table = [(fid, sp, 0) for fid in fids for sp in (2, 1, 0)]
    if not table:
        return 3, 1, 0

    codes = bytearray()
    for ch in text:
        c = ct.encode_char(ch)
        if c is not None:
            codes.append(c)

    max_h = max(1, (height - 1) // 2) if two_line else height
    chosen = None
    for f, sp, sh in table:
        fh = _font_ink_height(db, f)
        if fh == 0 or fh > max_h:
            continue
        w, ok = _measure_codes(db, f, sp, bytes(codes))
        if ok and w <= width:
            chosen = (f, sp, sh)
            break
    if chosen is None:
        # Pick last entry with existing glyphs & height <= max_h, or absolute fallback
        valid = [(f, sp, sh) for f, sp, sh in table if 0 < _font_ink_height(db, f) <= max_h]
        chosen = valid[-1] if valid else table[-1]

    f, sp, sh = chosen
    if vertical_center:
        fh = _font_ink_height(db, f) or 8
        sh = max(0, (max_h - fh) // 2)
    return f, sp, sh


def _encode_str(text: str, ct: CodeTable) -> tuple[bytes, list[str]]:
    out, probs = bytearray(), []
    for ch in text:
        c = ct.encode_char(ch)
        if c is None:
            probs.append(f"znak {ch!r} není v kódové tabulce")
        else:
            out.append(c)
    return bytes(out), probs


def _line_width(db, fid: int, sp: int, codes: bytes) -> int:
    w, _ = _measure_codes(db, fid, sp, codes)
    return w


def compose_record(spec: dict, db, field_name: str, ct: CodeTable) -> tuple[bytes, list[str]]:
    """Compose binary gBUSE0 record from visual 1-line/2-line/alternating editor spec."""
    fld = db.fields.get(field_name)
    if fld is None:
        # fallback geometry
        from .database import Field
        fld = Field(0, db.columns - 1, 0, db.rows - 1)
    win_x0 = 0 if spec.get("full_panel") else fld.x0
    win_x1 = (db.columns - 1) if spec.get("full_panel") else fld.x1
    win_y0 = 0 if spec.get("full_panel") else fld.y0
    win_y1 = (db.rows - 1) if spec.get("full_panel") else fld.y1
    win_w = win_x1 - win_x0 + 1
    win_h = win_y1 - win_y0 + 1

    mode = str(spec.get("mode") or "1")
    auto = bool(spec.get("autoformat", False))
    l1 = dict(spec.get("line1") or {})
    l2 = dict(spec.get("line2") or {})
    if spec.get("swap_lines") and mode == "2":
        l1, l2 = l2, l1

    out = bytearray()
    probs: list[str] = []

    if spec.get("full_panel"):
        out += bytes([ESC, ord("b")])
        out += bytes([ESC, ord("l"), win_x0 + 1 + 0x0F])
        out += bytes([ESC, ord("p"), win_x1 + 1 + 0x0F])
        out += bytes([ESC, ord("d"), (db.rows - win_y1) + 0x0F])
        out += bytes([ESC, ord("h"), (db.rows - win_y0) + 0x0F])
    if spec.get("suppress_stops"):
        out += bytes([ESC, ord("w")])
    if spec.get("interval") is not None and spec.get("interval") != "":
        zval = max(1, min(239, int(round(float(spec["interval"]) * 10))))
        out += bytes([ESC, ord("z"), zval + 0x0F])

    def emit_line(ld: dict, default_y: int, two_line: bool) -> bool:
        txt = str(ld.get("text") or "")
        codes, p = _encode_str(txt, ct)
        probs.extend(p)
        if auto:
            f, sp, sh = autoformat_line(txt, db, ct, win_w, win_h, two_line=two_line)
            if two_line:
                sh = default_y + sh
        else:
            f = int(ld["font"]) if ld.get("font") not in (None, "") else fld.font
            sp = int(ld["spacing"]) if ld.get("spacing") not in (None, "") else fld.spacing
            sh = int(ld["shift"]) if ld.get("shift") not in (None, "") else default_y
        out.append(0xE0 + (f & 0x0F))
        out.append(0xB0 + (sp & 0x0F))
        out.append(0xC0 + (sh & 0x1F))
        w = _line_width(db, f, sp, codes)
        align = ld.get("align") or "center"
        sx = ld.get("x")
        if sx not in (None, ""):
            col1 = int(sx) + 1
            out.extend(bytes([ESC, ord("s"), max(1, min(224, col1)) + 0x0F]))
        elif align == "left":
            out.extend(bytes([ESC, ord("s"), (win_x0 + 1) + 0x0F]))
        elif align == "right":
            col0 = max(win_x0, win_x1 + 1 - w)
            out.extend(bytes([ESC, ord("s"), (col0 + 1) + 0x0F]))
        out.extend(codes)
        return bool(ld.get("inverse"))

    if mode == "1":
        inv = emit_line(l1, 0, False)
        if inv:
            out += bytes([FF, ESC, ord("i")])
        out.append(CR)
        return bytes(out), probs

    # 2-line mode (possibly alternating with '|' in line 2)
    half = max(1, (win_h + 1) // 2)
    inv1 = emit_line(l1, 0, True)
    if inv1:
        out += bytes([FF, ESC, ord("i")])
    t2 = str(l2.get("text") or "")
    alts = [x for x in t2.split("|")] if "|" in t2 else [t2]
    if len(alts) == 1:
        out.append(LF)
        inv2 = emit_line(l2, half, True)
        if inv2:
            out += bytes([FF, ESC, ord("i")])
        out.append(CR)
    else:
        for alt in alts:
            out.append(LF)
            inv2 = emit_line({**l2, "text": alt}, half, True)
            if inv2:
                out += bytes([FF, ESC, ord("i")])
        out.append(VT)
    return bytes(out), probs


def decompose_record(rec: bytes, db, field_name: str, ct: CodeTable) -> dict | None:
    """Best-effort extraction into structured 1-line/2-line editor state; None if complex MNEMO."""
    if not rec:
        return {"mode": "1", "line1": {"text": ""}, "line2": {"text": ""}, "full_panel": False, "suppress_stops": False}
    # Quick scan: if it uses FF/o/x/n/r/c in complex ways, fall back to MNEMO
    i, n = 0, len(rec)
    full_panel = False
    suppress_stops = False
    interval = None
    frames = [[]]
    cur = {"font": None, "spacing": None, "shift": None, "x": None, "inverse": False, "text": bytearray()}
    while i < n:
        c = rec[i]
        if c == ESC and i + 1 < n:
            k = chr(rec[i + 1])
            if k in "lphdsz" and i + 2 < n:
                v = rec[i + 2] - 0x0F
                if k == "b":
                    full_panel = True
                elif k == "s":
                    cur["x"] = v - 1
                elif k == "z":
                    interval = round(v / 10, 1)
                elif k in "lphd":
                    full_panel = True
                i += 3
                continue
            elif k == "b":
                full_panel = True; i += 2; continue
            elif k == "w":
                suppress_stops = True; i += 2; continue
            elif k == "i":
                cur["inverse"] = not cur["inverse"]; i += 2; continue
            else:
                return None
        if 0xE0 <= c <= 0xEF:
            cur["font"] = c - 0xE0
        elif 0xB0 <= c <= 0xBF:
            cur["spacing"] = c - 0xB0
        elif 0xC0 <= c <= 0xDF:
            cur["shift"] = c - 0xC0
        elif c == FF:
            pass
        elif c in (LF, VT, CR):
            frames[-1].append(cur)
            cur = {"font": None, "spacing": None, "shift": None, "x": None, "inverse": False, "text": bytearray()}
            if c == LF:
                frames.append([])
            else:
                break
        elif c >= 0x20:
            cur["text"].append(c)
        i += 1
    if cur["text"] or not frames[0]:
        frames[-1].append(cur)
    frames = [f for f in frames if f]
    if not frames or len(frames[0]) != 1:
        return None
    l1_raw = frames[0][0]
    t1 = ct.decode(bytes(l1_raw["text"]))
    if t1 is None:
        return None
    line1 = {
        "text": t1,
        "font": l1_raw["font"],
        "spacing": l1_raw["spacing"],
        "shift": l1_raw["shift"],
        "x": l1_raw["x"],
        "inverse": l1_raw["inverse"],
        "align": "center" if l1_raw["x"] is None else "left",
    }
    if len(frames) == 1:
        return {"mode": "1", "line1": line1, "line2": {"text": ""},
                "full_panel": full_panel, "suppress_stops": suppress_stops, "interval": interval}
    texts2 = []
    l2_meta = frames[1][0]
    for fr in frames[1:]:
        if len(fr) != 1:
            return None
        s2 = ct.decode(bytes(fr[0]["text"]))
        if s2 is None:
            return None
        texts2.append(s2)
    line2 = {
        "text": "|".join(texts2),
        "font": l2_meta["font"],
        "spacing": l2_meta["spacing"],
        "shift": l2_meta["shift"],
        "x": l2_meta["x"],
        "inverse": l2_meta["inverse"],
        "align": "center" if l2_meta["x"] is None else "left",
    }
    return {"mode": "2", "line1": line1, "line2": line2,
            "full_panel": full_panel, "suppress_stops": suppress_stops, "interval": interval}
