"""Route of a line for the simulation – human-readable YAML (or CSV import).

Flat route = one trip. A line with directions and courses (kurzy) is in sim/line.py.

line: "7"
destination: Sídliště Dukla
dest_code: 123                 # optional, gBUSE0 CIL record for exterior panels with control: code
dest_short: Dukla              # optional shortened destination – used where the full one does not fit
                               # "přes" = the stops marked via: true (old files: via: "A - B" is converted)
stops:
  - {name: Hlavní nádraží, zone: P, icons: [vlak, bus], request: false}
  - {name: Masarykovo nám., zone: P, minutes: 3, via: true, code: "0042"}
  - {name: "Lázně Bohdaneč, aut.nádr.", short: "L. Bohdaneč"}   # short: where the name does not fit
messages:
  odklon: Spoj jede odklonem přes ul. Pražskou
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

import yaml


def _minutes(v):
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


@dataclass
class Stop:
    name: str
    zone: str = ""
    icons: list = field(default_factory=list)
    request: bool = False          # stop on request ("na znamení")
    note: str = ""
    zone_name: str = ""            # e.g. "Pardubice" for IREDO zone 600
    minutes: float | None = None   # optional: minutes from the start of the trip (LCD "3 min")
    via: bool = False              # announced on the exterior panels ("nácestná", dBUSE attribute K)
    code: str = ""                 # optional stop code of an exterior database (gBUSE0 DRU, 4 digits)
    short: str = ""                # optional shortened name – used where the full name does not fit

    def to_dict(self) -> dict:
        d = {"name": self.name}
        if self.zone:
            d["zone"] = self.zone
        if self.zone_name:
            d["zone_name"] = self.zone_name
        if self.icons:
            d["icons"] = list(self.icons)
        if self.request:
            d["request"] = True
        if self.note:
            d["note"] = self.note
        if self.minutes is not None:
            d["minutes"] = int(self.minutes) if float(self.minutes).is_integer() else self.minutes
        if self.via:
            d["via"] = True
        if self.code:
            d["code"] = self.code
        if self.short:
            d["short"] = self.short
        return d

    @staticmethod
    def from_dict(s) -> "Stop":
        if isinstance(s, str):
            s = {"name": s}
        icons = s.get("icons") or []
        if isinstance(icons, str):
            icons = [i.strip() for i in icons.replace(",", " ").split() if i.strip()]
        return Stop(str(s["name"]), str(s.get("zone", "") or ""), icons, bool(s.get("request")),
                    str(s.get("note", "") or ""), str(s.get("zone_name", "") or ""), _minutes(s.get("minutes")),
                    bool(s.get("via")), str(s.get("code", "") or ""), str(s.get("short", "") or ""))


def mark_via(stops: list, text: str):
    """Old free-text "přes A - B" -> mark the stops whose names match the parts (exact, then prefix,
    then substring; the first and last stop are never via stops)."""
    import re
    inner = stops[1:-1]
    for part in [x.strip() for x in re.split(r"\s[-–]\s|,\s*|/", text) if x.strip()]:
        low = part.lower()
        for test in (lambda n: n == low, lambda n: n.startswith(low), lambda n: low in n):
            hit = next((s for s in inner if test(s.name.lower()) and not s.via), None)
            if hit:
                hit.via = True
                break


@dataclass
class Route:
    line: str
    destination: str
    stops: list = field(default_factory=list)
    messages: dict = field(default_factory=dict)
    dest_code: str = ""            # optional destination code of an exterior database (gBUSE0 CIL)
    # set when the route is one trip of a line (sim/line.py); informative for the panels / UI
    direction: str = ""
    course: str = ""
    trip: int | None = None        # index of the trip in the course
    departure: str = ""            # "HH:MM"
    dest_short: str = ""           # optional shortened destination (where the full one does not fit)

    @staticmethod
    def from_dict(d: dict) -> "Route":
        stops = [Stop.from_dict(s) for s in d.get("stops") or []]
        if d.get("via") and not any(s.via for s in stops):
            mark_via(stops, str(d["via"]))          # BUSE Studio ≤ 0.3: "přes" was a free text
        trip = d.get("trip")
        return Route(str(d.get("line", "")), str(d.get("destination", "") or (stops[-1].name if stops else "")),
                     stops, dict(d.get("messages") or {}),
                     str(d.get("dest_code", "") or ""), str(d.get("direction", "") or ""),
                     str(d.get("course", "") or ""), int(trip) if trip is not None else None,
                     str(d.get("departure", "") or ""), str(d.get("dest_short", "") or ""))

    def to_dict(self) -> dict:
        d = {"line": self.line, "destination": self.destination}
        if self.dest_code:
            d["dest_code"] = self.dest_code
        if self.dest_short:
            d["dest_short"] = self.dest_short
        d["stops"] = [s.to_dict() for s in self.stops]
        d["messages"] = dict(self.messages)
        for k in ("direction", "course", "departure"):
            if getattr(self, k):
                d[k] = getattr(self, k)
        if self.trip is not None:
            d["trip"] = self.trip
        return d

    @staticmethod
    def load(path) -> "Route":
        with open(path, encoding="utf-8") as f:
            return Route.from_dict(yaml.safe_load(f) or {})

    def save(self, path):
        from ..core.project import dump_yaml
        dump_yaml(self.to_dict(), path)

    @staticmethod
    def from_csv(text: str, line: str = "", destination: str = "") -> "Route":
        """CSV: poradi;zastavka;pasmo;ikony;na_znameni[;minuty]"""
        try:
            dialect = csv.Sniffer().sniff(text[:2000], delimiters=";,\t")
        except csv.Error:
            dialect = csv.excel
        rows = []
        for r in csv.DictReader(io.StringIO(text), dialect=dialect):
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            name = r.get("zastavka") or r.get("zastávka") or r.get("name") or r.get("stop")
            if not name:
                continue
            order = r.get("poradi") or r.get("pořadí") or str(len(rows))
            rows.append((int(order) if order.isdigit() else len(rows), {
                "name": name, "zone": r.get("pasmo") or r.get("pásmo") or r.get("zone") or "",
                "icons": r.get("ikony") or r.get("icons") or "",
                "request": (r.get("na_znameni") or r.get("request") or "").lower() in ("1", "ano", "a", "x", "true", "yes"),
                "via": (r.get("pres") or r.get("přes") or r.get("via") or "").lower() in ("1", "ano", "a", "x", "true", "yes"),
                "code": r.get("kod") or r.get("kód") or r.get("code") or "",
                "short": r.get("zkratka") or r.get("short") or "",
                "minutes": r.get("minuty") or r.get("minutes") or r.get("min")}))
        rows.sort(key=lambda x: x[0])
        return Route.from_dict({"line": line, "destination": destination, "stops": [r for _, r in rows]})
