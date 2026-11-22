"""HTTP servers for LCD panels – one small app per port, running in a background thread.

The page (web/) polls api/lcd, which returns view.lcd_view() of the running simulation.
LcdServers.sync() starts servers for the ports the vehicle needs and stops the others.
"""
from __future__ import annotations

import os
import threading

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

WEB = os.path.join(os.path.dirname(__file__), "web")


def lcd_state() -> dict:
    from ...server.sim_api import SIM
    from .view import lcd_view
    if SIM.engine is None:
        return {"phase": "none", "line": "", "rows": [], "first": None, "terminal": None, "time": "", "zone": ""}
    return lcd_view(SIM.engine)


def add_routes(app: FastAPI, prefix: str = ""):
    """Page, its files and the state on `app` (the main app uses prefix /lcd)."""
    @app.get(prefix + "/api/lcd")
    def _state():
        return lcd_state()

    @app.get(prefix + "/" if prefix else "/")
    def _page():
        return FileResponse(os.path.join(WEB, "index.html"))

    app.mount(prefix + "/static", StaticFiles(directory=WEB), name=f"lcd-static{prefix.replace('/', '-')}")


def make_app() -> FastAPI:
    app = FastAPI(title="BUSE Studio – LCD panel")
    add_routes(app)
    return app


class LcdServers:
    def __init__(self):
        self.servers: dict[int, tuple] = {}     # port -> (uvicorn.Server, thread, host)
        self.errors: dict[int, str] = {}
        self.lock = threading.Lock()

    def error(self, port: int) -> str | None:
        return self.errors.get(int(port))

    def sync(self, wanted: dict[int, str]):
        """wanted = {port: host}. Start missing servers, stop the ones no longer wanted."""
        with self.lock:
            for port in list(self.servers):
                srv, th, host = self.servers[port]
                if port not in wanted or wanted[port] != host or not th.is_alive():
                    srv.should_exit = True
                    th.join(timeout=3)
                    del self.servers[port]
            for port, host in wanted.items():
                if port not in self.servers:
                    self._start(port, host)

    def _start(self, port: int, host: str):
        import uvicorn
        self.errors.pop(port, None)
        srv = uvicorn.Server(uvicorn.Config(make_app(), host=host, port=port, log_level="warning"))

        def run():
            try:
                srv.run()
            except SystemExit:              # uvicorn: bind failed
                self.errors[port] = f"LCD server na portu {port} nelze spustit (port je obsazený?)"
            except BaseException as e:      # noqa: BLE001
                self.errors[port] = f"LCD server na portu {port} nelze spustit: {e}"
            if not srv.started and port not in self.errors:
                self.errors[port] = f"LCD server na portu {port} nelze spustit (port je obsazený?)"

        th = threading.Thread(target=run, daemon=True, name=f"lcd-{port}")
        th.start()
        self.servers[port] = (srv, th, host)

    def stop_all(self):
        self.sync({})


LCD = LcdServers()
