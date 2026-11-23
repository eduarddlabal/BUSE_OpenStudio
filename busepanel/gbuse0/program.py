"""Interpreter of one gBUSE0 record – what the exterior panel draws for l / z / zD … data.

Byte codes (decoded from gBUSE0 HEX files, meaning from the gBUSE0 manual "MNEMO JAZYK"):

  E0+n  f n   font 0–15                    B0+n  m n   spacing between characters 0–15
  C0+n  y n   shift down from the window top 0–31
  1B 6C n  l  left window column   (n − 0x0F, columns 1-based from the left)
  1B 70 n  p  right window column
  1B 68 n  h  top window row       (n − 0x0F, rows 1-based from the BOTTOM)
  1B 64 n  d  bottom window row
  1B 73 n  s  text from column n − 0x0F (else centred in the window)
  1B 63    c  window back to the field from the configuration
  1B 69    i  invert the window          1B 6F  o  clear the window (FW 3.08+)
  1B 78    x  overlay with the previous content until the next FF/LF/VT/CR (FW 3.08+)
  1B 62    b  block all other IBIS telegrams while this record is shown
  1B 77    w  suppress the stops of zD (stop field)
  1B 7A n  z  display time of the following frames, n − 0x0F tenths of a second [ODHAD: code + offset]
  0C FF  format the text so far into the window
  0A LF  format; the FIRST LF only formats, every further LF shows a frame for the interval
  0B VT  format, show a frame, then repeat from the frame after the first LF
  0D CR  format, show, stop (the picture stays)
  other bytes ≥ 0x20 are character codes of the current font.

Formatting places the collected text centred in the window (or from column s) at the window top + y and
clears only the rows the text occupies – so a two-line text ("line 1 LF y10 line 2 CR") keeps line 1 and
alternating lower lines ("top LF a LF b LF c VT") replace each other (checked on sample data).
"""
from __future__ import annotations

from dataclasses import dataclass, field

ESC, LF, VT, FF, CR = 0x1B, 0x0A, 0x0B, 0x0C, 0x0D
ESC_ARG = set(b"lphdsz")


@dataclass
class Layer:
    """Bitmap of the panel: one int per column, bit y = row y from the top; mask = dots this layer owns."""
    columns: int
    rows: int
    bits: list = field(default_factory=list)
    mask: list = field(default_factory=list)

    def __post_init__(self):
        self.bits = self.bits or [0] * self.columns
        self.mask = self.mask or [0] * self.columns

    def copy(self) -> "Layer":
        return Layer(self.columns, self.rows, list(self.bits), list(self.mask))

    def clear(self, x0, x1, y0, y1):
        m = _rows(y0, y1)
        for x in range(max(0, x0), min(self.columns - 1, x1) + 1):
            self.bits[x] &= ~m
            self.mask[x] |= m

    def invert(self, x0, x1, y0, y1):
        m = _rows(y0, y1)
        for x in range(max(0, x0), min(self.columns - 1, x1) + 1):
            self.bits[x] ^= m
            self.mask[x] |= m

    def dot(self, x, y, on=True):
        if 0 <= x < self.columns and 0 <= y < self.rows:
            if on:
                self.bits[x] |= 1 << y
            self.mask[x] |= 1 << y


def _rows(y0, y1) -> int:
    return ((1 << (y1 - y0 + 1)) - 1) << y0 if y1 >= y0 else 0


def glyph_dots(g):
    """Glyph -> list of columns, each a list of row indices that are lit (0 = top)."""
    nb = max(1, (g.height + 7) // 8)
    out = []
    for i in range(0, len(g.data) - nb + 1, nb):
        v = int.from_bytes(g.data[i:i + nb], "big")
        out.append([r for r in range(g.height) if v >> (nb * 8 - 1 - r) & 1])
    return out


@dataclass
class Program:
    frames: list            # [(Layer, seconds | None)]; None = stays
    loop: int | None        # index of the first repeated frame (VT), None = no repetition
    block: bool = False     # b – other telegrams are ignored while shown
    suppress_stops: bool = False   # w
    overflow: bool = False  # some text did not fit into its window
    unsupported: list = field(default_factory=list)

    def at(self, t: float) -> Layer | None:
        """Frame shown t seconds after the record arrived."""
        if not self.frames:
            return None
        acc = 0.0
        for i, (layer, d) in enumerate(self.frames):
            if d is None or t < acc + d:
                return layer
            acc += d
        if self.loop is None:
            return self.frames[-1][0]
        cyc = self.frames[self.loop:]
        period = sum(d or 0 for _l, d in cyc)
        if period <= 0:
            return self.frames[-1][0]
        t = (t - acc) % period
        for layer, d in cyc:
            if t < (d or 0):
                return layer
            t -= d or 0
        return cyc[-1][0]

    @property
    def cycle(self) -> float:
        return sum(d or 0 for _l, d in self.frames)


def run(rec: bytes, db, fld, base: "Layer | None" = None, clear_field: bool = True) -> Program:
    """Interpret one record for the display field `fld` (database.Field) of panel `db` (Database0 or
    anything with .columns, .rows and .glyph(font, code))."""
    layer = base.copy() if base else Layer(db.columns, db.rows)
    win = [fld.x0, fld.x1, fld.y0, fld.y1]
    if clear_field:
        layer.clear(*win)
    font, spacing, shift, interval = fld.font, fld.spacing, fld.shift, fld.interval
    start_x = None
    overlay = False
    run_segs = []           # (font, spacing, shift, codes)
    cur = bytearray()
    prog = Program([], None)
    lf_seen = 0

    def flush():
        if cur:
            run_segs.append((font, spacing, shift, bytes(cur)))
            cur.clear()

    def fmt():
        nonlocal start_x, overlay
        flush()
        if not run_segs:
            overlay = False
            return
        cols = []           # (x offset, dots, y)
        x = 0
        first = True
        for f, sp, y, codes in run_segs:
            for c in codes:
                g = db.glyph(f, c)
                if not first:
                    x += sp
                first = False
                if g is None:
                    x += 2 if c == 0x20 else 0
                    continue
                for col in glyph_dots(g):
                    cols.append((x, col, y))
                    x += 1
        width = x
        x0, x1, y0, y1 = win
        if start_x is not None:
            sx = start_x
        else:
            sx = x0 + max(0, (x1 - x0 + 1 - width) // 2)
        if sx + width - 1 > x1 or sx < x0:
            prog.overflow = True
        heights = [max((g.height for g in (db.glyph(f, c) for c in codes) if g is not None), default=0)
                   for f, _sp, _y, codes in run_segs]
        top = min(y for _f, _sp, y, _c in run_segs)
        bottom = max(y + h for (_f, _sp, y, _c), h in zip(run_segs, heights))
        if not overlay:
            layer.clear(x0, x1, max(y0, y0 + top), min(y1, y0 + bottom - 1))
        for off, dots, y in cols:
            xx = sx + off
            if x0 <= xx <= x1:
                for r in dots:
                    yy = y0 + y + r
                    if y0 <= yy <= y1:
                        layer.dot(xx, yy)
        run_segs.clear()
        start_x = None
        overlay = False

    def show(seconds):
        prog.frames.append((layer.copy(), seconds))

    i, n = 0, len(rec)
    while i < n:
        c = rec[i]
        if c == ESC and i + 1 < n:
            k = rec[i + 1]
            arg = rec[i + 2] if k in ESC_ARG and i + 2 < n else None
            i += 3 if k in ESC_ARG else 2
            v = (arg or 0x0F) - 0x0F             # 1-based value
            if k in b"lphds":
                flush()
            if k == ord("l"):
                win[0] = v - 1
            elif k == ord("p"):
                win[1] = v - 1
            elif k == ord("h"):
                win[2] = db.rows - v
            elif k == ord("d"):
                win[3] = db.rows - v
            elif k == ord("s"):
                start_x = v - 1
            elif k == ord("z"):
                interval = max(0.1, v / 10)
            elif k == ord("c"):
                fmt()
                win = [fld.x0, fld.x1, fld.y0, fld.y1]
            elif k == ord("i"):
                fmt()
                layer.invert(*win)
            elif k == ord("o"):
                fmt()
                layer.clear(*win)
            elif k == ord("x"):
                flush()
                overlay = True
            elif k == ord("b"):
                prog.block = True
            elif k == ord("w"):
                prog.suppress_stops = True
            else:
                prog.unsupported.append(chr(k))
            continue
        if 0xE0 <= c <= 0xEF:
            flush(); font = c - 0xE0
        elif 0xB0 <= c <= 0xBF:
            flush(); spacing = c - 0xB0
        elif 0xC0 <= c <= 0xDF:
            flush(); shift = c - 0xC0
        elif c == FF:
            fmt()
        elif c == LF:
            fmt()
            lf_seen += 1
            if lf_seen == 1:
                prog.loop = len(prog.frames)      # a VT repeats from here
            else:
                show(interval)
        elif c == VT:
            fmt()
            show(interval)
            if prog.loop is None or prog.loop >= len(prog.frames):
                prog.loop = 0
            return prog
        elif c == CR:
            fmt()
            show(None)
            prog.loop = None
            return prog
        elif c >= 0x20:
            cur.append(c)
        i += 1
    fmt()
    show(None)
    prog.loop = None
    return prog
