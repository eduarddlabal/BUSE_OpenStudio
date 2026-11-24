"""Multi-panel ProjectBundle APIs (/api/bundle/*)."""
from __future__ import annotations

import os
import shutil

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ..core.bundle import PanelEntry, ProjectBundle, build_demo_bundle, build_empty_bundle
from ..core.project import Project
from ..core.render import render_frames, text_bitmap
from . import sim_api
from .state import (
    _INSPECT_CACHE, S, _abs, _activate_bundle, _apply_meta, _display_name,
    _load_fontset, _need_project, _project_json, _rel_or_abs,
    _safe_workdir_path, _slugify, _unique_project_relpath,
)

router = APIRouter()


class BundleNewReq(BaseModel):
    template: str = "demo_bus"      # demo_bus | empty_bus_3 | empty_bus_4 | empty_gbuse1 | empty_gbuse0
    name: str = ""
    description: str = ""
    path: str = ""
    save_now: bool = True


@router.post("/api/bundle/new")
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


@router.post("/api/bundle/select-panel")
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


@router.post("/api/bundle/add-panel")
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


@router.post("/api/bundle/remove-panel")
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


@router.post("/api/bundle/update-meta")
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


@router.post("/api/bundle/propagate")
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


@router.post("/api/bundle/preview-all")
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


@router.get("/api/bundle/export-zip")
def bundle_export_zip():
    if S.bundle is None:
        raise HTTPException(400, "hromadný ZIP export vyžaduje vícepanelový projekt")
    data = S.bundle.export_zip_bytes()
    slug = _slugify(str(S.bundle.meta.get("name") or "projekt"))
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'})


@router.post("/api/bundle/apply-sim")
def bundle_apply_sim():
    if S.bundle is None:
        raise HTTPException(400, "aktivní projekt není vícepanelový")
    sim = S.bundle.simulation or {}
    from ..sim.line import Line
    manifest = os.path.relpath(S.bundle.manifest_path, sim_api.SIM.workdir).replace("\\", "/")
    sim_api._open_project(manifest, apply_vehicle=True)      # database, vehicle, system, routes together
    if isinstance(sim.get("default_line"), str) and not sim["default_line"].startswith("template:"):
        lp = os.path.join(S.bundle.base_dir, sim["default_line"])
        if os.path.exists(lp):
            with open(lp, encoding="utf-8") as fh:
                sim_api.SIM.line = Line.from_dict(yaml.safe_load(fh) or {})
                sim_api.SIM.route = sim_api.SIM.line.route()
    sim_api._rebuild()
    return {"ok": True, "vehicle": sim_api.SIM.vehicle_cfg.get("name")}
