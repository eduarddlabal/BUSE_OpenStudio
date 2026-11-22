"""Panel fonts and the code table.

Binary font block:  [E0+id] [length BE16] { [code] [nbytes] [height] [column bytes…] }… [tail, usually FF 00]
Column byte: MSB = top dot, bit 0 = bottom dot (height 8).
Code table (160 B): panel code 0x21+i -> Windows-1250 byte (0 = undefined); upper half is Kamenický order.

Human-readable form (YAML):
  codetable: {undefined: [0x40], map: {0x80: Č, …}}     # 0x21–0x7F are ASCII unless listed
  fonts:
    - id: 3
      glyphs:
        A: |
          ......
          ..##..
          …                                           # 8 rows, '#' = dot on
"""
from __future__ import annotations

from dataclasses import dataclass, field

ON, OFF = "#", "."


@dataclass
class Glyph:
    code: int
    height: int
    data: bytes                     # column bytes as stored

    @property
    def width(self) -> int:
        return len(self.data) if self.height <= 8 else len(self.data) // ((self.height + 7) // 8)

    def columns(self) -> list[int]:
        """Column bitmasks, bit 7 = top (only for height <= 8)."""
        return list(self.data)

    def rows(self) -> list[str]:
        return ["".join(ON if c >> (7 - y) & 1 else OFF for c in self.data) for y in range(8)]

    @staticmethod
    def from_rows(code: int, rows: list[str], height: int = 8) -> "Glyph":
        rows = (rows + [""] * 8)[:8]
        w = max(len(r) for r in rows)
        cols = bytearray(w)
        for y, r in enumerate(rows):
            for x, ch in enumerate(r.ljust(w, OFF)):
                if ch not in (OFF, " ", "-"):
                    cols[x] |= 1 << (7 - y)
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
        self.raw = bytes(raw)
        self.to_char = {0x20: " "}
        for i, w in enumerate(self.raw):
            if w:
                self.to_char[0x21 + i] = bytes([w]).decode("cp1250", "replace")
        self.to_code = {}
        for code, ch in self.to_char.items():
            self.to_code.setdefault(ch, code)

    def encode_char(self, ch: str):
        return self.to_code.get(ch)

    def decode_code(self, code: int):
        return self.to_char.get(code)

    # ---- YAML -------------------------------------------------------
    def to_yaml(self) -> dict:
        undefined, mapping = [], {}
        for i, w in enumerate(self.raw):
            code = 0x21 + i
            if w == 0:
                undefined.append(code)
            elif not (code < 0x80 and w == code):
                mapping[code] = bytes([w]).decode("cp1250", "replace")
        return {"undefined": " ".join(f"{c:02X}" for c in undefined),
                "map": {f"{c:02X}": ch for c, ch in mapping.items()}}

    @staticmethod
    def from_yaml(d: dict) -> "CodeTable":
        raw = bytearray(code if code < 0x80 else 0 for code in range(0x21, 0x21 + 160))
        und = d.get("undefined", [])
        if isinstance(und, str):
            und = [int(x, 16) for x in und.split()]
        for code in und:
            raw[int(code) - 0x21] = 0
        for code, ch in (d.get("map") or {}).items():
            code = int(code, 16) if isinstance(code, str) else int(code)
            raw[code - 0x21] = ch.encode("cp1250")[0]
        return CodeTable(bytes(raw))


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

    # ---- binary -----------------------------------------------------
    @staticmethod
    def from_image(im) -> "FontSet":
        return FontSet([Font.from_bytes(b) for b in im.fonts], CodeTable(im.codetable), im.fonts_tail)

    def to_image(self, im):
        im.fonts = [f.to_bytes() for f in self.fonts]
        im.codetable = self.codetable.raw
        im.fonts_tail = self.tail

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
                if g.height == 8:
                    glyphs[key] = LiteralRows(g.rows()) if g.data else ""
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
                    g = Glyph.from_rows(code, str(v).splitlines())
                f.glyphs[code] = g
            if "tail" in fd:
                f.tail = bytes.fromhex(fd["tail"])
            fonts.append(f)
        return FontSet(fonts, ct, bytes.fromhex(d["tail"]) if "tail" in d else b"\xff\x00")


class LiteralRows(str):
    """Marker type so the YAML dumper writes glyph rows as a literal block."""

    def __new__(cls, rows):
        return super().__new__(cls, "\n".join(rows) + "\n")
