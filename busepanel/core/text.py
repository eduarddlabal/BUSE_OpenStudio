"""Formatted panel texts.

Bytes (as composed by the gBUSE1 editor, GBUSE1.EXE 0x44BB44):
    E0+font  B0+spacing  [ESC t p]  [ESC i]  [ESC s 0x10+col]  TEXT  [ESC i]  [VT]  CR

Readable forms used in YAML:
  * dict  – {text, font, spacing, run, inverse, x | align}
  * str   – lossless markup for anything else; plain text is just text.
            {f3} font  {m2} spacing  {run} ESC t 11h  {run:12} ESC t 12h  {/run} VT
            {inv} ESC i  {x:24} ESC s at column 24  {1B 61 05} raw bytes  {{ literal '{'
            {icon:metro} pictogram from the icon font (see icons.py)
            CR at the end is implicit; {nocr} at the very end suppresses it.
"""
from __future__ import annotations

import re

ESC, VT, CR = 0x1B, 0x0B, 0x0D
RUN_DEFAULT = 0x11


class TextError(ValueError):
    pass


# ------------------------------------------------------------------ structured
def encode_item(item: dict, fonts, x: int | None = None) -> tuple[bytes, list[str]]:
    """dict -> bytes. `x` (absolute start column) overrides item['x'] (used for align)."""
    font = item.get("font")
    spacing = item.get("spacing")
    run = item.get("run", False)
    inverse = item.get("inverse", False)
    x = x if x is not None else item.get("x")
    codes, problems = fonts.encode(str(item.get("text", "")), font)
    out = bytearray()
    if font is not None:
        out.append(0xE0 + int(font))
    if spacing is not None:
        out.append(0xB0 + int(spacing))
    if run:
        out += bytes([ESC, ord("t"), run if isinstance(run, int) and run is not True else RUN_DEFAULT])
    if inverse:
        out += bytes([ESC, ord("i")])
    if x is not None and not run:
        out += bytes([ESC, ord("s"), 0x10 + int(x)])
    out += codes
    if inverse:
        out += bytes([ESC, ord("i")])
    if run:
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
        item["run"] = True if b[p + 2] == RUN_DEFAULT else b[p + 2]; p += 3
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
                out.append("{run}" if b[i + 2] == RUN_DEFAULT else f"{{run:{b[i + 2]:02X}}}"); i += 3
            elif k == ord("i"):
                out.append("{inv}"); i += 2
            elif k == ord("s") and i + 2 < n:
                out.append(f"{{x:{b[i + 2] - 0x10}}}"); i += 3
            else:
                out.append("{1B}"); i += 1
        elif c == VT:
            out.append("{/run}"); i += 1
        elif c == ord("{"):
            out.append("{{"); i += 1
        else:
            from .icons import CODE_ICONS
            ch = fonts.codetable.decode_code(c) if 0x20 <= c < 0xE0 else None
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
        elif re.fullmatch(r"run:[0-9A-Fa-f]{1,2}", t):
            out += bytes([ESC, ord("t"), int(t[4:], 16)])
        elif t == "/run":
            out.append(VT)
        elif t == "inv":
            out += bytes([ESC, ord("i")])
        elif re.fullmatch(r"x:\d+", t):
            out += bytes([ESC, ord("s"), 0x10 + int(t[2:])])
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
