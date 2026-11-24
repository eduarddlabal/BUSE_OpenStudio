"""Workspace files and single-panel project CRUD/export endpoints."""
from __future__ import annotations

import base64
import os
import shutil

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from ..core import image as I
from ..core.bundle import ProjectBundle
from ..core.fonts import FontSet
from ..core.project import Project
from .state import (
    _INSPECT_CACHE, _SKIP_DIRS, S, _activate_bundle, _apply_meta,
    _display_name, _inspect_file, _is_project_yaml, _load_fontset,
    _need_project, _project_json, _safe_workdir_path, _unique_project_relpath,
)

router = APIRouter()


@router.get("/api/files")
def files():
    """Panel databases and multi-panel bundles in the workdir, plus shared templates."""
    out, items = [], []
    for root, dirs, fs in os.walk(S.workdir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS]
        if "projekt.yaml" in fs and ProjectBundle.is_bundle_yaml(os.path.join(root, "projekt.yaml")):
            if "panely" in dirs:
                dirs.remove("panely")
        if len(out) > 300:
            break
        for f in fs:
            full = os.path.join(root, f)
            low = f.lower()
            if low.endswith((".hex", ".bin")) or (low.endswith((".yaml", ".yml")) and f != "fonts.yaml"
                                                  and _is_project_yaml(full)):
                rel = os.path.relpath(full, S.workdir).replace("\\", "/")
                out.append(rel)
                item = _inspect_file(full, rel)
                if item:
                    items.append(item)
    items.sort(key=lambda x: (0 if x["kind"] == "bundle" else (1 if x["kind"] == "yaml" else 2), -x["mtime_ts"], x["path"]))
    tmpl = {}
    for folder in ("systemy", "vozidla", "trasy", "zive"):
        fp = os.path.join(S.workdir, folder)
        lst = []
        if os.path.isdir(fp):
            for r2, _d2, fs2 in os.walk(fp):
                for f2 in fs2:
                    if f2.lower().endswith((".yaml", ".yml")):
                        lst.append(os.path.relpath(os.path.join(r2, f2), S.workdir).replace("\\", "/"))
        tmpl[folder] = sorted(lst)
    return {"workdir": os.path.abspath(S.workdir), "files": sorted(out), "projects": items, "templates": tmpl}


class OpenReq(BaseModel):
    path: str


@router.post("/api/project/open")
def project_open(r: OpenReq):
    path = _safe_workdir_path(r.path)
    low = path.lower()
    if low.endswith(".hex"):
        S.bundle = None
        S.active_panel = None
        S.project = Project.from_hex(path, ibis_address=S.address)
        S.path = os.path.splitext(path)[0] + ".yaml"
    elif low.endswith(".bin"):
        S.bundle = None
        S.active_panel = None
        S.project = Project.from_bin(path, ibis_address=S.address)
        S.path = os.path.splitext(path)[0] + ".yaml"
    elif ProjectBundle.is_bundle_yaml(path):
        b = ProjectBundle.load(path)
        _activate_bundle(b)
        return _project_json(S.project)
    else:
        S.bundle = None
        S.active_panel = None
        try:
            S.project = Project.load(path)
        except ValueError as e:
            raise HTTPException(400, f"{os.path.basename(path)} není projekt panelu: {e}") from e
        S.path = path
    S.address = int((S.project.data.get("panel") or {}).get("ibis_address", S.address))
    return _project_json(S.project)


@router.get("/api/project")
def project_get():
    return _project_json(_need_project())


class SaveReq(BaseModel):
    data: dict
    path: str | None = None


@router.post("/api/project/save")
def project_save(r: SaveReq):
    p = _need_project()
    p.data = {**r.data, "fonts": p.data.get("fonts")}
    if S.bundle is not None:
        if r.path:
            S.bundle.manifest_path = _safe_workdir_path(r.path)
            S.bundle.base_dir = os.path.dirname(S.bundle.manifest_path)
        S.bundle.save()
        _INSPECT_CACHE.pop(S.bundle.manifest_path, None)
        _INSPECT_CACHE.pop(S.path or "", None)
        _reload_in_simulation(S.bundle.manifest_path)
        return {"saved": S.bundle.manifest_path, **_project_json(p)}
    if r.path:
        S.path = _safe_workdir_path(r.path)
    os.makedirs(os.path.dirname(S.path), exist_ok=True)
    p.save(S.path)
    _INSPECT_CACHE.pop(S.path, None)
    _reload_in_simulation(S.path)
    return {"saved": S.path, **_project_json(p)}


def _reload_in_simulation(abs_path: str):
    """The saved project is the one open in the simulation -> it reads it again (new lines, records)."""
    from . import sim_api
    try:
        sim_api.reload_project(abs_path)
    except Exception as e:      # noqa: BLE001 – saving must not fail because of the simulation
        sim_api.SIM._log(f"simulace: projekt nelze znovu načíst: {e}")


@router.post("/api/project/update")
def project_update(r: SaveReq):
    p = _need_project()
    p.data = {**r.data, "fonts": p.data.get("fonts")}
    return {"ok": True}


class NewReq(BaseModel):
    path: str = ""
    name: str = ""
    description: str = ""
    fonts_from: str = ""
    version: str = "NOVY"
    format: str = "gbuse1"
    preset: str = "front"
    fw: str = ""
    panel_kind: int = 1
    columns: int = 135
    rows: int = 19
    save_now: bool = True


@router.post("/api/project/new")
def project_new(r: NewReq):
    S.bundle = None
    S.active_panel = None
    fmt = "gbuse0" if r.format == "gbuse0" else "gbuse1"
    fs = _load_fontset(r.fonts_from, fmt=fmt)
    if fmt == "gbuse0":
        fw = r.fw or "3.08"
        S.project = Project.new_gbuse0(fs, preset=r.preset, version=r.version, fw=fw, ibis_address=S.address)
        if r.preset == "custom":
            cfg = S.project.data["config"]
            cfg["columns"] = int(r.columns or 140)
            cfg["rows"] = int(r.rows or 19)
            cfg["fields"] = {
                "line": {"r0": 1, "r1": cfg["rows"], "c0": 1, "c1": min(28, cfg["columns"]), "font": 8, "spacing": 1, "shift": 0, "interval": 1.0},
                "dest": {"r0": 1, "r1": cfg["rows"], "c0": min(29, cfg["columns"]), "c1": cfg["columns"], "font": 7, "spacing": 1, "shift": 0, "interval": 1.0},
            }
    else:
        fw = r.fw or ("2.12" if r.panel_kind == 2 else "2.06")
        S.project = Project.new_gbuse1(fs, version=r.version, columns=int(r.columns or 135),
                                       panel_kind=int(r.panel_kind or 1), fw=fw, ibis_address=S.address)
    _apply_meta(S.project, r.name, r.description)
    rel = r.path.strip() or _unique_project_relpath(r.name or r.version or fmt)
    S.path = _safe_workdir_path(rel)
    if r.save_now:
        os.makedirs(os.path.dirname(S.path), exist_ok=True)
        S.project.save(S.path)
        _INSPECT_CACHE.pop(S.path, None)
    return _project_json(S.project)


class UploadOpenReq(BaseModel):
    filename: str
    content_b64: str
    name: str = ""
    description: str = ""
    path: str = ""
    save_now: bool = True


@router.post("/api/project/upload-open")
def project_upload_open(r: UploadOpenReq):
    raw = base64.b64decode(r.content_b64)
    low = r.filename.lower()
    stem = os.path.splitext(os.path.basename(r.filename))[0]
    if low.endswith(".hex"):
        img = I.hex_to_image(raw.decode("latin1", "replace"))
        p = Project.from_bytes(img, source=r.filename, ibis_address=S.address)
    elif low.endswith(".bin"):
        p = Project.from_bytes(raw, source=r.filename, ibis_address=S.address)
    elif low.endswith((".yaml", ".yml")):
        data = yaml.safe_load(raw.decode("utf-8")) or {}
        f = data.get("fonts")
        if not isinstance(f, dict):
            raise HTTPException(400, "nahraný YAML musí obsahovat vložené fonty (ne odkaz na externí fonts.yaml)")
        p = Project(data, FontSet.from_yaml(f), S.workdir)
    else:
        raise HTTPException(400, "podporované soubory jsou .hex, .bin nebo samostatný .yaml")
    title = r.name.strip() or _display_name(p.data, None) or stem
    _apply_meta(p, title if r.name.strip() or not (p.data.get("meta") or {}).get("name") else "", r.description)
    rel = r.path.strip() or _unique_project_relpath(r.name or stem)
    S.bundle = None
    S.active_panel = None
    S.project = p
    S.path = _safe_workdir_path(rel)
    S.address = int((p.data.get("panel") or {}).get("ibis_address", S.address))
    if r.save_now:
        os.makedirs(os.path.dirname(S.path), exist_ok=True)
        S.project.save(S.path)
        _INSPECT_CACHE.pop(S.path, None)
    return _project_json(S.project)


class SaveAsReq(BaseModel):
    path: str = ""
    name: str = ""
    description: str = ""
    data: dict | None = None


@router.post("/api/project/save-as")
def project_save_as(r: SaveAsReq):
    p = _need_project()
    if r.data is not None:
        p.data = {**r.data, "fonts": p.data.get("fonts")}
    if S.bundle is not None:
        b = S.bundle
        if r.name.strip():
            b.meta["name"] = r.name.strip()
        if r.description or not b.meta.get("description"):
            b.meta["description"] = r.description.strip()
        rel = r.path.strip() or _unique_project_relpath(r.name or str(b.meta.get("name") or "kopie"))
        ap = _safe_workdir_path(rel)
        b.save(ap)
        _INSPECT_CACHE.pop(ap, None)
        _activate_bundle(b, S.active_panel)
        return _project_json(S.project)
    _apply_meta(p, r.name, r.description)
    rel = r.path.strip() or _unique_project_relpath(r.name or (p.data.get("meta") or {}).get("name") or "kopie")
    S.path = _safe_workdir_path(rel)
    os.makedirs(os.path.dirname(S.path), exist_ok=True)
    p.save(S.path)
    _INSPECT_CACHE.pop(S.path, None)
    return _project_json(p)


class DuplicateReq(BaseModel):
    source_path: str
    name: str = ""
    path: str = ""


@router.post("/api/project/duplicate")
def project_duplicate(r: DuplicateReq):
    src_abs = _safe_workdir_path(r.source_path)
    if not os.path.exists(src_abs):
        raise HTTPException(404, "zdrojový projekt neexistuje")
    if ProjectBundle.is_bundle_yaml(src_abs):
        b = ProjectBundle.load(src_abs)
        orig_name = str(b.meta.get("name") or "Projekt")
        new_name = r.name.strip() or f"{orig_name} (kopie)"
        b.meta["name"] = new_name
        rel = r.path.strip() or _unique_project_relpath(new_name)
        dst_abs = _safe_workdir_path(rel)
        b.save(dst_abs)
        _INSPECT_CACHE.pop(dst_abs, None)
        return {"ok": True, "project": _inspect_file(dst_abs, rel)}
    low = src_abs.lower()
    if low.endswith(".hex"):
        p = Project.from_hex(src_abs, ibis_address=S.address)
    elif low.endswith(".bin"):
        p = Project.from_bin(src_abs, ibis_address=S.address)
    else:
        p = Project.load(src_abs)
    orig_name = _display_name(p.data, r.source_path)
    new_name = r.name.strip() or f"{orig_name} (kopie)"
    _apply_meta(p, new_name, str((p.data.get("meta") or {}).get("description") or ""))
    rel = r.path.strip() or _unique_project_relpath(new_name)
    dst_abs = _safe_workdir_path(rel)
    os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
    p.save(dst_abs)
    _INSPECT_CACHE.pop(dst_abs, None)
    return {"ok": True, "project": _inspect_file(dst_abs, rel)}


class DeleteReq(BaseModel):
    path: str


@router.post("/api/project/delete")
def project_delete(r: DeleteReq):
    ap = _safe_workdir_path(r.path)
    if not ap.lower().endswith((".yaml", ".yml")):
        raise HTTPException(400, "mazat lze jen uložené YAML projekty (původní .hex/.bin se nezmazou)")
    if not os.path.exists(ap):
        raise HTTPException(404, "projekt neexistuje")
    folder = os.path.dirname(ap)
    wd = os.path.abspath(S.workdir)
    if ProjectBundle.is_bundle_yaml(ap):
        is_active = bool(S.bundle and os.path.abspath(S.bundle.manifest_path) == ap)
        if os.path.abspath(folder) != wd and os.path.basename(ap).lower() == "projekt.yaml":
            shutil.rmtree(folder)
        else:
            os.remove(ap)
        _INSPECT_CACHE.pop(ap, None)
        if is_active:
            S.bundle = None
            S.active_panel = None
            S.project = None
            S.path = None
        return {"ok": True, "closed": is_active}

    os.remove(ap)
    _INSPECT_CACHE.pop(ap, None)
    if os.path.abspath(folder) != wd and os.path.isdir(folder):
        rem = os.listdir(folder)
        if rem == ["fonts.yaml"]:
            os.remove(os.path.join(folder, "fonts.yaml"))
            rem = []
        if not rem:
            os.rmdir(folder)
    closed = bool(S.path and os.path.abspath(S.path) == ap)
    if closed:
        S.project = None
        S.path = None
    return {"ok": True, "closed": closed}


@router.post("/api/project/close")
def project_close():
    S.bundle = None
    S.active_panel = None
    S.project = None
    S.path = None
    return {"ok": True}


@router.get("/api/project/check")
def project_check():
    return {"items": _need_project().check()}


@router.get("/api/project/hex", response_class=PlainTextResponse)
def project_hex():
    text, problems = _need_project().to_hex()
    if problems:
        raise HTTPException(400, "; ".join(problems[:10]))
    return text


@router.get("/api/project/bin")
def project_bin():
    data, problems = _need_project().to_bin()
    if problems:
        raise HTTPException(400, "; ".join(problems[:10]))
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition": 'attachment; filename="data.bin"'})
