"""High level panel operations: scan, IBIS telegrams, backup, upload (BS120 procedure)."""
from __future__ import annotations

import os
import time

from ..core import ibis
from ..core import image as I
from ..core.link import LinkError, ServiceLink


def list_ports() -> list[dict]:
    try:
        from serial.tools import list_ports as lp
        return [{"port": p.device, "description": p.description} for p in lp.comports()]
    except Exception:       # noqa: BLE001
        return []


def default_port() -> str:
    """BUSE_PORT, else the first USB serial adapter, else a platform default."""
    env = os.environ.get("BUSE_PORT")
    if env:
        return env
    ports = list_ports()
    for p in ports:
        d = (p["port"] + " " + p["description"]).lower()
        if any(k in d for k in ("usb", "prolific", "pl2303", "ftdi", "ch340", "cp210", "ttyacm")):
            return p["port"]
    real = [p for p in ports if (p["description"] or "n/a").lower() != "n/a"]   # skip placeholder ttyS*
    if real:
        return real[0]["port"]
    if ports:
        return ports[0]["port"]
    return "COM5" if os.name == "nt" else "/dev/ttyUSB0"


def _noop(*_a, **_k):
    pass


class Panel:
    def __init__(self, port: str, addr: int = 10, log=None, progress=None):
        self.port, self.addr = port, addr
        self.log = log or _noop
        self.progress = progress or _noop     # progress(stage, done, total)

    # ---- IBIS ---------------------------------------------------------
    def send(self, *telegrams, pause: float = 0.25):
        """Telegrams as str (ASCII) or bytes (already in panel codes, >=0x80 escaped by ibis.encode)."""
        with ibis.IbisPort(self.port) as p:
            for t in telegrams:
                p.send(t)
                self.log(f"IBIS → {t if isinstance(t, str) else t.decode('cp1250', 'replace')}")
                time.sleep(pause)

    def query(self, telegram: str) -> str | None:
        with ibis.IbisPort(self.port) as p:
            r = p.query(telegram)
        self.log(f"IBIS → {telegram}  ← {r}")
        return r

    def version(self) -> str | None:
        r = self.query("aV" + ibis.addr_char(self.addr))
        return r[2:] if r and r.startswith("aV") else r

    @staticmethod
    def scan(port: str, log=None) -> list[dict]:
        found = []
        with ibis.IbisPort(port, timeout=0.5) as p:
            for a in range(16):
                r = p.query("aV" + ibis.addr_char(a), 0.7)
                if r:
                    found.append({"address": a, "reply": r, "version": r[2:] if r.startswith("aV") else r})
                    (log or _noop)(f"adresa {a}: {r}")
                time.sleep(0.05)
        return found

    # ---- service mode -------------------------------------------------
    def _link(self) -> ServiceLink:
        lk = ServiceLink(self.port, self.addr, log=self.log)
        try:
            lk.enter()
        except LinkError:
            # maybe still in service mode after an interrupted session
            self.log("panel neodpověděl, zkouším, zda nezůstal v servisním režimu")
            lk.enter(already_in_service=True)
        return lk

    def backup(self, eprom: bool = False, max_size: int = 0x10000) -> dict:
        """Read the data FLASH (until 512 bytes of 0xFF) and optionally the program EPROM."""
        lk = self._link()
        try:
            flash = bytearray()
            ff_run = 0
            while len(flash) < max_size:
                d = lk.read_block(len(flash), 0x20)
                flash += d
                ff_run = ff_run + len(d) if d == b"\xff" * len(d) else 0
                self.progress("flash", len(flash), None)
                if ff_run >= 0x200:
                    break
            flash = bytes(flash).rstrip(b"\xff")
            prog = None
            if eprom:
                prog = bytearray()
                while len(prog) < 0x8000:
                    prog += lk.read_block(len(prog), 0x20, program=True)
                    self.progress("eprom", len(prog), 0x8000)
                prog = bytes(prog)
        finally:
            lk.leave()
        self.log(f"záloha: FLASH {len(flash)} B" + (f", EPROM {len(prog)} B" if prog else ""))
        return {"flash": flash, "eprom": prog}

    def upload(self, hex_text: str, verify: bool = True) -> dict:
        recs = list(I.hex_records(hex_text))
        image = I.hex_to_image(hex_text)
        lk = self._link()
        t0 = time.time()
        try:
            self.log("mazání FLASH")
            self.progress("erase", 0, 1)
            lk.erase_data()
            self.progress("erase", 1, 1)
            for i, (a, data) in enumerate(recs):
                lk.write(a, data)
                self.progress("write", i + 1, len(recs))
            self.log(f"zapsáno {len(recs)} záznamů za {time.time() - t0:.1f} s")
            bad = []
            if verify:
                for a in range(0, len(image), 0x20):
                    exp = image[a:a + 0x20]
                    d = lk.read_block(a, 0x20)
                    if d[:len(exp)] != exp:
                        bad.append(a)
                    self.progress("verify", a + 0x20, len(image))
                self.log("kontrola: " + ("OK" if not bad else f"{len(bad)} rozdílných bloků"))
        finally:
            lk.leave()
        self.log("DŮLEŽITÉ: vypněte a zapněte napájení panelu – linky (l) se z nových dat načtou až po restartu")
        return {"records": len(recs), "bytes": len(image), "seconds": round(time.time() - t0, 1),
                "verify_ok": not bad if verify else None, "bad_blocks": bad, "power_cycle_needed": True}
