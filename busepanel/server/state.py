"""Shared server state and helpers for FastAPI route modules."""
from __future__ import annotations

import datetime as dt
import os
import re
import threading
import traceback
import unicodedata
import uuid

import yaml
from fastapi import HTTPException

from ..core import image as I
from ..core.bundle import ProjectBundle, load_builtin_fonts
from ..core.fonts import FontSet
from ..core.project import Project
from ..loader.device import Panel, default_port
from ..outputs.serial_out import port_lock

WEB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
_SKIP_DIRS = {"__pycache__", "node_modules", "venv", "build", "dist"}
_INSPECT_CACHE: dict[str, tuple[int, int, dict | None]] = {}


class State:
    def __init__(self, workdir: str = "."):
        self.workdir = os.path.abspath(workdir)
        self.project: Project | None = None
        self.path: str | None = None
        self.bundle: ProjectBundle | None = None
        self.active_panel: str | None = None
        self.port = default_port()
        self.address = 10
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()          # one serial operation at a time
        self.live_runner = None
        self.live_thread = None
        self.live_log: list[str] = []

    def reset(self, workdir: str = "."):
        """Reset state in-place so all router modules keep referencing the same `S` instance."""
        self.workdir = os.path.abspath(workdir)
        self.project = None
        self.path = None
        self.bundle = None
        self.active_panel = None
        self.port = default_port()
        self.address = 10
        self.jobs.clear()
        if self.live_runner:
            try:
                self.live_runner.stop()
            except Exception:          # noqa: BLE001
                pass
        self.live_runner = None
        self.live_thread = None
        self.live_log.clear()
        _INSPECT_CACHE.clear()


S: State = State(".")


def _need_project() -> Project:
    if S.project is None:
        raise HTTPException(400, "není otevřený žádný projekt")
    return S.project


def _abs(path: str) -> str:
    return os.path.abspath(os.path.join(S.workdir, path))


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


def _is_project_yaml(path) -> bool:
    if ProjectBundle.is_bundle_yaml(path):
        return True
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return any(line.startswith("fonts:") for line in fh)
    except OSError:
        return False


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
