"""Panel fonts and the code table.

Binary font block:  [E0+id] [length BE16] { [code] [nbytes] [height] [column bytes…] }… [tail, usually FF 00]
Column bytes: ceil(height/8) bytes per column, MSB of first byte = top dot (row 0).
Code table (160 B, verified in GBUSE0.EXE & GBUSE1.EXE):
  - bytes 0..142   (143 B): panel codes 0x21..0xAF -> Windows-1250 byte (0 = undefined)
  - bytes 143..158 (16 B):  panel codes 0xF0..0xFF -> Windows-1250 byte (0 = undefined)
  - byte 159       (1 B):   0xFF terminator

Human-readable form (YAML):
  codetable: {undefined: [0x40], map: {0x80: Č, …}}     # 0x21–0x7F are ASCII unless listed
  fonts:
    - id: 3
      glyphs:
        A: |
          ......
          ..##..
          …                                           # `height` rows (8 for gBUSE1, 1–28 for gBUSE0), '#' = dot on
"""
from __future__ import annotations

from dataclasses import dataclass, field

ON, OFF = "#", "."

CODETABLE_LEN = 160


def _ct_index(code: int) -> int | None:
    if 0x21 <= code <= 0xAF:
        return code - 0x21
    if 0xF0 <= code <= 0xFF:
        return 143 + (code - 0xF0)
    return None


def _ct_code(idx: int) -> int | None:
    if 0 <= idx < 143:
        return 0x21 + idx
    if 143 <= idx < 159:
        return 0xF0 + (idx - 143)
    return None


@dataclass
class Glyph:
    code: int
    height: int
    data: bytes                     # column bytes as stored

    @property
    def nbytes_col(self) -> int:
        return max(1, (self.height + 7) // 8)

    @property
    def width(self) -> int:
        nb = self.nbytes_col
        return len(self.data) // nb

    def columns(self) -> list[int]:
        """Column bitmasks, bit 7 = top (only for height <= 8)."""
        return list(self.data)

    def column_ints(self) -> list[int]:
        """Column bitmasks as ints where bit `y` = row `y` from the top (0 = top)."""
        nb = self.nbytes_col
        out = []
        for x in range(self.width):
            v = int.from_bytes(self.data[x * nb:(x + 1) * nb], "big")
            col = 0
            for y in range(self.height):
                if (v >> (nb * 8 - 1 - y)) & 1:
                    col |= 1 << y
            out.append(col)
        return out

    def rows(self) -> list[str]:
        h = max(1, self.height)
        nb = self.nbytes_col
        w = self.width
        out = []
        for y in range(h):
            shift = nb * 8 - 1 - y
            line = []
            for x in range(w):
                v = int.from_bytes(self.data[x * nb:(x + 1) * nb], "big")
                line.append(ON if (v >> shift) & 1 else OFF)
            out.append("".join(line))
        return out

    @staticmethod
    def from_rows(code: int, rows: list[str], height: int | None = None) -> "Glyph":
        if height is None:
            height = len(rows) or 8
        height = max(1, min(28, int(height)))
        rows = (list(rows) + [""] * height)[:height]
        w = max((len(r) for r in rows), default=0)
        nb = max(1, (height + 7) // 8)
        cols = bytearray(w * nb)
        for y, r in enumerate(rows):
            bit_pos = nb * 8 - 1 - y
            byte_off = nb - 1 - (bit_pos // 8)
            bit_in_byte = bit_pos % 8
            for x, ch in enumerate(r.ljust(w, OFF)):
                if ch not in (OFF, " ", "-"):
                    cols[x * nb + byte_off] |= 1 << bit_in_byte
        return Glyph(code, height, bytes(cols))


@dataclass
class Font:
    id: int
    glyphs: dict = field(default_factory=dict)     # code -> Glyph (insertion order = file order)
    tail: bytes = b"\xff\x00"

    def to_bytes(self) -> bytes:
        body = bytearray()
        for g in self.glyphs.values():
            body += bytes([g.code, len(g.data), g.height]) + g.data
        body += self.tail
        return bytes([0xE0 + self.id, len(body) >> 8, len(body) & 0xFF]) + bytes(body)

    @staticmethod
    def from_bytes(b: bytes) -> "Font":
        f = Font(b[0] - 0xE0)
        end = 3 + (b[1] << 8 | b[2])
        p = 3
        while p + 2 < end and b[p] != 0xFF:
            code, nb, h = b[p], b[p + 1], b[p + 2]
            f.glyphs[code] = Glyph(code, h, bytes(b[p + 3:p + 3 + nb]))
            p += 3 + nb
        f.tail = bytes(b[p:end])
        return f


class CodeTable:
    """Panel code <-> Unicode character."""

    def __init__(self, raw: bytes):
        raw = bytes(raw)
        if len(raw) < CODETABLE_LEN:
            raw = raw + b"\x00" * (CODETABLE_LEN - 1 - len(raw)) + b"\xff"
        self.raw = raw[:CODETABLE_LEN]
        self.to_char = {0x20: " "}
        for i in range(159):
            w = self.raw[i]
            code = _ct_code(i)
            if w and code is not None:
                self.to_char[code] = bytes([w]).decode("cp1250", "replace")
        self.to_code = {}
        for code, ch in self.to_char.items():
            self.to_code.setdefault(ch, code)

    def encode_char(self, ch: str):
        return self.to_code.get(ch)

    def decode_code(self, code: int):
        return self.to_char.get(code)

    def decode(self, codes: bytes) -> str | None:
        out = []
        for c in codes:
            ch = self.to_char.get(c)
            if ch is None:
                return None
            out.append(ch)
        return "".join(out)

    # ---- YAML -------------------------------------------------------
    def to_yaml(self) -> dict:
        undefined, mapping = [], {}
        for i in range(159):
            code = _ct_code(i)
            w = self.raw[i]
            if w == 0:
                undefined.append(code)
            elif not (code < 0x80 and w == code):
                mapping[code] = bytes([w]).decode("cp1250", "replace")
        out = {"undefined": " ".join(f"{c:02X}" for c in undefined),
               "map": {f"{c:02X}": ch for c, ch in mapping.items()}}
        if self.raw[159] != 0xFF:
            out["tail"] = f"{self.raw[159]:02X}"
        return out

    @staticmethod
    def from_yaml(d: dict) -> "CodeTable":
        raw = bytearray(CODETABLE_LEN)
        for code in range(0x21, 0x80):
            raw[code - 0x21] = code
        raw[159] = 0xFF
        und = d.get("undefined", [])
        if isinstance(und, str):
            und = [int(x, 16) for x in und.split()]
        for code in und:
            c = int(code)
            idx = _ct_index(c)
            if idx is None and 0xB0 <= c <= 0xBF:     # legacy YAML offset
                idx = 143 + (c - 0xB0)
            if idx is not None:
                raw[idx] = 0
        for code, ch in (d.get("map") or {}).items():
            c = int(code, 16) if isinstance(code, str) else int(code)
            bval = ch.encode("cp1250")[0] if ch else 0
            idx = _ct_index(c)
            if idx is not None:
                raw[idx] = bval
            elif 0xB0 <= c <= 0xBF:                   # legacy YAML offset
                raw[143 + (c - 0xB0)] = bval
            elif c == 0xC0:                           # legacy YAML terminator slot
                raw[159] = bval
        if "tail" in d:
            raw[159] = int(str(d["tail"]), 16)
        return CodeTable(bytes(raw))


def default_codetable() -> CodeTable:
    """Standard Kamenický + ASCII code table as used by gBUSE."""
    from .gbuse_font import gbuse_fontset
    return gbuse_fontset().codetable


class FontSet:
    def __init__(self, fonts: list[Font], codetable: CodeTable, tail: bytes = b"\xff\x00"):
        self.fonts = fonts
        self.codetable = codetable
        self.tail = tail          # bytes between last font and code table

    def font(self, fid: int) -> Font | None:
        for f in self.fonts:
            if f.id == fid:
                return f
        return None

    def ensure_font(self, fid: int) -> Font:
        f = self.font(fid)
        if f is None:
            f = Font(fid)
            self.fonts.append(f)
            self.fonts.sort(key=lambda x: x.id)
        return f

    def encode(self, text: str, fid: int | None = None) -> tuple[bytes, list[str]]:
        """Text -> panel codes; returns (codes, list of problems)."""
        out, problems = bytearray(), []
        f = self.font(fid) if fid is not None else None
        for ch in text:
            c = self.codetable.encode_char(ch)
            if c is None:
                problems.append(f"znak {ch!r} není v kódové tabulce")
                continue
            if f is not None and c != 0x20 and c not in f.glyphs:
                problems.append(f"znak {ch!r} není ve fontu {fid}")
            out.append(c)
        return bytes(out), problems

    def decode(self, codes: bytes) -> str | None:
        """Panel codes -> text, or None if a code has no character."""
        out = []
        for c in codes:
            ch = self.codetable.decode_code(c)
            if ch is None:
                return None
            out.append(ch)
        return "".join(out)

    def glyph_columns(self, fid: int, code: int) -> list[int]:
        f = self.font(fid)
        if f and code in f.glyphs:
            return f.glyphs[code].columns()
        if code == 0x20 and f:
            return [0, 0]
        return [0, 0]

    def text_columns(self, fid: int, codes: bytes, spacing: int) -> list[int]:
        cols = []
        for i, c in enumerate(codes):
            if i:
                cols += [0] * spacing
            cols += self.glyph_columns(fid, c)
        return cols

    def width(self, fid: int, codes: bytes, spacing: int) -> int:
        return len(self.text_columns(fid, codes, spacing))

    # ---- Font Manager operations ------------------------------------
    def copy_glyph(self, src: "FontSet", src_font: int, src_code: int, dst_font: int, dst_code: int) -> bool:
        sf = src.font(src_font)
        if sf is None or src_code not in sf.glyphs:
            return False
        g = sf.glyphs[src_code]
        df = self.ensure_font(dst_font)
        glyphs = dict(df.glyphs)
        glyphs[dst_code] = Glyph(dst_code, g.height, g.data)
        df.glyphs = dict(sorted(glyphs.items()))
        return True

    def copy_font(self, src: "FontSet", src_font: int, dst_font: int, overwrite: bool = True) -> int:
        sf = src.font(src_font)
        if sf is None:
            return 0
        df = self.ensure_font(dst_font)
        glyphs = dict(df.glyphs)
        n = 0
        for code, g in sf.glyphs.items():
            if overwrite or code not in glyphs:
                glyphs[code] = Glyph(code, g.height, g.data)
                n += 1
        df.glyphs = dict(sorted(glyphs.items()))
        return n

    def delete_font(self, fid: int):
        self.fonts = [f for f in self.fonts if f.id != fid]

    def delete_code_all(self, code: int):
        for f in self.fonts:
            f.glyphs.pop(code, None)

    # ---- binary & .fnt ----------------------------------------------
    @staticmethod
    def from_image(im) -> "FontSet":
        return FontSet([Font.from_bytes(b) for b in im.fonts], CodeTable(im.codetable), im.fonts_tail)

    def to_image(self, im):
        im.fonts = [f.to_bytes() for f in self.fonts]
        im.codetable = self.codetable.raw
        im.fonts_tail = self.tail

    def to_fnt_bytes(self) -> bytes:
        return b"".join(f.to_bytes() for f in self.fonts) + self.tail + self.codetable.raw

    @staticmethod
    def from_fnt_bytes(b: bytes) -> "FontSet":
        fonts, q = [], 0
        while q < len(b) and 0xE0 <= b[q] <= 0xEF:
            f = Font.from_bytes(b[q:])
            fonts.append(f)
            q += 3 + (b[q + 1] << 8 | b[q + 2])
        tail = bytes(b[q:q + 2]) if q + 2 <= len(b) else b"\xff\x00"
        ct = bytes(b[q + 2:q + 2 + CODETABLE_LEN]) if q + 2 + CODETABLE_LEN <= len(b) else default_codetable().raw
        return FontSet(fonts, CodeTable(ct), tail)

    # ---- YAML -------------------------------------------------------
    def glyph_key(self, code: int, used: set) -> str:
        ch = self.codetable.decode_code(code)
        if ch and ch.strip() and len(ch) == 1 and not ch.startswith("#") and ch not in used:
            return ch
        return f"#{code:02X}"

    def to_yaml(self) -> dict:
        fonts = []
        for f in self.fonts:
            glyphs, used = {}, set()
            for code, g in f.glyphs.items():
                key = self.glyph_key(code, used)
                used.add(key)
                nb = g.nbytes_col
                if g.data and len(g.data) % nb == 0:
                    glyphs[key] = LiteralRows(g.rows())
                elif g.height == 8 and not g.data:
                    glyphs[key] = ""
                else:
                    glyphs[key] = {"height": g.height, "hex": g.data.hex(" ")}
            entry = {"id": f.id, "glyphs": glyphs}
            if f.tail != b"\xff\x00":
                entry["tail"] = f.tail.hex(" ")
            fonts.append(entry)
        d = {"codetable": self.codetable.to_yaml(), "fonts": fonts}
        if self.tail != b"\xff\x00":
            d["tail"] = self.tail.hex(" ")
        return d

    @staticmethod
    def from_yaml(d: dict) -> "FontSet":
        ct = CodeTable.from_yaml(d.get("codetable") or {})
        fonts = []
        for fd in d.get("fonts", []):
            f = Font(int(fd["id"]))
            for key, v in (fd.get("glyphs") or {}).items():
                key = str(key)
                code = int(key[1:], 16) if key.startswith("#") and len(key) == 3 else ct.encode_char(key)
                if code is None:
                    raise ValueError(f"font {f.id}: znak {key!r} není v kódové tabulce")
                if isinstance(v, dict):
                    g = Glyph(code, int(v["height"]), bytes.fromhex(v["hex"]))
                elif not v:
                    g = Glyph(code, 8, b"")
                else:
                    lines = str(v).splitlines()
                    g = Glyph.from_rows(code, lines, height=len(lines) or 8)
                f.glyphs[code] = g
            if "tail" in fd:
                f.tail = bytes.fromhex(fd["tail"])
            fonts.append(f)
        return FontSet(fonts, ct, bytes.fromhex(d["tail"]) if "tail" in d else b"\xff\x00")


class LiteralRows(str):
    """Marker type so the YAML dumper writes glyph rows as a literal block."""

    def __new__(cls, rows):
        return super().__new__(cls, "\n".join(rows) + "\n")
