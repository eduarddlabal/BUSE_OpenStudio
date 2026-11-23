"""Live panel runner & preset template APIs (/api/live/*)."""
from __future__ import annotations

import os
import threading
import time

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..live import modes as live
from .state import S, _need_project, _project_json, _rel_or_abs, _slugify

router = APIRouter()


class LiveBuild(BaseModel):
    mode: str                 # clock | departures
    channel: str = "stops"
    format: str = "%H:%M"
    schedule_csv: str = ""
    style: dict = {}


@router.post("/api/live/build")
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


@router.post("/api/live/start")
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


@router.post("/api/live/stop")
def live_stop():
    if S.live_runner:
        S.live_runner.stop()
        S.live_runner = None
    return {"ok": True}


@router.get("/api/live/status")
def live_status():
    return {"running": S.live_runner is not None, "log": S.live_log[-30:]}


@router.get("/api/live/templates")
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


@router.post("/api/live/templates/save")
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
