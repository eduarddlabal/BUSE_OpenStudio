"""Local web server for the graphical editor (FastAPI). Start with `busepanel serve`."""
from __future__ import annotations

import base64
import os
import threading
import time
import traceback
import uuid
import webbrowser

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..core import image as I
from ..live import modes as live
from ..loader.device import Panel, list_ports
from ..core.fonts import Glyph
from ..core.importers import import_file, merge
from ..core.project import SECTIONS, Project
from ..core.render import text_bitmap
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


def _project_json(p: Project) -> dict:
    data = {k: v for k, v in p.data.items() if k != "fonts"}
    return {"path": S.path, "data": data, "fonts": [f.id for f in p.fonts.fonts],
            "geometry": p.geometry.__dict__, "defaults": p.defaults()}


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
@app.get("/api/files")
def files():
    """Panel databases in the workdir: HEX files and YAML projects (those with a 'fonts' key).

    Routes, vehicles and other YAML files are left out, so they cannot be opened as a project."""
    out = []
    for root, dirs, fs in os.walk(S.workdir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS]
        if len(out) > 300:
            break
        for f in fs:
            full = os.path.join(root, f)
            low = f.lower()
            if low.endswith(".hex") or (low.endswith((".yaml", ".yml")) and f != "fonts.yaml"
                                        and _is_project_yaml(full)):
                out.append(os.path.relpath(full, S.workdir).replace("\\", "/"))
    return {"workdir": S.workdir, "files": sorted(out)}


_SKIP_DIRS = {"__pycache__", "node_modules", "venv", "build", "dist"}


def _is_project_yaml(path) -> bool:
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
    if path.lower().endswith(".hex"):
        S.project = Project.from_hex(path, ibis_address=S.address)
        S.path = os.path.splitext(path)[0] + ".yaml"
    else:
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
    if r.path:
        S.path = _abs(r.path)
    os.makedirs(os.path.dirname(S.path), exist_ok=True)
    p.save(S.path)
    return {"saved": S.path}


@app.post("/api/project/update")
def project_update(r: SaveReq):
    p = _need_project()
    p.data = {**r.data, "fonts": p.data.get("fonts")}
    return {"ok": True}


class NewReq(BaseModel):
    path: str
    fonts_from: str
    version: str = "NOVY"


@app.post("/api/project/new")
def project_new(r: NewReq):
    src = Project.from_hex(_abs(r.fonts_from)) if r.fonts_from.lower().endswith(".hex") else Project.load(_abs(r.fonts_from))
    data = {"panel": {"type": "BS120", "ibis_address": S.address},
            "config": {"version": r.version, "columns": 135, "commands": ["l", "z", "x", "a", "aV"],
                       "defaults": {"font": 3, "spacing": 1, "interval": 1.0},
                       "tariff_digits": 1, "cycle_delay": 10, "panel_kind": 1},
            "fonts": None, "lines": {"000": ""}, "destinations": {"000": ""}}
    S.project = Project(data, src.fonts)
    S.path = _abs(r.path)
    return _project_json(S.project)


@app.get("/api/project/check")
def project_check():
    return {"items": _need_project().check()}


@app.get("/api/project/hex", response_class=PlainTextResponse)
def project_hex():
    text, problems = _need_project().to_hex()
    if problems:
        raise HTTPException(400, "; ".join(problems[:10]))
    return text


# ------------------------------------------------------------------ preview
class PreviewReq(BaseModel):
    value: object = None
    line: object = None


@app.post("/api/preview")
def preview(r: PreviewReq):
    p = _need_project()
    g = p.geometry
    f0, s0 = p.defaults()
    out = {"geometry": g.__dict__, "problems": []}
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
                "width": len(cols), "bytes": b.hex(" ").upper()}
        if name == "dest" and not info["run"]:
            start, lo, hi = g.dest_span(len(cols), info["x"])
            item.update(start=start, lo=lo, hi=hi, overflow=start + len(cols) - 1 > hi or start < lo)
        out[name] = item
    return out


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
                           "rows": g.rows() if g.height == 8 else None, "width": g.width})
        res.append({"id": f.id, "glyphs": glyphs})
    table = [{"code": c, "char": fs.codetable.decode_code(c)} for c in range(0x20, 0xC1)]
    return {"fonts": res, "codetable": table}


class GlyphReq(BaseModel):
    font: int
    code: int
    rows: list[str]


@app.post("/api/fonts/glyph")
def fonts_glyph(r: GlyphReq):
    p = _need_project()
    f = p.fonts.font(r.font)
    if f is None:
        raise HTTPException(404, "font neexistuje")
    f.glyphs[r.code] = Glyph.from_rows(r.code, r.rows)
    return {"ok": True, "width": f.glyphs[r.code].width}


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
    from ..core.fonts import CodeTable
    fs = _need_project().fonts
    raw = bytearray(fs.codetable.raw)
    if not 0x21 <= r.code <= 0xC0:
        raise HTTPException(400, "kód musí být 0x21–0xC0")
    raw[r.code - 0x21] = r.char.encode("cp1250")[0] if r.char else 0
    fs.codetable = CodeTable(bytes(raw))
    return {"ok": True}


# ------------------------------------------------------------------ import
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
    return {"counts": {k: len(v) for k, v in recs.items()}, "problems": problems, **_project_json(p)}


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
        im = I.parse(res["flash"])
        with open(os.path.join(folder, "data.hex"), "w", newline="", encoding="ascii") as f:
            f.write(I.blocks_to_hex(I.build(im)))
        Project.from_image(im, ibis_address=S.address).save(os.path.join(folder, "projekt.yaml"))
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
