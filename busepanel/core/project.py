"""Human-readable panel project (YAML) <-> panel image (both gBUSE1 and gBUSE0)."""
from __future__ import annotations

import copy
import os
import re

import yaml

from . import image as I
from . import text as T
from .fonts import CodeTable, FontSet, LiteralRows, default_codetable
from ..panels.profile import geometry_for
from .render import text_bitmap

SECTIONS = {"lines": "LIN", "destinations": "CIL", "destinations4": "CIL4",
            "stops": "ZST", "messages": "MIM"}
SECTION_NAMES = {v: k for k, v in SECTIONS.items()}
SECTIONS_0 = {"lines": "LIN", "destinations": "CIL", "destinations4": "CIL4", "stops": "DRU"}
SECTION_NAMES_0 = {v: k for k, v in SECTIONS_0.items()}

DEFAULT_EXTRA = {"0x01": "00", "0x20": "00 00 00 00 00 00 00 00 00 00", "0x3A": "20 42 75 0D 00 FF"}
EXTRA_RANGES = {"0x01": (0x01, 0x02), "0x20": (0x20, 0x2A), "0x3A": (0x3A, 0x40)}
EXTRA_RANGES_FW1 = {"0x01": (0x01, 0x02), "0x10": (0x10, 0x1D), "0x2F": (0x2F, 0x40)}


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
        if k in (*SECTIONS, "supplementary", "equivalents") and isinstance(v, dict):
            v = {kk: Flow(vv) if isinstance(vv, dict) else vv for kk, vv in v.items()}
        elif k == "config" and isinstance(v, dict):
            v = dict(v)
            for list_k in ("commands", "properties"):
                if isinstance(v.get(list_k), list):
                    v[list_k] = FlowList(v[list_k])
            if isinstance(v.get("defaults"), dict):
                v["defaults"] = Flow(v["defaults"])
            if isinstance(v.get("fields"), dict):
                v["fields"] = {fk: Flow(fv) if isinstance(fv, dict) else fv for fk, fv in v["fields"].items()}
            for af_k in ("afc", "afd"):
                if isinstance(v.get(af_k), list):
                    v[af_k] = [FlowList(x) if isinstance(x, list) else x for x in v[af_k]]
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

    @property
    def format(self) -> str:
        fmt = (self.data.get("panel") or {}).get("format")
        return "gbuse0" if fmt == "gbuse0" else "gbuse1"

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

    # ---- binary / image -> project ------------------------------------
    @staticmethod
    def from_bytes(img: bytes, source: str = "", **kw) -> "Project":
        if not img:
            raise ValueError("prázdná data panelu")
        if img[0] in (0x57, 0x55):
            from ..gbuse0.database import parse as parse0
            return Project.from_gbuse0(parse0(img, source=source), **kw)
        return Project.from_image(I.parse(img), **kw)

    @staticmethod
    def from_image(im: I.Image, panel_type="BS120", ibis_address=10) -> "Project":
        fonts = FontSet.from_image(im)
        h = im.header
        fw1 = im.magic == I.MAGIC_FW1
        if fw1:
            cfg = {
                "fw": "1.xx",
                "magic": I.MAGIC_FW1,
                "version": im.version,
                "columns": im.columns,
                "commands": im.commands,
                "defaults": {"font": h[5] - 0xE0, "spacing": h[6], "interval": h[7] / 10},
                "tariff_digits": 1,
                "cycle_delay": max(1, h[0x1D] // 2),
                "panel_kind": h[0x1E],
            }
            extra = {k: bytes(h[a:b]).hex(" ").upper() for k, (a, b) in EXTRA_RANGES_FW1.items()}
            cfg["header_extra"] = extra
        else:
            cfg = {
                "fw": "2.12" if im.cycles or im.supplementary else "2.06",
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
        if im.empty_ptrs:
            cfg["empty_ptrs"] = {k: v for k, v in im.empty_ptrs.items()}
        data = {"panel": {"format": "gbuse1", "type": panel_type, "ibis_address": ibis_address},
                "config": cfg, "fonts": None}
        for sec, recs in im.records.items():
            d, seen = {}, {}
            for key, body in recs:
                k = key.decode("latin1")
                seen[k] = seen.get(k, 0) + 1
                if seen[k] > 1:
                    k = f"{k} ({seen[k]})"
                d[k] = T.decode(body, fonts)
            data[SECTION_NAMES[sec]] = d
        if im.supplementary:
            data["supplementary"] = {str(k): T.decode(v, fonts) for k, v in sorted(im.supplementary.items())}
        if im.cycles:
            data["cycles"] = {str(k): v for k, v in sorted(im.cycles.items())}
        if im.kod:
            data["kod_table"] = im.kod.hex(" ").upper()
        if im.format_strings:
            data["format_strings"] = {str(k): T.decode(v, fonts) for k, v in sorted(im.format_strings.items())}
        if im.raw_sections:
            data["raw_sections"] = {k: v.hex(" ").upper() for k, v in im.raw_sections.items()}
        return Project(data, fonts)

    @staticmethod
    def from_gbuse0(db, panel_type: str = "BS210", ibis_address: int = 10) -> "Project":
        ct = CodeTable(db.codetable) if db.codetable else default_codetable()
        fonts = FontSet([db.fonts[k] for k in sorted(db.fonts)], ct, db.fonts_tail)
        from ..gbuse0 import mnemo as M0
        fields = {k: f.to_bottom_dict(db.rows) for k, f in db.fields.items()}
        fw = "1.6" if db.magic == 0x55 else ("3.18" if "kA" in db.command_list else ("3.08" if db.fw_3x else "2.15"))
        cfg = {
            "fw": fw,
            "magic": db.magic,
            "fw_3x": db.fw_3x,
            "version": db.version,
            "columns": db.columns,
            "rows": db.rows,
            "commands": db.command_list,
            "properties": db.property_flags,
            "prop_low": db.properties & 0x07,
            "fields": fields,
            "afc": [list(x) for x in db.afc],
            "afd": [list(x) for x in db.afd],
            "fnt_sep": db.fnt_sep,
            "title_fmt": db.title_fmt,
            "title_version": db.title_version,
            "raw_header": bytes(db.header).hex(" ").upper(),
        }
        if db.str_title:
            cfg["str_title"] = db.str_title
        if db.kod_title:
            cfg["kod_title"] = db.kod_title
        data = {
            "panel": {"format": "gbuse0", "type": panel_type, "ibis_address": ibis_address,
                      "panel_number": db.panel_number, "brightness": db.brightness},
            "config": cfg,
            "fonts": None,
        }
        for sec, recs in db.record_lists.items():
            d, seen = {}, {}
            for kbytes, body in recs:
                k = kbytes.decode("latin1")
                seen[k] = seen.get(k, 0) + 1
                if seen[k] > 1:
                    k = f"{k} ({seen[k]})"
                d[k] = M0.to_mnemo(body, ct)
            data[SECTION_NAMES_0[sec]] = d
        if db.kod:
            data["kod_table"] = db.kod.hex(" ").upper()
        if db.format_strings:
            data["format_strings"] = {str(k): M0.to_mnemo(v, ct) for k, v in sorted(db.format_strings.items())}
        return Project(data, fonts)

    @staticmethod
    def from_hex(path, **kw) -> "Project":
        with open(path, encoding="latin1") as f:
            img = I.hex_to_image(f.read())
        return Project.from_bytes(img, source=str(path), **kw)

    @staticmethod
    def from_bin(path, **kw) -> "Project":
        with open(path, "rb") as f:
            img = f.read()
        return Project.from_bytes(img, source=str(path), **kw)

    # ---- project -> image / database ----------------------------------
    @property
    def geometry(self):
        p = self.data.get("panel") or {}
        cfg = self.data.get("config") or {}
        if self.format == "gbuse0":
            from ..panels.profile import Geometry
            return Geometry(columns=int(cfg.get("columns", 140)), rows=int(cfg.get("rows", 19)),
                            line_left=0, line_right=27, dest_left=28, dest_right=int(cfg.get("columns", 140)) - 1)
        return geometry_for(p.get("type"), cfg.get("columns"))

    def defaults(self):
        d = (self.data.get("config") or {}).get("defaults") or {}
        return int(d.get("font", 3)), int(d.get("spacing", 1))

    def encode_value(self, value) -> tuple[bytes, list[str]]:
        """Encode one gBUSE1 record (or gBUSE0 MNEMO), resolving `align` with the panel geometry."""
        if self.format == "gbuse0":
            from ..gbuse0 import mnemo as M0
            if isinstance(value, dict):
                return M0.compose_record(value, self.to_gbuse0()[0], "dest", self.fonts.codetable)
            return M0.from_mnemo(str(value or ""), self.fonts.codetable)
        if isinstance(value, dict) and value.get("align") and value.get("x") is None:
            f0, s0 = self.defaults()
            probe, problems = T.encode_item({**value, "align": None}, self.fonts)
            cols, _, _ = text_bitmap(probe, self.fonts, f0, s0)
            x = self.geometry.x_for_align(value["align"], len(cols))
            b, _p2 = T.encode_item(value, self.fonts, x)
            return b, problems
        return T.encode(value, self.fonts)

    def to_image(self) -> tuple[I.Image, list[str]]:
        cfg = self.data.get("config") or {}
        magic = int(cfg.get("magic") or (I.MAGIC_FW1 if str(cfg.get("fw", "")).startswith("1") else I.MAGIC_FW2))
        h = bytearray(I.HEADER_SIZE)
        h[0] = magic
        im = I.Image(header=h, title_version=str(cfg.get("title_version", "1.00")),
                     empty_ptrs=dict(cfg.get("empty_ptrs") or {}))
        if magic == I.MAGIC_FW1:
            for k, v in (cfg.get("header_extra") or {}).items():
                if k in EXTRA_RANGES_FW1:
                    a, b = EXTRA_RANGES_FW1[k]
                    h[a:b] = bytes.fromhex(v)
            im.columns = int(cfg.get("columns", 135))
            im.commands = cfg.get("commands", ["l", "z", "a", "aV"])
            d = cfg.get("defaults") or {}
            h[5] = 0xE0 + int(d.get("font", 3))
            h[6] = int(d.get("spacing", 1))
            h[7] = int(round(float(d.get("interval", 1.0)) * 10))
            h[0x1D] = int(cfg.get("cycle_delay", 10)) * 2
            h[0x1E] = int(cfg.get("panel_kind", 1))
        else:
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
        if self.data.get("supplementary"):
            for k, v in self.data["supplementary"].items():
                b, p = self.encode_value(v)
                problems += [f"supplementary {k}: {x}" for x in p]
                im.supplementary[int(k)] = b
        if self.data.get("cycles"):
            im.cycles = {int(k): copy.deepcopy(v) for k, v in self.data["cycles"].items()}
        if self.data.get("kod_table"):
            im.kod = bytes.fromhex(self.data["kod_table"])
        if self.data.get("format_strings"):
            for k, v in self.data["format_strings"].items():
                b, p = self.encode_value(v)
                problems += [f"format_strings {k}: {x}" for x in p]
                im.format_strings[int(k)] = b
        for k, v in (self.data.get("raw_sections") or {}).items():
            im.raw_sections[k] = bytes.fromhex(v)
        return im, problems

    def to_gbuse0(self):
        from ..gbuse0 import database as D0, mnemo as M0
        cfg = self.data.get("config") or {}
        pnl = self.data.get("panel") or {}
        rows = int(cfg.get("rows", 19))
        cols = int(cfg.get("columns", 140))
        magic = int(cfg.get("magic") or (D0.MAGIC_OLD if str(cfg.get("fw", "")).startswith("1") else D0.MAGIC))
        raw_hdr = bytes.fromhex(cfg["raw_header"]) if cfg.get("raw_header") else None
        hsize = D0.HEADER_SIZE_055 if magic == D0.MAGIC_OLD else D0.HEADER_SIZE_057
        hdr = bytearray(raw_hdr[:hsize]) if raw_hdr else bytearray(hsize)
        if len(hdr) < hsize:
            hdr.extend(b"\x00" * (hsize - len(hdr)))
        hdr[0] = magic
        hdr[-1] = 0xFF
        fields = {k: D0.Field.from_bottom_dict(v, rows) for k, v in (cfg.get("fields") or {}).items()}
        ct = self.fonts.codetable
        db = D0.Database0(
            columns=cols,
            rows=rows,
            fields=fields,
            fonts={f.id: f for f in self.fonts.fonts},
            version=str(cfg.get("version", "")),
            source=self.base_dir,
            magic=magic,
            header=hdr,
            codetable=ct.raw,
            fonts_tail=self.fonts.tail,
            properties=(int(cfg.get("prop_low", 1)) & 0x07),
            brightness=int(pnl.get("brightness", 0)),
            panel_number=int(pnl.get("panel_number", 0)),
            fw_3x=bool(cfg.get("fw_3x", magic == D0.MAGIC and not str(cfg.get("fw", "")).startswith("2"))),
            title_version=str(cfg.get("title_version", "1.00")),
            title_fmt=str(cfg.get("title_fmt", "gBUSE0 - ")),
            fnt_sep=str(cfg.get("fnt_sep", ":")),
            str_title=str(cfg.get("str_title", "")),
            kod_title=str(cfg.get("kod_title", "")),
        )
        db.command_list = list(cfg.get("commands") or ["l", "z", "zI", "aV"])
        db.property_flags = list(cfg.get("properties") or [])
        if raw_hdr:
            # preserve low bits unless overridden
            db.properties = (raw_hdr[0x0B] & 0x07) | (db.properties & 0xF8)
        else:
            db.properties = 0xF0 | (db.properties & 0xF8) | (int(cfg.get("prop_low", 1)) & 0x07)
        db.afc = [tuple(int(x) for x in row) for row in (cfg.get("afc") or [])]
        db.afd = [tuple(int(x) for x in row) for row in (cfg.get("afd") or [])]
        if self.data.get("kod_table"):
            db.kod = bytes.fromhex(self.data["kod_table"])
        problems = []
        if self.data.get("format_strings"):
            for k, v in self.data["format_strings"].items():
                b, p = M0.from_mnemo(str(v), ct)
                problems += [f"format_strings {k}: {x}" for x in p]
                db.format_strings[int(k)] = b
        for name, sec in SECTIONS_0.items():
            recs = self.data.get(name)
            if recs is None:
                continue
            d, lst = {}, []
            kl = D0.KEYLEN[sec]
            for k, v in recs.items():
                key = _key(k)
                if len(key) != kl:
                    problems.append(f"{name} {key}: klíč musí mít {kl} znaky")
                b, p = M0.compose_record(v, db, {"lines": "line", "stops": "stop"}.get(name, "dest"), ct) \
                    if isinstance(v, dict) else M0.from_mnemo(str(v or ""), ct)
                problems += [f"{name} {key}: {x}" for x in p]
                d.setdefault(key, b)
                lst.append((key.encode("latin1"), b))
            db.records[sec] = d
            db.record_lists[sec] = lst
        return db, problems

    def to_hex(self) -> tuple[str, list[str]]:
        if self.format == "gbuse0":
            from ..gbuse0 import database as D0
            db, problems = self.to_gbuse0()
            return D0.to_hex(db), problems
        im, problems = self.to_image()
        return I.blocks_to_hex(I.build(im)), problems

    def to_bin(self) -> tuple[bytes, list[str]]:
        if self.format == "gbuse0":
            from ..gbuse0 import database as D0
            db, problems = self.to_gbuse0()
            return D0.to_bin(db), problems
        im, problems = self.to_image()
        return I.blocks_to_image(I.build(im)), problems

    # ---- checks ---------------------------------------------------------
    def check(self) -> list[dict]:
        """Validation report: missing glyphs, overflow, duplicate keys, BS110 limits."""
        out = []
        if self.format == "gbuse0":
            from ..gbuse0 import database as D0, program as P0
            db, probs = self.to_gbuse0()
            for p in probs:
                out.append({"section": "config", "key": "", "level": "error", "msg": p})
            for name, sec, fname in (("lines", "LIN", "line"), ("destinations", "CIL", "dest"),
                                     ("destinations4", "CIL4", "dest"), ("stops", "DRU", "stop")):
                fld = db.fields.get(fname)
                for k, b in (db.records.get(sec) or {}).items():
                    if fld is not None:
                        prog = P0.run(b, db, fld)
                        if prog.overflow:
                            out.append({"section": name, "key": k, "level": "warning",
                                        "msg": "text se nevejde na šířku zobrazovacího pole"})
            for w in D0.bs110_warnings(D0.build(db), db):
                out.append({"section": "config", "key": "", "level": "warning", "msg": w})
            return out

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

    # ---- factories ------------------------------------------------------
    @staticmethod
    def new_gbuse1(fonts: FontSet, version: str = "NOVY", columns: int = 135, panel_kind: int = 1,
                   fw: str = "2.06", ibis_address: int = 10) -> "Project":
        data = {
            "panel": {"format": "gbuse1", "type": "BS120", "ibis_address": ibis_address},
            "config": {
                "fw": fw, "version": version, "columns": columns,
                "commands": ["l", "z", "x", "u", "a", "aV"] if panel_kind != 2 else ["l", "z", "x", "xA", "xC", "u", "e", "a", "aV"],
                "defaults": {"font": 3, "spacing": 1, "interval": 1.0},
                "tariff_digits": 1, "cycle_delay": 10, "panel_kind": panel_kind,
            },
            "fonts": None,
            "lines": {"000": ""},
            "destinations": {"000": ""},
        }
        if panel_kind == 2:
            data["supplementary"] = {"3": "čas:", "4": "Pásmo:"}
            data["cycles"] = {"0": [{"left": {"width": 22, "source": 1, "dop": 0, "duration": 3, "enter": 0, "leave": 0, "id": "linka"},
                                     "right": {"width": 113, "source": 2, "dop": 0, "duration": 3, "enter": 0, "leave": 0, "id": "cíl"}}]}
        return Project(data, fonts)

    @staticmethod
    def new_gbuse0(fonts: FontSet, preset: str = "front", version: str = "NOVY",
                   fw: str = "3.08", ibis_address: int = 10) -> "Project":
        presets = {
            "front": (140, 19, ":", {"line": {"r0": 1, "r1": 19, "c0": 1, "c1": 28, "font": 8, "spacing": 1, "shift": 0, "interval": 1.0},
                                     "dest": {"r0": 1, "r1": 19, "c0": 29, "c1": 140, "font": 7, "spacing": 1, "shift": 0, "interval": 1.0}},
                      {"lines": {"001": "f8 m1 '1' CR"}, "destinations": {"001": "f7 m1 'HLAVNÍ NÁDRAŽÍ' CR"}}),
            "side": (112, 19, " ", {"line": {"r0": 1, "r1": 19, "c0": 1, "c1": 28, "font": 8, "spacing": 1, "shift": 0, "interval": 1.0},
                                    "dest": {"r0": 1, "r1": 19, "c0": 29, "c1": 112, "font": 7, "spacing": 1, "shift": 0, "interval": 1.0}},
                     {"lines": {"001": "f8 m1 '1' CR"}, "destinations": {"001": "f7 m1 'HLAVNÍ NÁDRAŽÍ' CR"}}),
            "side_via": (112, 19, " ", {"line": {"r0": 1, "r1": 19, "c0": 1, "c1": 28, "font": 8, "spacing": 1, "shift": 0, "interval": 1.0},
                                        "dest": {"r0": 9, "r1": 19, "c0": 29, "c1": 112, "font": 3, "spacing": 1, "shift": 0, "interval": 1.0},
                                        "stop": {"r0": 1, "r1": 8, "c0": 29, "c1": 112, "font": 11, "spacing": 1, "shift": 0, "interval": 3.0}},
                         {"lines": {"001": "f8 m1 '1' CR"}, "destinations": {"001": "f3 m1 'HLAVNÍ NÁDRAŽÍ' CR"},
                          "stops": {"0001": "f11 m1 'Náměstí' CR"}}),
            "rear": (28, 19, ".", {"line": {"r0": 1, "r1": 19, "c0": 1, "c1": 28, "font": 8, "spacing": 1, "shift": 0, "interval": 1.0}},
                     {"lines": {"001": "f8 m1 '1' CR"}}),
        }
        cols, rows, sep, fields, secs = presets.get(preset, presets["front"])
        cmds = ["l", "z", "zI", "aV", "aA"]
        if "stop" in fields:
            cmds += ["zD", "zN"]
        data = {
            "panel": {"format": "gbuse0", "type": "BS210", "ibis_address": ibis_address, "panel_number": 0, "brightness": 0},
            "config": {
                "fw": fw, "version": version, "columns": cols, "rows": rows,
                "commands": cmds, "properties": ["flip_all"], "prop_low": 1,
                "fields": fields, "afc": [], "afd": [], "fnt_sep": sep,
                "title_fmt": "gBUSE0 - ", "title_version": "1.00", "fw_3x": fw.startswith("3"),
            },
            "fonts": None,
            **secs,
        }
        return Project(data, fonts)
