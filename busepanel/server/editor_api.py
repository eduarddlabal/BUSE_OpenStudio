"""Editor preview, MNEMO/autoformat, and table/section import & export APIs."""
from __future__ import annotations

import base64
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ..core import image as I
from ..core import text as T
from ..core.importers import import_file, merge
from ..core.render import render_frames, simulate_cycle_step, text_bitmap
from .state import _need_project, _project_json

router = APIRouter()


class PreviewReq(BaseModel):
    value: object = None
    line: object = None
    dest: object = None
    stop: object = None
    section: str = "destinations"
    cycle_id: int | None = None
    step_idx: int | None = None
    free_text: str = ""
    time_str: str = "12:34"
    zone_str: str = "001000"


@router.post("/api/preview")
def preview(r: PreviewReq):
    p = _need_project()
    if p.format == "gbuse0":
        from ..gbuse0 import mnemo as M0
        from ..gbuse0.program import render_combined
        db, _ = p.to_gbuse0()
        ct = p.fonts.codetable
        problems: list[str] = []
        fld_map = {"lines": "line", "destinations": "dest", "destinations4": "dest", "stops": "stop"}
        target_fld = fld_map.get(r.section, "dest")

        def enc_val(val, fname: str) -> bytes | None:
            if val is None or val == "":
                return None
            if isinstance(val, dict):
                b, pr = M0.compose_record(val, db, fname, ct)
            else:
                b, pr = M0.from_mnemo(str(val), ct)
            problems.extend(pr)
            return b

        recs = {"line": None, "dest": None, "stop": None}
        if r.line is not None and r.line != "":
            recs["line"] = enc_val(r.line, "line")
        if r.dest is not None and r.dest != "":
            recs["dest"] = enc_val(r.dest, "dest")
        if r.stop is not None and r.stop != "":
            recs["stop"] = enc_val(r.stop, "stop")

        edit_bytes = b""
        mnemo_str = ""
        structured = None
        if r.value is not None and r.value != "":
            b = enc_val(r.value, target_fld) or b""
            edit_bytes = b
            mnemo_str = M0.to_mnemo(b, ct)
            structured = M0.decompose_record(b, db, target_fld, ct)
            if r.section in fld_map:
                recs[target_fld] = b
            elif r.section in ("supplementary", "format_strings"):
                recs["dest"] = b
        res = render_combined(db, line_rec=recs["line"], dest_rec=recs["dest"], stop_rec=recs["stop"])
        return {
            "format": "gbuse0",
            **res,
            "geometry": p.geometry.__dict__,
            "bytes": edit_bytes.hex(" ").upper(),
            "mnemo": mnemo_str,
            "structured": structured,
            "problems": problems,
        }

    g = p.geometry
    f0, s0 = p.defaults()
    cfg = p.data.get("config") or {}
    default_iv = float((cfg.get("defaults") or {}).get("interval", 1.0))

    if r.cycle_id is not None and r.step_idx is not None:
        def first_key(sec_name):
            d = p.data.get(sec_name) or {}
            return next(iter(d), "")

        cf = simulate_cycle_step(
            p, r.cycle_id, r.step_idx,
            line_key=str(r.line) if r.line else first_key("lines"),
            dest_key=str(r.dest) if r.dest else first_key("destinations"),
            dest4_key=first_key("destinations4"),
            stop_key=str(r.stop) if r.stop else first_key("stops"),
            msg_key=first_key("messages"),
            free_text=r.free_text,
            time_str=r.time_str,
            zone_str=r.zone_str,
        )
        return {"format": "gbuse1", "cycle_frame": cf, "geometry": g.__dict__, "problems": []}

    out = {"format": "gbuse1", "geometry": g.__dict__, "problems": [], "frames": [], "loop": None, "mnemo": ""}
    primary_area = "line" if r.section == "lines" else ("full" if r.section in ("supplementary", "format_strings") else "dest")
    for name, val in (("dest", r.value), ("line", r.line)):
        if val is None or val == "":
            out[name] = None
            continue
        try:
            b, problems = p.encode_value(val)
        except Exception as e:      # noqa: BLE001
            out["problems"].append(str(e))
            out[name] = None
            continue
        out["problems"] += problems
        cols, inv, info = text_bitmap(b, p.fonts, f0, s0)
        item = {"cols": cols, "inv": [1 if i else 0 for i in inv], "x": info["x"], "run": info["run"],
                "effect": info["effect"], "blink": info["blink"],
                "width": len(cols), "bytes": b.hex(" ").upper()}
        if name == "dest" and not info["run"]:
            start, lo, hi = g.dest_span(len(cols), info["x"])
            item.update(start=start, lo=lo, hi=hi, overflow=start + len(cols) - 1 > hi or start < lo)
        elif name == "dest":
            # running text: it runs through the whole text window (next to the line field, or the full width)
            lo, hi = {"full": (0, g.columns - 1), "line": (g.line_left, g.line_right)}.get(
                primary_area, (g.dest_left, g.dest_right))
            item.update(start=hi + 1, lo=lo, hi=hi, overflow=False)
        out[name] = item
        if name == "dest":
            rf = render_frames(b, p.fonts, g, (f0, s0), default_iv, area=primary_area)
            out["frames"] = rf["frames"]
            out["loop"] = rf["loop"]
            out["mnemo"] = T.to_mnemo1(b, p.fonts)
    return out


class Compose0Req(BaseModel):
    spec: dict
    section: str = "destinations"


@router.post("/api/gbuse0/compose")
def gbuse0_compose(r: Compose0Req):
    p = _need_project()
    if p.format != "gbuse0":
        raise HTTPException(400, "projekt není ve formátu gBUSE0")
    from ..gbuse0 import mnemo as M0
    db, _ = p.to_gbuse0()
    fld_name = {"lines": "line", "stops": "stop"}.get(r.section, "dest")
    b, probs = M0.compose_record(r.spec, db, fld_name, p.fonts.codetable)
    return {"mnemo": M0.to_mnemo(b, p.fonts.codetable), "bytes": b.hex(" ").upper(), "problems": probs}


class AutoformatReq(BaseModel):
    text: str
    two_line: bool = False
    section: str = "destinations"
    width: int | None = None


@router.post("/api/gbuse/autoformat")
def gbuse_autoformat(r: AutoformatReq):
    p = _need_project()
    if p.format == "gbuse0":
        from ..gbuse0 import mnemo as M0
        db, _ = p.to_gbuse0()
        fld_name = {"lines": "line", "stops": "stop"}.get(r.section, "dest")
        fld = db.fields.get(fld_name)
        w = int(r.width) if r.width else (fld.width if fld else db.columns)
        h = fld.height if fld else db.rows
        f, sp, sh = M0.autoformat_line(r.text, db, p.fonts.codetable, w, h, two_line=r.two_line)
        return {"font": f, "spacing": sp, "shift": sh}
    w = int(r.width) if r.width else (p.geometry.line_right - p.geometry.line_left + 1 if r.section == "lines" else p.geometry.dest_width)
    f, sp = T.autoformat_gbuse1(r.text, p.fonts, w)
    return {"font": f, "spacing": sp}


class MnemoReq(BaseModel):
    direction: str = "to_mnemo"    # to_mnemo | from_mnemo
    value: object = ""
    section: str = "destinations"


@router.post("/api/mnemo/convert")
def mnemo_convert(r: MnemoReq):
    p = _need_project()
    if p.format == "gbuse0":
        from ..gbuse0 import mnemo as M0
        db, _ = p.to_gbuse0()
        ct = p.fonts.codetable
        fld_name = {"lines": "line", "stops": "stop"}.get(r.section, "dest")
        if r.direction == "from_mnemo":
            b, probs = M0.from_mnemo(str(r.value or ""), ct)
            return {"mnemo": str(r.value or ""), "structured": M0.decompose_record(b, db, fld_name, ct),
                    "bytes": b.hex(" ").upper(), "problems": probs}
        b, probs = M0.compose_record(r.value, db, fld_name, ct) if isinstance(r.value, dict) else M0.from_mnemo(str(r.value or ""), ct)
        return {"mnemo": M0.to_mnemo(b, ct), "structured": M0.decompose_record(b, db, fld_name, ct),
                "bytes": b.hex(" ").upper(), "problems": probs}

    # gBUSE1
    if r.direction == "from_mnemo":
        b, probs = T.from_mnemo1(str(r.value or ""), p.fonts)
        return {"mnemo": str(r.value or ""), "structured": T.decode(b, p.fonts),
                "bytes": b.hex(" ").upper(), "problems": probs}
    b, probs = p.encode_value(r.value)
    return {"mnemo": T.to_mnemo1(b, p.fonts), "structured": T.decode(b, p.fonts),
            "bytes": b.hex(" ").upper(), "problems": probs}


class ImportReq(BaseModel):
    name: str
    content_b64: str
    section: str = "destinations"
    replace: bool = False
    defaults: dict = {}


@router.post("/api/import")
def do_import(r: ImportReq):
    p = _need_project()
    recs, problems = import_file(r.name, base64.b64decode(r.content_b64),
                                 default_section=r.section, defaults=r.defaults)
    merge(p, recs, replace=r.replace)
    return {**_project_json(p), "counts": {k: len(v) for k, v in recs.items()}, "problems": problems}


class SectionImportReq(BaseModel):
    name: str
    content_b64: str
    section: str = "destinations"
    replace: bool = False


@router.post("/api/gbuse/section/import")
def gbuse_section_import(r: SectionImportReq):
    from ..gbuse0 import formats as F0, mnemo as M0
    p = _need_project()
    raw = base64.b64decode(r.content_b64)
    low = r.name.lower()
    probs: list[str] = []
    counts: dict[str, int] = {}

    def put_records(sec: str, recs: dict[str, bytes]):
        target = p.data.setdefault(sec, {})
        if r.replace:
            target.clear()
        for k, b in recs.items():
            target[k] = M0.to_mnemo(b, p.fonts.codetable) if p.format == "gbuse0" else T.decode(b, p.fonts)
        counts[sec] = len(recs)

    ext_sec = {".lin": ("lines", 3), ".cil": ("destinations", 3), ".ci4": ("destinations4", 4),
               ".dru": ("stops", 4), ".zst": ("stops", 4), ".mim": ("messages", 4),
               ".dop": ("supplementary", None)}
    ext = os.path.splitext(low)[1]

    if ext in ext_sec:
        sec, kl = ext_sec[ext]
        if ext == ".dop":
            payload = raw[0x20:] if len(raw) >= 0x20 and raw[:10].startswith(b"DOP: gBUSE") else raw
            recs = I.parse_dop(payload)
            target = p.data.setdefault("supplementary", {})
            if r.replace:
                target.clear()
            for idx, b in recs.items():
                target[str(idx)] = T.decode(b, p.fonts)
            counts["supplementary"] = len(recs)
        else:
            put_records(sec, F0.read_records_file(raw, kl))
    elif ext == ".kod":
        p.data["kod_table"] = F0.read_kod_file(raw).hex(" ").upper()
        counts["kod_table"] = 1
    elif ext in (".al", ".la"):
        strs = F0.read_str_file(raw)
        target = p.data.setdefault("format_strings", {})
        if r.replace:
            target.clear()
        for d, b in strs.items():
            target[str(d)] = M0.to_mnemo(b, p.fonts.codetable) if p.format == "gbuse0" else T.decode(b, p.fonts)
        counts["format_strings"] = len(strs)
    elif ext in (".afc", ".afd", ".afm"):
        afc, afd = F0.read_af_file(raw, ext)
        cfg = p.data.setdefault("config", {})
        if afc is not None:
            cfg["afc"] = [list(x) for x in afc]
            counts["afc"] = len(afc)
        if afd is not None:
            cfg["afd"] = [list(x) for x in afd]
            counts["afd"] = len(afd)
    elif ext == ".cyk":
        payload = raw[0x20:] if len(raw) >= 0x20 and raw[:10].startswith(b"CYK: gBUSE") else raw
        cyk_map = I.parse_cyk(payload)
        target = p.data.setdefault("cycles", {})
        if r.replace:
            target.clear()
        for cid, steps in cyk_map.items():
            target[str(cid)] = steps
        counts["cycles"] = len(cyk_map)
    elif ext in (".bbd", ".bbf"):
        eq = F0.import_bbd(raw)
        target = p.data.setdefault("equivalents", {})
        if r.replace:
            target.clear()
        target.update(eq)
        counts["equivalents"] = len(eq)
    elif ext == ".txt":
        kl = 3 if r.section in ("lines", "destinations") else 4
        if p.format == "gbuse0":
            db, _ = p.to_gbuse0()
            fld = {"lines": "line", "stops": "stop"}.get(r.section, "dest")
            recs, probs = F0.import_txt_gbuse0(raw, db, fld, kl, p.fonts.codetable)
            put_records(r.section, recs)
        else:
            w = p.geometry.line_right - p.geometry.line_left + 1 if r.section == "lines" else p.geometry.dest_width
            recs, probs = F0.import_txt_gbuse1(raw, p.fonts, kl, width=w)
            target = p.data.setdefault(r.section, {})
            if r.replace:
                target.clear()
            target.update(recs)
            counts[r.section] = len(recs)
    else:
        raise HTTPException(400, f"nepodporovaná přípona {ext}")
    return {**_project_json(p), "counts": counts, "problems": probs}


class SectionExportReq(BaseModel):
    ext: str
    section: str = "destinations"
    sort_by: str = "num"


@router.post("/api/gbuse/section/export")
def gbuse_section_export(r: SectionExportReq):
    from ..gbuse0 import formats as F0, mnemo as M0
    p = _need_project()
    ext = r.ext.lower()
    if not ext.startswith("."):
        ext = "." + ext

    def encoded_map(sec: str, fld: str = "dest") -> dict[str, bytes]:
        out = {}
        for k, v in (p.data.get(sec) or {}).items():
            key = k.split(" (")[0]
            if p.format == "gbuse0":
                db, _ = p.to_gbuse0()
                b, _ = M0.compose_record(v, db, fld, p.fonts.codetable) if isinstance(v, dict) else M0.from_mnemo(str(v or ""), p.fonts.codetable)
            else:
                b, _ = p.encode_value(v)
            out[key] = b
        return out

    ext_sec = {".lin": ("lines", 3, "line"), ".cil": ("destinations", 3, "dest"),
               ".ci4": ("destinations4", 4, "dest"), ".dru": ("stops", 4, "stop"),
               ".zst": ("stops", 4, "stop"), ".mim": ("messages", 4, "dest")}
    if ext in ext_sec:
        sec, kl, fld = ext_sec[ext]
        data = F0.write_records_file(encoded_map(sec, fld), kl)
    elif ext == ".kod":
        kod = bytes.fromhex(p.data.get("kod_table") or "") if p.data.get("kod_table") else b""
        data = F0.write_kod_file(kod)
    elif ext in (".al", ".la"):
        strs = {}
        for k, v in (p.data.get("format_strings") or {}).items():
            b, _ = p.encode_value(v)
            strs[int(k)] = b
        data = F0.write_str_file(strs)
    elif ext in (".afc", ".afd", ".afm"):
        cfg = p.data.get("config") or {}
        data = F0.write_af_file(cfg.get("afc") or [], cfg.get("afd") or [], ext)
    elif ext == ".cyk":
        cyk = {int(k): v for k, v in (p.data.get("cycles") or {}).items()}
        data = I.build_cyk(cyk)
    elif ext == ".dop":
        dop = {}
        for k, v in (p.data.get("supplementary") or {}).items():
            b, _ = p.encode_value(v)
            dop[int(k)] = b
        data = I.build_dop(dop)
    elif ext == ".txt":
        if p.format == "gbuse0":
            fld = {"lines": "line", "stops": "stop"}.get(r.section, "dest")
            data = F0.export_txt_gbuse0(encoded_map(r.section, fld), p.fonts.codetable).encode("cp1250", "replace")
        else:
            lines = []
            for k in sorted(p.data.get(r.section) or {}):
                v = p.data[r.section][k]
                txt = v.get("text", "") if isinstance(v, dict) else str(v)
                lines.append(f"{k}\t{txt}")
            data = ("\r\n".join(lines) + ("\r\n" if lines else "")).encode("cp1250", "replace")
    elif ext == ".bbd":
        eq = dict(p.data.get("equivalents") or {})
        if not eq:
            fld = {"lines": "line", "stops": "stop"}.get(r.section, "dest")
            for k, b in encoded_map(r.section, fld).items():
                eq[k] = F0.default_equivalent(b, p.fonts.codetable)
        ver = str((p.data.get("config") or {}).get("version") or "")
        data = F0.export_bbd(eq, version=ver, sort_by=r.sort_by)
    else:
        raise HTTPException(400, f"nepodporovaný export {ext}")
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="export{ext}"'})
