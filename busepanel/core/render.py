"""Render panel texts to a dot bitmap (for previews, PNG/ASCII export and overflow checks)."""
from __future__ import annotations

import struct
import zlib

from ..panels.profile import Geometry

ESC, VT, CR = 0x1B, 0x0B, 0x0D


def interpret(b: bytes, default_font: int = 3, default_spacing: int = 1):
    """Walk the control codes of one record.

    Returns dict(columns=[...], inverse_mask=[...], x=int|None, run=bool, font, spacing).
    """
    font, spacing = default_font, default_spacing
    x, run, inverse = None, False, False
    segs = []           # (fid, spacing, codes, inverse)
    cur = bytearray()

    def flush():
        if cur:
            segs.append((font, spacing, bytes(cur), inverse))
            cur.clear()

    i = 0
    while i < len(b):
        c = b[i]
        if 0xE0 <= c <= 0xEF:
            flush(); font = c - 0xE0
        elif 0xB0 <= c <= 0xBF:
            flush(); spacing = c - 0xB0
        elif c == ESC and i + 1 < len(b):
            k = b[i + 1]
            if k == ord("i"):
                flush(); inverse = not inverse; i += 1
            elif k == ord("t"):
                run = True; i += 2
            elif k == ord("s"):
                x = b[i + 2] - 0x10; i += 2
            else:
                i += 2   # unknown ESC x n
        elif c in (VT, CR):
            pass
        elif c >= 0x20:
            cur.append(c)
        i += 1
    flush()
    return {"segments": segs, "x": x, "run": run, "font": font, "spacing": spacing}


def text_bitmap(b: bytes, fonts, default_font=3, default_spacing=1):
    """-> (columns, inverse flags, info)"""
    info = interpret(b, default_font, default_spacing)
    cols, inv = [], []
    first = True
    for fid, sp, codes, inverse in info["segments"]:
        for c in codes:
            if not first:
                cols += [0] * sp
                inv += [inverse] * sp
            first = False
            g = fonts.glyph_columns(fid, c)
            cols += g
            inv += [inverse] * len(g)
    return cols, inv, info


def preview_item(b: bytes, fonts, geom: Geometry, defaults=(3, 1), area: str = "dest",
                 lo: int | None = None, hi: int | None = None) -> dict:
    """JSON-friendly description of one text for the browser LED renderer.

    area="dest": placed like the BS120 places destinations (centred 24–134 or ESC s 0–111);
    area="full": centred over the whole panel; area="line": the line number field.
    """
    cols, inv, info = text_bitmap(b, fonts, *defaults)
    item = {"cols": cols, "inv": [1 if i else 0 for i in inv], "x": info["x"], "run": info["run"],
            "width": len(cols), "bytes": b.hex(" ").upper()}
    if area == "line":
        lw = geom.line_right - geom.line_left + 1
        item.update(start=geom.line_left + max(0, (lw - len(cols)) // 2), lo=geom.line_left, hi=geom.line_right)
    elif area == "full":
        lo_, hi_ = (0 if lo is None else lo), (geom.columns - 1 if hi is None else hi)
        start = lo_ + max(0, (hi_ - lo_ + 1 - len(cols)) // 2) if info["x"] is None else info["x"]
        item.update(start=start, lo=lo_, hi=hi_, overflow=start + len(cols) - 1 > hi_)
    else:
        start, lo_, hi_ = geom.dest_span(len(cols), info["x"])
        item.update(start=start, lo=lo_, hi=hi_, overflow=start + len(cols) - 1 > hi_ or start < lo_)
    return item


class Frame:
    def __init__(self, geom: Geometry):
        self.g = geom
        self.cols = [0] * geom.columns

    def blit(self, cols, inv, start, lo, hi):
        for i, v in enumerate(cols):
            x = start + i
            if lo <= x <= hi and 0 <= x < len(self.cols):
                self.cols[x] = (~v & 0xFF) if inv[i] else v

    def ascii(self, on="█", off="·") -> str:
        return "\n".join("".join(on if c >> (7 - y) & 1 else off for c in self.cols)
                         for y in range(self.g.rows))

    def png(self, scale: int = 6, on=(255, 170, 0), off=(40, 30, 20), bg=(10, 8, 6)) -> bytes:
        w, h = self.g.columns * scale, self.g.rows * scale
        raw = bytearray()
        for y in range(h):
            raw.append(0)
            row, inner_y = y // scale, y % scale
            for x in range(w):
                col, inner_x = x // scale, x % scale
                lit = self.cols[col] >> (7 - row) & 1
                edge = inner_x == scale - 1 or inner_y == scale - 1
                raw += bytes(bg if edge else (on if lit else off))
        def chunk(t, d):
            c = struct.pack(">I", len(d)) + t + d
            return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def compose(geom: Geometry, fonts, line: bytes | None, dest: bytes | None,
            defaults=(3, 1), scroll: int = 0) -> tuple[Frame, dict]:
    """Render line + destination like the BS120 does. `scroll` = running-text offset."""
    fr = Frame(geom)
    report = {}
    if dest:
        cols, inv, info = text_bitmap(dest, fonts, *defaults)
        w = len(cols)
        report["dest_width"] = w
        if info["run"]:
            span = geom.dest_width
            period = w + span
            start = geom.dest_left + span - (scroll % period)
            fr.blit(cols, inv, start, geom.dest_left, geom.dest_right)
            report["run"] = True
        else:
            start, lo, hi = geom.dest_span(w, info["x"])
            fr.blit(cols, inv, start, lo, hi)
            vis_hi = min(hi, start + w - 1)
            report["overflow"] = start + w - 1 > hi or start < lo
            report["start"] = start
            report["visible"] = (max(lo, start), vis_hi)
    if line:
        cols, inv, info = text_bitmap(line, fonts, *defaults)
        lw = geom.line_right - geom.line_left + 1
        start = geom.line_left + max(0, (lw - len(cols)) // 2)
        # the line field hides whatever the destination drew there
        for x in range(geom.line_left, geom.line_right + 1):
            fr.cols[x] = 0
        fr.blit(cols, inv, start, geom.line_left, geom.line_right)
        report["line_width"] = len(cols)
    else:
        for x in range(geom.line_left, geom.line_right + 1):
            fr.cols[x] = 0
    return fr, report
