"""Bus + peripherals (BS120 unchanged, exterior gBUSE0 panels), line with courses, gBUSE0 interpreter."""
import os
import pathlib

import pytest

from busepanel.gbuse0.program import run
from busepanel.outputs.virtual import VirtualPanel
from busepanel.panels.profile import profile
from busepanel.peripherals import Bus, ExteriorSlot, PanelSlot, build_peripherals
from busepanel.peripherals.exterior import (ExteriorPanel, autoformat, encode_text, variant_database,
                                            VARIANTS)
from busepanel.sim.engine import SimEngine
from busepanel.sim.line import Line
from busepanel.sim.route import Route
from busepanel.sim.universal_db import build_universal
from busepanel.sim.vehicle import Vehicle


class Spy:
    kind = "serial"

    def __init__(self):
        self.sent = []

    def send(self, *t):
        self.sent.extend(t)

    def status(self):
        return {"kind": self.kind}


@pytest.fixture(scope="module")
def univ():
    return build_universal(None, extra_lines=["607", "7"])


@pytest.fixture
def route():
    return Route.from_dict({"line": "7", "destination": "Sídliště Dukla", "stops": [
        {"name": "Hlavní nádraží"}, {"name": "Masarykovo nám.", "via": True}, {"name": "U Kostela"},
        {"name": "Sídliště Dukla"}]})


def lit(panel, field):
    """Number of lit dots inside a field of an exterior emulator."""
    f = panel.db.fields[field]
    bits = panel.bitmap()
    return sum(bin(bits[x] >> f.y0 & ((1 << f.height) - 1)).count("1") for x in range(f.x0, f.x1 + 1))


# ---------------------------------------------------------------- variants
@pytest.mark.parametrize("v,cols,fields", [("v1", 140, {"line", "dest"}), ("v2", 112, {"line", "dest"}),
                                           ("v4", 28, {"line"})])
def test_variants(v, cols, fields):
    db = variant_database(v)
    assert (db.columns, db.rows) == (cols, 19) and set(db.fields) == fields
    assert v in VARIANTS


def test_stop_field_position():
    db = variant_database("v2", stop_field=True)
    s, d = db.fields["stop"], db.fields["dest"]
    assert s.y1 < d.y0 and s.x0 == d.x0 and s.x1 == d.x1
    db = variant_database("v2", stop_field="bottom")
    s, d = db.fields["stop"], db.fields["dest"]
    assert d.y1 < s.y0 and s.height == 9 and d.y0 == 0      # fonts 0–3 with accents
    assert variant_database("v2").fields["dest"].x0 == 30                 # PID: 2 free columns after the line


def test_original_font_and_baseline():
    db = variant_database("v1")
    assert 9 in db.fonts and max(g.height for g in db.fonts[7].glyphs.values()) == 28
    rec = autoformat(db, db.fields["dest"], encode_text("Chlumec nad Cidlinou"))
    assert rec[0] != 0xE9                      # font 9 has small superscript lower case
    rec = autoformat(db, db.fields["dest"], encode_text("CENTRUM"))
    assert rec[0] - 0xE0 in (6, 7, 8, 9)       # capitals that fit get a large font


def test_forced_font():
    db = variant_database("v2", stop_field="bottom")
    from busepanel.core import appsettings
    rec = autoformat(db, db.fields["dest"], bytes([0xE0 + 2, 0xB0]) + encode_text("Dukla"))
    assert rec[:2] == bytes([0xE2, 0xB1])            # spacing 0 is not allowed by default (unreadable)
    appsettings.update(None, {"tight_spacing": True})
    try:
        rec = autoformat(db, db.fields["dest"], bytes([0xE0 + 2, 0xB0]) + encode_text("Dukla"))
        assert rec[:2] == bytes([0xE2, 0xB0])
        long = autoformat(db, db.fields["dest"], encode_text("Pardubice, Hlavní nádraží"))
    finally:
        appsettings.update(None, {"tight_spacing": False})
    assert long[1] == 0xB0 and autoformat(db, db.fields["dest"], encode_text("Pardubice, Hlavní nádraží"))[1] == 0xB1


def test_exterior_renders_line_and_text():
    p = ExteriorPanel("t", variant_database("v1"), {"607": "098"})
    p.send("l098", b"zI" + encode_text("Chlumec nad Cidlinou"))
    assert lit(p, "line") > 20 and lit(p, "dest") > 50
    p.send(b"v" + b"ignored", "x9996")               # interior telegrams are ignored
    assert p.values["l"] == "098"


def test_autoformat_skips_fonts_without_glyphs():
    db = variant_database("v2")
    rec = autoformat(db, db.fields["dest"], encode_text("607 Chlumec nad Cidlinou, žst."))
    prog = run(rec, db, db.fields["dest"])
    assert any(prog.frames[-1][0].bits)


def test_stop_cycling():
    t = [0.0]
    p = ExteriorPanel("s", variant_database("v2", stop_field=True), clock=lambda: t[0])
    p.send(b"zN" + encode_text("Alfa|Beta"))
    a = p.bitmap()
    t[0] = 3.5
    assert p.bitmap() != a


# ---------------------------------------------------------------- bus
def test_bus_broadcast_and_dedupe(univ, route):
    spy = Spy()
    bus = Bus("ibis", spy)
    a = ExteriorSlot("a", "front", variant_database("v1"), bus, {"variant": "v1"})
    b = ExteriorSlot("b", "side", variant_database("v2"), bus, {"variant": "v2"})
    i = PanelSlot("i", "interior", profile("BS120"), bus=bus, shadow=VirtualPanel("i", univ))
    veh = Vehicle(SimEngine(route), univ.fonts, [i, a, b])
    veh.tick()
    zi = [t for t in spy.sent if isinstance(t, bytes) and t.startswith(b"zI")]
    assert len(zi) == 1                                  # the same destination goes on the line once
    assert any(isinstance(t, str) and t.startswith("x999") for t in spy.sent)   # BS120 unchanged
    assert lit(b.emulator, "dest") > 0                   # b heard a's telegram
    n = len(spy.sent)
    veh.tick()
    assert len(spy.sent) == n                            # nothing changed -> nothing sent
    veh.resend()
    veh.tick()
    assert len(spy.sent) > n


def test_build_peripherals_legacy_and_buses(univ):
    cfg = {"buses": {"ibis": {"output": "virtual"}},
           "panels": [{"name": "old", "role": "interior", "profile": "BS120", "output": "virtual"},
                      {"name": "in", "type": "BS120", "role": "interior", "bus": "ibis"},
                      {"name": "front", "type": "BS210", "role": "front", "bus": "ibis"},
                      {"name": "rear", "profile": "BS210", "role": "rear", "output": "virtual"}]}
    per, buses = build_peripherals(cfg, univ)
    assert [p.kind for p in per] == ["bs120", "bs120", "exterior", "exterior"]
    assert per[1].bus is per[2].bus is buses["ibis"]
    assert per[0].bus is not per[1].bus
    assert per[3].db.columns == 28                       # rear -> v4 by default
    assert not any(p.real for p in per)


# ---------------------------------------------------------------- line with courses
LINE = {"line": "3",
        "directions": {"A": {"destination": "KONEČNÁ A", "stops": [
                           {"name": "S1", "minutes": 0}, {"name": "S2", "minutes": 2, "via": True},
                           {"name": "S3", "minutes": 5},
                           {"name": "S4", "minutes": 9}]},
                       "B": {"destination": "KONEČNÁ B", "stops": ["S4", "S3", "S2", "S1"]}},
        "courses": {"1": [{"direction": "A", "departure": "05:00"}, {"direction": "B", "departure": "05:30"},
                          {"direction": "A", "departure": "06:00", "to": "S3"},
                          {"direction": "A", "departure": "06:40", "from": 2}]}}


def test_line_trips():
    ln = Line.from_dict(LINE)
    r = ln.route(course="1", index=0)
    assert (r.destination, len(r.stops), r.departure, r.course, r.trip) == ("KONEČNÁ A", 4, "05:00", "1", 0)
    short = ln.route(course="1", index=2)
    assert short.destination == "S3" and len(short.stops) == 3
    late = ln.route(course="1", index=3)
    assert late.destination == "KONEČNÁ A" and late.stops[0].name == "S2" and late.stops[0].minutes == 0
    assert ln.next_trip("1", 2) == 3 and ln.next_trip("1", 3) is None
    assert ln.route(direction="B").destination == "KONEČNÁ B"
    assert [t["full"] for t in ln.summary()["courses"]["1"]] == [True, True, False, False]


def test_line_roundtrip_and_errors():
    ln = Line.from_dict(LINE)
    assert Line.from_dict(ln.to_dict()).to_dict() == ln.to_dict()
    with pytest.raises(ValueError):
        Line.from_dict({**LINE, "courses": {"1": [{"direction": "X"}]}})
    with pytest.raises(ValueError):
        Line.from_dict({**LINE, "courses": {"1": [{"direction": "A", "from": "S3", "to": "S1"}]}})


def test_line_put_route():
    ln = Line.from_dict(LINE)
    r = ln.route(course="1", index=1)
    r.stops[0].via = True
    ln.put_route(r, course="1", index=1)
    assert ln.directions["B"].stops[0].via and not ln.directions["A"].stops[0].via
    with pytest.raises(ValueError):
        ln.put_route(ln.route(course="1", index=2), course="1", index=2)


def test_flat_route_is_one_direction():
    flat = {"line": "7", "destination": "D", "stops": [{"name": "a"}, {"name": "D"}]}
    ln = Line.from_dict(flat)
    assert ln.flat and ln.route().destination == "D" and not ln.courses
    assert ln.to_dict() == Route.from_dict(flat).to_dict()


# ---------------------------------------------------------------- přes / via stops, text composition
def test_old_via_text_marks_stops():
    r = Route.from_dict({"line": "1", "destination": "Z", "via": "Bor - Lhota",
                         "stops": ["A", "Bor, náves", "Kopec", "Lhota", "Z"]})
    assert [s.name for s in r.stops if s.via] == ["Bor, náves", "Lhota"]
    assert "via" not in r.to_dict()


def test_via_context_and_pages(route):
    from busepanel.sim.engine import SimEngine
    route.stops[2].via = True
    e = SimEngine(route)
    e.event("start")
    assert e._context(0)["via"] == "Masarykovo nám. - U Kostela"
    pages = e.via_pages("side", 0)
    assert [p["text"] for p in pages] == ["Masarykovo nám.", "U Kostela"] and pages[0]["font"] == 11
    e.event("goto", index=2, phase="at_stop")
    assert e.via_pages("side", 0) == []


def test_systems_from_yaml(tmp_path):
    from busepanel.sim.engine import DEFAULT_SYSTEM, load_formats, system_settings
    (tmp_path / "vlastni.yaml").write_text("title: Vlastní\ninterior: {items: [next], sequences: {next: [{text: '{name}'}]}}\n",
                                           encoding="utf-8")
    fm = load_formats(str(tmp_path))
    assert fm["vlastni"]["title"] == "Vlastní"
    assert system_settings("vlastni", fm)["interior"]["items"] == ["next"]
    assert system_settings("neni", fm)["interior"]["items"] == DEFAULT_SYSTEM["interior"]["items"]   # fallback


def test_exterior_side_composition(univ, route):
    route.stops[1].via = True
    bus = Bus("b")
    side = ExteriorSlot("s", "side", variant_database("v2", stop_field="bottom"), bus, {})
    veh = Vehicle(SimEngine(route), univ.fonts, [side])
    veh.engine.event("start")
    veh.tick()
    zn = next(t for t in side.last if isinstance(t, bytes) and t.startswith(b"zN"))
    assert zn == b"zN" + bytes([0xEB]) + encode_text("Masarykovo nám.")
    assert lit(side.emulator, "stop") > 0 and lit(side.emulator, "dest") > 0


# ---------------------------------------------------------------- real gBUSE0 databases (not in the repo)
ROOT = pathlib.Path(os.environ.get("BUSE_SAMPLE_DIR", "/nonexistent"))


def _gbuse0_files():
    from busepanel.gbuse0.database import load_hex
    out = []
    for p in sorted(ROOT.rglob("*")) if ROOT.is_dir() else []:
        if p.suffix.lower() == ".hex":
            try:
                out.append((p, load_hex(str(p))))
            except Exception:      # noqa: BLE001 – gBUSE1 or other files
                pass
    return out


def test_gbuse0_samples():
    files = _gbuse0_files()
    if not files:
        pytest.skip("no gBUSE0 sample databases (set BUSE_SAMPLE_DIR)")
    for path, db in files:
        assert db.fields and db.fonts, path
        for kind, fname in (("LIN", "line"), ("CIL", "dest"), ("DRU", "stop")):
            fld = db.fields.get(fname)
            for rec in list(db.records.get(kind, {}).values())[:20]:
                if fld is not None:
                    run(rec, db, fld)                    # must not raise


def test_line_full_form_for_ui():
    flat = Line.from_dict({"line": "7", "destination": "D", "stops": [{"name": "a"}, {"name": "D"}]})
    full = flat.to_dict(full=True)
    assert set(full["directions"]) == {"1"} and full["directions"]["1"]["destination"] == "D"
    ln = Line.from_dict(full)
    assert not ln.flat and ln.route(direction="1").stops[-1].name == "D"
    assert Line.from_dict(Line.from_dict(LINE).to_dict(full=True)).to_dict() == Line.from_dict(LINE).to_dict()


# ---------------------------------------------------------------- test working folder in the repo
DATA = pathlib.Path(__file__).parent / "data" / "pracovni_slozka"


@pytest.mark.parametrize("path", sorted((DATA / "trasy").glob("*.yaml")), ids=lambda p: p.name)
def test_data_lines_load(path):
    ln = Line.load(path)
    assert ln.directions and all(r.stops for r in ln.directions.values())
    for c, trips in ln.courses.items():
        for i in range(len(trips)):
            assert ln.route(course=c, index=i).stops


@pytest.mark.parametrize("path", sorted((DATA / "vozidla").rglob("*.yaml")), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_data_vehicles_build(path, univ):
    import yaml
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    # no COM ports / ESP / LCD servers in tests: the physical panels become previews
    cfg["panels"] = [{**p, "output": "virtual"} for p in cfg["panels"] if p.get("output") != "lcd"]
    per, _ = build_peripherals(cfg, univ)
    assert len(per) == len(cfg["panels"])


# ---------------------------------------------------------------- running text: one pass
def test_running_text_one_pass(univ, route):
    from busepanel.sim.vehicle import free_text_layout, make_measure, run_seconds
    prof = profile("BS120")
    long = {"text": "Velmi dlouhý text, který se na jednořádkový panel nevejde a musí běžet", "font": 3, "spacing": 1}
    _p, _lk, room, width, runs = free_text_layout(long, prof, univ.fonts)
    assert runs and room == prof.geometry.columns
    assert run_seconds(long, prof, univ.fonts) == pytest.approx((width + room) * prof.geometry.scroll_ms / 1000)
    with_line = run_seconds({**long, "line": "7"}, prof, univ.fonts, {"7": "007"})
    assert with_line < run_seconds(long, prof, univ.fonts)           # narrower field next to the line number
    assert run_seconds({"text": "Krátký", "font": 3, "spacing": 1}, prof, univ.fonts) == 0

    m = make_measure(univ.fonts, prof)
    page = {**long, "seconds": 3.0, "role": "text"}
    e = SimEngine(route, {"interior": {"sequences": {}}})
    e.measure = m
    assert e._duration(page, False) == pytest.approx(m(page))         # default: exactly one pass
    assert e._duration({**page, "seconds": 60.0, "run_time": "min"}, False) == 60.0   # at least the page time
    assert e._duration({**page, "text": "Krátký"}, False) == 3.0      # static text keeps its time


def test_scroll_ms_per_panel(univ):
    per, _ = build_peripherals({"panels": [{"name": "a", "type": "BS120", "output": "virtual", "scroll_ms": 35}]}, univ)
    assert per[0].profile.geometry.scroll_ms == 35 and profile("BS120").geometry.scroll_ms == 20


# ---------------------------------------------------------------- display systems, vehicle = panels only
def test_systems_subfolders_and_sections(tmp_path):
    from busepanel.sim.engine import load_formats, system_settings
    (tmp_path / "skupina").mkdir()
    (tmp_path / "skupina" / "muj.yaml").write_text(
        "title: Můj\ninterior: {seconds: 5}\ninterior_2: {seconds: 2}\nexterior: {seconds: 6}\n", encoding="utf-8")
    fm = load_formats(str(tmp_path))
    assert fm["skupina/muj"]["exterior"]["seconds"] == 6
    st = system_settings("skupina/muj", fm)
    assert st["interior_2"]["seconds"] == 2 and st["interior"]["format"] == "skupina/muj"


def test_vehicle_names_system_only(tmp_path):
    from busepanel.server import sim_api as A
    from busepanel.sim.engine import vehicle_system
    assert vehicle_system({"system": "a"}) == "a"
    assert vehicle_system({"settings": {"interior": {"format": "b", "seconds": 3}}}) == "b"     # older file
    cfg = A._clean_vehicle({"name": "x", "panels": [], "settings": {"interior": {"format": "b", "seconds": 3}}})
    assert cfg == {"name": "x", "panels": [], "system": "b"}


def test_format_api_and_preview(tmp_path, univ):
    import shutil
    from busepanel.server import sim_api as A
    work = tmp_path / "w"
    shutil.copytree(DATA, work)
    A.init(str(work))
    A.SIM.db = univ
    A.route_load(A.PathReq(path="trasy/linka3_kurzy.yaml"))
    A.vehicle_load(A.PathReq(path="vozidla/test/autobus.yaml"))
    assert A.vehicle_get()["system"] == "pid_autobus" and "settings" not in A.SIM.vehicle_cfg
    names = {f["name"] for f in A.formats_list()["formats"]}
    assert {"pid_autobus", "pid_tramvaj", "dpmp"} <= names
    z = A.format_get("dpmp")
    A.format_save(A.FormatSave(name="moje/pomaly", title="Pomalý", interior={**z["interior"], "seconds": 6}))
    assert (work / "systemy" / "moje" / "pomaly.yaml").exists()
    assert any(f["name"] == "moje/pomaly" and f["group"] == "moje" for f in A.formats_list()["formats"])
    with pytest.raises(Exception):
        A.format_save(A.FormatSave(name="../ven", interior={}))
    pv = A.format_preview(A.PreviewReq(interior={**z["interior"], "seconds": 6}, panel={"type": "BS120"}, phase="driving", pos=1))
    assert pv["pages"] and all(p["seconds"] == pytest.approx(6, abs=0.15) for p in pv["pages"] if not p["frame"]["dest"].get("run"))
    ext = A.format_preview(A.PreviewReq(interior=z["interior"], panel={"type": "BS210", "variant": "v1", "role": "front"}))
    assert ext["pages"][0]["frame"]["matrix"]
    A.vehicle_use_system(A.SystemReq(name="moje/pomaly"))            # Jízda: only for this run
    assert A.SIM.engine.settings["interior"]["seconds"] == 6
    assert "pid_autobus" in (work / "vozidla" / "test" / "autobus.yaml").read_text(encoding="utf-8")
    A.vehicle_save(A.VehicleSave(path="vozidla/test/novy.yaml", config=A.SIM.vehicle_cfg))
    assert "system: moje/pomaly" in (work / "vozidla" / "test" / "novy.yaml").read_text(encoding="utf-8")


@pytest.mark.parametrize("t,variant", [("BS 210.0C", "v1"), ("BS210.B", "v2"), ("bs 210.0a", "v4"),
                                       ("BS 110.19-112-1", "v2"), ("BS 110.19-028-1", "v4"), ("BS210", None)])
def test_model_variant(t, variant):
    from busepanel.peripherals import model_variant, peripheral_type
    assert model_variant(t) == variant
    assert peripheral_type({"type": t}) == "BS210"


def test_pid_two_line_and_bus_width(tmp_path, univ):
    import shutil
    from busepanel.server import sim_api as A
    work = tmp_path / "w"
    shutil.copytree(DATA, work)
    A.init(str(work))
    A.SIM.db = univ
    A.route_load(A.PathReq(path="trasy/linka3_kurzy.yaml"))
    d = A.format_get("pid_autobus")
    assert d["interior_2"]["sequences"]["next"][0]["text"] == "příští zastávka/next:"
    two = A.format_preview(A.PreviewReq(interior=d["interior"], interior_2=d["interior_2"], panel={"type": "BS220"},
                                        phase="driving", pos=1))
    f = two["pages"][0]["frame"]
    assert f["geometry"]["columns"] == 128 and f["geometry"]["rows"] == 16
    # PID: line number + heading in the upper row, the stop name over the whole lower row
    assert f["line"] and f["heading"]["lo"] == f["geometry"]["dest_left"] and f["dest"]["lo"] == 0
    assert two["pages"][0]["seconds"] == pytest.approx(4, abs=0.15)          # Obraz 1: 4 s
    one = A.format_preview(A.PreviewReq(interior=d["interior"], panel={"type": "BS120", "columns": 120}, phase="driving", pos=1))
    g = one["pages"][0]["frame"]
    assert g["geometry"]["columns"] == 120 and g["geometry"]["dest_right"] == 119 and not g.get("heading")


def test_short_names_where_full_does_not_fit():
    from busepanel.peripherals.exterior import fit_text
    db = variant_database("v2", stop_field="bottom")
    fld = db.fields["stop"]
    full = "Rohovládova Bělá, BUS terminál"
    assert fit_text(db, fld, bytes([0xEB]), full, "Rohov. Bělá") == "Rohov. Bělá"       # never split / glued
    assert fit_text(db, fld, bytes([0xEB]), full) == full                                 # no short -> as it is
    assert fit_text(db, fld, bytes([0xEB]), "Vápno", "V.") == "Vápno"                     # fits -> full name


def test_route_short_names_round_trip():
    from busepanel.sim.engine import SimEngine
    r = Route.from_dict({"line": "1", "destination": "Chlumec nad Cidlinou, žst.", "dest_short": "Chlumec n.C.",
                         "stops": [{"name": "A"}, {"name": "Rohovládova Bělá, BUS terminál", "short": "Rohov. Bělá",
                                                   "via": True}, {"name": "B"}]})
    assert Route.from_dict(r.to_dict()).stops[1].short == "Rohov. Bělá" and r.to_dict()["dest_short"] == "Chlumec n.C."
    e = SimEngine(r, {"exterior": {"sequences": {"via": [{"text": "{via_stop}"}]}}}, clock=lambda: 0.0)
    p = e.via_pages("side", 0.0)
    assert p[0]["text"] == "Rohovládova Bělá, BUS terminál" and p[0]["short"] == "Rohov. Bělá"
