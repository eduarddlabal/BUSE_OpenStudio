"""gBUSE0 data image (exterior panels, magic 0x57) – read-only.

Header (0x50 B, GBUSE0.EXE 0x48E400, verified on sample gBUSE0 files):

  00      57 (magic; 0x55 = old variant)
  01–08   line field:  row from, row to, column from, column to   (0-based, rows counted from the BOTTOM)
          dest field:  row from, row to, column from, column to
  09–0C   enabled IBIS telegrams (bit masks; bit meaning not fully known [ODHAD])
  0D–10   line presets: font (E0+n), spacing, shift (posun), interval (0.1 s)
  11–14   dest presets
  15/17/19/1B  big-endian pointers FNT, LIN, CIL, CIL4 (empty = 0x004F -> FF)
  1F–2E   data version (16 chars, CR padded) – the answer to aV
  2F–32   stop field ("druhý řádek", DRU): rows / columns like 01–08
  33–36   stop field presets
  37      pointer DRU (stops, 4-digit keys, telegram zD)

Records: KEY (ASCII digits) + LENGTH (16 bit LE) + CONTENT, list ends with FF.
Fonts: the same block format as gBUSE1, glyphs up to 28 dots high (ceil(h/8) bytes per column,
MSB of the first byte = top dot).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core.fonts import Font
from ..core.image import hex_to_image

MAGIC = 0x57
EMPTY = 0x004F
PTR = {"FNT": 0x15, "LIN": 0x17, "CIL": 0x19, "CIL4": 0x1B, "DRU": 0x37}
KEYLEN = {"LIN": 3, "CIL": 3, "CIL4": 4, "DRU": 4}


@dataclass
class Field:
    """Display field (window) in 0-based panel coordinates: x = column from the left, y = row from the TOP."""
    x0: int
    x1: int
    y0: int
    y1: int
    font: int = 3
    spacing: int = 1
    shift: int = 0
    interval: float = 1.0

    @property
    def width(self) -> int:
        return self.x1 - self.x0 + 1

    @property
    def height(self) -> int:
        return self.y1 - self.y0 + 1

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Database0:
    columns: int
    rows: int
    fields: dict = field(default_factory=dict)       # "line" / "dest" / "stop" -> Field
    fonts: dict = field(default_factory=dict)        # id -> Font
    records: dict = field(default_factory=dict)      # "LIN"/"CIL"/"CIL4"/"DRU" -> {key: bytes}
    version: str = ""
    commands: bytes = b""
    source: str = ""

    def glyph(self, fid: int, code: int):
        f = self.fonts.get(fid)
        return f.glyphs.get(code) if f else None

    def summary(self) -> dict:
        return {"columns": self.columns, "rows": self.rows, "version": self.version, "source": self.source,
                "fields": {k: v.to_dict() for k, v in self.fields.items()},
                "fonts": sorted(self.fonts), "records": {k: len(v) for k, v in self.records.items()}}


def _field(h: bytes, at: int, pre: int, rows: int) -> Field:
    r0, r1, c0, c1 = h[at:at + 4]
    font, spacing, shift, interval = h[pre:pre + 4]
    # rows are counted from the bottom in the header -> convert to rows from the top
    return Field(c0, c1, rows - 1 - r1, rows - 1 - r0, font=font & 0x0F, spacing=spacing & 0x0F,
                 shift=shift & 0x1F, interval=(interval or 10) / 10)


def _ptr(img: bytes, off: int) -> int:
    return img[off] << 8 | img[off + 1]


def _records(img: bytes, ptr: int, keylen: int) -> dict:
    out, q = {}, ptr
    while q + keylen + 2 <= len(img) and img[q] != 0xFF:
        key = img[q:q + keylen].decode("latin1")
        n = img[q + keylen] | img[q + keylen + 1] << 8
        out[key] = bytes(img[q + keylen + 2:q + keylen + 2 + n])
        q += keylen + 2 + n
    return out


def _fonts(img: bytes, ptr: int) -> dict:
    fonts, q = {}, ptr
    while q < len(img) and 0xE0 <= img[q] <= 0xEF:
        f = Font.from_bytes(img[q:])
        fonts[f.id] = f
        q += 3 + (img[q + 1] << 8 | img[q + 2])
    return fonts


def parse(img: bytes, source: str = "") -> Database0:
    if not img or img[0] != MAGIC:
        raise ValueError("nejde o data gBUSE0 vnějšího panelu (očekáván začátek 0x57)")
    h = img[:0x50]
    line_rows = h[2] + 1
    rows = max(line_rows, h[6] + 1, 1)
    columns = max(h[4], h[8]) + 1
    db = Database0(columns, rows, commands=bytes(h[0x09:0x0D]), source=source,
                   version=h[0x1F:0x2F].split(b"\r")[0].decode("cp1250", "replace").strip())
    ptrs = {k: _ptr(img, off) for k, off in PTR.items()}
    db.fonts = _fonts(img, ptrs["FNT"])
    for name, keylen in KEYLEN.items():
        if ptrs[name] not in (EMPTY, 0) and ptrs[name] < len(img):
            db.records[name] = _records(img, ptrs[name], keylen)
    if "LIN" in db.records:
        db.fields["line"] = _field(h, 0x01, 0x0D, rows)
    if "CIL" in db.records or "CIL4" in db.records:
        db.fields["dest"] = _field(h, 0x05, 0x11, rows)
    if "DRU" in db.records:
        db.fields["stop"] = _field(h, 0x2F, 0x33, rows)
    if db.fields:            # the panel is as wide as its fields (a rear panel has only the line field)
        db.columns = max(f.x1 for f in db.fields.values()) + 1
    return db


def load_hex(path) -> Database0:
    with open(path, encoding="latin1") as f:
        return parse(hex_to_image(f.read()), source=str(path))
