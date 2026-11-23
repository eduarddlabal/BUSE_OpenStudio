"""Simulation module API (/api/sim/*, /ws/sim) – e.g. for OMSI: manual control now, plugin later."""
from __future__ import annotations

import json
import os
import time

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ..core.project import Project, load_yaml, dump_yaml
from ..sim.engine import DEFAULT_SETTINGS, SimEngine, merge_settings
from ..sim.line import Line
from ..sim.route import Route
from ..sim.universal_db import build_universal
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
    "panels": [{"name": "Vnitřní (náhled)", "role": "interior", "profile": "BS120", "output": "virtual"},
               {"name": "Čelní (náhled)", "role": "front", "profile": "BS120", "output": "virtual"},
               {"name": "Vnitřní BS120", "role": "interior", "profile": "BS120", "output": "serial",
                "port": "", "address": 10, "enabled": False}],          # "" = auto-detected adapter
    "settings": DEFAULT_SETTINGS,
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


DEFAULT_DB = "databaze/univerzalni/projekt.yaml"


def init(workdir: str):
    SIM.workdir = os.path.abspath(workdir)
    if os.path.exists(SIM.path(DEFAULT_DB)):
        try:
            SIM.db, SIM.db_path = Project.load(SIM.path(DEFAULT_DB)), DEFAULT_DB
            _rebuild()
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


# ------------------------------------------------------------------ files
@router.get("/api/sim/files")
def files():
    return {"routes": _list("trasy"), "vehicles": _list("vozidla"),
            "databases": _list("databaze", (".yaml", ".hex")),
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
    from ..sim.engine import DEFAULT_SEQUENCES, load_formats, preset_settings
    extra = SIM.path("formaty")                       # own text formats of the working folder
    formats = load_formats(extra)
    return {"sequences": DEFAULT_SEQUENCES, "peripherals": describe(),
            "presets": {k: {"title": v["title"], "interior": preset_settings(k, extra)} for k, v in formats.items()}}


@router.get("/api/sim/vehicle")
def vehicle_get():
    return {"path": SIM.vehicle_path, "config": SIM.vehicle_cfg}


@router.post("/api/sim/vehicle/load")
def vehicle_load(r: PathReq):
    SIM.vehicle_cfg = load_yaml(SIM.path(r.path))
    SIM.vehicle_path = r.path
    _rebuild()
    return vehicle_get()


@router.post("/api/sim/vehicle/save")
def vehicle_save(r: VehicleSave):
    SIM.vehicle_cfg = r.config
    if r.path:
        os.makedirs(os.path.dirname(SIM.path(r.path)), exist_ok=True)
        dump_yaml(r.config, SIM.path(r.path))
        SIM.vehicle_path = r.path
    _rebuild()
    return vehicle_get()


# ------------------------------------------------------------------ database
@router.post("/api/sim/db/open")
def db_open(r: PathReq):
    p = SIM.path(r.path)
    SIM.db = Project.from_hex(p) if p.lower().endswith(".hex") else Project.load(p)
    SIM.db_path = r.path
    _rebuild()
    return {"path": r.path, "fonts": [f.id for f in SIM.db.fonts.fonts]}


class BuildReq(BaseModel):
    base: str
    out: str = "databaze/univerzalni/projekt.yaml"
    version: str = "UNIV"
    extra_lines: list[str] = []


@router.post("/api/sim/db/build")
def db_build(r: BuildReq):
    font = "gbuse"
    if r.base in ("", "gbuse", "builtin", "vestavěný"):
        base = None                                   # font set shipped with BUSE Studio
        font = "builtin" if r.base in ("builtin", "vestavěný") else "gbuse"
    else:
        b = SIM.path(r.base)
        base = Project.from_hex(b) if b.lower().endswith(".hex") else Project.load(b)
    extra = sorted({Route.load(SIM.path(f)).line for f in _list("trasy")} | {SIM.route.line} | set(r.extra_lines))
    db = build_universal(base, version=r.version, extra_lines=extra, font=font)
    db.save(SIM.path(r.out))
    SIM.db, SIM.db_path = Project.load(SIM.path(r.out)), r.out
    _rebuild()
    return {"path": r.out, "fonts": [f.id for f in SIM.db.fonts.fonts], "line_map": SIM.db.data.get("line_map")}


# ------------------------------------------------------------------ run
def _rebuild(keep_state: bool = True):
    """(Re)create engine + vehicle after route / vehicle / database changes."""
    if SIM.db is None:
        return
    old = SIM.engine
    running = bool(SIM.vehicle and SIM.vehicle.running)
    if SIM.vehicle:
        SIM.vehicle.stop()
    SIM.engine = SimEngine(SIM.route, SIM.vehicle_cfg.get("settings"))
    if keep_state and old is not None:
        SIM.engine.phase, SIM.engine.pos = old.phase, min(old.pos, max(0, len(SIM.route.stops) - 1))
        SIM.engine.message, SIM.engine.message_key = old.message, old.message_key
    cfg = dict(SIM.vehicle_cfg)
    cfg["panels"] = [p for p in cfg.get("panels") or [] if p.get("enabled", True)]
    SIM.vehicle = Vehicle.from_config(cfg, SIM.engine, SIM.db, log=SIM._log, base_dir=SIM.workdir)
    _sync_lcd()
    if running:
        SIM.vehicle.start()


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
            "settings": merge_settings(SIM.vehicle_cfg.get("settings"))}


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
