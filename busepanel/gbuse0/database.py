"""gBUSE0 data image (exterior panels, magic 0x57 / 0x55) <-> sections <-> Intel HEX.

Header (0x50 B for 0x57 FW 2.11–3.18, 0x40 B for 0x55 FW 1.6, GBUSE0.EXE 0x48E468):

  00      57 (magic; 0x55 = FW 1.6)
  01–04   line field:  r0, r1, c0, c1 (0-based, rows counted from the BOTTOM)
  05–08   dest field:  r0, r1, c0, c1
  09–0A   enabled IBIS telegrams (CMD_BITS_0)
  0B      properties byte (low 3 bits + P1–P5 in bits 3–7)
  0C      reserved (00; 80 in 0x55)
  0D–10   line presets: font (E0+n), spacing, shift (posun), interval (0.1 s)
  11–14   dest presets
  15/17/19/1B/1D  BE16 pointers FNT, LIN, CIL, unused(004F), KOD(empty=0000)
  1F–2E   data version (16 chars, CR padded, panel-encoded) – answer to aV
  -- (0x57 only) --
  2F–32   stop field ("druhý řádek", DRU): r0, r1, c0, c1
  33–36   stop field presets
  37/39/3B/3D/3F  BE16 pointers DRU, AFC, AFD, STR, CIL4 (empty=004F)
  42–44   24-bit DRU pointer for FW >= 3.08 (addr_hi, addr_lo, bank; empty=00 4F 00)
  4D      brightness (jas)
  4E      panel number (číslo panelu)
  4F      FF

Records: KEY (ASCII digits) + LENGTH (16-bit LE) + CONTENT, list ends with FF.
Fonts: block format (E0+n + len BE16 + glyphs + FF 00 + 160 B codetable), or flat FW 1.6 format.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.fonts import CODETABLE_LEN, CodeTable, Font, FontSet, Glyph, default_codetable
from ..core.image import align32, blocks_to_hex, blocks_to_image, hex_to_image

MAGIC = 0x57
MAGIC_OLD = 0x55
EMPTY_057 = 0x004F
EMPTY_055 = 0x003F
HEADER_SIZE_057 = 0x50
HEADER_SIZE_055 = 0x40

PTR_057 = {
    "FNT": 0x15, "LIN": 0x17, "CIL": 0x19, "UNUSED": 0x1B, "KOD": 0x1D,
    "DRU": 0x37, "AFC": 0x39, "AFD": 0x3B, "STR": 0x3D, "CIL4": 0x3F,
}
PTR_055 = {"FNT": 0x15, "LIN": 0x17, "CIL": 0x19, "UNUSED": 0x1B, "KOD": 0x1D}
KEYLEN = {"LIN": 3, "CIL": 3, "CIL4": 4, "DRU": 4}

# Header bytes 0x09..0x0A -> enabled IBIS commands (verified GBUSE0.EXE 0x48EA84 & v1.21 0x492BE9)
CMD_BITS_057 = {
    0x09: ["l", "lA", "z", "zA", "zI", "zD", "aV", "aA"],
    0x0A: ["aL", "aSL", "aSZ", "aSD", "aSC", "zC", "zN", "kA"],
}
CMD_BITS_055 = {
    0x09: ["l", None, "z", "zA", "zI", None, "aV", "aA"],
}
ALL_COMMANDS_0 = [c for v in CMD_BITS_057.values() for c in v if c]

# Byte 0x0B bits 3..7 (verified GBUSE0.EXE 0x477D82..0x477E4F & TFormKONF CheckBoxP1..P5)
PROP_BITS = [
    (0x08, "flip_all"),               # P1: otáčet vždy všechny doty
    (0x10, "flip_on_current_err"),    # P2: otáčet všechny doty při chybě proudu
    (0x20, "report_current_err_7"),   # P3: vysílat chybu proudu jako status 7
    (0x40, "check_voltage"),          # P4: provádět kontrolu napájecího napětí
    (0x80, "report_voltage_err_8"),   # P5: vysílat chybu napětí jako status 8
]

# Panel layout presets (ComboBoxTyp in TFormKONF)
LAYOUT_TYPES = {
    "line": ("linka", ("line",)),
    "dest": ("cíl", ("dest",)),
    "line_dest": ("linka + cíl", ("line", "dest")),
    "dest_stop": ("cíl + zastávka", ("dest", "stop")),
    "line_dest_stop": ("linka + cíl + zastávka", ("line", "dest", "stop")),
}


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
        return max(0, self.x1 - self.x0 + 1)

    @property
    def height(self) -> int:
        return max(0, self.y1 - self.y0 + 1)

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    def to_bottom_dict(self, total_rows: int) -> dict:
        """1-based bottom-left coordinates as shown in gBUSE0 Konfigurace."""
        return {
            "c0": self.x0 + 1,
            "c1": self.x1 + 1,
            "r0": total_rows - self.y1,
            "r1": total_rows - self.y0,
            "font": self.font,
            "spacing": self.spacing,
            "shift": self.shift,
            "interval": self.interval,
        }

    @staticmethod
    def from_bottom_dict(d: dict, total_rows: int) -> "Field":
        r0 = int(d["r0"]) - 1
        r1 = int(d["r1"]) - 1
        c0 = int(d["c0"]) - 1
        c1 = int(d["c1"]) - 1
        return Field(
            c0, c1, total_rows - 1 - r1, total_rows - 1 - r0,
            font=int(d.get("font", 3)),
            spacing=int(d.get("spacing", 1)),
            shift=int(d.get("shift", 0)),
            interval=float(d.get("interval", 1.0)),
        )


@dataclass
class Database0:
    columns: int
    rows: int
    fields: dict = field(default_factory=dict)          # "line" / "dest" / "stop" -> Field
    fonts: dict = field(default_factory=dict)           # id -> Font
    records: dict = field(default_factory=dict)         # "LIN"/"CIL"/"CIL4"/"DRU" -> {key: bytes}
    record_lists: dict = field(default_factory=dict)    # section -> [(key_bytes, body_bytes)] (preserves duplicates)
    version: str = ""
    commands: bytes = b""
    source: str = ""
    # Extended fields from full decompilation
    magic: int = MAGIC
    header: bytearray = field(default_factory=lambda: bytearray(HEADER_SIZE_057))
    codetable: bytes = b""                              # 160 B
    fonts_tail: bytes = b"\xff\x00"
    kod: bytes = b""                                    # 128 B input recoding table
    format_strings: dict = field(default_factory=dict)  # 0..9 -> bytes (STR)
    afc: list = field(default_factory=list)             # [(font, spacing, shift), ...]
    afd: list = field(default_factory=list)             # [(font, spacing, shift), ...]
    properties: int = 0xF1                              # byte 0x0B
    brightness: int = 0                                 # byte 0x4D
    panel_number: int = 0                               # byte 0x4E
    fw_3x: bool = True                                  # byte 0x42..0x44 uses 00 4F 00 when DRU empty
    title_version: str = "1.00"
    title_fmt: str = "gBUSE0 - "
    fnt_sep: str = ":"                                  # ":" (front), " " (side), "." (rear)
    str_title: str = ""
    kod_title: str = ""

    def glyph(self, fid: int, code: int):
        f = self.fonts.get(fid)
        return f.glyphs.get(code) if f else None

    def fontset(self) -> FontSet:
        ct = CodeTable(self.codetable) if self.codetable else default_codetable()
        return FontSet([self.fonts[k] for k in sorted(self.fonts)], ct, self.fonts_tail)

    @property
    def command_list(self) -> list[str]:
        bits = CMD_BITS_055 if self.magic == MAGIC_OLD else CMD_BITS_057
        out = []
        for off, names in bits.items():
            b = self.commands[off - 0x09] if len(self.commands) > off - 0x09 else 0
            for i, nm in enumerate(names):
                if nm and (b >> i) & 1:
                    out.append(nm)
        return out

    @command_list.setter
    def command_list(self, cmds: list[str]):
        cset = set(cmds)
        bits = CMD_BITS_055 if self.magic == MAGIC_OLD else CMD_BITS_057
        b0 = b1 = 0
        for off, names in bits.items():
            val = 0
            for i, nm in enumerate(names):
                if nm and nm in cset:
                    val |= 1 << i
            if off == 0x09:
                b0 = val
            else:
                b1 = val
        if not isinstance(self.commands, (bytes, bytearray)) or len(self.commands) < 4:
            self.commands = bytes([b0, b1, self.properties, 0x80 if self.magic == MAGIC_OLD else 0x00])
        else:
            arr = bytearray(self.commands)
            arr[0], arr[1] = b0, b1
            self.commands = bytes(arr)

    @property
    def property_flags(self) -> list[str]:
        return [name for mask, name in PROP_BITS if self.properties & mask]

    @property_flags.setter
    def property_flags(self, flags: list[str]):
        fset = set(flags)
        p = self.properties & 0x07
        for mask, name in PROP_BITS:
            if name in fset:
                p |= mask
        self.properties = p

    @property
    def layout_type(self) -> str:
        fset = set(self.fields)
        if fset == {"line"}:
            return "line"
        if fset == {"dest"}:
            return "dest"
        if fset == {"line", "dest"}:
            return "line_dest"
        if fset == {"dest", "stop"}:
            return "dest_stop"
        return "line_dest_stop"

    def summary(self) -> dict:
        return {
            "columns": self.columns,
            "rows": self.rows,
            "version": self.version,
            "source": self.source,
            "magic": self.magic,
            "commands": self.command_list,
            "properties": self.property_flags,
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
            "fonts": sorted(self.fonts),
            "records": {k: len(v) for k, v in self.records.items()},
        }


def _field(h: bytes, at: int, pre: int, rows: int) -> Field:
    r0, r1, c0, c1 = h[at:at + 4]
    font, spacing, shift, interval = h[pre:pre + 4]
    # rows are counted from the bottom in the header -> convert to rows from the top
    return Field(c0, c1, rows - 1 - r1, rows - 1 - r0, font=font & 0x0F, spacing=spacing & 0x0F,
                 shift=shift & 0x1F, interval=(interval or 10) / 10)


def _write_field(h: bytearray, at: int, pre: int, fld: Field, rows: int):
    r0 = max(0, rows - 1 - fld.y1)
    r1 = max(0, rows - 1 - fld.y0)
    h[at:at + 4] = bytes([r0 & 0xFF, r1 & 0xFF, fld.x0 & 0xFF, fld.x1 & 0xFF])
    h[pre:pre + 4] = bytes([
        0xE0 | (fld.font & 0x0F),
        fld.spacing & 0x0F,
        fld.shift & 0x1F,
        max(1, min(255, int(round(fld.interval * 10)))) & 0xFF,
    ])


def _ptr(img: bytes, off: int) -> int:
    return img[off] << 8 | img[off + 1]


def _records(img: bytes, ptr: int, keylen: int) -> tuple[dict, list[tuple[bytes, bytes]]]:
    out, lst, q = {}, [], ptr
    while q + keylen + 2 <= len(img) and img[q] != 0xFF:
        kbytes = bytes(img[q:q + keylen])
        key = kbytes.decode("latin1")
        n = img[q + keylen] | img[q + keylen + 1] << 8
        body = bytes(img[q + keylen + 2:q + keylen + 2 + n])
        out.setdefault(key, body)
        lst.append((kbytes, body))
        q += keylen + 2 + n
    return out, lst


def _fonts_block(img: bytes, ptr: int) -> tuple[dict, bytes, bytes]:
    fonts, q = {}, ptr
    while q < len(img) and 0xE0 <= img[q] <= 0xEF:
        f = Font.from_bytes(img[q:])
        fonts[f.id] = f
        q += 3 + (img[q + 1] << 8 | img[q + 2])
    tail = bytes(img[q:q + 2]) if q + 2 <= len(img) else b"\xff\x00"
    ct = bytes(img[q + 2:q + 2 + CODETABLE_LEN]) if q + 2 + CODETABLE_LEN <= len(img) else b""
    return fonts, tail, ct


def _fonts_flat_055(img: bytes, ptr: int) -> tuple[dict, bytes, bytes]:
    """FW 1.6 flat font table: [code][0xE0+fid][nb][h][data…] ... FF 00 [codetable 160B]."""
    fonts, q = {}, ptr
    while q + 3 < len(img) and img[q] != 0xFF:
        code, fb, nb, h = img[q], img[q + 1], img[q + 2], img[q + 3]
        fid = fb & 0x0F
        f = fonts.setdefault(fid, Font(fid))
        f.glyphs[code] = Glyph(code, h, bytes(img[q + 4:q + 4 + nb]))
        q += 4 + nb
    tail = bytes(img[q:q + 2]) if q + 2 <= len(img) else b"\xff\x00"
    ct = bytes(img[q + 2:q + 2 + CODETABLE_LEN]) if q + 2 + CODETABLE_LEN <= len(img) else b""
    return {k: fonts[k] for k in sorted(fonts)}, tail, ct


def _af_parse(img: bytes, ptr: int) -> list[tuple[int, int, int]]:
    out, q = [], ptr
    while q + 2 < len(img) and img[q] != 0xFF:
        out.append((img[q] & 0x0F, img[q + 1] & 0x0F, img[q + 2] & 0x1F))
        q += 3
    return out


def _af_bytes(entries: list[tuple[int, int, int]]) -> bytes:
    out = bytearray()
    for f, sp, sh in entries[:16]:
        out += bytes([0xE0 | (int(f) & 0x0F), int(sp) & 0x0F, int(sh) & 0x1F])
    return bytes(out + b"\xff")


def _str_parse(img: bytes, ptr: int) -> dict[int, bytes]:
    out, q = {}, ptr
    while q + 1 < len(img) and img[q] != 0xFF:
        d = img[q] - ord("0")
        ln = img[q + 1]
        out[d] = bytes(img[q + 2:q + 2 + ln])
        q += 2 + ln
    return out


def _str_bytes(strs: dict[int, bytes]) -> bytes:
    out = bytearray()
    for d in sorted(strs):
        b = strs[d]
        out += bytes([ord("0") + int(d), len(b)]) + b
    return bytes(out)


def parse(img: bytes, source: str = "") -> Database0:
    if not img or img[0] not in (MAGIC, MAGIC_OLD):
        raise ValueError("nejde o data gBUSE0 vnějšího panelu (očekáván začátek 0x57 nebo 0x55)")
    magic = img[0]
    hsize = HEADER_SIZE_055 if magic == MAGIC_OLD else HEADER_SIZE_057
    empty = EMPTY_055 if magic == MAGIC_OLD else EMPTY_057
    ptr_map = PTR_055 if magic == MAGIC_OLD else PTR_057
    h = bytearray(img[:hsize])

    # Determine panel height & width from header fields
    rows = max(h[2] + 1, h[6] + 1, 1)
    columns = max(h[4], h[8]) + 1
    if magic == MAGIC and (h[0x37] << 8 | h[0x38]) not in (EMPTY_057, 0):
        rows = max(rows, h[0x30] + 1)
        columns = max(columns, h[0x32] + 1)

    ptrs = {k: _ptr(img, off) for k, off in ptr_map.items()}
    fnt_ptr = ptrs["FNT"]
    if magic == MAGIC_OLD and fnt_ptr < len(img) and not (0xE0 <= img[fnt_ptr] <= 0xEF):
        fonts, ftail, ct_raw = _fonts_flat_055(img, fnt_ptr)
    elif fnt_ptr not in (empty, 0) and fnt_ptr < len(img):
        fonts, ftail, ct_raw = _fonts_block(img, fnt_ptr)
    else:
        fonts, ftail, ct_raw = {}, b"\xff\x00", b""

    ct = CodeTable(ct_raw) if ct_raw else default_codetable()
    ver_raw = bytes(h[0x1F:0x2F]).split(b"\r")[0]
    ver = ct.decode(ver_raw)
    if ver is None:
        ver = ver_raw.decode("cp1250", "replace")
    ver = ver.strip()

    db = Database0(
        columns=columns, rows=rows, fonts=fonts, version=ver,
        commands=bytes(h[0x09:0x0D]), source=source, magic=magic, header=h,
        codetable=ct_raw, fonts_tail=ftail, properties=h[0x0B],
        brightness=h[0x4D] if magic == MAGIC else 0,
        panel_number=h[0x4E] if magic == MAGIC else 0,
        fw_3x=(magic == MAGIC and bytes(h[0x41:0x45]) != b"\x00\x00\x00\x00"),
    )

    # Extract section title format, FNT separator, and title version
    if fnt_ptr not in (empty, 0) and fnt_ptr >= 0x20:
        ftitle = img[fnt_ptr - 0x20:fnt_ptr].split(b"\xff", 1)[0].decode("latin1", "replace")
        m = re.match(r"^FNT:(.)(gBUSE0\s*-\s*)(.*)$", ftitle)
        if m:
            db.fnt_sep, db.title_fmt, db.title_version = m.group(1), m.group(2), m.group(3)
    else:
        m = re.search(rb"[A-Z]{3}: ?(gBUSE0\s*-\s*)([0-9.]+)", img)
        if m:
            db.title_fmt, db.title_version = m.group(1).decode(), m.group(2).decode()

    # Check if CIL uses 4-digit keys (zC enabled or CIL4 ptr set)
    cil_is_4 = False
    if magic == MAGIC:
        zC_on = bool(h[0x0A] & 0x20)
        cil4_ptr = ptrs.get("CIL4", empty)
        if zC_on or (cil4_ptr not in (empty, 0) and cil4_ptr == ptrs["CIL"]):
            cil_is_4 = True

    # Parse record sections
    if ptrs["LIN"] not in (empty, 0) and ptrs["LIN"] < len(img):
        d, lst = _records(img, ptrs["LIN"], 3)
        db.records["LIN"] = d
        db.record_lists["LIN"] = lst
    if ptrs["CIL"] not in (empty, 0) and ptrs["CIL"] < len(img):
        sec = "CIL4" if cil_is_4 else "CIL"
        d, lst = _records(img, ptrs["CIL"], 4 if cil_is_4 else 3)
        db.records[sec] = d
        db.record_lists[sec] = lst

    if magic == MAGIC:
        dru_ptr = ptrs["DRU"]
        if db.fw_3x and (h[0x42] or h[0x43] or h[0x44]) and bytes(h[0x42:0x45]) != b"\x00\x4f\x00":
            dru_24 = (h[0x44] << 16) | (h[0x42] << 8) | h[0x43]
            if dru_24 < len(img):
                dru_ptr = dru_24
        if dru_ptr not in (empty, 0) and dru_ptr < len(img):
            d, lst = _records(img, dru_ptr, 4)
            db.records["DRU"] = d
            db.record_lists["DRU"] = lst
        if ptrs["AFC"] not in (empty, 0) and ptrs["AFC"] < len(img):
            db.afc = _af_parse(img, ptrs["AFC"])
        if ptrs["AFD"] not in (empty, 0) and ptrs["AFD"] < len(img):
            db.afd = _af_parse(img, ptrs["AFD"])
        if ptrs["STR"] not in (empty, 0) and ptrs["STR"] < len(img):
            p = ptrs["STR"]
            db.format_strings = _str_parse(img, p)
            if p >= 0x20:
                db.str_title = img[p - 0x20:p].split(b"\xff", 1)[0].decode("latin1", "replace")

    if ptrs["KOD"] not in (empty, 0) and ptrs["KOD"] < len(img):
        p = ptrs["KOD"]
        db.kod = bytes(img[p:p + 128])
        if p >= 0x20:
            db.kod_title = img[p - 0x20:p].split(b"\xff", 1)[0].decode("latin1", "replace")

    if "LIN" in db.records:
        db.fields["line"] = _field(h, 0x01, 0x0D, rows)
    if "CIL" in db.records or "CIL4" in db.records:
        db.fields["dest"] = _field(h, 0x05, 0x11, rows)
    if "DRU" in db.records:
        db.fields["stop"] = _field(h, 0x2F, 0x33, rows)
    if db.fields:
        db.columns = max(f.x1 for f in db.fields.values()) + 1
    return db


def _sync_record_list(db: Database0, sec: str) -> list[tuple[bytes, bytes]]:
    """Use record_lists if keys match records, else build sorted list from records[sec]."""
    recs = db.records.get(sec)
    if not recs:
        return []
    lst = db.record_lists.get(sec)
    if lst and len(lst) == len(recs):
        return lst
    return [(k.encode("latin1"), recs[k]) for k in sorted(recs)]


def build(db: Database0) -> list[tuple[int, bytes]]:
    """Build (address, bytes) blocks matching GBUSE0.EXE 0x48E468 output layout."""
    magic = db.magic
    hsize = HEADER_SIZE_055 if magic == MAGIC_OLD else HEADER_SIZE_057
    empty = EMPTY_055 if magic == MAGIC_OLD else EMPTY_057
    ptr_map = PTR_055 if magic == MAGIC_OLD else PTR_057

    hdr = bytearray(db.header[:hsize])
    if len(hdr) < hsize:
        hdr.extend(b"\x00" * (hsize - len(hdr)))
    hdr[0] = magic
    hdr[-1] = 0xFF

    # Empty pointers
    for name, off in ptr_map.items():
        val = 0x0000 if name == "KOD" else empty
        hdr[off:off + 2] = val.to_bytes(2, "big")
    if magic == MAGIC:
        if db.fw_3x:
            hdr[0x42:0x45] = b"\x00\x4f\x00"
        else:
            hdr[0x42:0x45] = b"\x00\x00\x00"

    # Field geometry & presets (keep inactive field coords from hdr if field absent)
    rows = db.rows
    if "line" in db.fields:
        _write_field(hdr, 0x01, 0x0D, db.fields["line"], rows)
    else:
        hdr[0x0D] = 0xE0 | (hdr[0x0D] & 0x0F)
    if "dest" in db.fields:
        _write_field(hdr, 0x05, 0x11, db.fields["dest"], rows)
    else:
        hdr[0x11] = 0xE0 | (hdr[0x11] & 0x0F)
    if magic == MAGIC:
        if "stop" in db.fields:
            _write_field(hdr, 0x2F, 0x33, db.fields["stop"], rows)
        else:
            hdr[0x33] = 0xE0 | (hdr[0x33] & 0x0F)

    # Commands & properties
    cmds = db.commands if db.commands else bytes([0x45, 0x00, db.properties, 0x80 if magic == MAGIC_OLD else 0x00])
    hdr[0x09] = cmds[0]
    if magic == MAGIC_OLD:
        hdr[0x0A] = db.properties
        hdr[0x0B] = 0xFC
        hdr[0x0C] = 0x80
    else:
        hdr[0x0A] = cmds[1] if len(cmds) > 1 else 0
        hdr[0x0B] = db.properties
        hdr[0x0C] = cmds[3] if len(cmds) > 3 else 0
        hdr[0x4D] = db.brightness & 0xFF
        hdr[0x4E] = db.panel_number & 0xFF

    # Version string (encoded with codetable, CR-padded to 16 B)
    ct = CodeTable(db.codetable) if db.codetable else default_codetable()
    ver_bytes = bytearray()
    for ch in db.version[:16]:
        c = ct.encode_char(ch)
        if c is None:
            c = ch.encode("cp1250", "replace")[0]
        ver_bytes.append(c)
    hdr[0x1F:0x2F] = bytes(ver_bytes) + b"\r" * (16 - len(ver_bytes))

    blocks: list[tuple[int, bytes]] = [(0, bytes(hdr))]
    addr = hsize

    def add_section(title: str, data: bytes) -> int:
        nonlocal addr
        addr = align32(addr)
        tb = title.encode("latin1", "replace")
        blocks.append((addr, tb))
        addr = align32(addr + len(tb))
        start = addr
        blocks.append((addr, data))
        addr += len(data)
        return start

    def sec_title(sec: str) -> str:
        return f"{sec}: {db.title_fmt}{db.title_version}"

    # 1. STR (format strings 0..9)
    if magic == MAGIC and db.format_strings:
        t = db.str_title or sec_title("STR")
        p = add_section(t, _str_bytes(db.format_strings))
        hdr[ptr_map["STR"]:ptr_map["STR"] + 2] = p.to_bytes(2, "big")

    # 2. KOD (128 B input recoding table)
    if db.kod:
        t = db.kod_title or sec_title("KOD")
        p = add_section(t, db.kod[:128].ljust(128, b"\x00"))
        hdr[ptr_map["KOD"]:ptr_map["KOD"] + 2] = p.to_bytes(2, "big")

    # 3. AFM (AFC + 0x20-aligned AFD)
    if magic == MAGIC and (db.afc or db.afd):
        addr = align32(addr)
        tb = sec_title("AFM").encode("latin1")
        blocks.append((addr, tb))
        addr = align32(addr + len(tb))
        if db.afc:
            d = _af_bytes(db.afc)
            hdr[ptr_map["AFC"]:ptr_map["AFC"] + 2] = addr.to_bytes(2, "big")
            blocks.append((addr, d))
            addr += len(d)
        addr = align32(addr)
        if db.afd:
            d = _af_bytes(db.afd)
            hdr[ptr_map["AFD"]:ptr_map["AFD"] + 2] = addr.to_bytes(2, "big")
            blocks.append((addr, d))
            addr += len(d)

    # 4. FNT
    if db.fonts:
        ftitle = f"FNT:{db.fnt_sep}{db.title_fmt}{db.title_version}"
        ct_raw = db.codetable if db.codetable else default_codetable().raw
        if magic == MAGIC_OLD:
            # Flat table sorted by code 0x20..0xFE then font 0..15
            fbody = bytearray()
            for code in range(0x20, 0xFF):
                for fid in range(16):
                    f = db.fonts.get(fid)
                    if f and code in f.glyphs:
                        g = f.glyphs[code]
                        fbody += bytes([code, 0xE0 | fid, len(g.data), g.height]) + g.data
            fdata = bytes(fbody) + db.fonts_tail + ct_raw
        else:
            fbody = b"".join(db.fonts[fid].to_bytes() for fid in sorted(db.fonts))
            fdata = fbody + db.fonts_tail + ct_raw
        p = add_section(ftitle, fdata)
        hdr[ptr_map["FNT"]:ptr_map["FNT"] + 2] = p.to_bytes(2, "big")

    # 5. LIN
    lin_lst = _sync_record_list(db, "LIN")
    if lin_lst:
        out = bytearray()
        for k, b in lin_lst:
            out += k + len(b).to_bytes(2, "little") + b
        p = add_section(sec_title("LIN"), bytes(out + b"\xff"))
        hdr[ptr_map["LIN"]:ptr_map["LIN"] + 2] = p.to_bytes(2, "big")

    # 6. CIL / CIL4
    zC_on = bool(hdr[0x0A] & 0x20) if magic == MAGIC else False
    cil_sec = "CIL4" if ("CIL4" in db.records and db.records["CIL4"]) else "CIL"
    cil_lst = _sync_record_list(db, cil_sec)
    if cil_lst:
        out = bytearray()
        kl = 4 if (cil_sec == "CIL4" or zC_on) else 3
        for k, b in cil_lst:
            kpad = k.rjust(kl, b"0")[:kl]
            out += kpad + len(b).to_bytes(2, "little") + b
        p = add_section(sec_title("CIL"), bytes(out + b"\xff"))
        hdr[ptr_map["CIL"]:ptr_map["CIL"] + 2] = (p & 0xFFFF).to_bytes(2, "big")
        if magic == MAGIC and (zC_on or cil_sec == "CIL4"):
            hdr[ptr_map["CIL4"]:ptr_map["CIL4"] + 2] = (p & 0xFFFF).to_bytes(2, "big")

    # 7. DRU
    if magic == MAGIC:
        dru_lst = _sync_record_list(db, "DRU")
        if dru_lst:
            out = bytearray()
            for k, b in dru_lst:
                out += k.rjust(4, b"0")[:4] + len(b).to_bytes(2, "little") + b
            p = add_section(sec_title("DRU"), bytes(out + b"\xff"))
            hdr[ptr_map["DRU"]:ptr_map["DRU"] + 2] = (p & 0xFFFF).to_bytes(2, "big")
            if db.fw_3x:
                hdr[0x42] = (p >> 8) & 0xFF
                hdr[0x43] = p & 0xFF
                hdr[0x44] = (p >> 16) & 0xFF

    blocks[0] = (0, bytes(hdr))
    return blocks


def bs110_warnings(blocks, db: Database0) -> list[str]:
    """Warnings documented in gBUSE0.doc for BS110 panels (64 KB DRU bank / 128 KB Flash)."""
    if not blocks:
        return []
    end = max(a + len(d) for a, d in blocks)
    out = []
    if end > 128 * 1024:
        out.append("Přetečení paměti panelu BS110 (> 128 KB) – pro BS210 je v pořádku.")
    hdr = blocks[0][1]
    if db.magic == MAGIC and len(hdr) >= 0x45 and hdr[0x44] > 0:
        out.append("Přetečení adresy tabulky zastávek (DRU > 64 KB) – na BS110 nebudou zastávky fungovat.")
    return out


def to_hex(db: Database0) -> str:
    return blocks_to_hex(build(db))


def to_bin(db: Database0) -> bytes:
    return blocks_to_image(build(db))


def save_hex(db: Database0, path):
    with open(path, "w", newline="", encoding="ascii") as f:
        f.write(to_hex(db))


def load_hex(path) -> Database0:
    with open(path, encoding="latin1") as f:
        return parse(hex_to_image(f.read()), source=str(path))


def load_bin(path) -> Database0:
    with open(path, "rb") as f:
        return parse(f.read(), source=str(path))
