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
import time

THIN = 0          # headings and extra lines; other pages take the font from the role settings

DEFAULT_SEQUENCES = {
    "next": [
        {"text": "Příští zastávka / Next stop", "font": THIN, "role": "heading"},
        {"text": "{name}{icons}"},
        {"text": "zastávka na znamení / request stop", "font": THIN, "when": "request"},
    ],
    "destination": [
        {"text": "Směr / Direction", "font": THIN, "role": "heading"},
        {"text": "{destination}"},
        {"text": "Přes {via}", "font": THIN, "when": "via"},
    ],
    "exterior": [{"text": "{destination}"}],
    "zone": [{"text": "Tarifní zóna / Zone", "font": THIN, "role": "heading"},
             {"text": "{zone} {zone_name}"}],
    "time": [{"text": "{time}"}],
    "message": [{"text": "{message}"}],
    "final": [{"text": "Konečná zastávka - prosíme, vystupte"}],
    "stop": [{"text": "{name}{icons}"}],
}

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
    "exterior": {"font": 3, "spacing": 1, "seconds": 4},
}

# Ready-made display formats ("Formát" in the UI): a preset replaces the interior settings.
DPP_SEQUENCES = {
    # Standardy DPP – vnitřní informační panel s číslem linky (linka je zobrazena stále)
    "next": [   # mimo zastávku: příští zastávka + piktogramy přestupu; na znamení běžícím textem
        {"text": "{name}{icons}", "when": "not_request"},
        {"text": "{name}{icons} - na znamení / request stop {icon:znameni}", "when": "request", "run": True},
    ],
    "destination": [{"text": "{icon:sipka} {DESTINATION}{dest_icons}"}],   # zastávka: šipka + cíl spoje
    "exterior": [{"text": "{destination}"}],
    "zone": [{"text": "{zone} {zone_name}"}],
    "time": [{"text": "{time}"}],
    "message": [{"text": "{message}", "run": True}],
    "final": [{"text": "Konečná zastávka, prosíme vystupte / Final stop, please leave the tram.", "run": True}],
    "stop": [{"text": "{name}{icons}"}],
}

PRESETS = {
    "zakladni": {"title": "Základní (příští zastávka / čas / zóna)",
                 "interior": {k: v for k, v in DEFAULT_SETTINGS["interior"].items()} | {"line": "at_stop"}},
    "dpp": {"title": "DPP – linka stále, příští zastávka / šipka + cíl / konečná",
            "interior": {"items": ["next"], "seconds": 4, "time_format": "%H:%M", "at_stop": "line_dest",
                         "message_mode": "override", "line": "always", "font": 3, "spacing": 1,
                         "sequences": DPP_SEQUENCES}},
}

CONDITIONS = {"always", "request", "not_request", "via", "zone"}


def preset_settings(name: str) -> dict:
    """Interior settings of a preset (deep copy, tagged with the preset name)."""
    if name not in PRESETS:
        raise ValueError(f"neznámý formát {name!r}")
    return {**copy.deepcopy(PRESETS[name]["interior"]), "format": name}


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
        self.measure = measure        # measure(text, font, spacing, role) -> seconds to scroll once (or 0)
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
                "line": self.route.line, "destination": self.route.destination, "events": self.events[-10:]}

    # ---- page composition -----------------------------------------------------
    @staticmethod
    def _icons(stop) -> str:
        """LED icons of a stop (PID style). metro-A … metro-D become the stylised letter(s) [AB],
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
            "dest_icons": self._icons(last) if last else "", "via": self.route.via, "line": self.route.line,
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
            if not self._ok(p, ctx):
                continue
            try:
                # {icon:name} is panel markup, not a format field -> escape it before format()
                text = re.sub(r"\{(icon:[^{}]*)\}", r"{{\1}}", str(p.get("text", ""))).format(**ctx)
            except (KeyError, IndexError, ValueError):
                text = str(p.get("text", ""))
            out.append({"text": text,
                        "font": p.get("font", role_settings.get("font")),
                        "spacing": p.get("spacing", role_settings.get("spacing")),
                        "seconds": float(p.get("seconds") or role_settings.get("seconds") or 3),
                        "role": p.get("role", "text"), "run": bool(p.get("run"))})
        return out

    def _duration(self, page: dict, two_line: bool) -> float:
        d = page["seconds"]
        if self.measure:
            d = max(d, self.measure(page["text"], page["font"], page["spacing"], two_line,
                                    run=page.get("run", False)) or 0)
        return d

    def _pick(self, pages: list[dict], now: float, two_line: bool) -> dict | None:
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
        durs = [self._duration(u[1], two_line) for u in units]
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
            return self._spec(self._pick(self._pages("destination", ctx, s), now, two_line), self.route.line)
        if self.message and s.get("message_mode") == "override":
            return self._spec(self._pick(self._pages("message", ctx, s), now, two_line), ln)
        if self.phase == "ended":
            return self._spec(self._pick(self._pages("final", ctx, s), now, two_line), ln)
        if self.phase == "at_stop":
            if s.get("at_stop") == "stop_name":
                return self._spec(self._pick(self._pages("stop", ctx, s), now, two_line), ln)
            return self._spec(self._pick(self._pages("destination", ctx, s), now, two_line), self.route.line)
        pages = []
        for it in s.get("items") or ["next"]:
            if it == "zone" and not ctx["zone"]:
                continue
            pages += self._pages(it, ctx, s)
        if self.message:
            pages += self._pages("message", ctx, s)
        return self._spec(self._pick(pages, now, two_line), ln)

    def exterior(self, now: float | None = None, two_line: bool = False) -> dict:
        s = self.settings["exterior"]
        now = self.clock() if now is None else now
        ctx = self._context(now)
        return self._spec(self._pick(self._pages("exterior", ctx, s), now, two_line), self.route.line)

    def display(self, role: str, now: float | None = None, two_line: bool = False, cursor: dict | None = None) -> dict:
        """cursor: per-panel dict for page pacing (see _pick); None = pages purely by the clock."""
        self._cursor = cursor
        try:
            return self.interior(now, two_line) if role == "interior" else self.exterior(now, two_line)
        finally:
            self._cursor = None
