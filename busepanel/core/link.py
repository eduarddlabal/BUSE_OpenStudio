"""Service-mode link (binary frames) – verified on BS120.

enter : IBIS empty telegram + 'ESC S <addr>' at 1200 7E2, then 9600 8N1
frame : LEN DATA[LEN] CS, (LEN + sum(DATA) + CS) & 0xFF == 0
reply : 'O' ok, 'E' error, 'R' repeat; data replies are frames too
"""
from __future__ import annotations

import time

from . import ibis

ACK_OK, ACK_ERR, ACK_REPEAT = b"O", b"E", b"R"

# commands (first byte of DATA)
CMD_WRITE = 0x05      # a0 a1 a2 data…   write data FLASH
CMD_READ_FLASH = 0x06  # lo hi bank n
CMD_ERASE = 0x07      # (BS120: no parameter) erase data FLASH
CMD_READ_PROG = 0x08  # lo hi n           read program memory (EPROM)
CMD_LEAVE = 0x0F

WRITE_PAUSE = 0.08    # panel acks before programming finishes and drops bytes meanwhile


def frame(data: bytes) -> bytes:
    n = len(data)
    return bytes([n]) + data + bytes([(-(n + sum(data))) & 0xFF])


class LinkError(Exception):
    pass


class ServiceLink:
    def __init__(self, port: str, addr: int = 10, baud: int = 9600, log=None):
        self.port, self.addr, self.baud = port, addr, baud
        self.s = None
        self.log = log or (lambda msg: None)

    # -- session -------------------------------------------------------
    def enter(self, already_in_service: bool = False):
        if not already_in_service:
            s = ibis.open_port(self.port, 1200, True)
            s.write(ibis.encode(b""))
            s.flush()
            time.sleep(0.15)
            s.write(ibis.encode(b"\x1bS" + ibis.addr_char(self.addr).encode()))
            s.flush()
            time.sleep(0.15)
            s.close()
            time.sleep(0.1)
        self.s = ibis.open_port(self.port, self.baud, False)
        time.sleep(0.25)
        self.s.reset_input_buffer()
        if self.read_flash(0, 1)[0] != "ok" and not self.resync():
            self.close()
            raise LinkError("panel v servisním režimu neodpovídá")

    def leave(self):
        try:
            if self.s:
                self.s.reset_input_buffer()
                self.s.write(frame(bytes([CMD_LEAVE])))
                self.s.flush()
                return self.s.read(1)
        finally:
            self.close()

    def close(self):
        if self.s:
            self.s.close()
            self.s = None

    def __enter__(self):
        self.enter()
        return self

    def __exit__(self, *exc):
        self.leave()

    # -- low level -----------------------------------------------------
    def _wait(self, timeout: float) -> bytes:
        t0 = time.time()
        while time.time() - t0 < timeout:
            r = self.s.read(1)
            if r:
                return r
        return b""

    def resync(self) -> bool:
        """The panel lost bytes and waits in the middle of a frame: feed single 0x00
        bytes until it answers (00 00 is a valid empty frame), then confirm by a read."""
        self.log("resynchronizace spojení")
        time.sleep(0.3)
        for _ in range(300):
            self.s.reset_input_buffer()
            self.s.write(b"\x00")
            self.s.flush()
            if self._wait(0.06):
                break
        time.sleep(0.3)
        for _ in range(5):
            self.s.reset_input_buffer()
            if self._cmd(bytes([CMD_READ_FLASH, 0, 0, 0, 1]), True)[0] == "ok":
                return True
            time.sleep(0.3)
        return False

    def _cmd(self, data: bytes, expect_reply: bool):
        self.s.reset_input_buffer()
        self.s.write(frame(data))
        self.s.flush()
        ack = self._wait(2.0)
        if ack != ACK_OK:
            return ("noack", ack)
        if not expect_reply:
            return ("ok", b"")
        n = self.s.read(1)
        if not n:
            return ("noreply", b"")
        body = self.s.read(n[0] + 1)
        if len(body) != n[0] + 1 or (n[0] + sum(body)) & 0xFF:
            return ("badcs", b"")
        return ("ok", bytes(body[:-1]))

    def cmd(self, data: bytes, expect_reply: bool = True, tries: int = 4):
        res = ("noack", b"")
        for _ in range(tries):
            res = self._cmd(data, expect_reply)
            if res[0] == "ok":
                return res
            self.resync()
        return res

    # -- operations ----------------------------------------------------
    def read_flash(self, addr: int, n: int = 0x20, bank: int = 0):
        return self._cmd(bytes([CMD_READ_FLASH, addr & 0xFF, addr >> 8 & 0xFF, bank, n]), True)

    def read_block(self, addr: int, n: int = 0x20, program: bool = False) -> bytes:
        data = (bytes([CMD_READ_PROG, addr & 0xFF, addr >> 8 & 0xFF, n]) if program
                else bytes([CMD_READ_FLASH, addr & 0xFF, addr >> 8 & 0xFF, 0, n]))
        st, d = self.cmd(data, True)
        if st != "ok":
            raise LinkError(f"čtení 0x{addr:04X} selhalo ({st})")
        return d

    def erase_data(self, timeout: float = 60.0):
        self.s.reset_input_buffer()
        self.s.write(frame(bytes([CMD_ERASE])))
        self.s.flush()
        time.sleep(0.7)
        r = self._wait(timeout)
        if r != ACK_OK:
            raise LinkError(f"mazání selhalo (odpověď {r!r})")

    def write(self, addr: int, data: bytes):
        payload = bytes([CMD_WRITE, addr & 0xFF, addr >> 8 & 0xFF, addr >> 16 & 0xFF]) + data
        for _ in range(5):
            st, _r = self._cmd(payload, False)
            if st == "ok":
                time.sleep(WRITE_PAUSE)
                return
            self.resync()
            st, d = self.read_flash(addr & 0xFFFF, len(data))
            if st == "ok" and d == data:   # written although the ack got lost
                return
        raise LinkError(f"zápis na 0x{addr:05X} selhal")
