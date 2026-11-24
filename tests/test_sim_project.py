"""OpenProject – everything the simulation derives from the open project, without the server."""
from __future__ import annotations

import pytest

from busepanel.core.bundle import build_demo_bundle
from busepanel.core.project import dump_yaml
from busepanel.server.sim_project import OpenProject, strip_project_marks


@pytest.fixture
def opened(tmp_path):
    wd = tmp_path / "w"
    proj = wd / "projekty" / "demo"
    (proj / "trasy").mkdir(parents=True)
    (wd / "trasy").mkdir()
    dump_yaml({"line": "3", "destination": "A", "stops": [{"name": "X"}]}, str(proj / "trasy" / "l3.yaml"))
    dump_yaml({"line": "4", "destination": "B", "stops": [{"name": "Y"}]}, str(wd / "trasy" / "l4.yaml"))
    b = build_demo_bundle()
    b.meta["name"] = "Demo"
    b.simulation["system"] = "muj"
    b.simulation["routes"] = ["trasy/l3.yaml", "trasy/l4.yaml", "trasy/neni.yaml"]
    dump_yaml(b.simulation.pop("vehicle_data"), str(proj / "vozidlo.yaml"))
    b.simulation["vehicle"] = "vozidlo.yaml"
    b.save(str(proj / "projekt.yaml"))
    return wd, OpenProject.load(str(wd), "projekty/demo/projekt.yaml")


def test_identity_routes_vehicle(opened):
    wd, pr = opened
    assert pr.name == "Demo" and pr.system == "muj" and pr.db is not None
    found, missing = pr.routes()
    assert found == ["projekty/demo/trasy/l3.yaml", "trasy/l4.yaml"] and missing == ["trasy/neni.yaml"]
    assert pr.vehicle_path() == "projekty/demo/vozidlo.yaml"
    dbs = [p.get("database") for p in pr.vehicle_config()["panels"]]
    assert dbs and all(d.startswith("projekty/demo/") for d in dbs if d)


def test_other_vehicle_takes_databases_and_saves_without_them(opened):
    _wd, pr = opened
    own = [p for p in pr.vehicle_config()["panels"] if p.get("database")]
    bare = {"panels": [{k: v for k, v in p.items() if k != "database"} for p in own]}
    got = pr.with_databases(bare)["panels"]
    assert [p["database"] for p in got] == [p["database"] for p in own]
    assert all(p["database_from_project"] for p in got)
    assert not any("database" in p for p in pr.vehicle_for_disk({"panels": got}, "vozidla/x.yaml")["panels"])
    assert not any("database" in p for p in strip_project_marks({"panels": got})["panels"])


def test_project_vehicle_saved_relative_to_project(opened):
    _wd, pr = opened
    disk = pr.vehicle_for_disk(pr.vehicle_config(), pr.vehicle_path())
    assert all(not p["database"].startswith("projekty/") for p in disk["panels"] if p.get("database"))


def test_single_database_has_no_project_setup(opened):
    wd, pr = opened
    single = OpenProject(str(wd), "x.yaml", pr.db)
    assert single.routes() == ([], []) and single.vehicle_path() is None and single.system == ""
    assert single.with_databases({"panels": [{"role": "front"}]})["panels"] == [{"role": "front"}]


def test_sim_state_is_one_object():
    from busepanel.server.sim_api import SIM
    with pytest.raises(AttributeError):
        SIM.db = None                      # read-only view: open through _open_project only


def test_saved_project_is_reloaded_in_simulation(opened):
    from busepanel.server import sim_api as sim
    wd, _pr = opened
    old = (sim.SIM.workdir, sim.SIM.project, sim.SIM.vehicle_cfg, sim.SIM.vehicle_path,
           sim.SIM.line, sim.SIM.route, sim.SIM.route_path, sim.SIM.sel, sim.SIM.engine, sim.SIM.vehicle)
    try:
        sim.init(str(wd))
        sim.db_open(sim.OpenDbReq(path="projekty/demo/projekt.yaml", apply_vehicle=True))
        from busepanel.core.bundle import ProjectBundle
        b = ProjectBundle.load(str(wd / "projekty" / "demo" / "projekt.yaml"))
        key = "098"
        for pe in b.panels:
            pe.project.data.setdefault("line_map", {})["166"] = key
        b.save()
        assert "166" not in (sim.SIM.db.data.get("line_map") or {})
        sim.reload_project(b.manifest_path)
        assert sim.SIM.db.data["line_map"]["166"] == key
    finally:
        (sim.SIM.workdir, sim.SIM.project, sim.SIM.vehicle_cfg, sim.SIM.vehicle_path,
         sim.SIM.line, sim.SIM.route, sim.SIM.route_path, sim.SIM.sel, sim.SIM.engine, sim.SIM.vehicle) = old


def test_save_route_into_project(opened):
    """Linky „Uložit do projektu“: the route goes to the project's trasy/ and into simulation: routes."""
    from busepanel.core.project import load_yaml
    from busepanel.server import sim_api as sim
    wd, _pr = opened
    old = (sim.SIM.workdir, sim.SIM.project, sim.SIM.vehicle_cfg, sim.SIM.vehicle_path,
           sim.SIM.line, sim.SIM.route, sim.SIM.route_path, sim.SIM.sel, sim.SIM.engine, sim.SIM.vehicle)
    try:
        sim.init(str(wd))
        sim.db_open(sim.OpenDbReq(path="projekty/demo/projekt.yaml", apply_vehicle=True))
        line = {"line": "166", "destination": "Kavčí hory", "stops": [{"name": "A"}, {"name": "B"}]}
        r = sim.line_save(sim.LineReq(line=line, path="trasy/linka166.yaml", to_project=True))
        assert r["path"] == "projekty/demo/trasy/linka166.yaml" and r["added_to_project"]
        assert (wd / "projekty" / "demo" / "trasy" / "linka166.yaml").exists()
        assert not (wd / "trasy" / "linka166.yaml").exists()
        man = load_yaml(str(wd / "projekty" / "demo" / "projekt.yaml"))
        assert man["simulation"]["routes"][-1] == "trasy/linka166.yaml"
        assert "projekty/demo/trasy/linka166.yaml" in sim.files()["routes"]
        again = sim.line_save(sim.LineReq(line=line, path=r["path"]))     # plain Uložit of a project route
        assert not again["added_to_project"]
        assert load_yaml(str(wd / "projekty" / "demo" / "projekt.yaml"))["simulation"]["routes"].count(
            "trasy/linka166.yaml") == 1
    finally:
        (sim.SIM.workdir, sim.SIM.project, sim.SIM.vehicle_cfg, sim.SIM.vehicle_path,
         sim.SIM.line, sim.SIM.route, sim.SIM.route_path, sim.SIM.sel, sim.SIM.engine, sim.SIM.vehicle) = old
