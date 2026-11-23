"""Render panel texts to a dot bitmap (for previews, PNG/ASCII export, cycle preview and overflow checks)."""
from __future__ import annotations

import struct
import zlib

from ..panels.profile import Geometry

ESC, LF, VT, FF, CR = 0x1B, 0x0A, 0x0B, 0x0C, 0x0D


def interpret(b: bytes, default_font: int = 3, default_spacing: int = 1):
    """Walk the control codes of one record.

    Returns dict(segments=[(fid, sp, codes, inv, blink)], x=int|None, run=bool, effect=int,
                 blink=bool, interval=float|None, win_l=int|None, win_p=int|None, font, spacing).
    """
    font, spacing = default_font, default_spacing
    x, run, effect, inverse, blink = None, False, 0, False, False
    interval = None
    win_l = win_p = None
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
            elif k == ord("b"):
                flush(); blink = not blink; i += 1
            elif k == ord("t") and i + 2 < len(b):
                tb = b[i + 2]
                effect = tb - 0x10 if 0x10 <= tb <= 0x13 else 1
                run = (effect == 1) or (tb >= 0x10 and tb not in (0x10, 0x12, 0x13))
                i += 2
            elif k == ord("s") and i + 2 < len(b):
                x = b[i + 2] - 0x10; i += 2
            elif k == ord("z") and i + 2 < len(b):
                interval = max(0.1, (b[i + 2] - 0x0F) / 10); i += 2
            elif k == ord("l") and i + 2 < len(b):
                win_l = b[i + 2] - 0x0F - 1; i += 2
            elif k == ord("p") and i + 2 < len(b):
                win_p = b[i + 2] - 0x0F - 1; i += 2
            else:
                i += 2   # unknown ESC x n
        elif c in (VT, CR, LF, FF):
            pass
        elif c >= 0x20:
            cur.append(c)
        i += 1
    flush()
    return {"segments": segs, "x": x, "run": run, "effect": effect, "blink": blink,
            "interval": interval, "win_l": win_l, "win_p": win_p, "font": font, "spacing": spacing}


def _split_frames_bytes(b: bytes) -> tuple[list[bytes], bool]:
    """Split a gBUSE1 record by LF into pages; returns (pages, loops)."""
    pages = []
    cur = bytearray()
    loops = False
    i = 0
    while i < len(b):
        c = b[i]
        if c == ESC and i + 1 < len(b):
            step = 3 if chr(b[i + 1]) in "tszlpa" and i + 2 < len(b) else 2
            cur += b[i:i + step]
            i += step
            continue
        if c == LF:
            pages.append(bytes(cur))
            cur.clear()
        elif c == VT:
            pages.append(bytes(cur))
            cur.clear()
            loops = True
            break
        elif c == CR:
            pages.append(bytes(cur))
            cur.clear()
            break
        elif c != FF:
            cur.append(c)
        i += 1
    if cur or not pages:
        pages.append(bytes(cur))
    return pages, loops


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


def render_frames(b: bytes, fonts, geom: Geometry, defaults=(3, 1), default_interval: float = 1.0,
                  area: str = "dest", lo: int | None = None, hi: int | None = None) -> dict:
    """Split multi-page (LF/VT) gBUSE1 record into preview frames with effect & timing."""
    raw_pages, loops = _split_frames_bytes(b)
    frames = []
    font, spacing = defaults
    interval = default_interval
    for pbytes in raw_pages:
        item = preview_item(pbytes, fonts, geom, (font, spacing), area=area, lo=lo, hi=hi)
        info = interpret(pbytes, font, spacing)
        font, spacing = info["font"], info["spacing"]
        if info["interval"] is not None:
            interval = info["interval"]
        item["effect"] = info["effect"]
        item["blink"] = info["blink"]
        item["seconds"] = interval if len(raw_pages) > 1 else None
        frames.append(item)
    return {"frames": frames, "loop": 0 if loops and len(frames) > 1 else None}


def preview_item(b: bytes, fonts, geom: Geometry, defaults=(3, 1), area: str = "dest",
                 lo: int | None = None, hi: int | None = None) -> dict:
    """JSON-friendly description of one text for the browser LED renderer.

    area="dest": placed like the BS120 places destinations (centred 24–134 or ESC s 0–111);
    area="full": centred over the whole panel; area="line": the line number field.
    """
    cols, inv, info = text_bitmap(b, fonts, *defaults)
    item = {"cols": cols, "inv": [1 if i else 0 for i in inv], "x": info["x"], "run": info["run"],
            "effect": info["effect"], "blink": info["blink"],
            "width": len(cols), "bytes": b.hex(" ").upper()}
    if area == "line":
        lw = geom.line_right - geom.line_left + 1
        item.update(start=geom.line_left + max(0, (lw - len(cols)) // 2), lo=geom.line_left, hi=geom.line_right)
    elif area == "full":
        lo_ = (info["win_l"] if info["win_l"] is not None else 0) if lo is None else lo
        hi_ = (info["win_p"] if info["win_p"] is not None else geom.columns - 1) if hi is None else hi
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


def _format_zone_text(zone_str: str, tariff_digits: int) -> str:
    """Format e...... according to config tariff_digits (1, 2 or 3 numbers, gBUSE1.doc)."""
    digits = "".join(c for c in str(zone_str) if c.isdigit()).ljust(6, "0")
    if tariff_digits == 1:
        return str(int(digits[:3]))
    if tariff_digits == 2:
        a, b = int(digits[:2]), int(digits[2:4])
        return f"{a} {b}" if a else str(b)
    parts = [int(digits[0:2]), int(digits[2:4]), int(digits[4:6])]
    return " ".join(str(x) for x in parts if x) or "0"


def simulate_cycle_step(project, cycle_id: int, step_idx: int, *,
                        line_key: str = "", dest_key: str = "", dest4_key: str = "",
                        stop_key: str = "", msg_key: str = "", free_text: str = "",
                        time_str: str = "12:34", zone_str: str = "001000") -> dict:
    """Render one step of a gBUSE1 typ 2 cycle (left + right windows + DOP prefix)."""
    cycles = project.data.get("cycles") or {}
    steps = cycles.get(cycle_id) or cycles.get(str(cycle_id)) or []
    geom = project.geometry
    f0, s0 = project.defaults()
    cfg = project.data.get("config") or {}
    tariff_digits = int(cfg.get("tariff_digits", 1))
    dop_map = project.data.get("supplementary") or {}

    fr = Frame(geom)
    if not steps or step_idx >= len(steps):
        return {"cols": fr.cols, "width": geom.columns, "duration": 0}
    step = steps[step_idx]
    left, right = step.get("left"), step.get("right")
    total_w = geom.columns

    def get_side_bytes(side: dict) -> bytes:
        src = int(side.get("source", 0))
        dop_id = int(side.get("dop", 0))
        dop_val = dop_map.get(dop_id) if dop_id in dop_map else dop_map.get(str(dop_id))
        dop_bytes = b""
        if dop_val is not None:
            dop_bytes, _ = project.encode_value(dop_val)
            dop_bytes = dop_bytes.rstrip(b"\r")
        body = b""
        if src == 1:
            v = (project.data.get("lines") or {}).get(line_key)
            body, _ = project.encode_value(v if v is not None else line_key.lstrip("0") or "0")
        elif src == 2:
            v = (project.data.get("destinations") or {}).get(dest_key)
            if v is not None:
                body, _ = project.encode_value(v)
        elif src == 3:
            v = (project.data.get("stops") or {}).get(stop_key)
            if v is not None:
                body, _ = project.encode_value(v)
        elif src == 4:
            v = (project.data.get("messages") or {}).get(msg_key)
            if v is not None:
                body, _ = project.encode_value(v)
        elif src == 6:
            v = (project.data.get("destinations4") or {}).get(dest4_key)
            if v is not None:
                body, _ = project.encode_value(v)
        elif src in (5, 7, 8, 9, 10, 11, 12):
            body, _ = project.encode_value(free_text)
        elif src == 13:
            t = time_str.strip()
            if len(t) == 4 and t.isdigit():
                t = f"{t[:2]}:{t[2:]}"
            body, _ = project.encode_value(t)
        elif src == 14:
            body, _ = project.encode_value(_format_zone_text(zone_str, tariff_digits))
        body = body.rstrip(b"\r")
        if dop_bytes and body:
            return dop_bytes + body + b"\r"
        return (dop_bytes or body) + b"\r"

    def render_window(side: dict, x0: int, x1: int):
        b = get_side_bytes(side)
        cols, inv, _info = text_bitmap(b, project.fonts, f0, s0)
        w = x1 - x0 + 1
        start = x0 + max(0, (w - len(cols)) // 2)
        for x in range(x0, min(total_w, x1 + 1)):
            fr.cols[x] = 0
        fr.blit(cols, inv, start, x0, x1)

    lw = int((left or {}).get("width", 0))
    if left and int(left.get("source", 0)) > 0 and lw > 0:
        render_window(left, 0, min(total_w - 1, lw - 1))
    if right and int(right.get("source", 0)) > 0:
        rw = int(right.get("width", total_w - lw))
        x0 = lw if (left and int(left.get("source", 0)) > 0) else 0
        x1 = min(total_w - 1, x0 + rw - 1) if right.get("width") else (total_w - 1)
        render_window(right, x0, x1)

    dur = int((left or {}).get("duration") or (right or {}).get("duration") or cfg.get("cycle_delay", 10))
    return {"cols": fr.cols, "width": total_w, "duration": dur}
