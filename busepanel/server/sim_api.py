"""Simulation module API (/api/sim/*, /ws/sim) – e.g. for OMSI: manual control now, plugin later."""
from __future__ import annotations

import copy
import json
import os
import re
import time

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ..core import appsettings
from ..core.bundle import ProjectBundle
from ..core.project import Project, load_yaml, dump_yaml
from ..sim.engine import (DEFAULT_SETTINGS, DEFAULT_SEQUENCES, LEGACY_DIRS, SECTION_TITLES, SECTIONS, SYSTEMS_DIR,
                          SimEngine, load_formats, merge_settings, resolve_settings, system_settings, vehicle_system)
from ..sim.line import Line
from ..sim.route import Route
from ..sim.universal_db import build_universal, build_universal_bundle
from ..sim.vehicle import Vehicle

router = APIRouter()

EXAMPLE_ROUTE = {
    "line": "7", "destination": "Sídliště Dukla",
    "stops": [{"name": "Hlavní nádraží", "zone": "P", "icons": ["vlak", "bus"]},
              {"name": "Masarykovo náměstí", "zone": "P"},
              {"name": "Náměstí Republiky", "zone": "P", "icons": ["prestup"]},
              {"name": "U Kostela", "zone": "P", "request": True},
              {"name": "Sídliště Dukla", "zone": "1"}],
    "messages": {"odklon": "Spoj jede odklonem přes ul. Pražskou",
                 "vyluka": "Výluka - zastávka U Kostela se neobsluhuje"},
}
EXAMPLE_VEHICLE = {
    "name": "Testovací autobus",
    "panels": [
        {"name": "Čelní BS210", "type": "BS210", "variant": "v1", "role": "front", "output": "virtual"},
        {"name": "Boční BS210", "type": "BS210", "variant": "v2", "stop_field": "bottom", "role": "side", "output": "virtual"},
        {"name": "Vnitřní BS120", "type": "BS120", "profile": "BS120", "role": "interior", "output": "virtual"},
    ],
}


class Sim:
    def __init__(self):
        self.workdir = "."
        self.line = Line.from_dict(EXAMPLE_ROUTE)      # line = directions + courses (flat route = 1 direction)
        self.sel = {}                                  # selected trip: {course, index} or {direction}
        self.route = self.line.route()                 # the trip being simulated
        self.route_path = None
        self.vehicle_cfg = json.loads(json.dumps(EXAMPLE_VEHICLE))
        self.vehicle_path = None
        self.db: Project | None = None
        self.db_path = None
        self.engine: SimEngine | None = None
        self.vehicle: Vehicle | None = None
        self.log: list[str] = []

    def _log(self, m):
        self.log = (self.log + [time.strftime("%H:%M:%S ") + m])[-100:]

    def path(self, p):
        return os.path.abspath(os.path.join(self.workdir, p))


SIM = Sim()


_DEFAULT_DB_CANDIDATES = (
    "projekty/univerzalni/projekt.yaml",
    "databaze/univerzalni/projekt.yaml",
)
DEFAULT_DB = "projekty/univerzalni/projekt.yaml"


def _load_sim_project(abs_path: str) -> tuple[Project, ProjectBundle | None]:
    low = abs_path.lower()
    if low.endswith((".yaml", ".yml")):
        if ProjectBundle.is_bundle_yaml(abs_path):
            bundle = ProjectBundle.load(abs_path)
            pe = next((p for p in bundle.panels if p.format == "gbuse1" and p.project is not None), None)
            if pe is None:
                pe = next((p for p in bundle.panels if p.project is not None), None)
            if pe is None or pe.project is None:
                raise ValueError(f"projekt {abs_path} neobsahuje žádnou databázi panelu")
            return pe.project, bundle
        return Project.load(abs_path), None
    if low.endswith(".bin"):
        return Project.from_bin(abs_path), None
    return Project.from_hex(abs_path), None


def _is_sim_project_yaml(full: str) -> bool:
    if ProjectBundle.is_bundle_yaml(full):
        return True
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            head = "".join(fh.readline() for _ in range(30))
        return "fonts:" in head and ("panel:" in head or "config:" in head or "lines:" in head)
    except OSError:
        return False


def _scan_project_entries() -> list[dict]:
    """Scan `projekty/`, legacy `databaze/`, and root-level project YAMLs."""
    seen: set[str] = set()
    entries: list[dict] = []

    def add_yaml(full: str, rel: str):
        seen.add(rel)
        if ProjectBundle.is_bundle_yaml(full):
            try:
                b = ProjectBundle.load(full)
                entries.append({
                    "path": rel,
                    "kind": "bundle",
                    "name": str(b.meta.get("name") or rel),
                    "panels": len(b.panels),
                    "formats": sorted({pe.format for pe in b.panels}),
                })
            except Exception:      # noqa: BLE001
                entries.append({"path": rel, "kind": "bundle", "name": rel, "panels": 0, "formats": []})
        else:
            entries.append({"path": rel, "kind": "yaml", "name": rel, "panels": 1, "formats": []})

    for folder in ("projekty", "databaze"):
        base = SIM.path(folder)
        if not os.path.isdir(base):
            continue
        for root, dirs, fs in os.walk(base):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            if "projekt.yaml" in fs and ProjectBundle.is_bundle_yaml(os.path.join(root, "projekt.yaml")):
                if "panely" in dirs:
                    dirs.remove("panely")
            for f in sorted(fs):
                full = os.path.join(root, f)
                low = f.lower()
                rel = os.path.relpath(full, SIM.workdir).replace("\\", "/")
                if rel in seen:
                    continue
                if low.endswith((".yaml", ".yml")):
                    if f in ("fonts.yaml", "zive.yaml", "vozidlo.yaml", "nastaveni.yaml") or not _is_sim_project_yaml(full):
                        continue
                    add_yaml(full, rel)
                elif low.endswith((".hex", ".bin")):
                    seen.add(rel)
                    entries.append({"path": rel, "kind": low.rsplit(".", 1)[-1], "name": rel, "panels": 1, "formats": []})

    if os.path.isdir(SIM.workdir):
        for f in sorted(os.listdir(SIM.workdir)):
            full = os.path.join(SIM.workdir, f)
            low = f.lower()
            if not os.path.isfile(full) or not low.endswith((".yaml", ".yml")):
                continue
            if f in ("fonts.yaml", "zive.yaml", "vozidlo.yaml", "nastaveni.yaml") or f in seen:
                continue
            if _is_sim_project_yaml(full):
                add_yaml(full, f)

    entries.sort(key=lambda e: (0 if e["kind"] == "bundle" else 1, e["path"]))
    return entries


def _find_default_db() -> str | None:
    for cand in _DEFAULT_DB_CANDIDATES:
        if os.path.exists(SIM.path(cand)):
            return cand
    for item in _scan_project_entries():
        if item["kind"] in ("bundle", "yaml"):
            return item["path"]
    return None


def init(workdir: str):
    SIM.workdir = os.path.abspath(workdir)
    appsettings.load(SIM.workdir)
    if SIM.vehicle:
        SIM.vehicle.stop()
    SIM.db, SIM.db_path = None, None
    SIM.engine, SIM.vehicle = None, None
    SIM.line = Line.from_dict(EXAMPLE_ROUTE)
    SIM.sel = {}
    SIM.route = SIM.line.route()
    SIM.route_path = None
    SIM.vehicle_cfg = json.loads(json.dumps(EXAMPLE_VEHICLE))
    SIM.vehicle_path = None

    route_items = _scan_route_entries()
    if route_items:
        first_route = route_items[0]["path"]
        try:
            line = Line.load(SIM.path(first_route))
            SIM.line = line
            _select(_default_sel(line))
            SIM.route_path = first_route
        except Exception as e:      # noqa: BLE001
            SIM._log(f"linku {first_route} nelze načíst: {e}")

    vehs = _list("vozidla")
    if vehs:
        first_veh = vehs[0]
        try:
            SIM.vehicle_cfg = _clean_vehicle(load_yaml(SIM.path(first_veh)))
            SIM.vehicle_path = first_veh
        except Exception as e:      # noqa: BLE001
            SIM._log(f"vozidlo {first_veh} nelze načíst: {e}")

    cand = _find_default_db()
    if cand and os.path.exists(SIM.path(cand)):
        try:
            db, bundle = _load_sim_project(SIM.path(cand))
            SIM.db, SIM.db_path = db, cand
            if bundle is not None and SIM.vehicle_path is None:
                vcfg = bundle.resolved_vehicle_config(SIM.workdir)
                if vcfg:
                    SIM.vehicle_cfg = _clean_vehicle(vcfg)
            _rebuild(keep_state=False)
        except Exception as e:      # noqa: BLE001
            SIM._log(f"univerzální databázi nelze načíst: {e}")


def _need_db() -> Project:
    if SIM.db is None:
        raise HTTPException(400, "Není otevřená databáze panelu – sestavte univerzální databázi nebo otevřete projekt.")
    return SIM.db


def _list(folder, exts=(".yaml", ".yml")):
    base = SIM.path(folder)
    if not os.path.isdir(base):
        return []
    out = []
    for root, _d, fs in os.walk(base):
        for f in fs:
            if f.lower().endswith(exts) and f != "fonts.yaml":
                out.append(os.path.relpath(os.path.join(root, f), SIM.workdir).replace("\\", "/"))
    return sorted(out)


def _plain_g1(val) -> str:
    if isinstance(val, dict):
        return str(val.get("text") or "").strip()
    return re.sub(r"\{[^{}]*\}", "", str(val or "")).strip()


def _route_sort_key(item: dict) -> tuple:
    ln = str(item.get("line") or "").strip()
    m = re.match(r"^(\d+)(.*)$", ln)
    if m:
        return (0, int(m.group(1)), m.group(2), item.get("group") or "", item["path"])
    return (1, 0, ln.lower(), item.get("group") or "", item["path"])


def _scan_route_entries() -> list[dict]:
    """Structured metadata for every line/route YAML in `trasy/`, sorted naturally by line number."""
    items: list[dict] = []
    for rel in _list("trasy"):
        parts = rel.split("/")
        group = "/".join(parts[1:-1]) if len(parts) > 2 else ""
        try:
            ln = Line.load(SIM.path(rel))
            sm = ln.summary()
            dirs = sm.get("directions") or []
            courses = sm.get("courses") or {}
            d0 = dirs[0] if dirs else {}
            if len(dirs) >= 2 and dirs[0].get("destination") != dirs[1].get("destination"):
                summary = f"{dirs[0].get('first') or dirs[0].get('destination', '')} ↔ {dirs[0].get('destination', '')}"
                if not dirs[0].get("first"):
                    summary = f"{dirs[0].get('destination', '')} ↔ {dirs[1].get('destination', '')}"
            else:
                first, dest = d0.get("first") or "", d0.get("destination") or ""
                summary = dest if (not first or first == dest) else f"{first} → {dest}"
            items.append({
                "path": rel,
                "group": group,
                "line": ln.line,
                "directions": len(dirs),
                "courses": len(courses),
                "trips": sum(len(v) for v in courses.values()),
                "stops": max((d.get("stops", 0) for d in dirs), default=0),
                "summary": summary,
            })
        except Exception:      # noqa: BLE001
            items.append({"path": rel, "group": group, "line": "", "directions": 0, "courses": 0,
                          "trips": 0, "stops": 0, "summary": os.path.basename(rel)})
    items.sort(key=_route_sort_key)
    return items


def _collect_db_codes() -> dict[str, list[dict]]:
    """Collect destination & stop codes from active SIM.db and exterior panel databases for UI hints."""
    dests: dict[str, str] = {}
    stops: dict[str, str] = {}
    lines: dict[str, str] = {}

    if SIM.db is not None:
        for k, v in (SIM.db.data.get("lines") or {}).items():
            t = _plain_g1(v)
            if t:
                lines.setdefault(str(k), t)
        for sec in ("destinations", "destinations4"):
            for k, v in (SIM.db.data.get(sec) or {}).items():
                t = _plain_g1(v)
                if t:
                    dests.setdefault(str(k), t)
        for k, v in (SIM.db.data.get("stops") or {}).items():
            t = _plain_g1(v)
            if t:
                stops.setdefault(str(k), t)

    if SIM.vehicle is not None:
        from ..core.fonts import CodeTable, default_codetable
        from ..gbuse0 import mnemo as M0
        for slot in SIM.vehicle.panels:
            db0 = getattr(slot, "db", None)
            if db0 is None or not hasattr(db0, "records"):
                continue
            ct = CodeTable(db0.codetable) if getattr(db0, "codetable", None) else default_codetable()
            for sec, target in (("LIN", lines), ("CIL", dests), ("CIL4", dests), ("DRU", stops)):
                for k, raw in (db0.records.get(sec) or {}).items():
                    t = " ".join(re.findall(r"'([^']*)'", M0.to_mnemo(raw, ct))).strip()
                    if t:
                        target.setdefault(str(k), t)

    to_list = lambda m: [{"code": k, "text": v} for k, v in sorted(m.items())]
    return {"destinations": to_list(dests), "stops": to_list(stops), "lines": to_list(lines)}


# ------------------------------------------------------------------ files
@router.get("/api/sim/files")
def files():
    projects = _scan_project_entries()
    route_items = _scan_route_entries()
    return {"routes": [r["path"] for r in route_items], "route_items": route_items,
            "vehicles": _list("vozidla"),
            "databases": [p["path"] for p in projects],
            "projects": projects,
            "db_codes": _collect_db_codes(),
            "route_path": SIM.route_path, "vehicle_path": SIM.vehicle_path, "db_path": SIM.db_path}


class PathReq(BaseModel):
    path: str


class RouteSave(BaseModel):
    path: str
    route: dict


def _route_info():
    return {"path": SIM.route_path, "route": SIM.route.to_dict(), "line": SIM.line.summary(), "selection": SIM.sel,
            "data": SIM.line.to_dict(full=True)}


@router.get("/api/sim/route")
def route_get():
    return _route_info()


def _default_sel(line: Line) -> dict:
    if line.courses:
        return {"course": next(iter(line.courses)), "index": 0}
    return {"direction": next(iter(line.directions), "1")} if not line.flat else {}


def _select(sel: dict):
    route = SIM.line.route(**sel)
    SIM.sel, SIM.route = sel, route


@router.post("/api/sim/route/load")
def route_load(r: PathReq):
    try:
        line = Line.load(SIM.path(r.path))
    except ValueError as e:
        raise HTTPException(400, str(e))
    SIM.line = line
    _select(_default_sel(line))
    SIM.route_path = r.path
    _rebuild(keep_state=False)
    return route_get()


def _put(route: dict):
    """Route edited in the UI -> back into the line (direction); a shortened trip only for this run."""
    rt = Route.from_dict(route)
    try:
        SIM.line.put_route(rt, **SIM.sel)
        SIM.route = SIM.line.route(**SIM.sel)
    except ValueError:
        if not SIM.sel.get("course"):
            raise
        SIM.route = rt


@router.post("/api/sim/route/save")
def route_save(r: RouteSave):
    try:
        _put(r.route)
        if SIM.sel.get("course") and not SIM.line.is_full(SIM.sel["course"], SIM.sel.get("index", 0)):
            raise ValueError("zkrácený spoj nelze uložit z editoru – upravte směr (celý spoj) nebo soubor linky")
    except ValueError as e:
        raise HTTPException(400, str(e))
    os.makedirs(os.path.dirname(SIM.path(r.path)), exist_ok=True)
    SIM.line.save(SIM.path(r.path))
    SIM.route_path = r.path
    _rebuild()
    return route_get()


@router.post("/api/sim/route/update")
def route_update(r: RouteSave):
    try:
        _put(r.route)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _rebuild()
    return route_get()


class LineReq(BaseModel):
    line: dict
    path: str | None = None
    selection: dict | None = None


def _put_line(r: LineReq):
    """Whole line edited in the UI (directions, courses, messages) -> SIM.line + the simulated trip."""
    try:
        line = Line.from_dict(r.line)
    except (ValueError, TypeError, KeyError, AttributeError) as e:
        raise HTTPException(400, str(e))
    if not line.directions:
        raise HTTPException(400, "linka musí mít aspoň jeden směr")
    if SIM.line.flat and len(line.directions) == 1 and not line.courses:
        line.flat = True                               # an old flat file stays flat
    SIM.line = line
    sel = r.selection if r.selection is not None else SIM.sel
    try:
        _select(sel or _default_sel(line))
    except ValueError:
        _select(_default_sel(line))


@router.get("/api/sim/line")
def line_get():
    return _route_info()


@router.post("/api/sim/line/update")
def line_update(r: LineReq):
    _put_line(r)
    _rebuild()
    return {**_route_info(), "state": state()}


class NewLineReq(BaseModel):
    line: str = "1"
    destination: str = "Cíl A"
    back_destination: str = "Cíl B"


@router.post("/api/sim/line/new")
def line_new(r: NewLineReq):
    ln = Line.from_dict({
        "line": r.line.strip() or "1",
        "directions": {
            "A": {"destination": r.destination, "stops": [{"name": r.back_destination}, {"name": r.destination}]},
            "B": {"destination": r.back_destination, "stops": [{"name": r.destination}, {"name": r.back_destination}]},
        },
        "courses": {"1": [{"direction": "A"}, {"direction": "B"}]},
    })
    SIM.line = ln
    SIM.sel = {"course": "1", "index": 0}
    SIM.route = ln.route(**SIM.sel)
    SIM.route_path = None
    _rebuild(keep_state=False)
    return {**_route_info(), "state": state()}


@router.post("/api/sim/line/save")
def line_save(r: LineReq):
    _put_line(r)
    if not r.path:
        raise HTTPException(400, "chybí cesta souboru")
    os.makedirs(os.path.dirname(SIM.path(r.path)) or ".", exist_ok=True)
    SIM.line.save(SIM.path(r.path))
    SIM.route_path = r.path
    _rebuild()
    return {**_route_info(), "state": state()}


class TripReq(BaseModel):
    course: str | None = None
    index: int = 0
    direction: str | None = None


@router.post("/api/sim/trip")
def trip_select(r: TripReq):
    """Select the trip to simulate: course + index (kurz, spoj) or a whole direction."""
    sel = {"course": r.course, "index": r.index} if r.course else ({"direction": r.direction} if r.direction else {})
    try:
        _select(sel)
    except ValueError as e:
        raise HTTPException(400, str(e))
    SIM._log("spoj " + (f"kurz {r.course} / {r.index + 1}" if r.course else f"směr {r.direction or '-'}"))
    _rebuild(keep_state=False)
    return {**_route_info(), "state": state()}


class CsvReq(BaseModel):
    csv: str
    line: str = ""
    destination: str = ""


@router.post("/api/sim/route/import-csv")
def route_csv(r: CsvReq):
    """CSV -> stops (the UI puts them into the edited direction)."""
    return {"route": Route.from_csv(r.csv, r.line, r.destination).to_dict()}


# ------------------------------------------------------------------ vehicle
class VehicleSave(BaseModel):
    path: str | None = None
    config: dict


@router.get("/api/sim/defaults")
def defaults():
    from ..peripherals import describe
    return {"sequences": DEFAULT_SEQUENCES, "peripherals": describe(), "sections": SECTION_TITLES}


def _formats() -> dict:
    """Display systems of the working folder (systemy/, older folder formaty/ too)."""
    return load_formats(SIM.path(SYSTEMS_DIR), *(SIM.path(d) for d in LEGACY_DIRS))


def _clean_vehicle(cfg: dict) -> dict:
    """A vehicle file = its panels (+ the display system it uses by default); old text settings are dropped."""
    cfg = dict(cfg or {})
    name = vehicle_system(cfg)
    cfg.pop("settings", None)
    if name:
        cfg["system"] = name
    else:
        cfg.pop("system", None)
    return cfg


@router.get("/api/sim/vehicle")
def vehicle_get():
    name = vehicle_system(SIM.vehicle_cfg)
    fm = _formats()
    return {"path": SIM.vehicle_path, "config": SIM.vehicle_cfg, "system": name,
            "system_title": fm[name]["title"] if name in fm else "", "system_missing": bool(name) and name not in fm}


@router.post("/api/sim/vehicle/load")
def vehicle_load(r: PathReq):
    SIM.vehicle_cfg = _clean_vehicle(load_yaml(SIM.path(r.path)))
    SIM.vehicle_path = r.path
    _rebuild()
    return vehicle_get()


@router.post("/api/sim/vehicle/save")
def vehicle_save(r: VehicleSave):
    cfg = _clean_vehicle(r.config)
    SIM.vehicle_cfg = cfg
    if r.path:
        os.makedirs(os.path.dirname(SIM.path(r.path)), exist_ok=True)
        dump_yaml(cfg, SIM.path(r.path))
        SIM.vehicle_path = r.path
    _rebuild()
    return vehicle_get()


class SystemReq(BaseModel):
    name: str = ""


@router.post("/api/sim/vehicle/system")
def vehicle_use_system(r: SystemReq):
    """Display system for the running simulation (the vehicle file is not changed)."""
    SIM.vehicle_cfg = _clean_vehicle({**SIM.vehicle_cfg, "system": r.name})
    _rebuild()
    return vehicle_get()


# ------------------------------------------------------------------ display systems (systémy zobrazení)
_NAME = re.compile(r"^[\w\- ]+(/[\w\- ]+)*$")


def _format_path(name: str) -> str:
    name = (name or "").strip().strip("/")
    if not _NAME.match(name) or ".." in name:
        raise HTTPException(400, "neplatný název systému (písmena, číslice, -, _, mezera; podsložky přes /)")
    f = _formats().get(name)
    if f and f.get("path"):
        return f["path"]                                   # existing file (also in the older folder)
    return SIM.path(os.path.join(SYSTEMS_DIR, *name.split("/")) + ".yaml")


@router.get("/api/settings")
def settings_get():
    return appsettings.get()


@router.post("/api/settings")
def settings_set(values: dict):
    out = appsettings.update(SIM.workdir, values)
    _rebuild()                     # panels show the texts again with the new setting
    return out


@router.get("/api/sim/formats")
def formats_list():
    out = []
    for name, f in _formats().items():
        out.append({"name": name, "title": f["title"], "group": name.rsplit("/", 1)[0] if "/" in name else "",
                    "sections": [k for k in SECTIONS if f.get(k)]})
    return {"formats": out, "default_sequences": DEFAULT_SEQUENCES, "sections": SECTION_TITLES,
            "vehicle_format": vehicle_system(SIM.vehicle_cfg)}


@router.get("/api/sim/format")
def format_get(name: str):
    f = _formats().get(name)
    if f is None:
        raise HTTPException(404, f"systém zobrazení {name} neexistuje")
    full = merge_settings({"interior": copy.deepcopy(f["interior"]), "exterior": copy.deepcopy(f["exterior"])})
    full["interior"].pop("format", None)
    return {"name": name, "title": f["title"], "interior": full["interior"],
            "interior_2": copy.deepcopy(f.get("interior_2") or {}), "exterior": full.get("exterior") or {}}


class FormatSave(BaseModel):
    name: str
    title: str = ""
    interior: dict
    interior_2: dict = {}
    exterior: dict = {}


@router.post("/api/sim/format/save")
def format_save(r: FormatSave):
    path = _format_path(r.name)
    data = {"title": r.title or r.name.rsplit("/", 1)[-1],
            "interior": {k: v for k, v in r.interior.items() if k != "format"}}
    if r.interior_2:
        data["interior_2"] = r.interior_2
    ext = {k: v for k, v in (r.exterior or {}).items() if k not in ("font", "spacing", "format")}
    if ext:
        data["exterior"] = ext
    os.makedirs(os.path.dirname(path), exist_ok=True)
    dump_yaml(data, path)
    _rebuild()
    return {**formats_list(), "saved": r.name.strip().strip("/")}


class NameReq(BaseModel):
    name: str
    title: str = ""


@router.post("/api/sim/format/delete")
def format_delete(r: NameReq):
    if r.name not in _formats():
        raise HTTPException(404, f"systém zobrazení {r.name} neexistuje")
    if vehicle_system(SIM.vehicle_cfg) == r.name:
        raise HTTPException(400, "systém používá načtené vozidlo – nejdřív mu vyberte jiný")
    os.remove(_format_path(r.name))
    _rebuild()
    return formats_list()


class PreviewReq(BaseModel):
    interior: dict
    interior_2: dict = {}
    exterior: dict = {}
    panel: dict = {}
    phase: str = "driving"
    pos: int = 1
    message: bool = False
    seconds: float = 40


@router.post("/api/sim/format/preview")
def format_preview(r: PreviewReq):
    """What one panel shows with an (unsaved) format, as pages with their times – simulated on the
    loaded line without touching the running simulation."""
    db = _need_db()
    t = [time.time()]
    settings = {"interior": {k: v for k, v in r.interior.items() if k != "format"}, "exterior": r.exterior or {}}
    if r.interior_2:
        settings["interior_2"] = r.interior_2
    eng = SimEngine(SIM.route, settings, clock=lambda: t[0])
    n = len(SIM.route.stops)
    if r.phase in ("at_stop", "driving", "ended", "idle"):
        eng.phase = r.phase
    eng.pos = max(0, min(r.pos, n - 1)) if n else 0
    if r.phase == "ended":
        eng.pos = max(0, n - 1)
    if r.message:
        msgs = SIM.route.messages or {}
        eng.message_key = next(iter(msgs), None)
        eng.message = msgs[eng.message_key] if eng.message_key else "Text mimořádné informace"
    eng.since = t[0]
    panel = {"name": "náhled", "role": "interior", "type": "BS120", **(r.panel or {}), "output": "virtual"}
    veh = Vehicle.from_config({"panels": [panel]}, eng, db, base_dir=SIM.workdir)
    slot = veh.panels[0]
    step, pages, last, t0 = 0.1, [], None, t[0]
    for k in range(int(min(max(r.seconds, 5), 90) / step)):
        t[0] = t0 + k * step
        veh.tick(now=t[0], virtual_only=True)
        fr = slot.frame(t[0])
        key = json.dumps({x: y for x, y in (fr or {}).items() if x not in ("log", "changed", "mode_title")}, sort_keys=True)
        if key != last:
            pages.append({"start": round(k * step, 2), "frame": fr, "_key": key})
            last = key
    total = round(min(max(r.seconds, 5), 90), 2)
    keys = [p["_key"] for p in pages]
    for j in range(1, len(keys)):                      # one cycle: the first pages come again from j on
        m = min(2, len(keys) - j)                       # (not all of them – the clock page may change)
        if m >= 2 and keys[j:j + m] == keys[:m] or (m == 1 and j > 1 and keys[j] == keys[0]):
            total, pages = pages[j]["start"], pages[:j]
            break
    for p in pages:
        p.pop("_key", None)
    for i, p in enumerate(pages):
        p["seconds"] = round((pages[i + 1]["start"] if i + 1 < len(pages) else total) - p["start"], 2)
    return {"pages": pages, "total": total, "stop": SIM.route.stops[eng.pos].name if n else "",
            "line": SIM.route.line, "phase": eng.phase}


# ------------------------------------------------------------------ database
class OpenDbReq(BaseModel):
    path: str
    apply_vehicle: bool = False


@router.post("/api/sim/db/open")
def db_open(r: OpenDbReq):
    p = SIM.path(r.path)
    db, bundle = _load_sim_project(p)
    SIM.db = db
    SIM.db_path = r.path
    if bundle is not None and r.apply_vehicle:
        vcfg = bundle.resolved_vehicle_config(SIM.workdir)
        if vcfg:
            SIM.vehicle_cfg = _clean_vehicle(vcfg)
            SIM.vehicle_path = None
    _rebuild()
    return {
        "path": r.path,
        "kind": "bundle" if bundle is not None else "single",
        "fonts": [f.id for f in SIM.db.fonts.fonts],
        "panels": [pe.info() for pe in bundle.panels] if bundle is not None else None,
        "vehicle": SIM.vehicle_cfg.get("name"),
    }


def _interior_panel_params() -> tuple[str, int | None, int]:
    """Derive (panel_type, columns, ibis_address) from active SIM.vehicle_cfg."""
    from ..peripherals import TYPES, peripheral_type
    for p in SIM.vehicle_cfg.get("panels") or []:
        ptype = peripheral_type(p)
        if TYPES.get(ptype, {}).get("class") == "interior":
            prof = str(p.get("profile") or ptype or "BS120")
            cols = int(p["columns"]) if p.get("columns") else None
            addr = int(p.get("address", p.get("ibis_address", 10)))
            return prof, cols, addr
    return "BS120", None, 10


class BuildReq(BaseModel):
    base: str = "gbuse"
    out: str = DEFAULT_DB
    version: str = "UNIV"
    name: str = ""
    extra_lines: list[str] = []
    bundle: bool = False
    apply_vehicle: bool = True


@router.post("/api/sim/db/build")
def db_build(r: BuildReq):
    font = "gbuse"
    if r.base in ("", "gbuse", "builtin", "vestavěný"):
        base = None                                   # font set shipped with BUSE Studio
        font = "builtin" if r.base in ("builtin", "vestavěný") else "gbuse"
    else:
        base, _ = _load_sim_project(SIM.path(r.base))
    extra = sorted({Route.load(SIM.path(f)).line for f in _list("trasy")} | {SIM.route.line} | set(r.extra_lines))

    if r.bundle:
        out_path = r.out or DEFAULT_DB
        if not os.path.basename(out_path).endswith((".yaml", ".yml")):
            out_path = os.path.join(out_path, "projekt.yaml").replace("\\", "/")
        bname = r.name.strip() or (SIM.vehicle_cfg.get("name") or "Univerzální autobus (simulace)")
        bundle = build_universal_bundle(
            base,
            version=r.version,
            extra_lines=extra,
            font=font,
            name=bname,
            vehicle_cfg=SIM.vehicle_cfg,
        )
        bundle.save(SIM.path(out_path))
        db, loaded = _load_sim_project(SIM.path(out_path))
        SIM.db, SIM.db_path = db, out_path
        if r.apply_vehicle and loaded is not None:
            vcfg = loaded.resolved_vehicle_config(SIM.workdir)
            if vcfg:
                SIM.vehicle_cfg = _clean_vehicle(vcfg)
                SIM.vehicle_path = None
        _rebuild()
        return {
            "path": out_path,
            "kind": "bundle",
            "fonts": [f.id for f in SIM.db.fonts.fonts],
            "line_map": SIM.db.data.get("line_map"),
            "panels": [pe.info() for pe in loaded.panels] if loaded is not None else [],
        }

    ptype, cols, addr = _interior_panel_params()
    db = build_universal(
        base,
        version=r.version,
        extra_lines=extra,
        panel_type=ptype,
        ibis_address=addr,
        font=font,
        columns=cols,
        name=r.name.strip() or "Univerzální databáze (gBUSE1)",
    )
    db.save(SIM.path(r.out))
    SIM.db, SIM.db_path = Project.load(SIM.path(r.out)), r.out
    _rebuild()
    return {
        "path": r.out,
        "kind": "single",
        "fonts": [f.id for f in SIM.db.fonts.fonts],
        "line_map": SIM.db.data.get("line_map"),
    }


# ------------------------------------------------------------------ run
def _rebuild(keep_state: bool = True):
    """(Re)create engine + vehicle after route / vehicle / database changes."""
    if SIM.db is None:
        return
    old = SIM.engine
    running = bool(SIM.vehicle and SIM.vehicle.running)
    if SIM.vehicle:
        SIM.vehicle.stop()
    SIM.engine = SimEngine(SIM.route, system_settings(vehicle_system(SIM.vehicle_cfg), _formats()))
    if keep_state and old is not None:
        SIM.engine.phase, SIM.engine.pos = old.phase, min(old.pos, max(0, len(SIM.route.stops) - 1))
        SIM.engine.message, SIM.engine.message_key = old.message, old.message_key
    cfg = dict(SIM.vehicle_cfg)
    cfg["panels"] = [p for p in cfg.get("panels") or [] if p.get("enabled", True)]
    SIM.vehicle = Vehicle.from_config(cfg, SIM.engine, SIM.db, log=SIM._log, base_dir=SIM.workdir)
    _sync_lcd()
    if running:
        SIM.vehicle.start()
    else:
        SIM.vehicle.tick(virtual_only=True)


def _sync_lcd():
    """Add-on LCD (busepanel/extras/lcd): an HTTP server for every LCD panel of the vehicle."""
    from ..extras.lcd.server import LCD
    wanted = {}
    for s in (SIM.vehicle.panels if SIM.vehicle else []):
        if s.output.kind == "lcd":
            wanted[s.output.port] = s.output.host
    LCD.sync(wanted)


@router.post("/api/sim/start")
def start():
    _need_db()
    if SIM.vehicle is None:
        _rebuild()
    if SIM.engine.phase == "idle":
        SIM.engine.event("start")
    SIM.vehicle.resend()
    SIM.vehicle.start()
    return state()


@router.post("/api/sim/stop")
def stop():
    if SIM.vehicle:
        SIM.vehicle.stop()
    return state()


class EventReq(BaseModel):
    type: str
    key: str | None = None
    text: str | None = None
    index: int | None = None
    phase: str | None = None


def _event(e: dict):
    _need_db()
    if SIM.engine is None:
        _rebuild()
    if e["type"] == "next_trip":            # next trip of the course (BS100: after the terminal)
        c = SIM.sel.get("course")
        nxt = SIM.line.next_trip(c, SIM.sel.get("index", 0)) if c else None
        if nxt is None:
            raise ValueError("kurz nemá další spoj" if c else "není vybrán kurz")
        _select({"course": c, "index": nxt})
        SIM._log(f"událost next_trip → kurz {c} / spoj {nxt + 1}")
        _rebuild(keep_state=False)
        if SIM.vehicle:
            SIM.vehicle.tick(virtual_only=not SIM.vehicle.running)
        return
    kw = {k: v for k, v in e.items() if k != "type" and v is not None}
    SIM.engine.event(e["type"], **kw)
    SIM._log("událost " + e["type"] + (f" {kw}" if kw else ""))
    if SIM.vehicle:
        SIM.vehicle.tick(virtual_only=not SIM.vehicle.running)


@router.post("/api/sim/event")
def event(r: EventReq):
    try:
        _event(r.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return state()


@router.get("/api/sim/state")
def state():
    eng = SIM.engine
    panels = []
    if SIM.vehicle:
        if not SIM.vehicle.running:
            SIM.vehicle.tick(virtual_only=True)   # stopped: refresh previews only, never real panels
        for s in SIM.vehicle.panels:
            st = s.status()
            fr = s.frame()                        # emulator image; mirror=True for a real panel
            if fr is not None:
                st["frame"] = fr
            panels.append(st)
    return {"running": bool(SIM.vehicle and SIM.vehicle.running), "engine": eng.state() if eng else None,
            "panels": panels, "log": SIM.log[-30:], "db": SIM.db_path, "route_path": SIM.route_path,
            "settings": SIM.engine.settings if SIM.engine else None}


# ------------------------------------------------------------------ WebSocket for external control (OMSI plugin …)
@router.websocket("/ws/sim")
async def ws_sim(ws: WebSocket):
    """Send JSON events {"type": "arrive"} …; every event is answered with the new state."""
    await ws.accept()
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            try:
                if msg.get("type") != "state":
                    _event(msg)
                await ws.send_text(json.dumps({"ok": True, "state": state()}, default=str))
            except Exception as e:      # noqa: BLE001
                await ws.send_text(json.dumps({"ok": False, "error": str(e)}))
    except WebSocketDisconnect:
        pass
