"""Bulk import of texts from CSV / XLSX into a project.

Expected columns (header row, case-insensitive, Czech or English names):
  section   lines|destinations|stops|messages  (linky|cile|zastavky|mimoradne)   – optional, default destinations
  number    číslo                                                                  – required
  text      text                                                                   – required
  font, spacing (mezera), align (zarovnani: center/left/right), run (bezici), inverse (inverze)
"""
from __future__ import annotations

import csv
import io

ALIASES = {
    "section": ["section", "sekce", "typ"], "number": ["number", "cislo", "číslo", "kod", "kód"],
    "text": ["text", "nazev", "název"], "font": ["font"], "spacing": ["spacing", "mezera"],
    "align": ["align", "zarovnani", "zarovnání"], "run": ["run", "bezici", "běžící"],
    "inverse": ["inverse", "inverze"],
}
SECTION_ALIASES = {
    "lines": ["lines", "linky", "linka", "lin", "l"],
    "destinations": ["destinations", "cile", "cíle", "cil", "cíl", "z"],
    "destinations4": ["destinations4"],
    "stops": ["stops", "zastavky", "zastávky", "zst"],
    "messages": ["messages", "mimoradne", "mimořádné", "mim"],
}
KEYLEN = {"lines": 3, "destinations": 3, "destinations4": 4, "stops": 4, "messages": 4}
TRUE = {"1", "true", "ano", "yes", "a", "x", "y"}


def _norm(h: str) -> str | None:
    h = (h or "").strip().lower()
    for k, names in ALIASES.items():
        if h in names:
            return k
    return None


def _section(v: str, default: str) -> str:
    v = (v or "").strip().lower()
    if not v:
        return default
    for k, names in SECTION_ALIASES.items():
        if v in names:
            return k
    raise ValueError(f"neznámá sekce {v!r}")


def rows_from_csv(data: bytes | str) -> list[dict]:
    if isinstance(data, bytes):
        for enc in ("utf-8-sig", "cp1250"):
            try:
                data = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    sample = data[:2000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
    return list(csv.DictReader(io.StringIO(data), dialect=dialect))


def rows_from_xlsx(data: bytes) -> list[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    head = [str(h or "") for h in rows[0]]
    return [{head[i]: ("" if v is None else str(v)) for i, v in enumerate(r) if i < len(head)} for r in rows[1:]]


def to_records(rows: list[dict], default_section="destinations", defaults: dict | None = None):
    """-> {section: {key: value}}, list of problems"""
    defaults = defaults or {}
    out, problems = {}, []
    for n, row in enumerate(rows, start=2):
        r = {}
        for k, v in row.items():
            nk = _norm(k)
            if nk:
                r[nk] = (v or "").strip() if isinstance(v, str) else v
        if not r.get("number") and not r.get("text"):
            continue
        try:
            sec = _section(r.get("section", ""), default_section)
            num = str(r.get("number", "")).strip()
            if num.endswith(".0"):
                num = num[:-2]
            if not num.isdigit():
                raise ValueError(f"číslo {num!r} není číselné")
            key = num.zfill(KEYLEN[sec])
            if len(key) > KEYLEN[sec]:
                raise ValueError(f"číslo {num} je delší než {KEYLEN[sec]} číslice")
            item = {"text": r.get("text", "")}
            for f in ("font", "spacing"):
                v = r.get(f) or defaults.get(f)
                if v not in (None, ""):
                    item[f] = int(float(v))
            align = r.get("align") or defaults.get("align")
            if align and align not in ("center", "střed", "stred"):
                item["align"] = {"vlevo": "left", "vpravo": "right"}.get(align, align)
            if str(r.get("run", defaults.get("run", ""))).lower() in TRUE:
                item["run"] = True
            if str(r.get("inverse", defaults.get("inverse", ""))).lower() in TRUE:
                item["inverse"] = True
            out.setdefault(sec, {})[key] = item if len(item) > 1 else item["text"]
        except ValueError as e:
            problems.append(f"řádek {n}: {e}")
    return out, problems


def import_file(name: str, data: bytes, **kw):
    rows = rows_from_xlsx(data) if name.lower().endswith((".xlsx", ".xlsm")) else rows_from_csv(data)
    return to_records(rows, **kw)


def merge(project, records: dict, replace: bool = False):
    for sec, recs in records.items():
        cur = {} if replace else dict(project.data.get(sec) or {})
        cur.update(recs)
        project.data[sec] = dict(sorted(cur.items(), key=lambda kv: kv[0]))
