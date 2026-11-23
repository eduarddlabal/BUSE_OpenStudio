"""Vehicle state machine for the simulation and what each panel role should show.

Events: start, depart, arrive, next (depart or arrive, whichever comes next), prev,
        message (key from route.messages or free text), clear_message, end, reset, goto.

What a panel shows is composed from *sequences* of pages (see displays_doc.txt): e.g.
"Příští zastávka / Next stop" (thin font) -> "Voleč" (normal font) -> "zastávka na znamení".
A display spec is {"line": "7" | None, "text": "<markup>", "font", "spacing", "heading": "<markup>"|None};
vehicle.py turns it into telegrams for the panel profile (free text v + x9996/x9997 on BS120).
"""
from __future__ import annotations

import copy
import datetime as dt
import re
import os
import time

import yaml

FORMATS_DIR = os.path.join(os.path.dirname(__file__), "formats")
FORMAT_ALIASES = {"dpp": "linka_stale"}       # names used by BUSE Studio 2.x vehicle files


def load_formats(extra_dir: str | None = None) -> dict:
    """Text formats (skladba textů): built-in sim/formats/*.yaml + <working folder>/formaty/*.yaml.
    name -> {"title", "interior": settings incl. sequences}"""
    out = {}
    for d in (FORMATS_DIR, extra_dir):
        if not d or not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith((".yaml", ".yml")):
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                if isinstance(data.get("interior"), dict):
                    out[os.path.splitext(fn)[0]] = {"title": str(data.get("title") or fn), "interior": data["interior"]}
    return out


PRESETS = load_formats()
DEFAULT_SEQUENCES = copy.deepcopy(PRESETS["zakladni"]["interior"]["sequences"])

DEFAULT_SETTINGS = {
    "interior": {
        "items": ["next", "time", "zone"],         # cycle while driving
        "seconds": 3,                              # default duration of one page
        "time_format": "%H:%M",
        "at_stop": "line_dest",                    # line_dest | stop_name
        "message_mode": "cycle",                   # cycle | override
        "font": 3,
        "spacing": 1,
        "sequences": copy.deepcopy(DEFAULT_SEQUENCES),
    },
    # exterior panels: font / spacing only from the pages (otherwise the panel formats the text itself)
    "exterior": {"seconds": 4},
}

_UNSET = object()
CONDITIONS = {"always", "request", "not_request", "via", "zone"}


def preset_settings(name: str, extra_dir: str | None = None) -> dict:
    """Interior settings of a format (deep copy, tagged with the format name)."""
    name = FORMAT_ALIASES.get(name, name)
    formats = load_formats(extra_dir) if extra_dir else PRESETS
    if name not in formats:
        raise ValueError(f"neznámý formát {name!r}")
    return {**copy.deepcopy(formats[name]["interior"]), "format": name}


def merge_settings(user: dict | None) -> dict:
    s = copy.deepcopy(DEFAULT_SETTINGS)
    for role, vals in (user or {}).items():
        vals = dict(vals or {})
        seq = vals.pop("sequences", None)
        s.setdefault(role, {}).update(vals)
        if seq:
            s[role].setdefault("sequences", {}).update(seq)
    # legacy single-line formats (BUSE Studio 2.0) -> one-page sequences
    i = s["interior"]
    if "next_format" in (user or {}).get("interior", {}) and not ((user or {}).get("interior", {}).get("sequences") or {}).get("next"):
        i["sequences"]["next"] = [{"text": i["next_format"] + "{request}"}]
    if "exterior" in (user or {}) and "format" in user["exterior"]:
        i["sequences"]["exterior"] = [{"text": user["exterior"]["format"]}]
    return s


class SimEngine:
    def __init__(self, route, settings: dict | None = None, clock=time.time, measure=None):
        self.route = route
        self.settings = merge_settings(settings)
        self.clock = clock
        self.measure = measure        # measure(page, line) -> seconds for one pass of a running text (or 0)
        self._measure = _UNSET        # the measure of the panel being served (display())
        self.reset()

    # ---- state ------------------------------------------------------------
    def reset(self):
        self.phase = "idle"          # idle | at_stop | driving | ended
        self.pos = 0                 # current stop (at_stop) or next stop (driving)
        self.message = None          # text of the active extraordinary message
        self.message_key = None
        self.since = self.clock()
        self.events: list[str] = []

    def _set(self, phase, pos=None):
        self.phase = phase
        if pos is not None:
            self.pos = max(0, min(pos, len(self.route.stops) - 1))
        self.since = self.clock()

    def event(self, kind: str, **kw) -> dict:
        n = len(self.route.stops)
        if kind == "start":
            self._set("at_stop", 0)
        elif kind == "depart":
            if self.phase == "idle":
                self.pos = 0
            if self.phase in ("at_stop", "idle") and self.pos < n - 1:
                self._set("driving", self.pos + 1)
        elif kind == "arrive":
            if self.phase == "driving":
                self._set("ended" if self.pos == n - 1 else "at_stop")
        elif kind == "next":
            if self.phase == "idle":
                self._set("at_stop", 0)
            elif self.phase == "at_stop":
                if self.pos < n - 1:
                    self._set("driving", self.pos + 1)
            elif self.phase == "driving":
                self._set("ended" if self.pos == n - 1 else "at_stop")
        elif kind == "prev":
            # sequence: at_stop(k-1) -> driving(k) -> at_stop(k) / ended
            if self.phase == "driving":
                self._set("at_stop", self.pos - 1)
            elif self.phase in ("at_stop", "ended") and self.pos > 0:
                self._set("driving", self.pos)
        elif kind == "goto":
            self._set(kw.get("phase", "at_stop"), int(kw.get("index", 0)))
        elif kind == "message":
            key = kw.get("key")
            self.message_key = key
            self.message = self.route.messages.get(key) if key else kw.get("text")
            self.since = self.clock()
        elif kind == "clear_message":
            self.message = self.message_key = None
            self.since = self.clock()
        elif kind == "end":
            self._set("ended", n - 1)
        elif kind == "reset":
            self.reset()
        else:
            raise ValueError(f"neznámá událost {kind!r}")
        self.events = (self.events + [time.strftime("%H:%M:%S ") + kind])[-30:]
        return self.state()

    def state(self) -> dict:
        st = self.route.stops
        cur = st[self.pos] if st else None
        return {"phase": self.phase, "pos": self.pos, "stop": cur.name if cur else None,
                "next": st[self.pos].name if self.phase == "driving" and st else None,
                "message": self.message, "message_key": self.message_key,
                "line": self.route.line, "destination": self.route.destination, "events": self.events[-10:],
                "direction": getattr(self.route, "direction", ""), "course": getattr(self.route, "course", ""),
                "trip": getattr(self.route, "trip", None), "departure": getattr(self.route, "departure", "")}

    # ---- page composition -----------------------------------------------------
    @staticmethod
    def _icons(stop) -> str:
        """LED icons of a stop. metro-A … metro-D become the stylised letter(s) [AB],
        S lines / vlak-S9 the train pictogram; names without an LED pictogram are left out."""
        from ..core.icons import ICONS
        letters, out = "", []
        for i in stop.icons:
            base, _, sub = str(i).partition("-")
            b = base.lower()
            if b == "metro" and sub.upper() in ("A", "B", "C", "D"):
                letters += "" if sub.upper() in letters else sub.upper()
                continue
            if b == "s" or (base[:1] == "S" and base[1:].isdigit()):
                b = "vlak"
            if b in ICONS and b not in out:
                out.append(b)
        text = " {icon:metro_l}%s{icon:metro_r}" % letters if letters else ""
        return text + "".join(" {icon:%s}" % i for i in out)

    def _context(self, now: float) -> dict:
        st = self.route.stops
        stop = st[self.pos] if st else None
        last = st[-1] if st else None
        s = self.settings["interior"]
        return {
            "name": stop.name if stop else "", "icons": self._icons(stop) if stop else "",
            "zone": stop.zone if stop else "", "zone_name": stop.zone_name if stop else "",
            "request": " – na znamení" if stop and stop.request else "", "is_request": bool(stop and stop.request),
            "destination": self.route.destination, "DESTINATION": self.route.destination.upper(),
            "dest_icons": self._icons(last) if last else "", "via": " - ".join(x.name for x in self.via_stops()),
            "line": self.route.line,
            "time": dt.datetime.fromtimestamp(now).strftime(s.get("time_format", "%H:%M")),
            "message": self.message or "",
        }

    @staticmethod
    def _ok(page: dict, ctx: dict) -> bool:
        w = page.get("when", "always") or "always"
        return {"always": True, "request": ctx["is_request"], "not_request": not ctx["is_request"],
                "via": bool(ctx["via"]), "zone": bool(ctx["zone"])}.get(w, True)

    def _pages(self, seq_name: str, ctx: dict, role_settings: dict) -> list[dict]:
        seqs = self.settings["interior"].get("sequences") or {}
        out = []
        for p in seqs.get(seq_name) or DEFAULT_SEQUENCES.get(seq_name, []):
            if not self._ok(p, {**ctx, "via_stop": ""}):
                continue
            try:
                # {icon:name} is panel markup, not a format field -> escape it before format()
                text = re.sub(r"\{(icon:[^{}]*)\}", r"{{\1}}", str(p.get("text", ""))).format(**ctx, via_stop="")
            except (KeyError, IndexError, ValueError):
                text = str(p.get("text", ""))
            out.append({"text": text,
                        "font": p.get("font", role_settings.get("font")),
                        "spacing": p.get("spacing", role_settings.get("spacing")),
                        "seconds": float(p.get("seconds") or role_settings.get("seconds") or 3),
                        "role": p.get("role", "text"), "run": bool(p.get("run"))})
        return out

    def _duration(self, page: dict, two_line: bool, line=None) -> float:
        """Page time. A running text: run_time "once" (default) = exactly one pass through the panel,
        "min" = at least the page time (and at least one pass)."""
        d = page["seconds"]
        m = self.measure if self._measure is _UNSET else self._measure
        if m:
            t = m(page, line) or 0
            if t:
                d = t if self.settings["interior"].get("run_time", "once") == "once" else max(d, t + 0.5)
        return d

    def _show(self, pages: list[dict], now: float, two_line: bool, line) -> dict:
        return self._spec(self._pick(pages, now, two_line, line), line)

    def _pick(self, pages: list[dict], now: float, two_line: bool, line=None) -> dict | None:
        """Choose the page for `now`; on two-line panels a heading pairs with the next page."""
        if not pages:
            return None
        units = []                       # (heading page | None, page)
        i = 0
        while i < len(pages):
            p = pages[i]
            if two_line and p["role"] == "heading" and i + 1 < len(pages):
                units.append((p, pages[i + 1]))
                i += 2
            else:
                units.append((None, p))
                i += 1
        durs = [self._duration(u[1], two_line, line) for u in units]
        cur = getattr(self, "_cursor", None)
        if cur is not None:
            # per-panel pacing: next page only after the current one has been shown for its time
            # (counted from when it really reached the panel) – no page is skipped when sending lags
            key = (self.since, self.phase, self.pos, self.message, len(units))
            if cur.get("key") != key:
                cur.update(key=key, i=0, t=now)
            elif now - cur["t"] >= durs[cur["i"] % len(units)]:
                cur["i"] = (cur["i"] + 1) % len(units)
                cur["t"] = now
            head, page = units[cur["i"] % len(units)]
            return {**page, "heading": head["text"] if head else None, "heading_font": head["font"] if head else None}
        t = (now - self.since) % sum(durs)
        for (head, page), d in zip(units, durs):
            if t < d:
                return {**page, "heading": head["text"] if head else None,
                        "heading_font": head["font"] if head else None}
            t -= d
        head, page = units[-1]
        return {**page, "heading": head["text"] if head else None, "heading_font": head["font"] if head else None}

    def _spec(self, page: dict | None, line) -> dict:
        if page is None:
            return {"line": line, "text": ""}
        return {"line": line, "text": page["text"], "font": page["font"], "spacing": page["spacing"],
                "run": page.get("run", False), "heading": page.get("heading"), "heading_font": page.get("heading_font")}

    # ---- displays -----------------------------------------------------------
    def interior(self, now: float | None = None, two_line: bool = False) -> dict:
        s = self.settings["interior"]
        now = self.clock() if now is None else now
        ctx = self._context(now)
        st = self.route.stops
        ln = self.route.line if s.get("line") == "always" else None   # line number on every page
        if self.phase == "idle" or not st:
            return self._show(self._pages("destination", ctx, s), now, two_line, self.route.line)
        if self.message and s.get("message_mode") == "override":
            return self._show(self._pages("message", ctx, s), now, two_line, ln)
        if self.phase == "ended":
            return self._show(self._pages("final", ctx, s), now, two_line, ln)
        if self.phase == "at_stop":
            if s.get("at_stop") == "stop_name":
                return self._show(self._pages("stop", ctx, s), now, two_line, ln)
            return self._show(self._pages("destination", ctx, s), now, two_line, self.route.line)
        pages = []
        for it in s.get("items") or ["next"]:
            if it == "zone" and not ctx["zone"]:
                continue
            pages += self._pages(it, ctx, s)
        if self.message:
            pages += self._pages("message", ctx, s)
        return self._show(pages, now, two_line, ln)

    def via_stops(self) -> list:
        """Via stops (marked via: true) still ahead, without the terminal."""
        st = self.route.stops
        if not st or self.phase == "ended":
            return []
        start = self.pos + (1 if self.phase in ("at_stop", "idle") else 0)
        return [x for x in st[start:-1] if x.via]

    def _ext_settings(self) -> dict:
        s = self.settings.get("exterior") or {}
        return {"seconds": s.get("seconds", 4)}     # no default font: the panel formats the text

    def _seq_for(self, base: str, role: str) -> str:
        seqs = self.settings["interior"].get("sequences") or {}
        name = f"{base}_{role}"
        return name if seqs.get(name) or DEFAULT_SEQUENCES.get(name) else base

    def exterior(self, now: float | None = None, two_line: bool = False, role: str = "front") -> dict:
        now = self.clock() if now is None else now
        ctx = self._context(now)
        pages = self._pages(self._seq_for("exterior", role), ctx, self._ext_settings())
        return self._show(pages, now, two_line, self.route.line)

    def via_pages(self, role: str = "side", now: float | None = None) -> list[dict]:
        """Pages of the via-stop line of an exterior panel: a page with {via_stop} is repeated for every
        remaining via stop; the panel itself flips through them."""
        now = self.clock() if now is None else now
        ctx = self._context(now)
        seqs = self.settings["interior"].get("sequences") or {}
        name = self._seq_for("via", role)
        out = []
        for p in seqs.get(name) or DEFAULT_SEQUENCES.get(name, []):
            stops = self.via_stops() if "{via_stop}" in str(p.get("text", "")) else [None]
            for stop in stops:
                c = {**ctx, "via_stop": stop.name if stop else ""}
                if not self._ok(p, c):
                    continue
                try:
                    text = re.sub(r"\{(icon:[^{}]*)\}", r"{{\1}}", str(p.get("text", ""))).format(**c)
                except (KeyError, IndexError, ValueError):
                    text = str(p.get("text", ""))
                if text.strip():
                    out.append({"text": text, "font": p.get("font"), "spacing": p.get("spacing"),
                                "seconds": float(p.get("seconds") or 3), "code": stop.code if stop else ""})
        return out

    def display(self, role: str, now: float | None = None, two_line: bool = False, cursor: dict | None = None,
                measure=_UNSET) -> dict:
        """cursor: per-panel dict for page pacing (see _pick); None = pages purely by the clock.
        measure: the panel's running-text timing (make_measure); None = the panel does not run texts."""
        self._cursor = cursor
        self._measure = measure
        try:
            return self.interior(now, two_line) if role == "interior" else self.exterior(now, two_line, role)
        finally:
            self._cursor = None
            self._measure = _UNSET
