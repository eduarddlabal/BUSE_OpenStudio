"""Formatted panel texts (gBUSE1).

Bytes (as composed by the gBUSE1 editor, GBUSE1.EXE 0x44BB44):
    E0+font  B0+spacing  [ESC t p]  [ESC b]  [ESC i]  [ESC s 0x10+col]  [ESC z n]  TEXT  [ESC i]  [VT]  CR

Readable forms used in YAML:
  * dict  – {text, font, spacing, run, effect, blink, inverse, x | align, interval}
  * str   – lossless markup for anything else; plain text is just text.
            {f3} font  {m2} spacing  {run} ESC t 11h  {t0}/{t2}/{t3} effect  {/run} VT
            {inv} ESC i  {blink} ESC b  {x:24} ESC s at column 24  {z:10} ESC z 1.0s
            {l:1} {p:135} window cols  {ff} FF  {lf} LF  {1B 61 05} raw bytes  {{ literal '{'
            {icon:metro} pictogram from the icon font (see icons.py)
            CR at the end is implicit; {nocr} at the very end suppresses it.

Also implements official gBUSE1 MNEMO language (gBUSE1.doc) and gBUSE1 autoformat.
"""
from __future__ import annotations

import re

ESC, LF, VT, FF, CR = 0x1B, 0x0A, 0x0B, 0x0C, 0x0D
RUN_DEFAULT = 0x11


class TextError(ValueError):
    pass


# ------------------------------------------------------------------ structured
def encode_item(item: dict, fonts, x: int | None = None) -> tuple[bytes, list[str]]:
    """dict -> bytes. `x` (absolute start column) overrides item['x'] (used for align)."""
    font = item.get("font")
    spacing = item.get("spacing")
    effect = item.get("effect")
    run = item.get("run", False)
    if effect in (None, "") and run:
        effect = 1 if run is True else (run - 0x10 if isinstance(run, int) and run >= 0x10 else int(run))
    blink = item.get("blink", False)
    inverse = item.get("inverse", False)
    interval = item.get("interval")
    x = x if x is not None else item.get("x")
    codes, problems = fonts.encode(str(item.get("text", "")), font)
    out = bytearray()
    if font is not None:
        out.append(0xE0 + int(font))
    if spacing is not None:
        out.append(0xB0 + int(spacing))
    if interval not in (None, ""):
        z = max(0, min(239, int(round(float(interval) * 10))))
        out += bytes([ESC, ord("z"), z + 0x0F])
    if effect not in (None, 0, False, ""):
        tcode = effect if isinstance(effect, int) and effect >= 0x10 else (0x10 + int(effect))
        out += bytes([ESC, ord("t"), tcode])
    if blink:
        out += bytes([ESC, ord("b")])
    if inverse:
        out += bytes([ESC, ord("i")])
    if x is not None and not run and effect in (None, 0, False, ""):
        out += bytes([ESC, ord("s"), 0x10 + int(x)])
    out += codes
    if inverse:
        out += bytes([ESC, ord("i")])
    if blink:
        out += bytes([ESC, ord("b")])
    if run or effect in (1, 0x11):
        out.append(VT)
    out.append(CR)
    return bytes(out), problems


def _decode_structured(b: bytes, fonts):
    p = 0
    item = {}
    if p < len(b) and 0xE0 <= b[p] <= 0xEF:
        item["font"] = b[p] - 0xE0; p += 1
    if p < len(b) and 0xB0 <= b[p] <= 0xBF:
        item["spacing"] = b[p] - 0xB0; p += 1
    if b[p:p + 2] == b"\x1bt" and p + 2 < len(b):
        tb = b[p + 2]
        if tb == RUN_DEFAULT:
            item["run"] = True
        elif 0x10 <= tb <= 0x13:
            item["effect"] = tb - 0x10
        else:
            item["run"] = tb
        p += 3
    if b[p:p + 2] == b"\x1bi":
        item["inverse"] = True; p += 2
    if b[p:p + 2] == b"\x1bs" and p + 2 < len(b):
        item["x"] = b[p + 2] - 0x10; p += 3
    end = len(b)
    if end and b[end - 1] == CR:
        end -= 1
    if item.get("run") and end and b[end - 1] == VT:
        end -= 1
    if item.get("inverse") and b[end - 2:end] == b"\x1bi":
        end -= 2
    codes = b[p:end]
    if any(c < 0x20 or c >= 0xB0 for c in codes):
        return None
    text = fonts.decode(codes)
    if text is None:
        return None
    item = {"text": text, **item}
    enc, _ = encode_item(item, fonts)
    return item if enc == b else None


# ------------------------------------------------------------------ markup
_TOKEN = re.compile(r"\{\{|\{([^{}]*)\}")


def decode_markup(b: bytes, fonts) -> str:
    out = []
    i, n = 0, len(b)
    if n and b[-1] == CR:
        n -= 1
        nocr = False
    else:
        nocr = True
    while i < n:
        c = b[i]
        if 0xE0 <= c <= 0xEF:
            out.append(f"{{f{c - 0xE0}}}"); i += 1
        elif 0xB0 <= c <= 0xBF:
            out.append(f"{{m{c - 0xB0}}}"); i += 1
        elif c == ESC and i + 1 < n:
            k = b[i + 1]
            if k == ord("t") and i + 2 < n:
                tb = b[i + 2]
                if tb == RUN_DEFAULT:
                    out.append("{run}")
                elif 0x10 <= tb <= 0x13:
                    out.append(f"{{t{tb - 0x10}}}")
                else:
                    out.append(f"{{run:{tb:02X}}}")
                i += 3
            elif k == ord("i"):
                out.append("{inv}"); i += 2
            elif k == ord("b"):
                out.append("{blink}"); i += 2
            elif k == ord("s") and i + 2 < n:
                out.append(f"{{x:{b[i + 2] - 0x10}}}"); i += 3
            elif k == ord("z") and i + 2 < n:
                out.append(f"{{z:{b[i + 2] - 0x0F}}}"); i += 3
            elif k == ord("l") and i + 2 < n:
                out.append(f"{{l:{b[i + 2] - 0x0F}}}"); i += 3
            elif k == ord("p") and i + 2 < n:
                out.append(f"{{p:{b[i + 2] - 0x0F}}}"); i += 3
            else:
                out.append("{1B}"); i += 1
        elif c == VT:
            out.append("{/run}"); i += 1
        elif c == LF:
            out.append("{lf}"); i += 1
        elif c == FF:
            out.append("{ff}"); i += 1
        elif c == ord("{"):
            out.append("{{"); i += 1
        else:
            from .icons import CODE_ICONS
            ch = fonts.codetable.decode_code(c) if 0x20 <= c < 0xE0 or c >= 0xF0 else None
            if ch is None and c in CODE_ICONS:
                ch = "{icon:%s}" % CODE_ICONS[c]
            out.append(ch if ch is not None and ch != "{" else f"{{{c:02X}}}")
            i += 1
    if nocr:
        out.append("{nocr}")
    return "".join(out)


def encode_markup(s: str, fonts, default_font: int = 3, cr: bool = True) -> tuple[bytes, list[str]]:
    out, problems = bytearray(), []
    font = None
    nocr = False
    pos = 0
    for m in _TOKEN.finditer(s):
        out += _plain(s[pos:m.start()], fonts, font, problems)
        pos = m.end()
        tok = m.group(1)
        if m.group(0) == "{{":
            out += _plain("{", fonts, font, problems)
            continue
        t = tok.strip()
        if re.fullmatch(r"f\d+", t):
            font = int(t[1:]); out.append(0xE0 + font)
        elif re.fullmatch(r"m\d+", t):
            out.append(0xB0 + int(t[1:]))
        elif t == "run":
            out += bytes([ESC, ord("t"), RUN_DEFAULT])
        elif re.fullmatch(r"t[0-3]", t):
            out += bytes([ESC, ord("t"), 0x10 + int(t[1])])
        elif re.fullmatch(r"run:[0-9A-Fa-f]{1,2}", t):
            out += bytes([ESC, ord("t"), int(t[4:], 16)])
        elif t == "/run":
            out.append(VT)
        elif t == "inv":
            out += bytes([ESC, ord("i")])
        elif t == "blink":
            out += bytes([ESC, ord("b")])
        elif re.fullmatch(r"x:\d+", t):
            out += bytes([ESC, ord("s"), 0x10 + int(t[2:])])
        elif re.fullmatch(r"z:\d+", t):
            out += bytes([ESC, ord("z"), 0x0F + int(t[2:])])
        elif re.fullmatch(r"l:\d+", t):
            out += bytes([ESC, ord("l"), 0x0F + int(t[2:])])
        elif re.fullmatch(r"p:\d+", t):
            out += bytes([ESC, ord("p"), 0x0F + int(t[2:])])
        elif t == "lf":
            out.append(LF)
        elif t == "ff":
            out.append(FF)
        elif t == "nocr":
            nocr = True
        elif t.startswith("icon:"):
            from .icons import icon_bytes
            try:
                out += icon_bytes(t[5:].strip(), font if font is not None else default_font, fonts)
            except KeyError as e:
                raise TextError(str(e.args[0])) from None
        elif re.fullmatch(r"([0-9A-Fa-f]{2}\s*)+", t):
            out += bytes.fromhex(t)
        else:
            raise TextError(f"neznámá značka {{{tok}}}")
    out += _plain(s[pos:], fonts, font, problems)
    if not nocr and cr:
        out.append(CR)
    return bytes(out), problems


def _plain(s: str, fonts, font, problems) -> bytes:
    if not s:
        return b""
    codes, p = fonts.encode(s, font)
    problems += p
    return codes


# ------------------------------------------------------------------ gBUSE1 MNEMO (gBUSE1.doc)
def to_mnemo1(b: bytes, fonts) -> str:
    """Binary gBUSE1 record -> official gBUSE1 MNEMO syntax."""
    tokens, buf = [], []
    ct = fonts.codetable

    def flush():
        if buf:
            tokens.append("'" + "".join(buf) + "'")
            buf.clear()

    i, n = 0, len(b)
    while i < n:
        c = b[i]
        if 0xE0 <= c <= 0xEF:
            flush(); tokens.append(f"f{c - 0xE0}"); i += 1
        elif 0xB0 <= c <= 0xBF:
            flush(); tokens.append(f"m{c - 0xB0}"); i += 1
        elif c == ESC and i + 1 < n:
            flush()
            k = chr(b[i + 1])
            if k == "t" and i + 2 < n:
                tokens.append(f"t{b[i + 2] - 0x10}" if 0x10 <= b[i + 2] <= 0x13 else f"#27 #116 #{b[i + 2]}")
                i += 3
            elif k in "szlp" and i + 2 < n:
                tokens.append(f"{k}{b[i + 2] - 0x0F}")
                i += 3
            elif k in ("i", "b"):
                tokens.append(k); i += 2
            else:
                tokens.append(f"#27 #{b[i + 1]}"); i += 2
        elif c == FF:
            flush(); tokens.append("FF"); i += 1
        elif c == LF:
            flush(); tokens.append("LF"); i += 1
        elif c == VT:
            flush(); tokens.append("VT"); i += 1
        elif c == CR:
            flush(); tokens.append("CR"); i += 1
        elif c >= 0x20:
            ch = ct.decode_code(c)
            if ch and ch != "'":
                buf.append(ch)
            else:
                flush(); tokens.append(f"#{c}")
            i += 1
        else:
            flush(); tokens.append(f"#{c}"); i += 1
    flush()
    return " ".join(tokens)


def from_mnemo1(s: str, fonts) -> tuple[bytes, list[str]]:
    """Compile official gBUSE1 MNEMO syntax -> binary record bytes."""
    out, probs = bytearray(), []
    ct = fonts.codetable
    pos, n = 0, len(s)
    while pos < n:
        if s[pos].isspace():
            pos += 1
            continue
        if s[pos] == "'":
            end = s.find("'", pos + 1)
            lit = s[pos + 1:] if end == -1 else s[pos + 1:end]
            if end == -1:
                probs.append("neuzavřený řetězec v apostrofech")
                pos = n
            else:
                pos = end + 1
            for ch in lit:
                c = ct.encode_char(ch)
                if c is None:
                    probs.append(f"znak {ch!r} není v kódové tabulce")
                else:
                    out.append(c)
            continue
        if s[pos] == "#":
            m = re.match(r"#(\d+)", s[pos:])
            if not m:
                probs.append(f"chybný kód na pozici {pos}"); pos += 1; continue
            out.append(int(m.group(1)) & 0xFF)
            pos += m.end()
            continue
        m = re.match(r"[A-Za-z0-9]+", s[pos:])
        if not m:
            probs.append(f"neznámý znak {s[pos]!r}"); pos += 1; continue
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
        elif tok in ("i", "b"):
            out += bytes([ESC, ord(tok)])
        elif len(tok) >= 2 and tok[0] in "fmtszl p".replace(" ", "") and tok[1:].isdigit():
            cmd, val = tok[0], int(tok[1:])
            if cmd == "f":
                out.append(0xE0 + (val & 0x0F))
            elif cmd == "m":
                out.append(0xB0 + (val & 0x0F))
            elif cmd == "t":
                out += bytes([ESC, ord("t"), 0x10 + (val & 0x0F)])
            else:
                out += bytes([ESC, ord(cmd), max(0, min(255, val + 0x0F))])
        else:
            probs.append(f"neznámý příkaz MNEMO {tok!r}")
    return bytes(out), probs


def autoformat_gbuse1(text: str, fonts, width: int = 111) -> tuple[int, int]:
    """gBUSE1 autoformat (gBUSE1.doc): start at font 3 spacing 2, step down spacing then font 3..0
    until text fits `width`; fallback to font 0 spacing 1."""
    codes, _ = fonts.encode(text)
    avail = {f.id for f in fonts.fonts}
    for fid in (3, 2, 1, 0):
        if fid not in avail:
            continue
        for sp in (2, 1, 0) if fid == 3 else (2, 1):
            if fonts.width(fid, codes, sp) <= width:
                return fid, sp
    return (0 if 0 in avail else next(iter(avail), 3)), 1


# ------------------------------------------------------------------ public
def decode(b: bytes, fonts):
    """bytes -> YAML value (plain str, markup str or dict)."""
    item = _decode_structured(b, fonts)
    if item is not None:
        if set(item) == {"text"}:
            return item["text"]
        return item
    return decode_markup(b, fonts)


def encode(value, fonts, x: int | None = None) -> tuple[bytes, list[str]]:
    """YAML value -> bytes."""
    if isinstance(value, dict):
        return encode_item(value, fonts, x)
    return encode_markup("" if value is None else str(value), fonts)


def plain_text(value) -> str:
    """Human text without formatting (for lists / search)."""
    if isinstance(value, dict):
        return str(value.get("text", ""))
    return _TOKEN.sub(lambda m: "{" if m.group(0) == "{{" else "", str(value or ""))
