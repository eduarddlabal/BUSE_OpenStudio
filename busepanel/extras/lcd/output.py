"""LCD panel output: a web page on its own HTTP port (PID style). It takes no IBIS telegrams –
the page reads the simulation state directly (see view.py, server.py)."""
from __future__ import annotations

from ...outputs.base import Output

DEFAULT_PORT = 8091


class LcdOutput(Output):
    kind = "lcd"

    def __init__(self, name: str, port: int = DEFAULT_PORT, host: str = "127.0.0.1"):
        super().__init__(name)
        self.port, self.host = int(port or DEFAULT_PORT), host or "127.0.0.1"

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host
        return f"http://{host}:{self.port}/"

    def send(self, *telegrams):
        pass

    def status(self) -> dict:
        from .server import LCD
        st = super().status()
        st.update({"url": self.url, "port": self.port, "host": self.host})
        st["error"] = LCD.error(self.port)
        return st
