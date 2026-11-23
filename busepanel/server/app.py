"""Local web server for the graphical editor (FastAPI). Start with `busepanel serve`."""
from __future__ import annotations

import base64
import copy
import datetime as dt
import os
import re
import shutil
import threading
import time
import traceback
import unicodedata
import uuid
import webbrowser

import yaml

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..core import image as I
from ..core import text as T
from ..live import modes as live
from ..loader.device import Panel, list_ports
from ..core.fonts import CodeTable, FontSet, Glyph, _ct_code, _ct_index
from ..core.bundle import (PanelEntry, ProjectBundle, build_demo_bundle,
                           build_empty_bundle, load_builtin_fonts)
from ..core.importers import import_file, merge
from ..core.project import SECTIONS, Project
from ..core.render import render_frames, simulate_cycle_step, text_bitmap
from ..outputs.serial_out import port_lock
from ..outputs.ws_out import HUB
from ..panels.profile import PROFILES
from . import sim_api

WEB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")


class State:
    def __init__(self, workdir: str):
        self.workdir = os.path.abspath(workdir)
        self.project: Project | None = None
        self.path: str | None = None
        self.bundle: ProjectBundle | None = None
        self.active_panel: str | None = None
        from ..loader.device import default_port
        self.port = default_port()
        self.address = 10
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()          # one serial operation at a time
        self.live_runner = None
        self.live_thread = None
        self.live_log: list[str] = []


S: State = State(".")
app = FastAPI(title="BUSE Studio")
app.include_router(sim_api.router)


@app.middleware("http")
async def no_stale_ui(request, call_next):
    """Browsers must revalidate the UI files, otherwise an updated app mixes old and new modules."""
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# ------------------------------------------------------------------ helpers
def _need_project() -> Project:
    if S.project is None:
        raise HTTPException(400, "není otevřený žádný projekt")
    return S.project


def _abs(path: str) -> str:
    p = os.path.abspath(os.path.join(S.workdir, path))
    return p


def _safe_workdir_path(path: str) -> str:
    wd = os.path.abspath(S.workdir)
    ap = os.path.abspath(os.path.join(wd, path))
    try:
        if os.path.commonpath([wd, ap]) != wd:
            raise HTTPException(400, "cesta musí být uvnitř pracovní složky")
    except ValueError as e:
        raise HTTPException(400, "neplatná cesta") from e
    return ap


def _slugify(text: str) -> str:
    norm = unicodedata.normalize("NFKD", (text or "").strip())
    ascii_s = norm.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_s).strip("-")
    return slug or "projekt"


def _unique_project_relpath(base_name: str) -> str:
    slug = _slugify(base_name)
    cand = f"projekty/{slug}/projekt.yaml"
    if not os.path.exists(_abs(cand)):
        return cand
    i = 2
    while os.path.exists(_abs(f"projekty/{slug}-{i}/projekt.yaml")):
        i += 1
    return f"projekty/{slug}-{i}/projekt.yaml"


def _counts_from_data(data: dict) -> dict[str, int]:
    out = {}
    for sec in ("lines", "destinations", "destinations4", "stops", "messages", "supplementary", "cycles", "format_strings"):
        v = data.get(sec)
        if isinstance(v, dict):
            out[sec] = len(v)
    return out


def _display_name(data: dict, rel: str | None) -> str:
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    if meta.get("name"):
        return str(meta["name"])
    if not rel:
        return (data.get("config") or {}).get("version") or "Bez názvu"
    base = os.path.basename(rel)
    stem = os.path.splitext(base)[0]
    if stem.lower() == "projekt":
        parent = os.path.basename(os.path.dirname(rel))
        if parent and parent != ".":
            return parent
    return stem


def _rel_or_abs(path: str | None) -> str | None:
    if not path:
        return None
    wd = os.path.abspath(S.workdir)
    ap = os.path.abspath(path)
    try:
        if os.path.commonpath([wd, ap]) == wd:
            return os.path.relpath(ap, wd).replace("\\", "/")
    except ValueError:
        pass
    return path


def _bundle_json() -> dict | None:
    if S.bundle is None:
        return None
    b = S.bundle
    return {
        "manifest_path": b.manifest_path,
        "rel_path": _rel_or_abs(b.manifest_path),
        "name": str(b.meta.get("name") or "Projekt"),
        "description": str(b.meta.get("description") or ""),
        "active_panel": S.active_panel,
        "panels": [pe.info() for pe in b.panels],
        "simulation": b.simulation,
        "live": b.live,
    }


def _activate_bundle(bundle: ProjectBundle, panel_id: str | None = None) -> Project:
    S.bundle = bundle
    pe = bundle.panel(panel_id) if panel_id else None
    if pe is None and bundle.panels:
        pe = bundle.panels[0]
    if pe is None or pe.project is None:
        raise HTTPException(400, "vícepanelový projekt neobsahuje žádný panel")
    S.active_panel = pe.id
    S.project = pe.project
    S.path = os.path.join(bundle.base_dir, pe.db) if bundle.base_dir else pe.db
    S.address = int((S.project.data.get("panel") or {}).get("ibis_address", pe.ibis_address))
    return S.project


def _project_json(p: Project) -> dict:
    from ..core.image import CYK_ENTER, CYK_LEAVE, CYK_SOURCES
    from ..gbuse0.database import ALL_COMMANDS_0, LAYOUT_TYPES, PROP_BITS
    data = {k: v for k, v in p.data.items() if k != "fonts"}
    g = p.geometry
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    rel = _rel_or_abs(S.bundle.manifest_path if S.bundle else S.path)
    return {
        "path": S.bundle.manifest_path if S.bundle else S.path,
        "rel_path": rel,
        "workdir": os.path.abspath(S.workdir),
        "name": str(S.bundle.meta.get("name") or _display_name(data, rel)) if S.bundle else _display_name(data, rel),
        "description": str(S.bundle.meta.get("description") or "") if S.bundle else str(meta.get("description") or ""),
        "section_counts": _counts_from_data(data),
        "bundle": _bundle_json(),
        "data": data,
        "format": p.format,
        "columns": g.columns,
        "rows": g.rows,
        "fonts": [f.id for f in p.fonts.fonts],
        "geometry": g.__dict__,
        "defaults": p.defaults(),
        "cyk_meta": {"sources": CYK_SOURCES, "enter": CYK_ENTER, "leave": CYK_LEAVE},
        "gbuse0_meta": {"commands": ALL_COMMANDS_0, "properties": PROP_BITS, "layouts": LAYOUT_TYPES},
    }


def _job(fn, *args) -> str:
    jid = uuid.uuid4().hex[:8]
    job = {"id": jid, "state": "running", "stage": "", "done": 0, "total": 0, "log": [], "result": None}
    S.jobs[jid] = job

    def log(m):
        job["log"].append(m)

    def progress(stage, done, total):
        job.update(stage=stage, done=done, total=total or 0)

    def run():
        with port_lock(S.port):
            try:
                panel = Panel(S.port, S.address, log=log, progress=progress)
                job["result"] = fn(panel, *args)
                job["state"] = "done"
            except Exception as e:      # noqa: BLE001
                job["state"] = "error"
                job["log"].append(f"CHYBA: {e}")
                traceback.print_exc()
    threading.Thread(target=run, daemon=True).start()
    return jid


# ------------------------------------------------------------------ files / project
_INSPECT_CACHE: dict[str, tuple[int, int, dict | None]] = {}


def _inspect_file(full: str, rel: str) -> dict | None:
    try:
        st = os.stat(full)
    except OSError:
        return None
    cached = _INSPECT_CACHE.get(full)
    if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return cached[2]

    low = full.lower()
    mtime_iso = dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
    info: dict | None = None
    try:
        if low.endswith((".yaml", ".yml")):
            if ProjectBundle.is_bundle_yaml(full):
                b = ProjectBundle.load(full)
                meta = b.meta or {}
                p_list = [pe.info() for pe in b.panels]
                info = {
                    "path": rel,
                    "kind": "bundle",
                    "name": str(meta.get("name") or _display_name({}, rel)),
                    "description": str(meta.get("description") or ""),
                    "format": "multi",
                    "panel_type": f"{len(b.panels)} panely",
                    "columns": p_list[0]["columns"] if p_list else 0,
                    "rows": p_list[0]["rows"] if p_list else 0,
                    "fw": "",
                    "version": "",
                    "panel_kind": None,
                    "panels": p_list,
                    "has_sim": bool(b.simulation),
                    "has_live": bool(b.live),
                    "counts": b.aggregated_counts(),
                    "mtime": mtime_iso,
                    "mtime_ts": st.st_mtime,
                    "size": st.st_size,
                }
                _INSPECT_CACHE[full] = (st.st_mtime_ns, st.st_size, info)
                return info
            with open(full, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            if not isinstance(data, dict) or "fonts" not in data:
                _INSPECT_CACHE[full] = (st.st_mtime_ns, st.st_size, None)
                return None
            pnl = data.get("panel") or {}
            cfg = data.get("config") or {}
            fmt = "gbuse0" if pnl.get("format") == "gbuse0" else "gbuse1"
            meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
            info = {
                "path": rel,
                "kind": "yaml",
                "name": _display_name(data, rel),
                "description": str(meta.get("description") or ""),
                "format": fmt,
                "panel_type": str(pnl.get("type") or ("BS210" if fmt == "gbuse0" else "BS120")),
                "columns": int(cfg.get("columns", 140 if fmt == "gbuse0" else 135)),
                "rows": int(cfg.get("rows", 19 if fmt == "gbuse0" else 8)),
                "fw": str(cfg.get("fw") or ""),
                "version": str(cfg.get("version") or ""),
                "panel_kind": cfg.get("panel_kind"),
                "counts": _counts_from_data(data),
                "mtime": mtime_iso,
                "mtime_ts": st.st_mtime,
                "size": st.st_size,
            }
        elif low.endswith((".hex", ".bin")):
            if low.endswith(".hex"):
                with open(full, encoding="latin1", errors="replace") as fh:
                    img = I.hex_to_image(fh.read())
                kind = "hex"
            else:
                with open(full, "rb") as fh:
                    img = fh.read()
                kind = "bin"
            if img[:2] == b"cT":
                info = {
                    "path": rel, "kind": kind, "name": os.path.splitext(os.path.basename(rel))[0],
                    "description": "Formát gBUSE2b (Perla – pouze pro čtení)", "format": "gbuse2b",
                    "panel_type": "Perla", "columns": 0, "rows": 0, "fw": "", "version": "",
                    "panel_kind": None, "counts": {}, "mtime": mtime_iso, "mtime_ts": st.st_mtime, "size": st.st_size,
                }
            elif img:
                p = Project.from_bytes(img, source=full)
                cfg = p.data.get("config") or {}
                pnl = p.data.get("panel") or {}
                info = {
                    "path": rel,
                    "kind": kind,
                    "name": os.path.splitext(os.path.basename(rel))[0],
                    "description": "",
                    "format": p.format,
                    "panel_type": str(pnl.get("type") or ("BS210" if p.format == "gbuse0" else "BS120")),
                    "columns": p.geometry.columns,
                    "rows": p.geometry.rows,
                    "fw": str(cfg.get("fw") or ""),
                    "version": str(cfg.get("version") or ""),
                    "panel_kind": cfg.get("panel_kind"),
                    "counts": _counts_from_data(p.data),
                    "mtime": mtime_iso,
                    "mtime_ts": st.st_mtime,
                    "size": st.st_size,
                }
    except Exception:      # noqa: BLE001 – skip unreadable/unrelated files
        info = None
    _INSPECT_CACHE[full] = (st.st_mtime_ns, st.st_size, info)
    return info


@app.get("/api/files")
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


_SKIP_DIRS = {"__pycache__", "node_modules", "venv", "build", "dist"}


def _is_project_yaml(path) -> bool:
    if ProjectBundle.is_bundle_yaml(path):
        return True
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return any(line.startswith("fonts:") for line in fh)
    except OSError:
        return False


class OpenReq(BaseModel):
    path: str


@app.post("/api/project/open")
def project_open(r: OpenReq):
    path = _abs(r.path)
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


@app.get("/api/project")
def project_get():
    return _project_json(_need_project())


class SaveReq(BaseModel):
    data: dict
    path: str | None = None


@app.post("/api/project/save")
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
        return {"saved": S.bundle.manifest_path, **_project_json(p)}
    if r.path:
        S.path = _safe_workdir_path(r.path)
    os.makedirs(os.path.dirname(S.path), exist_ok=True)
    p.save(S.path)
    _INSPECT_CACHE.pop(S.path, None)
    return {"saved": S.path, **_project_json(p)}


@app.post("/api/project/update")
def project_update(r: SaveReq):
    p = _need_project()
    p.data = {**r.data, "fonts": p.data.get("fonts")}
    return {"ok": True}


def _load_fontset(source: str, fmt: str = "gbuse1") -> FontSet:
    src = (source or "").strip()
    if not src:
        return load_builtin_fonts("gbuse0" if fmt == "gbuse0" else "gbuse1")
    if src in ("gbuse1", "gbuse", "gbuse0", "builtin"):
        return load_builtin_fonts(src)
    ap = _abs(src)
    low = ap.lower()
    if low.endswith(".hex"):
        return Project.from_hex(ap).fonts
    if low.endswith(".bin"):
        return Project.from_bin(ap).fonts
    if ProjectBundle.is_bundle_yaml(ap):
        b = ProjectBundle.load(ap)
        for pe in b.panels:
            if pe.project and pe.format == fmt:
                return pe.project.fonts
        if b.panels and b.panels[0].project:
            return b.panels[0].project.fonts
    return Project.load(ap).fonts


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


def _apply_meta(p: Project, name: str, description: str):
    if name or description:
        meta = dict(p.data.get("meta") or {})
        if name:
            meta["name"] = name.strip()
        if description:
            meta["description"] = description.strip()
        elif "description" in meta and not description:
            meta.pop("description", None)
        p.data["meta"] = meta


@app.post("/api/project/new")
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


@app.post("/api/project/upload-open")
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


@app.post("/api/project/save-as")
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


@app.post("/api/project/duplicate")
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


@app.post("/api/project/delete")
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


@app.post("/api/project/close")
def project_close():
    S.bundle = None
    S.active_panel = None
    S.project = None
    S.path = None
    return {"ok": True}


# ------------------------------------------------------------------ multi-panel bundle APIs
class BundleNewReq(BaseModel):
    template: str = "demo_bus"      # demo_bus | empty_bus_3 | empty_bus_4 | empty_gbuse1 | empty_gbuse0
    name: str = ""
    description: str = ""
    path: str = ""
    save_now: bool = True


@app.post("/api/bundle/new")
def bundle_new(r: BundleNewReq):
    if r.template == "demo_bus":
        b = build_demo_bundle(
            name=r.name.strip() or "Ukázkový autobus (3 panely)",
            description=r.description.strip() or "Čelní 140×19 + Boční s nácestnými 112×19 + Vnitřní BS120 (135×8)",
        )
    else:
        preset_map = {
            "empty_bus_3": "bus_3",
            "empty_bus_4": "bus_4",
            "empty_gbuse1": "single_gbuse1",
            "empty_gbuse0": "single_gbuse0",
        }
        preset = preset_map.get(r.template, "bus_3")
        b = build_empty_bundle(
            preset=preset,
            name=r.name.strip() or "Prázdný projekt",
            description=r.description.strip() or "Čistá databáze pouze s originálními fonty",
        )
    rel = r.path.strip() or _unique_project_relpath(str(b.meta.get("name") or "projekt"))
    ap = _safe_workdir_path(rel)
    b.manifest_path = ap
    b.base_dir = os.path.dirname(ap)
    if r.save_now:
        b.save(ap)
        _INSPECT_CACHE.pop(ap, None)
    _activate_bundle(b)
    return _project_json(S.project)


class BundleSelectReq(BaseModel):
    panel_id: str
    data: dict | None = None


@app.post("/api/bundle/select-panel")
def bundle_select_panel(r: BundleSelectReq):
    if S.bundle is None:
        raise HTTPException(400, "aktivní projekt není vícepanelový")
    if r.data is not None and S.project is not None:
        S.project.data = {**r.data, "fonts": S.project.data.get("fonts")}
    pe = S.bundle.panel(r.panel_id)
    if pe is None:
        raise HTTPException(404, f"panel {r.panel_id!r} neexistuje")
    _activate_bundle(S.bundle, r.panel_id)
    return _project_json(S.project)


class BundleAddPanelReq(BaseModel):
    name: str
    role: str = "front"             # front | side | rear | interior | custom
    format: str = "gbuse0"          # gbuse0 | gbuse1
    preset: str = "front"
    fw: str = ""
    columns: int = 140
    rows: int = 19
    panel_kind: int = 1
    ibis_address: int = 10
    fonts_from: str = ""


@app.post("/api/bundle/add-panel")
def bundle_add_panel(r: BundleAddPanelReq):
    p_cur = _need_project()
    # Upgrade single-panel project into a multi-panel bundle automatically
    if S.bundle is None:
        cur_name = _display_name(p_cur.data, _rel_or_abs(S.path))
        cur_role = "interior" if p_cur.format == "gbuse1" else "front"
        base_dir = os.path.dirname(S.path) if S.path else _abs(_unique_project_relpath(cur_name))
        if S.path and not os.path.basename(S.path).lower() == "projekt.yaml":
            base_dir = os.path.splitext(S.path)[0]
        if os.path.basename(os.path.dirname(base_dir)) != "projekty" and S.path:
            base_dir = os.path.dirname(_safe_workdir_path(_unique_project_relpath(cur_name)))
        manifest = os.path.join(base_dir, "projekt.yaml")
        first = PanelEntry(
            id="hlavni",
            name=(p_cur.data.get("meta") or {}).get("name") or ("Vnitřní panel" if cur_role == "interior" else "Hlavní panel"),
            role=cur_role,
            db="panely/hlavni/projekt.yaml",
            ibis_address=int((p_cur.data.get("panel") or {}).get("ibis_address", 10)),
            project=p_cur,
        )
        b = ProjectBundle(
            meta={"name": cur_name, "description": str((p_cur.data.get("meta") or {}).get("description") or "")},
            panels=[first],
            base_dir=base_dir,
            manifest_path=manifest,
        )
        S.bundle = b
    else:
        b = S.bundle

    fmt = "gbuse0" if r.format == "gbuse0" else "gbuse1"
    fs = _load_fontset(r.fonts_from, fmt=fmt)
    if fmt == "gbuse0":
        np = Project.new_gbuse0(fs, preset=r.preset, version="NOVY", fw=r.fw or "3.08", ibis_address=r.ibis_address)
        if r.preset == "custom" or r.columns or r.rows:
            cfg = np.data["config"]
            if r.preset == "custom":
                cfg["columns"] = int(r.columns or 140)
                cfg["rows"] = int(r.rows or 19)
                cfg["fields"] = {
                    "line": {"r0": 1, "r1": cfg["rows"], "c0": 1, "c1": min(28, cfg["columns"]), "font": 8, "spacing": 1, "shift": 0, "interval": 1.0},
                    "dest": {"r0": 1, "r1": cfg["rows"], "c0": min(29, cfg["columns"]), "c1": cfg["columns"], "font": 7, "spacing": 1, "shift": 0, "interval": 1.0},
                }
    else:
        fw = r.fw or ("2.12" if r.panel_kind == 2 else "2.06")
        np = Project.new_gbuse1(fs, version="NOVY", columns=int(r.columns or 135),
                                panel_kind=int(r.panel_kind or 1), fw=fw, ibis_address=r.ibis_address)
    pid = b.unique_panel_id(r.name or r.role)
    np.data["meta"] = {"name": r.name.strip() or pid}
    b.add_panel(PanelEntry(id=pid, name=r.name.strip() or pid, role=r.role, ibis_address=r.ibis_address, project=np))
    b.save()
    _INSPECT_CACHE.pop(b.manifest_path, None)
    _activate_bundle(b, pid)
    return _project_json(S.project)


@app.post("/api/bundle/remove-panel")
def bundle_remove_panel(r: BundleSelectReq):
    if S.bundle is None:
        raise HTTPException(400, "aktivní projekt není vícepanelový")
    try:
        ok = S.bundle.remove_panel(r.panel_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not ok:
        raise HTTPException(404, "panel neexistuje")
    pdir = os.path.join(S.bundle.base_dir, "panely", r.panel_id)
    if os.path.isdir(pdir):
        shutil.rmtree(pdir)
    S.bundle.save()
    _INSPECT_CACHE.pop(S.bundle.manifest_path, None)
    next_id = S.bundle.panels[0].id if S.active_panel == r.panel_id else S.active_panel
    _activate_bundle(S.bundle, next_id)
    return _project_json(S.project)


class BundleMetaReq(BaseModel):
    name: str = ""
    description: str = ""
    simulation: dict | None = None
    live: dict | None = None


@app.post("/api/bundle/update-meta")
def bundle_update_meta(r: BundleMetaReq):
    if S.bundle is None:
        p = _need_project()
        _apply_meta(p, r.name, r.description)
        return _project_json(p)
    if r.name.strip():
        S.bundle.meta["name"] = r.name.strip()
    S.bundle.meta["description"] = r.description.strip()
    if r.simulation is not None:
        S.bundle.simulation = r.simulation
    if r.live is not None:
        S.bundle.live = r.live
    return _project_json(_need_project())


class PropagateReq(BaseModel):
    section: str
    key: str
    target_panels: list[str] | None = None
    overwrite: bool = True
    data: dict | None = None


@app.post("/api/bundle/propagate")
def bundle_propagate(r: PropagateReq):
    if S.bundle is None or S.active_panel is None:
        raise HTTPException(400, "rozkopírování vyžaduje vícepanelový projekt")
    p = _need_project()
    if r.data is not None:
        p.data = {**r.data, "fonts": p.data.get("fonts")}
    try:
        res = S.bundle.propagate_record(S.active_panel, r.section, r.key, r.target_panels, overwrite=r.overwrite)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    return {**res, "project": _project_json(p)}


class PreviewAllReq(BaseModel):
    line_key: str = ""
    dest_key: str = ""
    stop_key: str = ""
    section: str = "destinations"
    value: object = None


@app.post("/api/bundle/preview-all")
def bundle_preview_all(r: PreviewAllReq):
    if S.bundle is None:
        p = _need_project()
        entries = [PanelEntry(id="active", name=_display_name(p.data, _rel_or_abs(S.path)), role="custom", project=p)]
    else:
        entries = S.bundle.panels

    out = []
    for pe in entries:
        pr = pe.project
        if pr is None:
            continue
        is_active = (pe.id == S.active_panel) or (S.bundle is None)
        lval = (r.value if is_active and r.section == "lines" else (pr.data.get("lines") or {}).get(r.line_key)) if (r.line_key or (is_active and r.section == "lines")) else None
        dval = (r.value if is_active and r.section in ("destinations", "destinations4") else (pr.data.get("destinations") or {}).get(r.dest_key)) if (r.dest_key or (is_active and r.section in ("destinations", "destinations4"))) else None
        sval = (r.value if is_active and r.section == "stops" else (pr.data.get("stops") or {}).get(r.stop_key)) if (r.stop_key or (is_active and r.section == "stops")) else None

        if pr.format == "gbuse0":
            from ..gbuse0 import mnemo as M0
            from ..gbuse0.program import render_combined
            db, _ = pr.to_gbuse0()
            ct = pr.fonts.codetable
            enc = lambda v, fn: (M0.compose_record(v, db, fn, ct)[0] if isinstance(v, dict) else M0.from_mnemo(str(v), ct)[0]) if v else None
            lr = enc(lval, "line")
            dr = enc(dval, "dest") if pe.role != "rear" else None
            sr = enc(sval, "stop") if pe.role != "rear" and "stop" in db.fields else None
            res = render_combined(db, line_rec=lr, dest_rec=dr, stop_rec=sr)
            out.append({
                "id": pe.id, "name": pe.name, "role": pe.role, "format": "gbuse0",
                "columns": res["columns"], "rows": res["rows"],
                "frames": res["frames"], "overflow": res["overflow"],
            })
        else:
            g = pr.geometry
            f0, s0 = pr.defaults()
            default_iv = float(((pr.data.get("config") or {}).get("defaults") or {}).get("interval", 1.0))
            lb, _ = pr.encode_value(lval) if lval else (b"", [])
            target_val = dval if dval is not None else (sval if r.section == "stops" else None)
            db_bytes, _ = pr.encode_value(target_val) if target_val else (b"", [])
            rf = render_frames(db_bytes, pr.fonts, g, (f0, s0), default_iv, area="dest") if db_bytes else {"frames": [], "loop": None}
            l_item = None
            if lb:
                lc, li, _ = text_bitmap(lb, pr.fonts, f0, s0)
                lw = g.line_right - g.line_left + 1
                l_item = {"cols": lc, "inv": [1 if x else 0 for x in li],
                          "start": g.line_left + max(0, (lw - len(lc)) // 2), "lo": g.line_left, "hi": g.line_right}
            ov = any(fr.get("overflow") for fr in rf["frames"])
            out.append({
                "id": pe.id, "name": pe.name, "role": pe.role, "format": "gbuse1",
                "columns": g.columns, "rows": 8, "geometry": g.__dict__,
                "line": l_item, "frames": rf["frames"], "loop": rf["loop"], "overflow": {"dest": ov},
            })
    return {"panels": out}


@app.get("/api/bundle/export-zip")
def bundle_export_zip():
    if S.bundle is None:
        raise HTTPException(400, "hromadný ZIP export vyžaduje vícepanelový projekt")
    data = S.bundle.export_zip_bytes()
    slug = _slugify(str(S.bundle.meta.get("name") or "projekt"))
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'})


@app.post("/api/bundle/apply-sim")
def bundle_apply_sim():
    if S.bundle is None:
        raise HTTPException(400, "aktivní projekt není vícepanelový")
    sim = S.bundle.simulation or {}
    from ..sim.line import Line
    v_cfg = sim.get("vehicle_data")
    if v_cfg is None and isinstance(sim.get("vehicle"), str) and not sim["vehicle"].startswith("template:"):
        vp = os.path.join(S.bundle.base_dir, sim["vehicle"])
        if os.path.exists(vp):
            with open(vp, encoding="utf-8") as fh:
                v_cfg = yaml.safe_load(fh)
    if isinstance(v_cfg, dict):
        # Resolve relative database paths inside the bundle directory
        resolved = copy.deepcopy(v_cfg)
        for pitem in resolved.get("panels") or []:
            dbp = pitem.get("database")
            if isinstance(dbp, str) and not os.path.isabs(dbp):
                cand = os.path.join(S.bundle.base_dir, dbp)
                if os.path.exists(cand):
                    pitem["database"] = os.path.relpath(cand, S.workdir)
        sim_api.SIM.vehicle_cfg = sim_api._clean_vehicle(resolved)
    if isinstance(sim.get("default_line"), str) and not sim["default_line"].startswith("template:"):
        lp = os.path.join(S.bundle.base_dir, sim["default_line"])
        if os.path.exists(lp):
            with open(lp, encoding="utf-8") as fh:
                sim_api.SIM.line = Line.from_dict(yaml.safe_load(fh) or {})
                sim_api.SIM.route = sim_api.SIM.line.route()
    for pe in S.bundle.panels:
        if pe.format == "gbuse1" and pe.project is not None:
            sim_api.SIM.db = pe.project
            sim_api.SIM.db_path = os.path.join(S.bundle.base_dir, pe.db)
            break
    sim_api._rebuild()
    return {"ok": True, "vehicle": sim_api.SIM.vehicle_cfg.get("name")}


@app.get("/api/project/check")
def project_check():
    return {"items": _need_project().check()}


@app.get("/api/project/hex", response_class=PlainTextResponse)
def project_hex():
    text, problems = _need_project().to_hex()
    if problems:
        raise HTTPException(400, "; ".join(problems[:10]))
    return text


@app.get("/api/project/bin")
def project_bin():
    data, problems = _need_project().to_bin()
    if problems:
        raise HTTPException(400, "; ".join(problems[:10]))
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition": 'attachment; filename="data.bin"'})


# ------------------------------------------------------------------ preview
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


@app.post("/api/preview")
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


@app.post("/api/gbuse0/compose")
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


@app.post("/api/gbuse/autoformat")
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


@app.post("/api/mnemo/convert")
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


# ------------------------------------------------------------------ fonts
@app.get("/api/fonts")
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


@app.post("/api/fonts/glyph")
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


@app.post("/api/fonts/glyph/delete")
def fonts_glyph_delete(r: GlyphDel):
    f = _need_project().fonts.font(r.font)
    if f and r.code in f.glyphs:
        del f.glyphs[r.code]
    return {"ok": True}


class CodeReq(BaseModel):
    code: int
    char: str


@app.post("/api/fonts/codetable")
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


@app.post("/api/fonts/manage")
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


@app.get("/api/fonts/export-fnt")
def fonts_export_fnt():
    fs = _need_project().fonts
    return Response(content=fs.to_fnt_bytes(), media_type="application/octet-stream",
                    headers={"Content-Disposition": 'attachment; filename="fonty.fnt"'})


class ImportFntReq(BaseModel):
    content_b64: str


@app.post("/api/fonts/import-fnt")
def fonts_import_fnt(r: ImportFntReq):
    p = _need_project()
    p.fonts = FontSet.from_fnt_bytes(base64.b64decode(r.content_b64))
    return {"ok": True, "fonts": [f.id for f in p.fonts.fonts]}


# ------------------------------------------------------------------ import / gbuse section files
class ImportReq(BaseModel):
    name: str
    content_b64: str
    section: str = "destinations"
    replace: bool = False
    defaults: dict = {}


@app.post("/api/import")
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


@app.post("/api/gbuse/section/import")
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


@app.post("/api/gbuse/section/export")
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


# ------------------------------------------------------------------ panel
class PanelCfg(BaseModel):
    port: str
    address: int = 10


@app.get("/api/panel/ports")
def panel_ports():
    return {"ports": list_ports(), "port": S.port, "address": S.address}


@app.post("/api/panel/config")
def panel_config(r: PanelCfg):
    S.port, S.address = r.port, r.address
    return {"ok": True}


@app.post("/api/panel/scan")
def panel_scan():
    return {"job": _job(lambda panel: Panel.scan(S.port, log=panel.log))}


class SendReq(BaseModel):
    telegrams: list[str]
    query: bool = False


@app.post("/api/panel/send")
def panel_send(r: SendReq):
    with port_lock(S.port):
        panel = Panel(S.port, S.address)
        if r.query:
            return {"reply": panel.query(r.telegrams[0])}
        panel.send(*r.telegrams, pause=0.15)
    return {"ok": True}


class TextReq(BaseModel):
    text: str
    kind: str = "v"            # v | v+line | zI | zM
    font: int | None = None
    spacing: int | None = None
    line: str | None = None    # optional line number (3 digits) for v+line


@app.post("/api/panel/text")
def panel_text(r: TextReq):
    p = _need_project()
    if r.kind not in live.TEXT_MODES:
        raise HTTPException(400, "neznámý druh textu")
    payload, problems = live.text_payload(p.fonts, r.text, r.font, r.spacing)
    switch, cmd = live.TEXT_MODES[r.kind]
    tel = [switch, cmd.encode() + payload]      # switch first, then the (new) text
    if r.line:
        tel.insert(0, "l" + r.line.zfill(3))
    with port_lock(S.port):
        Panel(S.port, S.address).send(*tel, pause=0.2)
    return {"sent": [t if isinstance(t, str) else t.decode("cp1250", "replace") for t in tel], "problems": problems}


@app.post("/api/panel/upload")
def panel_upload():
    text, problems = _need_project().to_hex()
    if problems:
        raise HTTPException(400, "Projekt obsahuje chyby: " + "; ".join(problems[:10]))
    return {"job": _job(lambda panel: panel.upload(text))}


class BackupReq(BaseModel):
    name: str = ""
    eprom: bool = False


@app.post("/api/panel/backup")
def panel_backup(r: BackupReq):
    name = r.name or "zaloha_" + time.strftime("%Y%m%d_%H%M%S")
    folder = _abs(os.path.join("zalohy", name))

    def run(panel):
        res = panel.backup(eprom=r.eprom)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "flash.bin"), "wb") as f:
            f.write(res["flash"])
        if res["eprom"]:
            with open(os.path.join(folder, "eprom.bin"), "wb") as f:
                f.write(res["eprom"])
        p_bak = Project.from_bytes(res["flash"], ibis_address=S.address)
        hx, _ = p_bak.to_hex()
        with open(os.path.join(folder, "data.hex"), "w", newline="", encoding="ascii") as f:
            f.write(hx)
        p_bak.save(os.path.join(folder, "projekt.yaml"))
        return {"folder": os.path.relpath(folder, S.workdir), "flash": len(res["flash"])}
    return {"job": _job(run)}


@app.get("/api/jobs/{jid}")
def job_get(jid: str):
    if jid not in S.jobs:
        raise HTTPException(404)
    return S.jobs[jid]


# ------------------------------------------------------------------ live
class LiveBuild(BaseModel):
    mode: str                 # clock | departures
    channel: str = "stops"
    format: str = "%H:%M"
    schedule_csv: str = ""
    style: dict = {}


@app.post("/api/live/build")
def live_build(r: LiveBuild):
    p = _need_project()
    if r.mode == "clock":
        live.add_clock(p, r.channel, r.format, style=r.style or None)
    else:
        live.add_departures(p, live.read_schedule(r.schedule_csv), style=r.style or None)
    return _project_json(p)


class LiveStart(BaseModel):
    mode: str                 # clock | departures | playlist
    channel: str = "stops"    # clock: text | stops | split
    format: str = ""
    font: int | None = None
    spacing: int | None = None
    line_field: bool = False
    output: str = "serial"     # serial | esp
    device: str = ""
    keys: list[str] = []
    interval: float = 10.0
    schedule_csv: str = ""


@app.post("/api/live/start")
def live_start(r: LiveStart):
    live_stop()
    S.live_log = []

    def log(m):
        S.live_log.append(time.strftime("%H:%M:%S ") + m)
        del S.live_log[:-200]

    if r.output == "esp":
        from ..outputs.ws_out import WebSocketOutput
        panel = WebSocketOutput("živě", r.device or "panel")
    else:
        from ..outputs.serial_out import SerialOutput
        panel = SerialOutput("živě", S.port, S.address, pause=0.25)
    if r.mode == "clock" and r.channel == "text":
        runner = live.text_clock_runner(panel, _need_project().fonts, r.format or "%H:%M", log=log)
    elif r.mode == "clock":
        runner = live.clock_runner(panel, r.channel, log=log)
    elif r.mode == "departures":
        sched = live.read_schedule(r.schedule_csv)
        if not sched:
            raise HTTPException(400, "jízdní řád je prázdný nebo má špatné sloupce (cas;linka;cil;dny)")
        runner = live.departures_text_runner(panel, _need_project().fonts, sched, r.format or "{line}  {dest}  {za}",
                                             font=r.font, spacing=r.spacing, log=log, line_field=r.line_field)
    else:
        runner = live.playlist_runner(panel, r.keys, r.interval, channel=r.channel, log=log)
    S.live_runner = runner
    S.live_thread = threading.Thread(target=runner.run, daemon=True)
    S.live_thread.start()
    return {"ok": True}


@app.post("/api/live/stop")
def live_stop():
    if S.live_runner:
        S.live_runner.stop()
        S.live_runner = None
    return {"ok": True}


@app.get("/api/live/status")
def live_status():
    return {"running": S.live_runner is not None, "log": S.live_log[-30:]}


@app.get("/api/live/templates")
def live_templates():
    presets: list[dict] = []
    if S.bundle and isinstance(S.bundle.live, dict):
        for pr in S.bundle.live.get("presets") or []:
            presets.append({**pr, "scope": "project"})
    zdir = os.path.join(S.workdir, "zive")
    if os.path.isdir(zdir):
        for fn in sorted(os.listdir(zdir)):
            if fn.lower().endswith((".yaml", ".yml")):
                try:
                    with open(os.path.join(zdir, fn), encoding="utf-8") as fh:
                        doc = yaml.safe_load(fh) or {}
                    if isinstance(doc, dict):
                        presets.append({**doc, "scope": "template", "file": f"zive/{fn}"})
                except OSError:
                    pass
    return {"presets": presets}


class LiveTemplateReq(BaseModel):
    name: str
    text: str
    kind: str = "v"
    font: int | None = None
    spacing: int | None = None
    line: str = ""
    scope: str = "template"       # template (zive/*.yaml) | project (bundle.live.presets)


@app.post("/api/live/templates/save")
def live_template_save(r: LiveTemplateReq):
    item = {"name": r.name.strip() or "Předvolba", "text": r.text, "kind": r.kind}
    if r.font is not None:
        item["font"] = r.font
    if r.spacing is not None:
        item["spacing"] = r.spacing
    if r.line:
        item["line"] = r.line
    if r.scope == "project" and S.bundle is not None:
        live_d = S.bundle.live = S.bundle.live or {}
        lst = live_d.setdefault("presets", [])
        lst = [x for x in lst if x.get("name") != item["name"]]
        lst.append(item)
        live_d["presets"] = lst
        return {"ok": True, "scope": "project"}
    os.makedirs(os.path.join(S.workdir, "zive"), exist_ok=True)
    fn = os.path.join(S.workdir, "zive", f"{_slugify(item['name'])}.yaml")
    with open(fn, "w", encoding="utf-8") as fh:
        yaml.safe_dump(item, fh, allow_unicode=True, sort_keys=False)
    return {"ok": True, "file": _rel_or_abs(fn)}


# ------------------------------------------------------------------ profiles, icons, devices
@app.get("/api/profiles")
def profiles():
    return {"profiles": [p.to_dict() for p in PROFILES.values()]}


@app.post("/api/fonts/add-icons")
def fonts_add_icons():
    from ..core.icons import ICON_FONT, add_icon_font
    add_icon_font(_need_project().fonts)
    return {"ok": True, "font": ICON_FONT}


@app.get("/api/icons")
def icons():
    from ..core.icons import ICON_CODES, ICON_FONT, ICONS
    return {"font": ICON_FONT, "icons": [{"name": n, "code": ICON_CODES[n], "rows": r} for n, r in ICONS.items()]}


@app.get("/api/devices")
def devices():
    return {"devices": HUB.list()}


@app.websocket("/ws/device/{dev_id}")
async def ws_device(ws: WebSocket, dev_id: str):
    """ESP gateway: see docs/esp-protokol.md"""
    import json as _json
    await ws.accept()
    await HUB.attach(dev_id, ws)
    try:
        while True:
            try:
                HUB.on_message(dev_id, _json.loads(await ws.receive_text()))
            except ValueError:
                pass
    except WebSocketDisconnect:
        HUB.detach(dev_id, ws)


# ------------------------------------------------------------------ static
@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))


from ..extras.lcd.server import add_routes as _lcd_routes   # noqa: E402  add-on: LCD page at /lcd/
_lcd_routes(app, "/lcd")
app.mount("/static", StaticFiles(directory=WEB), name="static")


def serve(host="127.0.0.1", port=8090, workdir=".", open_browser=True):
    import uvicorn
    global S
    S = State(workdir)
    sim_api.init(workdir)
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{host}:{port}/")).start()
    print(f"BUSE Studio běží na http://{host}:{port}/  (pracovní složka {S.workdir})")
    uvicorn.run(app, host=host, port=port, log_level="warning")
