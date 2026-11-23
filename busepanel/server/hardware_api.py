"""Physical hardware communication APIs: local serial/RS-485 panel loader and network ESP gateway."""
from __future__ import annotations

import json
import os
import time

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ..core.project import Project
from ..live import modes as live
from ..loader.device import Panel, list_ports
from ..outputs.serial_out import port_lock
from ..outputs.ws_out import HUB
from .state import S, _abs, _job, _need_project

router = APIRouter()


class PanelCfg(BaseModel):
    port: str
    address: int = 10


@router.get("/api/panel/ports")
def panel_ports():
    return {"ports": list_ports(), "port": S.port, "address": S.address}


@router.post("/api/panel/config")
def panel_config(r: PanelCfg):
    S.port, S.address = r.port, r.address
    return {"ok": True}


@router.post("/api/panel/scan")
def panel_scan():
    return {"job": _job(lambda panel: Panel.scan(S.port, log=panel.log))}


class SendReq(BaseModel):
    telegrams: list[str]
    query: bool = False


@router.post("/api/panel/send")
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


@router.post("/api/panel/text")
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


@router.post("/api/panel/upload")
def panel_upload():
    text, problems = _need_project().to_hex()
    if problems:
        raise HTTPException(400, "Projekt obsahuje chyby: " + "; ".join(problems[:10]))
    return {"job": _job(lambda panel: panel.upload(text))}


class BackupReq(BaseModel):
    name: str = ""
    eprom: bool = False


@router.post("/api/panel/backup")
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


@router.get("/api/jobs/{jid}")
def job_get(jid: str):
    if jid not in S.jobs:
        raise HTTPException(404)
    return S.jobs[jid]


@router.get("/api/devices")
def devices():
    return {"devices": HUB.list()}


@router.websocket("/ws/device/{dev_id}")
async def ws_device(ws: WebSocket, dev_id: str):
    """ESP gateway: see docs/esp-protokol.md"""
    await ws.accept()
    await HUB.attach(dev_id, ws)
    try:
        while True:
            try:
                HUB.on_message(dev_id, json.loads(await ws.receive_text()))
            except ValueError:
                pass
    except WebSocketDisconnect:
        HUB.detach(dev_id, ws)
