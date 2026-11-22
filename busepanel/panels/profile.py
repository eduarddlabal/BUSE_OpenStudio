"""Panel profiles (geometry + capabilities). BS120 values were measured on a real panel:

* 135 columns × 8 rows, line number field ~ columns 0–23
* destination without ESC s: centred, uses columns 24–134
* destination with ESC s n: drawn from absolute column n, clipped to 0–111
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

_FILE = os.path.join(os.path.dirname(__file__), "profiles.yaml")


@dataclass(frozen=True)
class Geometry:
    name: str = "BS120"
    columns: int = 135
    rows: int = 8
    line_left: int = 0
    line_right: int = 23
    dest_left: int = 24
    dest_right: int = 134          # centred / running text
    positioned_right: int = 111    # last visible column for ESC s texts
    scroll_ms: int = 20            # running text: milliseconds per column (preview speed)

    @property
    def dest_width(self) -> int:
        return self.dest_right - self.dest_left + 1

    def x_for_align(self, align: str | None, width: int) -> int | None:
        """Start column (ESC s) for an alignment, None = let the panel centre it."""
        if align in (None, "center", "centre", "stred", "střed"):
            return None
        if align in ("left", "vlevo"):
            return self.dest_left
        if align in ("right", "vpravo"):
            return max(self.dest_left, self.positioned_right + 1 - width)
        raise ValueError(f"neznámé zarovnání {align!r} (center/left/right)")

    def dest_span(self, width: int, x: int | None) -> tuple[int, int, int]:
        """(start column of text, first visible column, last visible column)."""
        if x is None:
            start = self.dest_left + (self.dest_width - width) // 2
            return max(start, self.dest_left), self.dest_left, self.dest_right
        return x, self.dest_left, self.positioned_right


@dataclass(frozen=True)
class PanelProfile:
    name: str
    title: str
    verified: bool
    geometry: Geometry
    data_format: str = "gbuse1"
    service_baud: int = 9600
    free_text: bool = False
    modes: dict = field(default_factory=dict)
    commands: tuple = ()

    def mode(self, key: str) -> str | None:
        return self.modes.get(key)

    def to_dict(self) -> dict:
        return {"name": self.name, "title": self.title, "verified": self.verified,
                "geometry": self.geometry.__dict__, "data_format": self.data_format,
                "free_text": self.free_text, "modes": dict(self.modes), "commands": list(self.commands)}


def _load() -> dict[str, PanelProfile]:
    with open(_FILE, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    out = {}
    for name, d in raw.items():
        g = Geometry(name=name, columns=d["columns"], rows=d.get("rows", 8),
                     line_left=d["line"][0], line_right=d["line"][1],
                     dest_left=d["dest"][0], dest_right=d["dest"][1],
                     positioned_right=d.get("positioned_right", d["dest"][1]),
                     scroll_ms=int(d.get("scroll_ms", 20)))
        out[name] = PanelProfile(name=name, title=d.get("title", name), verified=bool(d.get("verified")),
                                 geometry=g, data_format=d.get("data_format", "gbuse1"),
                                 service_baud=d.get("service_baud", 9600), free_text=bool(d.get("free_text")),
                                 modes=d.get("modes") or {}, commands=tuple(d.get("commands") or ()))
    return out


PROFILES = _load()


def profile(name: str | None) -> PanelProfile:
    key = (name or "BS120").upper().replace(" ", "")
    return PROFILES.get(key, PROFILES["BS120"])


def geometry_for(panel_type: str | None, columns: int | None = None) -> Geometry:
    g = profile(panel_type).geometry
    if columns and columns != g.columns:
        # unknown panel width: keep the proportions as a best guess
        g = Geometry(name=g.name, columns=columns, rows=g.rows, line_left=g.line_left, line_right=g.line_right,
                     dest_left=g.dest_left, dest_right=columns - 1,
                     positioned_right=min(g.positioned_right, columns - 1), scroll_ms=g.scroll_ms)
    return g
