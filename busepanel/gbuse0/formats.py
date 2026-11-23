"""Auxiliary file formats of gBUSE0 and gBUSE1:
  - Standalone section files: .lin, .cil, .ci4, .dru, .zst, .mim, .dop, .cyk, .kod, .aL, .LA, .afm, .afc, .afd
  - Text import/export (.txt) including '$' template lines and legacy DOS GEBUSE '@' Kamenický files
  - BS100 / BS230 equivalents (.bbd / .bbf)
"""
from __future__ import annotations

import re

from ..core.fonts import CodeTable, default_codetable
from . import mnemo as M0

# ------------------------------------------------------------------ Kamenický (DOS) <-> Unicode
_KAMENICKY = {
    0x80: "Č", 0x81: "ü", 0x82: "é", 0x83: "ď", 0x84: "ä", 0x85: "Ď", 0x86: "Ť", 0x87: "č",
    0x88: "ě", 0x89: "Ě", 0x8A: "Ĺ", 0x8B: "Í", 0x8C: "ľ", 0x8D: "ĺ", 0x8E: "Ä", 0x8F: "Á",
    0x90: "É", 0x91: "ž", 0x92: "Ž", 0x93: "ô", 0x94: "ö", 0x95: "Ó", 0x96: "ů", 0x97: "Ú",
    0x98: "ý", 0x99: "Ö", 0x9A: "Ü", 0x9B: "Š", 0x9C: "Ľ", 0x9D: "Ý", 0x9E: "Ř", 0x9F: "ť",
    0xA0: "á", 0xA1: "í", 0xA2: "ó", 0xA3: "ú", 0xA4: "ň", 0xA5: "Ň", 0xA6: "Ů", 0xA7: "Ô",
    0xA8: "š", 0xA9: "ř",
}


def decode_kamenicky(b: bytes) -> str:
    out = []
    for x in b:
        if x in _KAMENICKY:
            out.append(_KAMENICKY[x])
        elif 0x20 <= x <= 0x7F:
            out.append(chr(x))
        else:
            out.append(bytes([x]).decode("cp1250", "replace"))
    return "".join(out)


def _decode_text(data: bytes) -> tuple[str, bool]:
    """Decode bytes -> (text, is_dos_kamenicky)."""
    # DOS GEBUSE .txt files start lines with '@' and use Kamenický encoding
    if data.lstrip().startswith(b"@"):
        return decode_kamenicky(data), True
    for enc in ("utf-8-sig", "cp1250", "latin1"):
        try:
            return data.decode(enc), False
        except UnicodeDecodeError:
            continue
    return data.decode("cp1250", "replace"), False


# ------------------------------------------------------------------ Standalone record / section files
def read_records_file(data: bytes, keylen: int) -> dict[str, bytes]:
    """Read .lin/.cil/.ci4/.dru/.zst/.mim (either raw record list or 0x20-titled block)."""
    q = 0
    if len(data) >= 0x20 and re.match(rb"^[A-Z0-9]{3}: gBUSE", data[:16]):
        q = 0x20
    out = {}
    while q + keylen + 2 <= len(data) and data[q] != 0xFF:
        key = data[q:q + keylen].decode("latin1")
        n = data[q + keylen] | data[q + keylen + 1] << 8
        out[key] = bytes(data[q + keylen + 2:q + keylen + 2 + n])
        q += keylen + 2 + n
    return out


def write_records_file(records: dict[str, bytes], keylen: int) -> bytes:
    out = bytearray()
    for k in sorted(records):
        b = records[k]
        out += k.rjust(keylen, "0")[:keylen].encode("latin1") + len(b).to_bytes(2, "little") + b
    return bytes(out + b"\xff")


def read_kod_file(data: bytes) -> bytes:
    if len(data) >= 0x20 + 128 and re.match(rb"^KOD: gBUSE", data[:16]):
        data = data[0x20:]
    return bytes(data[:128]).ljust(128, b"\x00")


def write_kod_file(kod: bytes) -> bytes:
    return bytes(kod[:128]).ljust(128, b"\x00")


def read_str_file(data: bytes) -> dict[int, bytes]:
    """Read .aL / .LA formatting strings (digits 0..9)."""
    q = 0x20 if len(data) >= 0x20 and re.match(rb"^STR: gBUSE", data[:16]) else 0
    out = {}
    while q + 1 < len(data) and data[q] != 0xFF:
        if ord("0") <= data[q] <= ord("9"):
            d = data[q] - ord("0")
            ln = data[q + 1]
            out[d] = bytes(data[q + 2:q + 2 + ln])
            q += 2 + ln
        else:
            break
    return out


def write_str_file(strs: dict[int, bytes]) -> bytes:
    out = bytearray()
    for d in sorted(strs):
        b = strs[d]
        out += bytes([ord("0") + int(d), len(b)]) + b
    return bytes(out + b"\xff")


def read_af_file(data: bytes, ext: str = ".afm") -> tuple[list[tuple[int, int, int]] | None, list[tuple[int, int, int]] | None]:
    """Read .afc (1-line), .afd (2-line), or .afm (both: 16*3 + 16*3 or FF-separated)."""
    def parse_seq(b: bytes) -> list[tuple[int, int, int]]:
        out, i = [], 0
        while i + 2 < len(b) and b[i] != 0xFF:
            out.append((b[i] & 0x0F, b[i + 1] & 0x0F, b[i + 2] & 0x1F))
            i += 3
        return out

    ext = ext.lower()
    if ext.endswith(".afc"):
        return parse_seq(data), None
    if ext.endswith(".afd"):
        return None, parse_seq(data)
    # .afm: two tables separated by 0xFF (aligned or contiguous) or fixed 48+48 bytes
    if 0xFF in data:
        p = data.index(0xFF)
        afc = parse_seq(data[:p + 1])
        rest = data[p + 1:].lstrip(b"\x00\xff")
        afd = parse_seq(rest) if rest else []
        return afc, afd
    return parse_seq(data[:48]), parse_seq(data[48:96])


def write_af_file(afc: list, afd: list, ext: str = ".afm") -> bytes:
    def enc(tbl):
        out = bytearray()
        for f, sp, sh in (tbl or [])[:16]:
            out += bytes([0xE0 | (int(f) & 0x0F), int(sp) & 0x0F, int(sh) & 0x1F])
        return bytes(out + b"\xff")

    ext = ext.lower()
    if ext.endswith(".afc"):
        return enc(afc)
    if ext.endswith(".afd"):
        return enc(afd)
    return enc(afc) + enc(afd)


# ------------------------------------------------------------------ .txt import/export with $ templates
def _apply_template(tmpl: str, texts: list[str], db, ct: CodeTable, field_name: str) -> tuple[bytes, list[str]]:
    """Expand gBUSE0 '$' template: $N (raw), [$N] (1-line autoformat), ($N) (2-line autoformat)."""
    from .database import Field
    fld = db.fields.get(field_name) or Field(0, db.columns - 1, 0, db.rows - 1)

    def repl_1(m):
        idx = int(m.group(1)) - 1
        t = texts[idx] if 0 <= idx < len(texts) else ""
        f, sp, sh = M0.autoformat_line(t, db, ct, fld.width, fld.height, two_line=False)
        return f"f{f} m{sp} y{sh} '{t}'"

    def repl_2(m):
        idx = int(m.group(1)) - 1
        t = texts[idx] if 0 <= idx < len(texts) else ""
        f, sp, sh = M0.autoformat_line(t, db, ct, fld.width, fld.height, two_line=True)
        return f"f{f} m{sp} y{sh} '{t}'"

    def repl_raw(m):
        idx = int(m.group(1)) - 1
        t = texts[idx] if 0 <= idx < len(texts) else ""
        return f"'{t}'"

    s = re.sub(r"\[\$(\d+)\]", repl_1, tmpl)
    s = re.sub(r"\(\$(\d+)\)", repl_2, s)
    s = re.sub(r"\$(\d+)", repl_raw, s)
    return M0.from_mnemo(s, ct)


def import_txt_gbuse0(data: bytes, db, field_name: str, keylen: int, ct: CodeTable) -> tuple[dict[str, bytes], list[str]]:
    """Import gBUSE0 .txt (TAB-separated, optional $ template lines, or DOS GEBUSE @ lines)."""
    text, _is_dos = _decode_text(data)
    out, probs = {}, []
    template = None
    for lno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip("\r\n")
        if not line.strip():
            continue
        if line.startswith("$"):
            template = line[1:].strip()
            continue
        if line.startswith("@"):
            line = line[1:]
        parts = line.split("\t")
        if len(parts) < 2:
            probs.append(f"řádek {lno}: chybí tabulátor")
            continue
        num = parts[0].strip()
        if not num.isdigit():
            probs.append(f"řádek {lno}: neplatné číslo {num!r}")
            continue
        key = num.zfill(keylen)
        texts = [p.strip() for p in parts[1:] if p.strip() != "" or len(parts) == 2]
        if not texts:
            texts = [""]
        if template:
            rec, p = _apply_template(template, texts, db, ct, field_name)
        else:
            if len(texts) == 1:
                spec = {"mode": "1", "autoformat": True, "line1": {"text": texts[0]}}
            else:
                spec = {"mode": "2", "autoformat": True, "line1": {"text": texts[0]},
                        "line2": {"text": "|".join(texts[1:])}}
            rec, p = M0.compose_record(spec, db, field_name, ct)
        probs.extend(f"řádek {lno}: {x}" for x in p)
        out[key] = rec
    return out, probs


def export_txt_gbuse0(records: dict[str, bytes], ct: CodeTable) -> str:
    """Export gBUSE0 records to TAB-separated plain text (.txt)."""
    lines = []
    for k in sorted(records):
        rec = records[k]
        # Extract printable characters split by LF
        parts = [bytearray()]
        i = 0
        while i < len(rec):
            c = rec[i]
            if c == 0x1B and i + 1 < len(rec):
                i += 3 if chr(rec[i + 1]) in "lphdsz" else 2
                continue
            if c == 0x0A:
                parts.append(bytearray())
            elif c in (0x0B, 0x0D):
                break
            elif c >= 0x20 and not (0xB0 <= c <= 0xEF):
                parts[-1].append(c)
            i += 1
        texts = [ct.decode(bytes(p)) or bytes(p).decode("cp1250", "replace") for p in parts if p]
        lines.append(k + "\t" + "\t".join(texts))
    return "\r\n".join(lines) + ("\r\n" if lines else "")


def import_txt_gbuse1(data: bytes, fonts, keylen: int, width: int = 111) -> tuple[dict[str, str], list[str]]:
    """Import gBUSE1 .txt (TAB-separated, optional DOS '@' Kamenický) using gBUSE1 autoformat."""
    from ..core import text as T1
    text, _ = _decode_text(data)
    out, probs = {}, []
    for lno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip("\r\n")
        if not line.strip():
            continue
        if line.startswith("@"):
            line = line[1:]
        parts = line.split("\t", 1)
        if len(parts) < 2:
            probs.append(f"řádek {lno}: chybí tabulátor")
            continue
        num = parts[0].strip()
        if not num.isdigit():
            probs.append(f"řádek {lno}: neplatné číslo {num!r}")
            continue
        key = num.zfill(keylen)
        txt = parts[1].strip()
        f, sp = T1.autoformat_gbuse1(txt, fonts, width)
        out[key] = {"text": txt, "font": f, "spacing": sp}
    return out, probs


# ------------------------------------------------------------------ BS100 / BS230 equivalents (.bbd / .bbf)
def default_equivalent(rec: bytes, ct: CodeTable, max_len: int = 12) -> str:
    """Derive BS100/BS230 equivalent text from a destination record."""
    out = bytearray()
    i = 0
    while i < len(rec):
        c = rec[i]
        if c == 0x1B and i + 1 < len(rec):
            i += 3 if chr(rec[i + 1]) in "lphdsz" else 2
            continue
        if c in (0x0A, 0x0B, 0x0D):
            if c == 0x0A and out and out[-1] != 0x20:
                out.append(0x20)
            i += 1
            continue
        if c >= 0x20 and not (0xB0 <= c <= 0xEF):
            out.append(c)
        i += 1
    txt = ct.decode(bytes(out)) or bytes(out).decode("cp1250", "replace")
    txt = " ".join(txt.split())
    return txt[:max_len] if max_len else txt


def export_bbd(equivalents: dict[str, str], version: str = "", sort_by: str = "num", max_len: int = 12) -> bytes:
    """Write BS100 v3 / BS230 .bbd equivalents file (clean line-based format with version header)."""
    items = list(equivalents.items())
    if sort_by == "alpha":
        items.sort(key=lambda kv: (kv[1].lower(), kv[0]))
    else:
        items.sort(key=lambda kv: kv[0])
    lines = [f"#BUSE BBD {version}".strip()]
    for k, v in items:
        txt = str(v)[:max_len] if max_len else str(v)
        lines.append(f"{k}\t{txt}")
    return ("\r\n".join(lines) + "\r\n").encode("cp1250", "replace")


def import_bbd(data: bytes) -> dict[str, str]:
    """Read .bbd (or extract text equivalents from .bbf)."""
    if data[:2] == b"Tv":
        return _read_bbf_equivalents(data)
    text = data.decode("cp1250", "replace")
    out = {}
    for ln in text.splitlines():
        if not ln.strip() or ln.startswith("#"):
            continue
        parts = ln.split("\t", 1) if "\t" in ln else ln.split(None, 1)
        if len(parts) == 2 and parts[0].strip().isdigit():
            out[parts[0].strip().zfill(3)] = parts[1].rstrip("\r\n")
    return out


def _read_bbf_equivalents(data: bytes) -> dict[str, str]:
    """Best-effort scan of BS100 .bbf destination table (12-char cp1250/ASCII entries)."""
    out = {}
    # In BBF (CENTRALA V1.3, e.g. KOSICE_2001_01/BS100.BBF), scan for 3-digit key + fixed slots if needed
    # Fallback: parse ASCII/cp1250 lines if text, else extract NNN=TEXT pairs if present
    for m in re.finditer(rb"(\d{3})([^\x00-\x1f\xff]{1,12})\x00", data):
        k = m.group(1).decode("ascii")
        t = m.group(2).decode("cp1250", "replace").strip()
        if t:
            out.setdefault(k, t)
    return out
