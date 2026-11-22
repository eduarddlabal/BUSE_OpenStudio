"""gBUSE1 (FW 2.x, magic 0xAB) data image <-> sections <-> Intel HEX.

Verified by byte-identical round-trip of all gBUSE1 HEX files in BUSE_PANELY.
Layout (GBUSE1.EXE 0x491230):
  0x0000 header (0x40 B); then for each present section, at 0x20-aligned addresses:
  title "XXX: gBUSE1 - 1.00" (18 B) and the data at the next 0x20 boundary.
  The header holds big-endian pointers to the section data; empty = 0x003F (-> 0xFF).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MAGIC_FW2 = 0xAB
HEADER_SIZE = 0x40
EMPTY_PTR = 0x003F

# header pointer slots (FW 2.x)
PTR = {"FNT": 0x09, "LIN": 0x0B, "CIL": 0x0D, "CIL4": 0x0F, "ZST": 0x11,
       "MIM": 0x13, "CYK": 0x15, "DOP": 0x17, "KOD": 0x19, "STR": 0x1B}
EMPTY_VALUE = {"DOP": 0x0000}                 # gBUSE1 leaves this slot 00 00
ORDER = ["CYK", "DOP", "FNT", "MIM", "LIN", "CIL", "ZST", "CIL4"]   # write order of gBUSE1
TITLE = {"CIL4": "CIL"}
KEYLEN = {"LIN": 3, "CIL": 3, "CIL4": 4, "ZST": 4, "MIM": 4}
RECORD_SECTIONS = ("LIN", "CIL", "CIL4", "ZST", "MIM")

# header byte 3/4/5 -> enabled IBIS telegrams (bit 0 first)
CMD_BITS = {
    3: ["l", "z", "x", "xA", "xB", "zC", "zD", "v"],
    4: ["zI", "zM", "zN", "aA", "u", "e", "xC", "a"],
    5: ["aV", "lA"],
}
ALL_COMMANDS = [c for v in CMD_BITS.values() for c in v]


def align32(a: int) -> int:
    return (a + 0x1F) & ~0x1F


# ---------------------------------------------------------------- Intel HEX
def hex_records(text: str):
    """Yield (address, data) for data records; handles 02/04 extended addresses."""
    base = 0
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln.startswith(":"):
            continue
        b = bytes.fromhex(ln[1:])
        if sum(b) & 0xFF:
            raise ValueError("chybný kontrolní součet: " + ln)
        n, a, t = b[0], b[1] << 8 | b[2], b[3]
        if t == 0:
            yield base + a, b[4:4 + n]
        elif t == 2:
            base = (b[4] << 8 | b[5]) << 4
        elif t == 4:
            base = (b[4] << 8 | b[5]) << 16


def hex_to_image(text: str) -> bytes:
    mem = {}
    for a, d in hex_records(text):
        for i, v in enumerate(d):
            mem[a + i] = v
    if not mem:
        return b""
    hi = max(mem)
    return bytes(mem.get(i, 0xFF) for i in range(hi + 1))


def blocks_to_hex(blocks) -> str:
    lines = []
    seg = 0
    for base, data in blocks:
        for i in range(0, len(data), 0x20):
            chunk = data[i:i + 0x20]
            a = base + i
            if a >> 16 != seg:
                seg = a >> 16
                rec = bytes([2, 0, 0, 2, (seg << 12) >> 8 & 0xFF, 0])
                lines.append(":" + rec.hex().upper() + "%02X" % (-sum(rec) & 0xFF))
            rec = bytes([len(chunk), a >> 8 & 0xFF, a & 0xFF, 0]) + chunk
            lines.append(":" + rec.hex().upper() + "%02X" % (-sum(rec) & 0xFF))
    lines.append(":00000001FF")
    return "\r\n".join(lines) + "\r\n"


def blocks_to_image(blocks) -> bytes:
    end = max(b + len(d) for b, d in blocks)
    img = bytearray(b"\xff" * end)
    for b, d in blocks:
        img[b:b + len(d)] = d
    return bytes(img)


# ---------------------------------------------------------------- model
@dataclass
class Image:
    header: bytearray
    fonts: list = field(default_factory=list)          # raw font blocks: E0+n, len BE, body
    codetable: bytes = b""                              # 160 B, panel code 0x21.. -> cp1250
    fonts_tail: bytes = b"\xff\x00"                     # terminator between fonts and code table
    records: dict = field(default_factory=dict)         # section -> [(key bytes, body bytes)]
    raw_sections: dict = field(default_factory=dict)    # CYK/DOP/KOD/STR kept verbatim
    title_version: str = "1.00"

    # ---- header fields ---------------------------------------------
    @property
    def columns(self) -> int:
        return self.header[2] + 1

    @columns.setter
    def columns(self, v: int):
        self.header[2] = v - 1

    @property
    def commands(self) -> list:
        h = self.header
        return [n for b, names in CMD_BITS.items() for i, n in enumerate(names) if h[b] >> i & 1]

    @commands.setter
    def commands(self, cmds):
        cmds = set(cmds)
        unknown = cmds - set(ALL_COMMANDS)
        if unknown:
            raise ValueError(f"neznámé povely: {sorted(unknown)}")
        for b, names in CMD_BITS.items():
            v = 0x80 if b == 5 else 0
            for i, n in enumerate(names):
                if n in cmds:
                    v |= 1 << i
            self.header[b] = v

    @property
    def version(self) -> str:
        return bytes(self.header[0x2A:0x3A]).split(b"\r")[0].decode("cp1250")

    @version.setter
    def version(self, v: str):
        b = v.encode("cp1250")[:16]
        self.header[0x2A:0x3A] = b + b"\r" * (16 - len(b))


def parse(img: bytes) -> Image:
    if not img or img[0] != MAGIC_FW2:
        raise ValueError("není to obraz gBUSE1 pro FW 2.x (první bajt musí být 0xAB)")
    h = bytearray(img[:HEADER_SIZE])
    im = Image(header=h)
    m = re.search(rb"[A-Z]{3}: gBUSE1 - ([0-9.]+)", img)
    if m:
        im.title_version = m.group(1).decode()
    for name, off in PTR.items():
        p = h[off] << 8 | h[off + 1]
        if p in (EMPTY_PTR, 0):
            continue
        if name == "FNT":
            q = p
            while 0xE0 <= img[q] <= 0xEF:
                ln = img[q + 1] << 8 | img[q + 2]
                im.fonts.append(bytes(img[q:q + 3 + ln]))
                q += 3 + ln
            im.fonts_tail = bytes(img[q:q + 2])
            im.codetable = bytes(img[q + 2:q + 2 + 160])
        elif name in RECORD_SECTIONS:
            kl, q, recs = KEYLEN[name], p, []
            while img[q] != 0xFF:
                key = bytes(img[q:q + kl])
                n = img[q + kl] | img[q + kl + 1] << 8
                recs.append((key, bytes(img[q + kl + 2:q + kl + 2 + n])))
                q += kl + 2 + n
            im.records[name] = recs
        else:
            # unknown structure: keep everything up to the next 0x20-aligned title or end
            nxt = [h[o] << 8 | h[o + 1] for o in PTR.values()]
            later = [x - 0x20 for x in nxt if x > p and x not in (EMPTY_PTR, 0)]
            end = min(later) if later else len(img)
            data = bytes(img[p:end]).rstrip(b"\xff")
            im.raw_sections[name] = data
    return im


def section_bytes(im: Image, name: str) -> bytes:
    if name == "FNT":
        return b"".join(im.fonts) + im.fonts_tail + im.codetable
    if name in RECORD_SECTIONS:
        out = bytearray()
        for key, body in im.records[name]:
            out += key + bytes([len(body) & 0xFF, len(body) >> 8]) + body
        return bytes(out + b"\xff")
    return im.raw_sections[name]


def build(im: Image):
    """Return list of (address, bytes) blocks exactly as gBUSE1 writes them."""
    hdr = bytearray(im.header)
    for name, off in PTR.items():
        hdr[off:off + 2] = EMPTY_VALUE.get(name, EMPTY_PTR).to_bytes(2, "big")
    present = set(im.records) | set(im.raw_sections) | ({"FNT"} if im.fonts else set())
    blocks = [(0, hdr)]
    addr = HEADER_SIZE
    for name in ORDER + ["KOD", "STR"]:
        if name not in present:
            continue
        addr = align32(addr)
        title = (TITLE.get(name, name) + ": gBUSE1 - " + im.title_version).encode()
        blocks.append((addr, title))
        addr = align32(addr + len(title))
        data = section_bytes(im, name)
        hdr[PTR[name]] = addr >> 8
        hdr[PTR[name] + 1] = addr & 0xFF
        blocks.append((addr, data))
        addr += len(data)
    blocks[0] = (0, bytes(hdr))
    return blocks


def load_hex(path) -> Image:
    with open(path, encoding="ascii") as f:
        return parse(hex_to_image(f.read()))


def save_hex(im: Image, path):
    with open(path, "w", newline="", encoding="ascii") as f:
        f.write(blocks_to_hex(build(im)))
