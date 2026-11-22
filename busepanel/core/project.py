"""Human-readable panel project (YAML) <-> panel image."""
from __future__ import annotations

import copy
import os
import re

import yaml

from . import image as I
from . import text as T
from .fonts import FontSet, LiteralRows
from ..panels.profile import geometry_for
from .render import text_bitmap

SECTIONS = {"lines": "LIN", "destinations": "CIL", "destinations4": "CIL4",
            "stops": "ZST", "messages": "MIM"}
SECTION_NAMES = {v: k for k, v in SECTIONS.items()}
DEFAULT_EXTRA = {"0x01": "00", "0x20": "00 00 00 00 00 00 00 00 00 00", "0x3A": "20 42 75 0D 00 FF"}
EXTRA_RANGES = {"0x01": (0x01, 0x02), "0x20": (0x20, 0x2A), "0x3A": (0x3A, 0x40)}


# ------------------------------------------------------------------ YAML style
class _Dumper(yaml.SafeDumper):
    pass


def _literal(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style="|")


def _str(dumper, data):
    style = None
    if re.fullmatch(r"\d+", data) or data == "" or data.strip() != data or ":" in data or "#" in data:
        style = '"'
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


class Flow(dict):
    """dict written on one line"""


class FlowList(list):
    """list written on one line"""


def _flow_dict(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items(), flow_style=True)


def _flow_list(dumper, data):
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)


_Dumper.add_representer(LiteralRows, _literal)
_Dumper.add_representer(str, _str)
_Dumper.add_representer(Flow, _flow_dict)
_Dumper.add_representer(FlowList, _flow_list)


def styled(data: dict) -> dict:
    """Apply compact styles to a project dict before dumping."""
    out = {}
    for k, v in data.items():
        if k in SECTIONS and isinstance(v, dict):
            v = {kk: Flow(vv) if isinstance(vv, dict) else vv for kk, vv in v.items()}
        elif k == "config" and isinstance(v, dict):
            v = dict(v)
            if isinstance(v.get("commands"), list):
                v["commands"] = FlowList(v["commands"])
            if isinstance(v.get("defaults"), dict):
                v["defaults"] = Flow(v["defaults"])
        elif k == "panel" and isinstance(v, dict):
            v = Flow(v)
        out[k] = v
    return out


def dump_yaml(data, path=None) -> str:
    s = yaml.dump(data, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=200)
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(s)
    return s


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _key(k) -> str:
    """Record key from YAML ('010', '010 (2)' for duplicates)."""
    return re.sub(r"\s*\(\d+\)$", "", str(k))


# ------------------------------------------------------------------ project
class Project:
    def __init__(self, data: dict, fonts: FontSet, base_dir: str = "."):
        self.data = data
        self.fonts = fonts
        self.base_dir = base_dir

    # ---- file I/O -----------------------------------------------------
    @staticmethod
    def load(path) -> "Project":
        data = load_yaml(path)
        base = os.path.dirname(os.path.abspath(path))
        f = data.get("fonts")
        if isinstance(f, str):
            fonts = FontSet.from_yaml(load_yaml(os.path.join(base, f)))
        elif isinstance(f, dict):
            fonts = FontSet.from_yaml(f)
        else:
            raise ValueError("projekt neobsahuje fonty (klíč 'fonts')")
        return Project(data, fonts, base)

    def save(self, path, fonts_file: str | None = "fonts.yaml"):
        data = dict(self.data)
        base = os.path.dirname(os.path.abspath(path))
        os.makedirs(base, exist_ok=True)
        if fonts_file:
            data["fonts"] = fonts_file
            dump_yaml(self.fonts.to_yaml(), os.path.join(base, fonts_file))
        else:
            data["fonts"] = self.fonts.to_yaml()
        dump_yaml(styled(data), path)

    # ---- image -> project --------------------------------------------
    @staticmethod
    def from_image(im: I.Image, panel_type="BS120", ibis_address=10) -> "Project":
        fonts = FontSet.from_image(im)
        h = im.header
        cfg = {
            "version": im.version,
            "columns": im.columns,
            "commands": im.commands,
            "defaults": {"font": h[6] - 0xE0, "spacing": h[7], "interval": h[8] / 10},
            "tariff_digits": h[0x1D],
            "cycle_delay": h[0x1E],
            "panel_kind": h[0x1F],
        }
        extra = {k: bytes(h[a:b]).hex(" ").upper() for k, (a, b) in EXTRA_RANGES.items()}
        extra = {k: v for k, v in extra.items() if v != DEFAULT_EXTRA[k]}
        if extra:
            cfg["header_extra"] = extra
        if im.title_version != "1.00":
            cfg["title_version"] = im.title_version
        data = {"panel": {"type": panel_type, "ibis_address": ibis_address}, "config": cfg,
                "fonts": None}
        for sec, recs in im.records.items():
            d, seen = {}, {}
            for key, body in recs:
                k = key.decode("latin1")
                seen[k] = seen.get(k, 0) + 1
                if seen[k] > 1:
                    k = f"{k} ({seen[k]})"
                d[k] = T.decode(body, fonts)
            data[SECTION_NAMES[sec]] = d
        if im.raw_sections:
            data["raw_sections"] = {k: v.hex(" ").upper() for k, v in im.raw_sections.items()}
        return Project(data, fonts)

    @staticmethod
    def from_hex(path, **kw) -> "Project":
        return Project.from_image(I.load_hex(path), **kw)

    # ---- project -> image --------------------------------------------
    @property
    def geometry(self):
        p = self.data.get("panel") or {}
        return geometry_for(p.get("type"), (self.data.get("config") or {}).get("columns"))

    def defaults(self):
        d = (self.data.get("config") or {}).get("defaults") or {}
        return int(d.get("font", 3)), int(d.get("spacing", 1))

    def encode_value(self, value) -> tuple[bytes, list[str]]:
        """Encode one record, resolving `align` with the panel geometry."""
        if isinstance(value, dict) and value.get("align") and value.get("x") is None:
            f0, s0 = self.defaults()
            probe, problems = T.encode_item({**value, "align": None}, self.fonts)
            cols, _, _ = text_bitmap(probe, self.fonts, f0, s0)
            x = self.geometry.x_for_align(value["align"], len(cols))
            b, p2 = T.encode_item(value, self.fonts, x)
            return b, problems
        return T.encode(value, self.fonts)

    def to_image(self) -> tuple[I.Image, list[str]]:
        cfg = self.data.get("config") or {}
        h = bytearray(I.HEADER_SIZE)
        h[0] = I.MAGIC_FW2
        im = I.Image(header=h, title_version=str(cfg.get("title_version", "1.00")))
        for k, v in {**DEFAULT_EXTRA, **(cfg.get("header_extra") or {})}.items():
            a, b = EXTRA_RANGES[k]
            h[a:b] = bytes.fromhex(v)
        im.columns = int(cfg.get("columns", 135))
        im.commands = cfg.get("commands", ["l", "z", "a", "aV"])
        d = cfg.get("defaults") or {}
        h[6] = 0xE0 + int(d.get("font", 3))
        h[7] = int(d.get("spacing", 1))
        h[8] = int(round(float(d.get("interval", 1.0)) * 10))
        h[0x1D] = int(cfg.get("tariff_digits", 1))
        h[0x1E] = int(cfg.get("cycle_delay", 10))
        h[0x1F] = int(cfg.get("panel_kind", 1))
        im.version = str(cfg.get("version", ""))
        self.fonts.to_image(im)
        problems = []
        for name, sec in SECTIONS.items():
            recs = self.data.get(name)
            if recs is None:
                continue
            out = []
            for k, v in recs.items():
                key = _key(k)
                if len(key) != I.KEYLEN[sec]:
                    problems.append(f"{name} {key}: klíč musí mít {I.KEYLEN[sec]} znaky")
                b, p = self.encode_value(v)
                problems += [f"{name} {key}: {x}" for x in p]
                out.append((key.encode("latin1"), b))
            im.records[sec] = out
        for k, v in (self.data.get("raw_sections") or {}).items():
            im.raw_sections[k] = bytes.fromhex(v)
        return im, problems

    def to_hex(self) -> tuple[str, list[str]]:
        im, problems = self.to_image()
        return I.blocks_to_hex(I.build(im)), problems

    # ---- checks ---------------------------------------------------------
    def check(self) -> list[dict]:
        """Validation report: missing glyphs, overflow, duplicate keys."""
        out = []
        g = self.geometry
        f0, s0 = self.defaults()
        for name, sec in SECTIONS.items():
            for k, v in (self.data.get(name) or {}).items():
                try:
                    b, problems = self.encode_value(v)
                except Exception as e:      # noqa: BLE001 – report everything to the user
                    out.append({"section": name, "key": str(k), "level": "error", "msg": str(e)})
                    continue
                for p in problems:
                    out.append({"section": name, "key": str(k), "level": "error", "msg": p})
                if name in ("destinations", "destinations4", "stops", "messages"):
                    cols, _, info = text_bitmap(b, self.fonts, f0, s0)
                    if not info["run"]:
                        start, lo, hi = g.dest_span(len(cols), info["x"])
                        if start + len(cols) - 1 > hi or start < lo:
                            out.append({"section": name, "key": str(k), "level": "warning",
                                        "msg": f"text ({len(cols)} sl.) se nevejde, panel ho ořízne "
                                               f"– zkraťte ho nebo zapněte běžící text"})
                if " (" in str(k):
                    out.append({"section": name, "key": str(k), "level": "warning",
                                "msg": "duplicitní číslo (panel použije první výskyt)"})
        return out

    def copy(self) -> "Project":
        return Project(copy.deepcopy(self.data), self.fonts, self.base_dir)
