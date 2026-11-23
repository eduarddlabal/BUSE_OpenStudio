"""Multi-panel BUSE Studio project bundle (gBUSE0 + gBUSE1 panels + optional simulation & live presets)."""
from __future__ import annotations

import copy
import io
import os
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field

import yaml

from . import text as T
from .fonts import FontSet, default_codetable
from .project import Flow, Project, dump_yaml, load_yaml

BUNDLE_SCHEMA = "busestudio.project/v2"
SUPPORTED_FORMATS = ("gbuse0", "gbuse1")


def _slug(text: str) -> str:
    norm = unicodedata.normalize("NFKD", (text or "").strip())
    ascii_s = norm.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "_", ascii_s).strip("_") or "panel"


def load_builtin_fonts(source: str = "gbuse1") -> FontSet:
    """Load built-in FontSet: 'gbuse0', 'gbuse1'/'gbuse', or 'builtin'."""
    src = (source or "gbuse1").strip()
    if src == "gbuse0":
        yml = os.path.join(os.path.dirname(os.path.dirname(__file__)), "peripherals", "gbuse0_font.yaml")
        with open(yml, encoding="utf-8") as fh:
            fs = FontSet.from_yaml(yaml.safe_load(fh))
        fs.codetable = default_codetable()
        return fs
    from .gbuse_font import fontset
    return fontset("builtin" if src == "builtin" else "gbuse")


@dataclass
class PanelEntry:
    id: str
    name: str
    role: str = "front"             # front | side | rear | interior | custom
    db: str = ""
    ibis_address: int = 10
    project: Project | None = None

    @property
    def format(self) -> str:
        return self.project.format if self.project else "gbuse1"

    def to_manifest(self) -> Flow:
        return Flow({
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "format": self.format,
            "ibis_address": int((self.project.data.get("panel") or {}).get("ibis_address", self.ibis_address)
                                if self.project else self.ibis_address),
            "db": self.db or f"panely/{self.id}/projekt.yaml",
        })

    def info(self) -> dict:
        p = self.project
        cfg = (p.data.get("config") or {}) if p else {}
        g = p.geometry if p else None
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "format": self.format,
            "ibis_address": int((p.data.get("panel") or {}).get("ibis_address", self.ibis_address) if p else self.ibis_address),
            "columns": g.columns if g else 0,
            "rows": g.rows if g else 0,
            "fw": str(cfg.get("fw") or ""),
            "version": str(cfg.get("version") or ""),
            "panel_kind": cfg.get("panel_kind"),
            "db": self.db,
        }


class ProjectBundle:
    """Container uniting 1..N panel databases (gBUSE0 + gBUSE1) and optional simulation/live configs."""

    def __init__(self, meta: dict | None = None, panels: list[PanelEntry] | None = None,
                 simulation: dict | None = None, live: dict | None = None,
                 base_dir: str = ".", manifest_path: str = ""):
        self.meta: dict = dict(meta or {})
        self.panels: list[PanelEntry] = list(panels or [])
        self.simulation: dict = dict(simulation or {})
        self.live: dict = dict(live or {})
        self.base_dir: str = base_dir
        self.manifest_path: str = manifest_path

    @staticmethod
    def is_bundle_yaml(path: str) -> bool:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for _ in range(12):
                    ln = fh.readline()
                    if not ln:
                        break
                    if ln.startswith("schema:") and BUNDLE_SCHEMA in ln:
                        return True
        except OSError:
            pass
        return False

    @staticmethod
    def load(manifest_path: str) -> "ProjectBundle":
        ap = os.path.abspath(manifest_path)
        base = os.path.dirname(ap)
        doc = load_yaml(ap)
        if not isinstance(doc, dict) or doc.get("schema") != BUNDLE_SCHEMA:
            raise ValueError("soubor není vícepanelový projekt (chybí schema: busestudio.project/v2)")
        panels: list[PanelEntry] = []
        for item in doc.get("panels") or []:
            db_rel = str(item.get("db") or f"panely/{item['id']}/projekt.yaml")
            db_abs = os.path.join(base, db_rel)
            proj = Project.load(db_abs)
            panels.append(PanelEntry(
                id=str(item["id"]),
                name=str(item.get("name") or item["id"]),
                role=str(item.get("role") or "front"),
                db=db_rel,
                ibis_address=int(item.get("ibis_address", 10)),
                project=proj,
            ))
        live_val = doc.get("live")
        live_dict: dict = {}
        if isinstance(live_val, str):
            lp = os.path.join(base, live_val)
            if os.path.exists(lp):
                live_dict = load_yaml(lp)
        elif isinstance(live_val, dict):
            cf = live_val.get("config")
            if isinstance(cf, str) and os.path.exists(os.path.join(base, cf)):
                live_dict = load_yaml(os.path.join(base, cf))
            else:
                live_dict = dict(live_val)
        return ProjectBundle(
            meta=doc.get("meta") or {},
            panels=panels,
            simulation=doc.get("simulation") or {},
            live=live_dict,
            base_dir=base,
            manifest_path=ap,
        )

    def save(self, manifest_path: str | None = None):
        ap = os.path.abspath(manifest_path or self.manifest_path)
        base = os.path.dirname(ap)
        os.makedirs(base, exist_ok=True)
        self.manifest_path = ap
        self.base_dir = base

        for pe in self.panels:
            if pe.project is None:
                continue
            if not pe.db:
                pe.db = f"panely/{pe.id}/projekt.yaml"
            db_abs = os.path.join(base, pe.db)
            pe.project.save(db_abs)

        out: dict = {
            "schema": BUNDLE_SCHEMA,
            "meta": dict(self.meta),
            "panels": [pe.to_manifest() for pe in self.panels],
        }
        if self.simulation:
            sim_doc = dict(self.simulation)
            if isinstance(sim_doc.get("vehicle_data"), dict):
                vrel = sim_doc.get("vehicle") or "vozidlo.yaml"
                if not str(vrel).startswith("template:"):
                    dump_yaml(sim_doc.pop("vehicle_data"), os.path.join(base, vrel))
                    sim_doc["vehicle"] = vrel
            out["simulation"] = sim_doc
        if self.live:
            dump_yaml(self.live, os.path.join(base, "zive.yaml"))
            out["live"] = {"config": "zive.yaml"}
        dump_yaml(out, ap)

    def panel(self, panel_id: str) -> PanelEntry | None:
        for pe in self.panels:
            if pe.id == panel_id:
                return pe
        return None

    def unique_panel_id(self, hint: str) -> str:
        base = _slug(hint)
        used = {pe.id for pe in self.panels}
        if base not in used:
            return base
        i = 2
        while f"{base}_{i}" in used:
            i += 1
        return f"{base}_{i}"

    def add_panel(self, entry: PanelEntry):
        if not entry.id:
            entry.id = self.unique_panel_id(entry.name)
        if not entry.db:
            entry.db = f"panely/{entry.id}/projekt.yaml"
        self.panels.append(entry)

    def remove_panel(self, panel_id: str) -> bool:
        if len(self.panels) <= 1:
            raise ValueError("vícepanelový projekt musí obsahovat alespoň jeden panel")
        for i, pe in enumerate(self.panels):
            if pe.id == panel_id:
                self.panels.pop(i)
                return True
        return False

    def aggregated_counts(self) -> dict[str, int]:
        agg: dict[str, int] = {}
        for pe in self.panels:
            if not pe.project:
                continue
            for sec in ("lines", "destinations", "destinations4", "stops", "messages", "supplementary", "cycles"):
                d = pe.project.data.get(sec)
                if isinstance(d, dict):
                    agg[sec] = max(agg.get(sec, 0), len(d))
        return agg

    def summary(self) -> dict:
        return {
            "schema": BUNDLE_SCHEMA,
            "name": str(self.meta.get("name") or "Projekt"),
            "description": str(self.meta.get("description") or ""),
            "manifest_path": self.manifest_path,
            "panels": [pe.info() for pe in self.panels],
            "simulation": self.simulation,
            "live": self.live,
            "counts": self.aggregated_counts(),
        }

    def resolved_vehicle_config(self, workdir: str | None = None) -> dict | None:
        """Load bundle's simulation vehicle config and resolve relative panel database paths."""
        sim = self.simulation or {}
        v_cfg = sim.get("vehicle_data")
        if v_cfg is None and isinstance(sim.get("vehicle"), str) and not sim["vehicle"].startswith("template:"):
            vp = os.path.join(self.base_dir, sim["vehicle"])
            if os.path.exists(vp):
                v_cfg = load_yaml(vp)
        if not isinstance(v_cfg, dict):
            return None
        resolved = copy.deepcopy(v_cfg)
        for pitem in resolved.get("panels") or []:
            dbp = pitem.get("database")
            if isinstance(dbp, str) and not os.path.isabs(dbp):
                cand = os.path.join(self.base_dir, dbp)
                if os.path.exists(cand):
                    pitem["database"] = os.path.relpath(cand, workdir) if workdir else os.path.abspath(cand)
        if sim.get("system") and not resolved.get("system"):
            resolved["system"] = sim["system"]
        return resolved

    # ---- record propagation across panels ------------------------------
    @staticmethod
    def _extract_lines(src: Project, section: str, value) -> tuple[str, str]:
        """Extract plain (line1, line2) strings from either gBUSE0 or gBUSE1 record."""
        if src.format == "gbuse0":
            from ..gbuse0 import mnemo as M0
            db, _ = src.to_gbuse0()
            fld = {"lines": "line", "stops": "stop"}.get(section, "dest")
            b = M0.compose_record(value, db, fld, src.fonts.codetable)[0] if isinstance(value, dict) \
                else M0.from_mnemo(str(value or ""), src.fonts.codetable)[0]
            dec = M0.decompose_record(b, db, fld, src.fonts.codetable)
            if dec:
                l1 = str((dec.get("line1") or {}).get("text") or "")
                l2 = str((dec.get("line2") or {}).get("text") or "") if dec.get("mode") == "2" else ""
                return l1, l2
            parts = [m.group(1) for m in re.finditer(r"'([^']*)'", M0.to_mnemo(b, src.fonts.codetable))]
            if not parts:
                return "", ""
            return parts[0], "|".join(parts[1:])
        return T.plain_text(value).strip(), ""

    def propagate_record(self, source_panel_id: str, section: str, key: str,
                         target_ids: list[str] | None = None, overwrite: bool = True) -> dict:
        """Autoformat & copy one record (section, key) from source panel to other panels in the bundle."""
        src_entry = self.panel(source_panel_id)
        if src_entry is None or src_entry.project is None:
            raise KeyError(f"zdrojový panel {source_panel_id!r} neexistuje")
        src_proj = src_entry.project
        src_sec = src_proj.data.get(section) or {}
        if key not in src_sec:
            raise KeyError(f"záznam {section}:{key} na zdrojovém panelu neexistuje")

        line1, line2 = self._extract_lines(src_proj, section, src_sec[key])
        updated: list[str] = []
        skipped: list[tuple[str, str]] = []

        for pe in self.panels:
            if pe.id == source_panel_id or pe.project is None:
                continue
            if target_ids is not None and pe.id not in target_ids:
                continue
            tp = pe.project
            tsec = tp.data.get(section)
            if not overwrite and isinstance(tsec, dict) and key in tsec:
                skipped.append((pe.name, "záznam už existuje"))
                continue

            if tp.format == "gbuse0":
                if section in ("messages", "supplementary"):
                    skipped.append((pe.name, f"sekce {section} neexistuje v gBUSE0"))
                    continue
                if pe.role == "rear" and section != "lines":
                    skipped.append((pe.name, "zadní panel zobrazuje jen linku"))
                    continue
                db, _ = tp.to_gbuse0()
                fld_name = {"lines": "line", "stops": "stop"}.get(section, "dest")
                if fld_name not in db.fields and (section != "destinations4" or "dest" not in db.fields):
                    skipped.append((pe.name, f"panel nemá definované pole {fld_name}"))
                    continue
                from ..gbuse0 import mnemo as M0
                spec: dict = {"mode": "2" if line2 else "1", "autoformat": True, "line1": {"text": line1}}
                if line2:
                    spec["line2"] = {"text": line2}
                b, _ = M0.compose_record(spec, db, fld_name, tp.fonts.codetable)
                tp.data.setdefault(section, {})[key] = M0.to_mnemo(b, tp.fonts.codetable)
                updated.append(pe.name)
            else:
                if pe.role == "rear" and section != "lines":
                    skipped.append((pe.name, "zadní panel zobrazuje jen linku"))
                    continue
                first_alt = line2.split("|")[0] if line2 else ""
                plain = f"{line1} – {first_alt}".strip(" –") if (line1 and first_alt) else (line1 or first_alt)
                if section == "lines":
                    w = tp.geometry.line_right - tp.geometry.line_left + 1
                    f, sp = T.autoformat_gbuse1(plain, tp.fonts, width=w)
                    tp.data.setdefault(section, {})[key] = {"text": plain, "font": f, "spacing": sp}
                else:
                    w = tp.geometry.dest_width
                    f, sp = T.autoformat_gbuse1(plain, tp.fonts, width=w)
                    codes, _ = tp.fonts.encode(plain)
                    needs_run = tp.fonts.width(f, codes, sp) > w
                    rec: dict = {"text": plain, "font": f, "spacing": sp}
                    if needs_run:
                        rec["run"] = True
                    tp.data.setdefault(section, {})[key] = rec
                updated.append(pe.name)

        return {"updated": updated, "skipped": skipped}

    # ---- ZIP batch export ----------------------------------------------
    def export_zip_bytes(self) -> bytes:
        from ..gbuse0 import formats as F0
        buf = io.BytesIO()
        manifest_lines = [
            f"BUSE Studio – Export vícepanelového projektu",
            f"Název: {self.meta.get('name', 'Projekt')}",
            f"Popis: {self.meta.get('description', '')}",
            "",
            "Panely:",
        ]
        equivalents: dict[str, str] = {}
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for pe in self.panels:
                if pe.project is None:
                    continue
                p = pe.project
                hx, probs_h = p.to_hex()
                bn, probs_b = p.to_bin()
                zf.writestr(f"{pe.id}.hex", hx.encode("ascii", "replace"))
                zf.writestr(f"{pe.id}.bin", bn)
                chk = p.check()
                errs = sum(1 for c in chk if c["level"] == "error")
                warns = sum(1 for c in chk if c["level"] == "warning")
                ver = (p.data.get("config") or {}).get("version", "")
                adr = (p.data.get("panel") or {}).get("ibis_address", pe.ibis_address)
                manifest_lines.append(
                    f"  - {pe.id} ({pe.name}): {p.format.upper()} {p.geometry.columns}x{p.geometry.rows}, "
                    f"IBIS={adr}, aV={ver!r}, chyby={errs}, varování={warns}"
                )
                for pr in probs_h or probs_b:
                    manifest_lines.append(f"      ! {pr}")
                # Derive BS100/BS230 equivalents from first destination-carrying panel
                if not equivalents and pe.role != "rear":
                    eq_saved = p.data.get("equivalents") or {}
                    for k, v in (p.data.get("destinations") or {}).items():
                        b, _ = p.encode_value(v)
                        equivalents[k] = eq_saved.get(k) or F0.default_equivalent(b, p.fonts.codetable)
            if equivalents:
                ver0 = (self.panels[0].project.data.get("config") or {}).get("version", "") if self.panels and self.panels[0].project else ""
                zf.writestr("ekvivalenty.bbd", F0.export_bbd(equivalents, version=str(ver0)))
            zf.writestr("manifest.txt", "\r\n".join(manifest_lines) + "\r\n")
        return buf.getvalue()


# ------------------------------------------------------------------ built-in project factories
def _new_panel_entry(pid: str, name: str, role: str, fmt: str, *, preset: str = "front",
                     fw: str = "", columns: int | None = None, rows: int | None = None,
                     panel_kind: int = 1, ibis_address: int = 10, version: str = "NOVY",
                     empty: bool = False) -> PanelEntry:
    fs = load_builtin_fonts("gbuse0" if fmt == "gbuse0" else "gbuse1")
    if fmt == "gbuse0":
        p = Project.new_gbuse0(fs, preset=preset, version=version, fw=fw or "3.08", ibis_address=ibis_address)
        if columns or rows:
            cfg = p.data["config"]
            cfg["columns"] = int(columns or cfg["columns"])
            cfg["rows"] = int(rows or cfg["rows"])
        if empty:
            p.data["lines"] = {}
            p.data["destinations"] = {}
            if "stops" in p.data:
                p.data["stops"] = {}
    else:
        p = Project.new_gbuse1(fs, version=version, columns=int(columns or 135),
                               panel_kind=panel_kind, fw=fw or ("2.12" if panel_kind == 2 else "2.06"),
                               ibis_address=ibis_address)
        if empty:
            p.data["lines"] = {}
            p.data["destinations"] = {}
            p.data.pop("supplementary", None)
            p.data.pop("cycles", None)
    p.data["meta"] = {"name": name}
    return PanelEntry(id=pid, name=name, role=role, db=f"panely/{pid}/projekt.yaml",
                      ibis_address=ibis_address, project=p)


def build_demo_bundle(name: str = "Ukázkový autobus (3 panely)",
                      description: str = "Čelní 140×19 + Boční s nácestnými 112×19 + Vnitřní BS120 (135×8)") -> ProjectBundle:
    """Built-in 3-panel demonstration bundle with realistic Czech lines, destinations & stops (no carrier data)."""
    celni = _new_panel_entry("celni", "Čelní panel", "front", "gbuse0", preset="front", fw="3.08", ibis_address=1, version="DEMO_C")
    bocni = _new_panel_entry("bocni", "Boční panel", "side", "gbuse0", preset="side_via", fw="3.08", ibis_address=2, version="DEMO_B")
    vnitrni = _new_panel_entry("vnitrni", "Vnitřní jednořádek", "interior", "gbuse1", panel_kind=2, fw="2.12", ibis_address=10, version="DEMO_V")

    lines_0 = {"001": "f8 m1 '1' CR", "003": "f8 m1 '3' CR", "010": "f8 m1 '10' CR"}
    lines_1 = {"001": {"text": "1", "font": 3, "spacing": 1},
               "003": {"text": "3", "font": 3, "spacing": 1},
               "010": {"text": "10", "font": 3, "spacing": 1}}

    celni.project.data["lines"] = copy.deepcopy(lines_0)
    celni.project.data["destinations"] = {
        "001": "f7 m1 'HLAVNÍ NÁDRAŽÍ' CR",
        "002": "f3 m1 'POLIKLINIKA' LF f11 m1 'přes Centrum' CR",
        "003": "f7 m1 'LÁZNĚ BOHDANEČ' CR",
        "010": "f7 m1 'UNIVERZITA' CR",
    }
    celni.project.data["equivalents"] = {
        "001": "HL. NÁDRAŽÍ",
        "002": "POLIKLINIKA",
        "003": "LÁZNĚ BOHD.",
        "010": "UNIVERZITA",
    }

    bocni.project.data["lines"] = copy.deepcopy(lines_0)
    bocni.project.data["destinations"] = {
        "001": "f3 m1 'HLAVNÍ NÁDRAŽÍ' CR",
        "002": "f3 m1 'POLIKLINIKA' LF f11 m1 'přes Centrum|přes Náměstí' VT",
        "003": "f3 m1 'LÁZNĚ BOHDANEČ' CR",
        "010": "f3 m1 'UNIVERZITA' CR",
    }
    bocni.project.data["stops"] = {
        "0001": "f11 m1 'Náměstí Republiky' CR",
        "0002": "f11 m1 'Poliklinika' CR",
        "0003": "f11 m1 'Třída Míru' CR",
        "0004": "f11 m1 'Hlavní nádraží' CR",
    }

    vnitrni.project.data["lines"] = copy.deepcopy(lines_1)
    vnitrni.project.data["destinations"] = {
        "001": {"text": "HLAVNÍ NÁDRAŽÍ", "font": 3, "spacing": 1},
        "002": {"text": "POLIKLINIKA - přes Náměstí a Centrum", "font": 3, "spacing": 1, "run": True},
        "003": {"text": "LÁZNĚ BOHDANEČ", "font": 3, "spacing": 1},
        "010": {"text": "UNIVERZITA - kampus a koleje", "font": 3, "spacing": 1, "run": True},
    }
    vnitrni.project.data["stops"] = {
        "0001": {"text": "Náměstí Republiky", "font": 3, "spacing": 1},
        "0002": {"text": "Poliklinika", "font": 3, "spacing": 1},
        "0003": {"text": "Třída Míru", "font": 3, "spacing": 1},
        "0004": {"text": "Hlavní nádraží", "font": 3, "spacing": 1},
    }
    vnitrni.project.data["messages"] = {
        "0001": {"text": "Výluka na trase - mimořádná linka", "font": 0, "spacing": 1, "run": True},
    }

    sim_cfg = {
        "system": "default",
        "vehicle_data": {
            "name": name,
            "panels": [
                {"name": "Vnější čelní (140×19)", "type": "BS210", "variant": "v1", "role": "front", "output": "virtual", "database": "panely/celni/projekt.yaml"},
                {"name": "Vnější boční (112×19)", "type": "BS210", "variant": "v2", "stop_field": "bottom", "role": "side", "output": "virtual", "database": "panely/bocni/projekt.yaml"},
                {"name": "Vnitřní BS120 (135×8)", "type": "BS120", "role": "interior", "output": "virtual", "database": "panely/vnitrni/projekt.yaml"},
            ],
        },
    }
    live_cfg = {
        "presets": [
            {"name": "Služební jízda", "text": "SLUZEBNE", "kind": "v", "font": 3, "spacing": 1},
            {"name": "Mimo provoz", "text": "MIMO PROVOZ", "kind": "v", "font": 3, "spacing": 1},
            {"name": "Zkušební jízda", "text": "ZKUSEBNI JIZDA", "kind": "v+line", "line": "000", "font": 3, "spacing": 1},
        ],
        "clock": {"channel": "text", "format": "%H:%M"},
    }
    return ProjectBundle(meta={"name": name, "description": description},
                         panels=[celni, bocni, vnitrni], simulation=sim_cfg, live=live_cfg)


def build_empty_bundle(preset: str = "bus_3", name: str = "Prázdný projekt",
                       description: str = "Čistá databáze pouze s originálními fonty (bez záznamů)") -> ProjectBundle:
    """Built-in empty project bundle containing only clean panel configs and fonts."""
    if preset == "single_gbuse1":
        panels = [_new_panel_entry("vnitrni", "Vnitřní jednořádek", "interior", "gbuse1", panel_kind=1, fw="2.06", ibis_address=10, version="NOVY", empty=True)]
    elif preset == "single_gbuse0":
        panels = [_new_panel_entry("celni", "Čelní panel", "front", "gbuse0", preset="front", fw="3.08", ibis_address=1, version="NOVY", empty=True)]
    elif preset == "bus_4":
        panels = [
            _new_panel_entry("celni", "Čelní panel", "front", "gbuse0", preset="front", fw="3.08", ibis_address=1, version="NOVY", empty=True),
            _new_panel_entry("bocni", "Boční panel", "side", "gbuse0", preset="side_via", fw="3.08", ibis_address=2, version="NOVY", empty=True),
            _new_panel_entry("zadni", "Zadní panel", "rear", "gbuse0", preset="rear", fw="3.08", ibis_address=3, version="NOVY", empty=True),
            _new_panel_entry("vnitrni", "Vnitřní jednořádek", "interior", "gbuse1", panel_kind=2, fw="2.12", ibis_address=10, version="NOVY", empty=True),
        ]
    else:  # bus_3 default (čelní + boční + vnitřní)
        panels = [
            _new_panel_entry("celni", "Čelní panel", "front", "gbuse0", preset="front", fw="3.08", ibis_address=1, version="NOVY", empty=True),
            _new_panel_entry("bocni", "Boční panel", "side", "gbuse0", preset="side_via", fw="3.08", ibis_address=2, version="NOVY", empty=True),
            _new_panel_entry("vnitrni", "Vnitřní jednořádek", "interior", "gbuse1", panel_kind=2, fw="2.12", ibis_address=10, version="NOVY", empty=True),
        ]
    return ProjectBundle(meta={"name": name, "description": description}, panels=panels)
