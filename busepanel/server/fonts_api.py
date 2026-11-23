"""Font, codetable, icon, and panel profile APIs."""
from __future__ import annotations

import base64

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ..core.fonts import CodeTable, FontSet, Glyph, _ct_code, _ct_index
from ..panels.profile import PROFILES
from .state import _load_fontset, _need_project

router = APIRouter()


@router.get("/api/fonts")
def fonts_get():
    p = _need_project()
    fs = p.fonts
    res = []
    for f in fs.fonts:
        glyphs = []
        for code, g in f.glyphs.items():
            glyphs.append({"code": code, "char": fs.codetable.decode_code(code), "height": g.height,
                           "rows": g.rows(), "width": g.width})
        res.append({"id": f.id, "glyphs": glyphs})
    codes = [0x20] + [c for i in range(159) if (c := _ct_code(i)) is not None]
    table = [{"code": c, "char": fs.codetable.decode_code(c)} for c in codes]
    return {"fonts": res, "codetable": table}


class GlyphReq(BaseModel):
    font: int
    code: int
    rows: list[str]
    height: int | None = None


@router.post("/api/fonts/glyph")
def fonts_glyph(r: GlyphReq):
    p = _need_project()
    f = p.fonts.ensure_font(r.font)
    rows = list(r.rows)
    if r.height is not None and r.height > 0:
        h = max(1, min(28, int(r.height)))
        w = max((len(x) for x in rows), default=1)
        if len(rows) < h:
            rows = rows + ["." * w] * (h - len(rows))
        elif len(rows) > h:
            rows = rows[:h]
    f.glyphs[r.code] = Glyph.from_rows(r.code, rows)
    f.glyphs = dict(sorted(f.glyphs.items()))
    return {"ok": True, "width": f.glyphs[r.code].width, "height": f.glyphs[r.code].height}


class GlyphDel(BaseModel):
    font: int
    code: int


@router.post("/api/fonts/glyph/delete")
def fonts_glyph_delete(r: GlyphDel):
    f = _need_project().fonts.font(r.font)
    if f and r.code in f.glyphs:
        del f.glyphs[r.code]
    return {"ok": True}


class CodeReq(BaseModel):
    code: int
    char: str


@router.post("/api/fonts/codetable")
def fonts_codetable(r: CodeReq):
    fs = _need_project().fonts
    idx = _ct_index(r.code)
    if idx is None:
        raise HTTPException(400, "kód musí být 0x21–0xAF nebo 0xF0–0xFF")
    raw = bytearray(fs.codetable.raw)
    raw[idx] = r.char.encode("cp1250")[0] if r.char else 0
    fs.codetable = CodeTable(bytes(raw))
    return {"ok": True}


class FontManageReq(BaseModel):
    action: str                     # copy_glyph | copy_font | copy_all | delete_font | delete_code_all | new_font
    source: str = "current"
    src_font: int | None = None
    src_code: int | None = None
    dst_font: int | None = None
    dst_code: int | None = None
    overwrite: bool = True


@router.post("/api/fonts/manage")
def fonts_manage(r: FontManageReq):
    fs = _need_project().fonts
    if r.action == "delete_font":
        if r.dst_font is None:
            raise HTTPException(400, "chybí cílový font")
        fs.delete_font(int(r.dst_font))
        return {"ok": True, "fonts": [f.id for f in fs.fonts]}
    if r.action == "delete_code_all":
        if r.dst_code is None:
            raise HTTPException(400, "chybí kód znaku")
        fs.delete_code_all(int(r.dst_code))
        return {"ok": True, "fonts": [f.id for f in fs.fonts]}
    if r.action == "new_font":
        if r.dst_font is None:
            raise HTTPException(400, "chybí číslo fontu")
        fs.ensure_font(int(r.dst_font))
        return {"ok": True, "fonts": [f.id for f in fs.fonts]}

    src_fs = fs if r.source == "current" else _load_fontset(r.source)
    if r.action == "copy_glyph":
        ok = fs.copy_glyph(src_fs, int(r.src_font), int(r.src_code), int(r.dst_font), int(r.dst_code))
        if not ok:
            raise HTTPException(404, "zdrojový znak nenalezen")
        return {"ok": True, "fonts": [f.id for f in fs.fonts]}
    if r.action == "copy_font":
        n = fs.copy_font(src_fs, int(r.src_font), int(r.dst_font), overwrite=r.overwrite)
        return {"ok": True, "copied": n, "fonts": [f.id for f in fs.fonts]}
    if r.action == "copy_all":
        total = sum(fs.copy_font(src_fs, sf.id, sf.id, overwrite=r.overwrite) for sf in src_fs.fonts)
        return {"ok": True, "copied": total, "fonts": [f.id for f in fs.fonts]}
    raise HTTPException(400, f"neznámá akce {r.action}")


@router.get("/api/fonts/export-fnt")
def fonts_export_fnt():
    fs = _need_project().fonts
    return Response(content=fs.to_fnt_bytes(), media_type="application/octet-stream",
                    headers={"Content-Disposition": 'attachment; filename="fonty.fnt"'})


class ImportFntReq(BaseModel):
    content_b64: str


@router.post("/api/fonts/import-fnt")
def fonts_import_fnt(r: ImportFntReq):
    p = _need_project()
    p.fonts = FontSet.from_fnt_bytes(base64.b64decode(r.content_b64))
    return {"ok": True, "fonts": [f.id for f in p.fonts.fonts]}


@router.get("/api/profiles")
def profiles():
    return {"profiles": [p.to_dict() for p in PROFILES.values()]}


@router.post("/api/fonts/add-icons")
def fonts_add_icons():
    from ..core.icons import ICON_FONT, add_icon_font
    add_icon_font(_need_project().fonts)
    return {"ok": True, "font": ICON_FONT}


@router.get("/api/icons")
def icons():
    from ..core.icons import ICON_CODES, ICON_FONT, ICONS
    return {"font": ICON_FONT, "icons": [{"name": n, "code": ICON_CODES[n], "rows": r} for n, r in ICONS.items()]}
