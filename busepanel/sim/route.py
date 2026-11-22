"""Route of a line for the simulation – human-readable YAML (or CSV import).

line: "7"
destination: Sídliště Dukla
stops:
  - {name: Hlavní nádraží, zone: P, icons: [vlak, bus], request: false}
  - {name: Masarykovo nám., zone: P, minutes: 3}
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
        return d


@dataclass
class Route:
    line: str
    destination: str
    stops: list = field(default_factory=list)
    messages: dict = field(default_factory=dict)
    via: str = ""                  # optional "přes …" for exterior panels

    @staticmethod
    def from_dict(d: dict) -> "Route":
        stops = []
        for s in d.get("stops") or []:
            if isinstance(s, str):
                s = {"name": s}
            icons = s.get("icons") or []
            if isinstance(icons, str):
                icons = [i.strip() for i in icons.replace(",", " ").split() if i.strip()]
            stops.append(Stop(str(s["name"]), str(s.get("zone", "") or ""), icons, bool(s.get("request")),
                              str(s.get("note", "") or ""), str(s.get("zone_name", "") or ""),
                              _minutes(s.get("minutes"))))
        return Route(str(d.get("line", "")), str(d.get("destination", "") or (stops[-1].name if stops else "")),
                     stops, dict(d.get("messages") or {}), str(d.get("via", "") or ""))

    def to_dict(self) -> dict:
        d = {"line": self.line, "destination": self.destination}
        if self.via:
            d["via"] = self.via
        d["stops"] = [s.to_dict() for s in self.stops]
        d["messages"] = dict(self.messages)
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
                "minutes": r.get("minuty") or r.get("minutes") or r.get("min")}))
        rows.sort(key=lambda x: x[0])
        return Route.from_dict({"line": line, "destination": destination, "stops": [r for _, r in rows]})
