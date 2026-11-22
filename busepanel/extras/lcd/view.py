"""LCD (web) panel in the PID style: line, zone, list of the next stops with minutes, clock, terminal.

lcd_view() turns the simulation state into JSON for the page in web/; the page is served on its own
HTTP port (server.py) and also on the main port at /lcd/.
"""
from __future__ import annotations

import datetime as dt
import math


def _stop(stop, minutes=None) -> dict:
    return {"name": stop.name, "icons": list(stop.icons), "request": bool(stop.request),
            "zone": stop.zone, "minutes": minutes}


def lcd_view(engine, now: float | None = None) -> dict:
    """{line, destination, zone, time, phase, message, first, rows, terminal}.
    first = the stop the passenger cares about now (next stop while driving, current stop at a stop),
    rows = the following stops up to (not including) the terminal, terminal = last stop."""
    now = engine.clock() if now is None else now
    route = engine.route
    st = route.stops
    out = {"line": route.line, "destination": route.destination, "phase": engine.phase,
           "time": dt.datetime.fromtimestamp(now).strftime("%H:%M"), "message": engine.message,
           "zone": "", "first": None, "rows": [], "terminal": None, "at_stop": engine.phase == "at_stop"}
    if not st:
        return out
    pos = 0 if engine.phase == "idle" else engine.pos
    if engine.phase == "ended":
        pos = len(st) - 1

    # minutes to the stops (only when the route has them)
    base = None
    if st[pos].minutes is not None:
        if engine.phase == "driving" and pos > 0 and st[pos - 1].minutes is not None:
            base = st[pos - 1].minutes + (now - engine.since) / 60.0      # time since departure
        else:
            base = st[pos].minutes

    def mins(stop):
        if base is None or stop.minutes is None:
            return None
        return max(0, int(math.ceil(stop.minutes - base - 1e-9)))

    first = st[pos]
    out["zone"] = first.zone
    out["first"] = _stop(first, mins(first) if engine.phase == "driving" else None)
    out["terminal"] = _stop(st[-1], mins(st[-1]))
    if pos < len(st) - 1:
        out["rows"] = [_stop(s, mins(s)) for s in st[pos + 1:-1]]
    else:
        out["terminal"] = None                     # first row already is the terminal
    return out
