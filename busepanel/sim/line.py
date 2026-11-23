"""Line with directions and courses (kurzy) – the timetable model the BS100 works with.

A BS100 driver logs in with line + course (or service); the computer then runs the trips of that
course one after another, each in its direction (dBUSE: linka + směr 1–99 → trasa), and a trip may
cover only a part of the direction's route (short turns, depot runs, trips starting midway).

YAML (trasy/*.yaml):

  line: "607"
  messages: {odklon: …}                     # shared by all directions
  directions:                               # směry; ids are free ("A", "1", "tam" …)
    A:
      destination: Chlumec nad Cidlinou, žst.
      dest_code: 123                        # optional – gBUSE0 CIL record (exterior panels, control: code)
      stops: [{name: …, zone: …, via: true, code: "0042", minutes: 3}, …]   # via: true = "přes" stop
    B: {destination: …, stops: […]}
  courses:                                  # kurzy: trips in the order they are driven
    "1":
      - {direction: A, departure: "05:12"}
      - {direction: B, departure: "06:20"}
      - {direction: A, departure: "07:30", from: "Nádraží", to: 12}
      - {direction: B, departure: "08:40", to: "Náměstí", destination: "Centrum"}

from / to: stop name or 1-based order in the direction (default: whole route). A trip that ends early
gets the last served stop as destination unless it says destination: (and dest_code:); a trip
that only starts later keeps the direction's destination.
A file without directions is the old flat route (sim/route.py) = one direction "1" and no courses.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from .route import Route, Stop

_DIR_KEYS = ("destination", "dest_code", "dest_short")


@dataclass
class Trip:
    direction: str
    departure: str = ""
    start: str | int | None = None          # "from" in YAML
    end: str | int | None = None            # "to"
    overrides: dict = field(default_factory=dict)   # destination / dest_code / via of a shortened trip

    @staticmethod
    def from_dict(d) -> "Trip":
        if isinstance(d, (str, int)):
            d = {"direction": d}
        return Trip(str(d.get("direction", "")), str(d.get("departure", "") or ""), d.get("from"), d.get("to"),
                    {k: str(d[k]) for k in _DIR_KEYS if d.get(k) not in (None, "")})

    def to_dict(self) -> dict:
        d = {"direction": self.direction}
        if self.departure:
            d["departure"] = self.departure
        if self.start not in (None, ""):
            d["from"] = self.start
        if self.end not in (None, ""):
            d["to"] = self.end
        d.update(self.overrides)
        return d


@dataclass
class Line:
    line: str
    directions: dict = field(default_factory=dict)     # id -> Route (whole direction)
    courses: dict = field(default_factory=dict)        # course -> [Trip]
    messages: dict = field(default_factory=dict)
    flat: bool = False                                 # loaded from an old flat route file

    # ---- load / save --------------------------------------------------
    @staticmethod
    def is_line(d: dict) -> bool:
        return bool(d and d.get("directions"))

    @staticmethod
    def from_dict(d: dict) -> "Line":
        d = d or {}
        if not Line.is_line(d):
            r = Route.from_dict(d)
            return Line(r.line, {"1": r}, {}, dict(r.messages), flat=True)
        line = str(d.get("line", ""))
        msgs = dict(d.get("messages") or {})
        dirs = d["directions"]
        if isinstance(dirs, list):                     # also accept a list with id:
            dirs = {str(x.get("id", i + 1)): x for i, x in enumerate(dirs)}
        directions = {}
        for did, dd in dirs.items():
            r = Route.from_dict({**dd, "line": dd.get("line", line), "messages": {**msgs, **(dd.get("messages") or {})}})
            r.direction = str(did)
            directions[str(did)] = r
        courses = {}
        for cid, trips in (d.get("courses") or {}).items():
            if isinstance(trips, dict):                # {trips: […]} form
                trips = trips.get("trips") or []
            courses[str(cid)] = [Trip.from_dict(t) for t in trips or []]
        ln = Line(line, directions, courses, msgs)
        ln.validate()
        return ln

    @staticmethod
    def load(path) -> "Line":
        with open(path, encoding="utf-8") as f:
            return Line.from_dict(yaml.safe_load(f) or {})

    def to_dict(self, full: bool = False) -> dict:
        """YAML form; an old flat file stays flat unless full=True (the UI always edits the full form)."""
        if self.flat and not full:
            r = self.directions[next(iter(self.directions))]
            d = r.to_dict()
            for k in ("direction", "course", "trip", "departure"):
                d.pop(k, None)
            return d
        out = {"line": self.line}
        if self.messages:
            out["messages"] = dict(self.messages)
        out["directions"] = {}
        for did, r in self.directions.items():
            d = r.to_dict()
            for k in ("line", "direction", "course", "trip", "departure"):
                d.pop(k, None)
            if d.get("messages") == self.messages or not d.get("messages"):
                d.pop("messages", None)
            out["directions"][did] = d
        if self.courses:
            out["courses"] = {c: [t.to_dict() for t in trips] for c, trips in self.courses.items()}
        return out

    def save(self, path):
        from ..core.project import dump_yaml
        dump_yaml(self.to_dict(), path)

    def validate(self):
        for cid, trips in self.courses.items():
            for i, t in enumerate(trips):
                if t.direction not in self.directions:
                    raise ValueError(f"kurz {cid}, spoj {i + 1}: neznámý směr {t.direction!r}")
                self._span(self.directions[t.direction], t)

    # ---- trips ----------------------------------------------------------
    @staticmethod
    def _index(route: Route, ref, default: int) -> int:
        if ref in (None, ""):
            return default
        if isinstance(ref, int) or str(ref).isdigit():
            i = int(ref) - 1
            if not 0 <= i < len(route.stops):
                raise ValueError(f"zastávka č. {ref} není ve směru {route.direction} ({len(route.stops)} zastávek)")
            return i
        for i, s in enumerate(route.stops):
            if s.name == str(ref):
                return i
        raise ValueError(f"zastávka {ref!r} není ve směru {route.direction}")

    def _span(self, route: Route, trip: Trip) -> tuple[int, int]:
        a = self._index(route, trip.start, 0)
        b = self._index(route, trip.end, len(route.stops) - 1)
        if b < a:
            raise ValueError(f"spoj směru {route.direction}: 'from' je za 'to'")
        return a, b

    def trip(self, course: str, index: int) -> Trip:
        trips = self.courses.get(str(course))
        if not trips:
            raise ValueError(f"kurz {course} neexistuje")
        if not 0 <= index < len(trips):
            raise ValueError(f"kurz {course} nemá spoj č. {index + 1}")
        return trips[index]

    def route(self, direction: str | None = None, course: str | None = None, index: int = 0) -> Route:
        """Flat route for the simulation: one trip of a course, or a whole direction."""
        if course not in (None, ""):
            t = self.trip(course, index)
            r = self._trip_route(t)
            r.course, r.trip = str(course), index
            return r
        did = str(direction) if direction not in (None, "") else next(iter(self.directions), None)
        if did not in self.directions:
            raise ValueError(f"směr {direction} neexistuje")
        return Route.from_dict(self.directions[did].to_dict())

    def _trip_route(self, t: Trip) -> Route:
        base = self.directions[t.direction]
        a, b = self._span(base, t)
        r = Route.from_dict(base.to_dict())
        r.stops = r.stops[a:b + 1]
        if b < len(base.stops) - 1:
            # ends early: own destination (default the last served stop), not the via / code of the full route
            r.destination = r.stops[-1].name
            r.dest_code = ""
            r.dest_short = r.stops[-1].short
        if a > 0:
            m0 = r.stops[0].minutes
            if m0:
                for s in r.stops:
                    if s.minutes is not None:
                        s.minutes = s.minutes - m0
        for k in _DIR_KEYS:
            if k in t.overrides:
                setattr(r, k, t.overrides[k])
        r.departure = t.departure
        return r

    def is_full(self, course: str, index: int) -> bool:
        t = self.trip(course, index)
        a, b = self._span(self.directions[t.direction], t)
        return (a, b) == (0, len(self.directions[t.direction].stops) - 1)

    def next_trip(self, course: str, index: int) -> int | None:
        trips = self.courses.get(str(course)) or []
        return index + 1 if index + 1 < len(trips) else None

    def summary(self) -> dict:
        """For the UI: directions and the trips of every course."""
        dirs = [{"id": d, "destination": r.destination, "stops": len(r.stops),
                 "first": r.stops[0].name if r.stops else "", "last": r.stops[-1].name if r.stops else ""}
                for d, r in self.directions.items()]
        courses = {}
        for c, trips in self.courses.items():
            rows = []
            for i, t in enumerate(trips):
                r = self._trip_route(t)
                rows.append({"index": i, "direction": t.direction, "departure": t.departure,
                             "from": r.stops[0].name if r.stops else "", "to": r.stops[-1].name if r.stops else "",
                             "destination": r.destination, "stops": len(r.stops),
                             "full": self.is_full(c, i)})
            courses[c] = rows
        return {"line": self.line, "flat": self.flat, "directions": dirs, "courses": courses}

    def put_route(self, route: Route, direction: str | None = None, course: str | None = None, index: int = 0):
        """Write a route edited in the UI back. A whole-direction route replaces the direction; a
        shortened trip cannot be edited this way (its stops belong to the direction)."""
        if self.flat:
            route.direction = next(iter(self.directions))
            self.directions = {route.direction: route}
            self.line, self.messages = route.line, dict(route.messages)
            return
        if course not in (None, ""):
            if not self.is_full(course, index):
                raise ValueError("zkrácený spoj – zastávky upravte ve směru (nebo v souboru linky)")
            direction = self.trip(course, index).direction
        did = str(direction or route.direction or next(iter(self.directions)))
        if did not in self.directions:
            raise ValueError(f"směr {did} neexistuje")
        new = Route.from_dict(route.to_dict())
        for k in ("course", "departure"):
            setattr(new, k, "")
        new.trip = None
        new.direction = did
        self.directions[did] = new
        self.messages = dict(route.messages)
        self.validate()


__all__ = ["Line", "Trip", "Stop"]
