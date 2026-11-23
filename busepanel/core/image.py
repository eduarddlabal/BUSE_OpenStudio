"""gBUSE1 (FW 2.x magic 0xAB and FW 1.xx magic 0xAA) data image <-> sections <-> Intel HEX.

Verified by byte-identical round-trip of all gBUSE1 HEX files (v0.02, v1.00, v1.12).
Layout (GBUSE1.EXE 0x491230):
  0x0000 header (0x40 B); then for each present section, at 0x20-aligned addresses:
  title "XXX: gBUSE1 - 1.00" (18 B) and the data at the next 0x20 boundary.
  The header holds big-endian pointers to the section data; empty = 0x003F (-> 0xFF) or 0x0000.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MAGIC_FW1 = 0xAA
MAGIC_FW2 = 0xAB
HEADER_SIZE = 0x40
EMPTY_PTR = 0x003F

# header pointer slots (FW 2.x)
PTR = {"FNT": 0x09, "LIN": 0x0B, "CIL": 0x0D, "CIL4": 0x0F, "ZST": 0x11,
       "MIM": 0x13, "CYK": 0x15, "DOP": 0x17, "KOD": 0x19, "STR": 0x1B}
# header pointer slots (FW 1.xx, magic 0xAA)
PTR_FW1 = {"FNT": 0x08, "LIN": 0x0A, "CIL": 0x0C, "ZST": 0x0E, "KOD": 0x12}

EMPTY_VALUE = {"DOP": 0x0000}
ORDER = ["STR", "KOD", "CYK", "DOP", "FNT", "MIM", "LIN", "CIL", "ZST", "CIL4"]
TITLE = {"CIL4": "CIL"}
KEYLEN = {"LIN": 3, "CIL": 3, "CIL4": 4, "ZST": 4, "MIM": 4}
RECORD_SECTIONS = ("LIN", "CIL", "CIL4", "ZST", "MIM")

# header byte 3/4/5 -> enabled IBIS telegrams (FW 2.x, bit 0 first)
CMD_BITS = {
    3: ["l", "z", "x", "xA", "xB", "zC", "zD", "v"],
    4: ["zI", "zM", "zN", "aA", "u", "e", "xC", "a"],
    5: ["aV", "lA"],
}
# header byte 3/4 -> enabled IBIS telegrams (FW 1.xx, verified GBUSE1.EXE 0x491340)
CMD_BITS_FW1 = {
    3: ["l", "z", "x", None, None, "v", "zI", "a"],
    4: ["aA", "aV", "u", "e"],
}
ALL_COMMANDS = [c for v in CMD_BITS.values() for c in v]

# gBUSE1 CYK (cycles) labels from TFormEdCykl DFM
CYK_SOURCES = [
    "[žádná]",
    "linka (kódem)",
    "cíl - trojmístný (kódem)",
    "zastávka (kódem)",
    "mimořádná informace (kódem)",
    "nácestné zastávky (kódem - xB)",
    "cíl - čtyřmístný (kódem)",
    "nácestné zastávky (kódem - zD)",
    "zastávka (textem)",
    "cíl (textem)",
    "mimořádná informace (textem)",
    "nácestné zastávky (textem)",
    "text povelu aA",
    "čas",
    "pásmo",
]
CYK_ENTER = [
    "okamžité rozsvícení",
    "přiběhnutí zprava doleva",
    "nasunutí zdola nahoru",
    "nasunutí shora dolů",
    "trvale běžící text",
]
CYK_LEAVE = [
    "okamžité zhasnutí",
    "vyběhnutí doleva",
    "vysunutí nahoru",
    "vysunutí dolů",
    "ponechání do přepisu novým textem",
]


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


# ---------------------------------------------------------------- CYK / DOP / KOD / STR codecs
def parse_cyk(raw: bytes) -> dict[int, list[dict]]:
    """Parse CYK binary section -> {cycle_id: [{"left": dict|None, "right": dict|None}, ...]}."""
    out: dict[int, list[dict]] = {}
    p = 0
    n = len(raw)
    while p < n and raw[p] != 0xFF:
        cid = raw[p]
        if p + 3 > n:
            break
        ln = raw[p + 1] | (raw[p + 2] << 8)
        body = raw[p + 3:p + 3 + ln]
        p += 3 + ln
        q = 0
        steps_raw = []
        while q < len(body) and body[q] != 0xFF:
            left = None
            if body[q] == 0:
                q += 1
            else:
                w, src, dop, dur, el = body[q:q + 5]
                left = {"width": w, "source": src, "dop": dop, "duration": dur,
                        "enter": (el >> 4) & 0x0F, "leave": el & 0x0F}
                q += 5
            right = None
            if q < len(body):
                if body[q] == 0:
                    q += 1
                else:
                    w, src, dop, dur, el = body[q:q + 5]
                    right = {"width": w, "source": src, "dop": dop, "duration": dur,
                             "enter": (el >> 4) & 0x0F, "leave": el & 0x0F}
                    q += 5
            steps_raw.append((left, right))
        if q < len(body) and body[q] == 0xFF:
            q += 1
        steps = []
        for left, right in steps_raw:
            for item in (left, right):
                if q < len(body):
                    ilen = body[q]
                    s = bytes(body[q + 1:q + 1 + ilen]).decode("cp1250", "replace")
                    q += 1 + ilen
                else:
                    s = ""
                if item is not None:
                    item["id"] = s
            steps.append({"left": left, "right": right})
        out[cid] = steps
    return out


def build_cyk(cycles: dict[int, list[dict]]) -> bytes:
    """Serialize {cycle_id: steps} -> CYK section bytes (exact match to GBUSE1.EXE 0x491C80)."""
    out = bytearray()
    for cid in sorted(cycles):
        steps = cycles[cid]
        if not steps:
            continue
        body = bytearray()
        for st in steps:
            for side in (st.get("left"), st.get("right")):
                if not side or int(side.get("source", 0)) == 0:
                    body.append(0)
                else:
                    el = ((int(side.get("enter", 0)) & 0x0F) << 4) | (int(side.get("leave", 0)) & 0x0F)
                    body += bytes([
                        int(side.get("width", 135)) & 0xFF,
                        int(side.get("source", 0)) & 0xFF,
                        int(side.get("dop", 0)) & 0xFF,
                        int(side.get("duration", 0)) & 0xFF,
                        el,
                    ])
        body.append(0xFF)
        for st in steps:
            for side in (st.get("left"), st.get("right")):
                sid = str((side or {}).get("id") or "").encode("cp1250", "replace")[:255]
                body.append(len(sid))
                body += sid
        out += bytes([int(cid) & 0xFF, len(body) & 0xFF, (len(body) >> 8) & 0xFF]) + body
    return bytes(out + b"\xff")


def parse_dop(raw: bytes) -> dict[int, bytes]:
    """Parse DOP binary section -> {index (1..254): record bytes}."""
    out: dict[int, bytes] = {}
    p = 0
    while p + 3 <= len(raw) and raw[p] != 0xFF:
        idx = raw[p]
        ln = raw[p + 1] | (raw[p + 2] << 8)
        out[idx] = bytes(raw[p + 3:p + 3 + ln])
        p += 3 + ln
    return out


def build_dop(dop: dict[int, bytes]) -> bytes:
    out = bytearray()
    for idx in sorted(dop):
        b = dop[idx]
        if not b:
            continue
        if b[-1] != 0x0D:
            b = b + b"\r"
        out += bytes([int(idx) & 0xFF, len(b) & 0xFF, (len(b) >> 8) & 0xFF]) + b
    return bytes(out + b"\xff")


def parse_str(raw: bytes) -> dict[int, bytes]:
    out, q = {}, 0
    while q + 1 < len(raw) and raw[q] != 0xFF:
        d = raw[q] - ord("0")
        ln = raw[q + 1]
        out[d] = bytes(raw[q + 2:q + 2 + ln])
        q += 2 + ln
    return out


def build_str(strs: dict[int, bytes]) -> bytes:
    out = bytearray()
    for d in sorted(strs):
        b = strs[d]
        out += bytes([ord("0") + int(d), len(b)]) + b
    return bytes(out)


# ---------------------------------------------------------------- model
@dataclass
class Image:
    header: bytearray
    fonts: list = field(default_factory=list)          # raw font blocks: E0+n, len BE, body
    codetable: bytes = b""                              # 160 B
    fonts_tail: bytes = b"\xff\x00"                     # terminator between fonts and code table
    records: dict = field(default_factory=dict)         # section -> [(key bytes, body bytes)]
    cycles: dict = field(default_factory=dict)          # CYK: cycle_id -> [steps]
    supplementary: dict = field(default_factory=dict)   # DOP: id (1..254) -> bytes
    kod: bytes = b""                                    # KOD: 128 B
    format_strings: dict = field(default_factory=dict)  # STR: digit -> bytes
    raw_sections: dict = field(default_factory=dict)    # any unexpected section kept verbatim
    empty_ptrs: dict = field(default_factory=dict)      # preserved empty pointer slots
    title_version: str = "1.00"

    @property
    def magic(self) -> int:
        return self.header[0] if self.header else MAGIC_FW2

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
        bits = CMD_BITS_FW1 if self.magic == MAGIC_FW1 else CMD_BITS
        return [n for b, names in bits.items() for i, n in enumerate(names) if n and (h[b] >> i & 1)]

    @commands.setter
    def commands(self, cmds):
        cmds = set(cmds)
        if self.magic == MAGIC_FW1:
            for b, names in CMD_BITS_FW1.items():
                v = 0x80 if b == 4 else 0
                for i, n in enumerate(names):
                    if n and n in cmds:
                        v |= 1 << i
                self.header[b] = v
            return
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
        sl = slice(0x1F, 0x2F) if self.magic == MAGIC_FW1 else slice(0x2A, 0x3A)
        return bytes(self.header[sl]).split(b"\r")[0].decode("cp1250", "replace")

    @version.setter
    def version(self, v: str):
        b = v.encode("cp1250", "replace")[:16]
        sl = slice(0x1F, 0x2F) if self.magic == MAGIC_FW1 else slice(0x2A, 0x3A)
        self.header[sl] = b + b"\r" * (16 - len(b))


def parse(img: bytes) -> Image:
    if not img or img[0] not in (MAGIC_FW1, MAGIC_FW2):
        raise ValueError("není to obraz gBUSE1 (první bajt musí být 0xAB nebo 0xAA)")
    h = bytearray(img[:HEADER_SIZE])
    im = Image(header=h)
    m = re.search(rb"[A-Z]{3}: gBUSE1 - ([0-9.]+)", img)
    if m:
        im.title_version = m.group(1).decode()
    ptr_map = PTR_FW1 if h[0] == MAGIC_FW1 else PTR
    for name, off in ptr_map.items():
        p = h[off] << 8 | h[off + 1]
        if p in (EMPTY_PTR, 0):
            im.empty_ptrs[name] = p
            continue
        if name == "FNT":
            q = p
            while q < len(img) and 0xE0 <= img[q] <= 0xEF:
                ln = img[q + 1] << 8 | img[q + 2]
                im.fonts.append(bytes(img[q:q + 3 + ln]))
                q += 3 + ln
            im.fonts_tail = bytes(img[q:q + 2])
            im.codetable = bytes(img[q + 2:q + 2 + 160])
        elif name in RECORD_SECTIONS:
            kl, q, recs = KEYLEN[name], p, []
            while q < len(img) and img[q] != 0xFF:
                key = bytes(img[q:q + kl])
                n = img[q + kl] | img[q + kl + 1] << 8
                recs.append((key, bytes(img[q + kl + 2:q + kl + 2 + n])))
                q += kl + 2 + n
            im.records[name] = recs
        else:
            nxt = [h[o] << 8 | h[o + 1] for o in ptr_map.values()]
            later = [x - 0x20 for x in nxt if x > p and x not in (EMPTY_PTR, 0)]
            end = min(later) if later else len(img)
            data = bytes(img[p:end])
            if name == "CYK":
                im.cycles = parse_cyk(data)
            elif name == "DOP":
                im.supplementary = parse_dop(data)
            elif name == "KOD":
                im.kod = data[:128]
            elif name == "STR":
                im.format_strings = parse_str(data.rstrip(b"\xff"))
            else:
                im.raw_sections[name] = data.rstrip(b"\xff")
    return im


def section_bytes(im: Image, name: str) -> bytes:
    if name == "FNT":
        return b"".join(im.fonts) + im.fonts_tail + im.codetable
    if name in RECORD_SECTIONS:
        out = bytearray()
        for key, body in im.records[name]:
            out += key + bytes([len(body) & 0xFF, len(body) >> 8]) + body
        return bytes(out + b"\xff")
    if name == "CYK":
        return build_cyk(im.cycles)
    if name == "DOP":
        return build_dop(im.supplementary)
    if name == "KOD":
        return im.kod[:128].ljust(128, b"\x00")
    if name == "STR":
        return build_str(im.format_strings)
    return im.raw_sections[name]


def build(im: Image):
    """Return list of (address, bytes) blocks exactly as gBUSE1 writes them."""
    hdr = bytearray(im.header)
    ptr_map = PTR_FW1 if hdr[0] == MAGIC_FW1 else PTR
    for name, off in ptr_map.items():
        if hdr[0] == MAGIC_FW1 and name == "KOD":
            default_empty = 0x0000
        else:
            default_empty = EMPTY_VALUE.get(name, EMPTY_PTR)
        val = im.empty_ptrs.get(name, default_empty)
        hdr[off:off + 2] = val.to_bytes(2, "big")
    present = set(im.records) | set(im.raw_sections)
    if im.fonts:
        present.add("FNT")
    if im.cycles:
        present.add("CYK")
    if im.supplementary:
        present.add("DOP")
    if im.kod:
        present.add("KOD")
    if im.format_strings:
        present.add("STR")
    blocks = [(0, hdr)]
    addr = HEADER_SIZE
    for name in ORDER:
        if name not in present or name not in ptr_map:
            continue
        addr = align32(addr)
        title = (TITLE.get(name, name) + ": gBUSE1 - " + im.title_version).encode()
        blocks.append((addr, title))
        addr = align32(addr + len(title))
        data = section_bytes(im, name)
        hdr[ptr_map[name]] = addr >> 8
        hdr[ptr_map[name] + 1] = addr & 0xFF
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
