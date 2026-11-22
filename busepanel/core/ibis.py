"""IBIS layer (VDV 300 style) as used by BUSE panels.

1200 Bd, 7 data bits, even parity, 2 stop bits, DTR must be LOW (otherwise the BS120 stays silent).
Telegram = text + CR + P, where P = 0x7F ^ XOR(all bytes incl. CR).
Bytes >= 0x80 are escaped as 0x0E, (b & 0x7F).
"""
from __future__ import annotations

import time

ADDR_CHARS = "0123456789:;<=>?"


def addr_char(addr: int) -> str:
    if not 0 <= addr <= 15:
        raise ValueError("IBIS adresa musí být 0–15")
    return ADDR_CHARS[addr]


def checksum(body: bytes) -> int:
    x = 0
    for c in body:
        x ^= c
    return (~x) & 0x7F


def encode(text: str | bytes) -> bytes:
    if isinstance(text, str):
        text = text.encode("cp1250")
    out = bytearray()
    for c in text:
        if c >= 0x80:
            out += bytes([0x0E, c & 0x7F])
        else:
            out.append(c)
    out.append(0x0D)
    out.append(checksum(out))
    return bytes(out)


def decode(raw: bytes) -> tuple[str, bool]:
    """Return (text, checksum_ok) for a received telegram (text + CR + P)."""
    if len(raw) >= 2 and raw[-2] == 0x0D:
        body, p = raw[:-1], raw[-1]
        ok = checksum(body) == p
        out = bytearray()
        it = iter(body[:-1])
        for c in it:
            out.append((next(it, 0) | 0x80) if c == 0x0E else c)
        return out.decode("cp1250", "replace"), ok
    return raw.decode("latin1", "replace"), False


def open_port(port: str, baud: int = 1200, ibis: bool = True, timeout: float = 0.4):
    import serial  # imported lazily so that offline tools work without pyserial
    s = serial.Serial()
    s.port = port
    s.baudrate = baud
    s.timeout = timeout
    if ibis:
        s.bytesize, s.parity, s.stopbits = 7, "E", 2
    else:
        s.bytesize, s.parity, s.stopbits = 8, "N", 1
    s.dtr = False  # must be set before open()
    s.rts = False
    for attempt in range(4):
        try:
            s.open()
            return s
        except Exception as e:      # noqa: BLE001 – USB adapter briefly gone ("device not ready")
            if attempt == 3 or "PermissionError(13" not in repr(e) and "not ready" not in str(e).lower():
                raise
            time.sleep(1.0)
    return s


class IbisPort:
    def __init__(self, port: str, timeout: float = 0.6):
        self.s = open_port(port, 1200, True, timeout)

    def close(self):
        self.s.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def send(self, text: str | bytes) -> bytes:
        raw = encode(text)
        self.s.reset_input_buffer()
        self.s.write(raw)
        self.s.flush()
        return raw

    def recv(self, deadline: float = 1.0) -> bytes:
        buf = bytearray()
        t0 = time.time()
        while time.time() - t0 < deadline:
            b = self.s.read(1)
            if not b:
                continue
            c = b[0] & 0x7F
            buf.append(c)
            if c == 0x0D:
                p = self.s.read(1)
                if p:
                    buf.append(p[0] & 0x7F)
                break
        return bytes(buf)

    def query(self, text: str, deadline: float = 1.0) -> str | None:
        self.send(text)
        raw = self.recv(deadline)
        if not raw:
            return None
        txt, ok = decode(raw)
        return txt if ok else None
