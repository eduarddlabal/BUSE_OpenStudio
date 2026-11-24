"""The project open in the simulation – one object for everything derived from it.

A simulation project is either a single panel database (gBUSE1 YAML, HEX, BIN) or a multi-panel project
(ProjectBundle, `projekty/<name>/projekt.yaml`) with its own `simulation:` section: vehicle, display system
and routes. Everything the Jízda / Linky / Vozidla / Systémy pages show for an open project is derived here,
so the server keeps a single piece of state (Sim.project) instead of several variables that must agree.
"""
from __future__ import annotations

import os

from ..core.bundle import ProjectBundle
from ..core.project import Project


def _rel(path: str, start: str) -> str:
    return os.path.relpath(path, start).replace("\\", "/")


def load_project_file(abs_path: str) -> tuple[Project, ProjectBundle | None]:
    """Panel database (+ the multi-panel project it belongs to) of a file: the interior gBUSE1 database of
    a project bundle (else its first panel database), or the single database of a YAML / HEX / BIN file."""
    low = abs_path.lower()
    if low.endswith((".yaml", ".yml")):
        if ProjectBundle.is_bundle_yaml(abs_path):
            bundle = ProjectBundle.load(abs_path)
            pe = next((p for p in bundle.panels if p.format == "gbuse1" and p.project is not None), None)
            if pe is None:
                pe = next((p for p in bundle.panels if p.project is not None), None)
            if pe is None or pe.project is None:
                raise ValueError(f"projekt {abs_path} neobsahuje žádnou databázi panelu")
            return pe.project, bundle
        return Project.load(abs_path), None
    if low.endswith(".bin"):
        return Project.from_bin(abs_path), None
    return Project.from_hex(abs_path), None


class OpenProject:
    """What is open in the simulation: `path` (relative to the working folder), the panel database `db`
    used by the engine and, for a multi-panel project, its `bundle`."""

    def __init__(self, workdir: str, path: str, db: Project, bundle: ProjectBundle | None = None):
        self.workdir = os.path.abspath(workdir)
        self.path = path
        self.db = db
        self.bundle = bundle

    @classmethod
    def load(cls, workdir: str, path: str) -> "OpenProject":
        db, bundle = load_project_file(os.path.abspath(os.path.join(workdir, path)))
        return cls(workdir, path, db, bundle)

    # ---- identity ----------------------------------------------------
    @property
    def sim(self) -> dict:
        return (self.bundle.simulation or {}) if self.bundle is not None else {}

    @property
    def name(self) -> str:
        return str((self.bundle.meta or {}).get("name") or "") if self.bundle is not None else ""

    @property
    def system(self) -> str:
        """Display system named by the project ('' = the vehicle decides)."""
        return str(self.sim.get("system") or "")

    # ---- routes --------------------------------------------------------
    def routes(self) -> tuple[list[str], list[str]]:
        """Routes of the simulation: section -> (found paths relative to the workdir, missing as written).
        A path is looked up in the project folder first, then in the working folder."""
        found, missing = [], []
        if self.bundle is None:
            return found, missing
        for r in self.sim.get("routes") or []:
            r = str(r)
            for base in (self.bundle.base_dir, self.workdir):
                full = os.path.abspath(os.path.join(base, r))
                if os.path.isfile(full):
                    found.append(_rel(full, self.workdir))
                    break
            else:
                missing.append(r)
        return found, missing

    def route_target(self, path: str) -> str:
        """Where „Uložit do projektu“ writes a route: the path itself when it is already inside the project
        folder, else trasy/<file name> in the project folder (relative to the working folder)."""
        full = os.path.abspath(os.path.join(self.workdir, path))
        if self.contains(full):
            return _rel(full, self.workdir)
        return _rel(os.path.join(self.bundle.base_dir, "trasy", os.path.basename(path) or "linka.yaml"), self.workdir)

    def contains(self, abs_path: str) -> bool:
        if self.bundle is None:
            return False
        base = os.path.abspath(self.bundle.base_dir)
        return os.path.commonpath([base, os.path.abspath(abs_path)]) == base

    def add_route(self, path: str) -> bool:
        """Name a route (relative to the working folder) in the project's simulation: routes – in memory and
        in projekt.yaml (only that list is rewritten, panel databases are not touched). False = already there."""
        if self.bundle is None:
            return False
        if path in self.routes()[0]:
            return False
        full = os.path.abspath(os.path.join(self.workdir, path))
        entry = _rel(full, self.bundle.base_dir) if self.contains(full) else path
        sim = self.bundle.simulation = dict(self.bundle.simulation or {})
        sim["routes"] = list(sim.get("routes") or []) + [entry]
        from ..core.project import dump_yaml, load_yaml
        doc = load_yaml(self.bundle.manifest_path) or {}
        doc.setdefault("simulation", {})
        doc["simulation"]["routes"] = list(doc["simulation"].get("routes") or []) + [entry]
        dump_yaml(doc, self.bundle.manifest_path)
        return True

    # ---- vehicle -------------------------------------------------------
    def vehicle_path(self) -> str | None:
        """The project's vehicle file relative to the working folder (None = inline / template / none)."""
        v = self.sim.get("vehicle")
        if self.bundle is None or not isinstance(v, str) or v.startswith("template:"):
            return None
        full = os.path.abspath(os.path.join(self.bundle.base_dir, v))
        return _rel(full, self.workdir) if os.path.isfile(full) else None

    def vehicle_config(self) -> dict | None:
        """The project's vehicle with database paths relative to the working folder."""
        return self.bundle.resolved_vehicle_config(self.workdir) if self.bundle is not None else None

    def with_databases(self, cfg: dict) -> dict:
        """Another vehicle: its panels without their own database take the database of the project's panel
        with the same role, type and variant (marked database_from_project, never saved into that vehicle).
        Marks of an earlier project are dropped first."""
        src = [p for p in (self.vehicle_config() or {}).get("panels") or [] if p.get("database")]
        cfg = dict(cfg)
        out = []
        for p in cfg.get("panels") or []:
            p = dict(p)
            if p.pop("database_from_project", False):
                p.pop("database", None)
            if not p.get("database") and p.get("output") != "lcd" and str(p.get("type", "")).upper() != "LCD":
                same = [q for q in src if q.get("role") == p.get("role")
                        and str(q.get("type", "")).upper() == str(p.get("type", "")).upper()
                        and (q.get("variant") or "") == (p.get("variant") or "")]
                if same:
                    p["database"] = same[0]["database"]
                    p["database_from_project"] = True
            out.append(p)
        cfg["panels"] = out
        return cfg

    def vehicle_for_disk(self, cfg: dict, path: str) -> dict:
        """A vehicle as written to `path`: no databases taken over from the project; the project's own
        vehicle gets its database paths relative to the project folder again."""
        disk = dict(cfg)
        disk["panels"] = [{k: v for k, v in p.items() if k != "database_from_project"
                           and not (k == "database" and p.get("database_from_project"))}
                          for p in cfg.get("panels") or []]
        if path == self.vehicle_path():
            for q in disk["panels"]:
                if isinstance(q.get("database"), str) and not os.path.isabs(q["database"]):
                    q["database"] = _rel(os.path.join(self.workdir, q["database"]), self.bundle.base_dir)
        return disk

    # ---- display systems ----------------------------------------------
    def systems_dir(self, folder: str) -> str | None:
        """The project's own folder of display systems (searched before the working folder)."""
        return os.path.join(self.bundle.base_dir, folder) if self.bundle is not None else None


def strip_project_marks(cfg: dict) -> dict:
    """Vehicle without databases taken over from a project (no project open / for saving)."""
    cfg = dict(cfg)
    cfg["panels"] = [{k: v for k, v in p.items() if k != "database_from_project"
                      and not (k == "database" and p.get("database_from_project"))}
                     for p in cfg.get("panels") or []]
    return cfg
