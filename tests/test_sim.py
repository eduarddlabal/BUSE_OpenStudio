import pathlib

import pytest

from busepanel.core import image as I
from busepanel.core.icons import ICON_CODES, ICON_FONT
from busepanel.core.project import Project
from busepanel.core import text as T
from busepanel.outputs.virtual import VirtualPanel
from busepanel.panels.profile import profile
from busepanel.sim.engine import SimEngine
from busepanel.sim.route import Route
from busepanel.sim.universal_db import build_universal
from busepanel.sim.vehicle import spec_telegrams, line_key

@pytest.fixture(scope="module")
def univ():
    return build_universal(None, extra_lines=["607", "7", "X1"])      # built-in fonts


@pytest.fixture
def route():
    return Route.from_dict({"line": "7", "destination": "Sídliště Dukla", "stops": [
        {"name": "Hlavní nádraží", "zone": "P", "icons": ["vlak"]},
        {"name": "Masarykovo nám.", "zone": "P", "request": True},
        {"name": "Sídliště Dukla", "zone": "1"}],
        "messages": {"odklon": "Spoj jede odklonem"}})


# ---------------------------------------------------------------- universal database
def test_universal_db(univ):
    out, problems = univ.to_hex()
    assert not problems
    im = I.parse(I.hex_to_image(out))
    assert {"l", "z", "x", "v", "zI", "zM", "aV"} <= set(im.commands)
    assert im.version == "UNIV"
    assert len(im.records["LIN"]) == 100
    assert len(I.hex_to_image(out)) < 0x8000
    assert univ.fonts.font(ICON_FONT) is not None


def test_icon_markup(univ):
    b, probs = T.encode("{f3}Vlak {icon:vlak} zde", univ.fonts)
    assert not probs
    assert bytes([0xE0 + ICON_FONT, ICON_CODES["vlak"], 0xE3]) in b


# ---------------------------------------------------------------- virtual panel (real-panel behaviour)
def test_virtual_modes(univ):
    v = VirtualPanel("t", univ)
    v.send("l007", "z000")
    assert v.mode == "normal"
    v.send("l607")
    assert v.values["l"] == "007"      # firmware uses only the last two digits
    v.send(b"vAHOJ", "x9996")
    assert v.mode == "text" and v.frame()["dest"]["width"] > 0
    v.send("x0001")
    assert v.mode == "stop"
    v.send("x9999")
    assert v.mode == "normal" and v.frame()["line"] is not None
    v.send(b"vX", "x9997")
    f = v.frame()
    assert v.mode == "line_text" and f["line"] and f["dest"]


# ---------------------------------------------------------------- engine
def test_engine_sequence(route):
    t = [1000.0]
    e = SimEngine(route, {"interior": {"items": ["next", "zone"], "seconds": 4}}, clock=lambda: t[0])
    e.event("start")
    assert e.phase == "at_stop" and e.interior()["line"] == "7"
    assert e.interior()["text"] == "Směr / Direction" and e.interior()["font"] == 0
    e.event("depart")
    assert e.phase == "driving" and e.pos == 1
    seq = []
    for k in range(6):               # pages: heading, name, request, zone heading, zone value
        seq.append(e.interior(t[0] + 4 * k + 0.1)["text"])
    assert seq[:5] == ["Příští zastávka / Next stop", "Masarykovo nám.", "zastávka na znamení / request stop",
                       "Tarifní zóna / Zone", "P "]
    # two-line panel: heading together with the value
    two = e.interior(t[0] + 0.1, two_line=True)
    assert two["heading"] == "Příští zastávka / Next stop" and two["text"] == "Masarykovo nám."
    e.event("message", key="odklon")
    texts = {e.interior(t[0] + k)["text"] for k in range(0, 40)}
    assert "Spoj jede odklonem" in texts
    e.event("clear_message")
    e.event("arrive"); e.event("depart"); e.event("arrive")
    assert e.phase == "ended" and "Konečná" in e.interior()["text"]
    e.event("prev")
    assert e.phase == "driving" and e.pos == 2
    e.event("prev")
    assert e.phase == "at_stop" and e.pos == 1


def test_spec_telegrams(univ):
    bs120 = profile("BS120")
    tel = spec_telegrams({"line": "7", "text": "Sídliště Dukla"}, bs120, univ.fonts)
    assert tel[0] == "l007" and tel[1] == "x9997" and tel[2].startswith(b"v")
    tel = spec_telegrams({"line": None, "text": "Příští {icon:metro}"}, bs120, univ.fonts)
    assert tel[0] == "x9996" and bytes([0xE0 + ICON_FONT, ICON_CODES["metro"]]) in tel[1]
    tel = spec_telegrams({"line": "607", "text": "Chlumec"}, bs120, univ.fonts, univ.data["line_map"])
    assert tel[0] == "l099"
    tel = spec_telegrams({"line": "X7", "text": "Dukla"}, bs120, univ.fonts)
    assert tel[0] == "x9996" and tel[1].startswith(b"vX7")
    assert line_key("12") == "012" and line_key("X1") is None and line_key("607") is None
    assert univ.data["line_map"] == {"607": "099", "X1": "098"}
    assert line_key("607", univ.data["line_map"]) == "099"
    assert univ.data["lines"]["099"]["text"] == "607"


def test_route_csv():
    r = Route.from_csv("poradi;zastavka;pasmo;ikony;na_znameni\n2;B;P;;ano\n1;A;P;vlak metro;\n", "9", "B")
    assert [s.name for s in r.stops] == ["A", "B"]
    assert r.stops[0].icons == ["vlak", "metro"] and r.stops[1].request


def test_stopped_vehicle_sends_only_to_virtual(univ, route):
    from busepanel.outputs.base import Output
    from busepanel.sim.vehicle import PanelSlot, Vehicle

    class Spy(Output):
        kind = "serial"
        def __init__(self):
            super().__init__("spy"); self.got = []
        def send(self, *t):
            self.got.append(t)

    spy, virt = Spy(), VirtualPanel("v", univ)
    e = SimEngine(route)
    e.event("start")
    veh = Vehicle(e, univ.fonts, [PanelSlot("real", "interior", profile("BS120"), spy),
                                  PanelSlot("virt", "interior", profile("BS120"), virt)])
    veh.tick(virtual_only=True)
    assert spy.got == [] and virt.sent > 0
    veh.tick()
    assert spy.got


def test_order_for_panel():
    from busepanel.live.modes import order_for_panel
    # mode change: numbers, then switch, then text
    out, m = order_for_panel([b"vA", "x9996"], None)
    assert out == ["x9996", b"vA"] and m == "x9996"
    # same mode: text only, no repeated switch
    out, m = order_for_panel(["x9996", b"vB"], m)
    assert out == [b"vB"] and m == "x9996"
    # back to line + text
    out, m = order_for_panel(["l099", "x9997", b"vC"], m)
    assert out == ["l099", "x9997", b"vC"] and m == "x9997"
