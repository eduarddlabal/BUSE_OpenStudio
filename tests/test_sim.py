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
    assert univ.fonts.font(ICON_FONT) is None                    # icons live in the text fonts
    assert all(ICON_CODES["vlak"] in univ.fonts.font(i).glyphs for i in (0, 1, 2, 3))


def test_icon_markup(univ):
    b, probs = T.encode("{f3}Vlak {icon:vlak} zde", univ.fonts)
    assert not probs
    assert bytes([0x20, ICON_CODES["vlak"], 0x20]) in b and 0xE0 + ICON_FONT not in b


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
    assert tel[0] == "x9996" and bytes([ICON_CODES["metro"]]) in tel[1] and 0xE0 + ICON_FONT not in tel[1]
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


# ---------------------------------------------------------------- DPP format (preset)
def test_dpp_preset(route, univ):
    from busepanel.sim.engine import preset_settings
    e = SimEngine(route, {"interior": preset_settings("dpp")}, clock=lambda: 0.0)
    e.event("start")
    d = e.interior()                                   # zastávka: šipka + cíl velkými písmeny
    assert d["line"] == "7" and d["text"] == "{icon:sipka} SÍDLIŠTĚ DUKLA"
    e.event("depart")                                  # mimo zastávku, příští je na znamení
    d = e.interior()
    assert d["line"] == "7" and d["run"]
    assert d["text"] == "Masarykovo nám. - na znamení / request stop {icon:znameni}"
    tel = spec_telegrams(d, profile("BS120"), univ.fonts, {})
    assert tel[0] == "l007" and bytes([0x1B, 0x74, 0x11]) in tel[-1]
    e.event("arrive"); e.event("depart")
    d = e.interior()
    assert d["line"] == "7" and d["text"] == "Sídliště Dukla" and not d["run"]
    e.event("arrive")
    d = e.interior()
    assert e.phase == "ended" and d["line"] == "7" and d["text"].startswith("Konečná zastávka, prosíme vystupte")


def test_default_line_only_at_stop(route):
    e = SimEngine(route, clock=lambda: 0.0)
    e.event("start"); e.event("depart")
    assert e.interior()["line"] is None


def test_universal_db_relocates_i_acute(univ):
    # Í at 0x8B ends free text on the BS120 -> moved to 0xAA
    b, probs = T.encode("{f3}HLAVNÍ", univ.fonts)
    from busepanel.core.gbuse_font import fontset
    assert not probs and 0x8B not in b and 0x40 in b
    assert univ.fonts.font(3).glyphs[0x40].data == fontset("gbuse").font(3).glyphs[0x8B].data
    assert T.encode("{f3}Ľ", univ.fonts)[0][1:2] == b"\x9c"                # Ľ stays


def test_icon_codes_are_characters_for_bs120():
    # firmware: 0xB0-0xBF spacing, 0xC0-0xDF dropped, 0xE0-0xEF font, 0xFF end of font
    from busepanel.core.icons import ICON_CODES
    for c in ICON_CODES.values():
        assert 0x20 <= c < 0xB0 or 0xF0 <= c < 0xFF
    assert len(set(ICON_CODES.values())) == len(ICON_CODES)
    assert not {0xAF, 0xF1, 0xF2, 0xF3, 0xF4, 0xF5} & set(ICON_CODES.values())   # original pictograms


# ---------------------------------------------------------------- LCD panel (web page)
def _pid_route():
    return Route.from_dict({"line": "152", "destination": "Českomoravská", "stops": [
        {"name": "Sídliště Čimice", "zone": "P", "minutes": 0},
        {"name": "Ládví", "zone": "P", "icons": ["metro-C"], "minutes": 1},
        {"name": "Štěpničná", "zone": "P", "minutes": 3},
        {"name": "Vysočanská", "zone": "P", "icons": ["metro-B", "vlak-S"], "request": True, "minutes": 13},
        {"name": "Českomoravská", "zone": "P", "icons": ["metro-B"], "minutes": 16}]})


def test_lcd_view():
    from busepanel.extras.lcd.view import lcd_view
    t = [1000.0]
    e = SimEngine(_pid_route(), clock=lambda: t[0])
    e.event("start")
    v = lcd_view(e)
    assert v["line"] == "152" and v["zone"] == "P" and v["first"]["name"] == "Sídliště Čimice"
    assert v["first"]["minutes"] is None                      # at a stop: no countdown
    assert [r["name"] for r in v["rows"]] == ["Ládví", "Štěpničná", "Vysočanská"]
    assert v["terminal"]["name"] == "Českomoravská" and v["terminal"]["minutes"] == 16
    e.event("depart")
    v = lcd_view(e)
    assert v["first"]["name"] == "Ládví" and v["first"]["minutes"] == 1
    assert v["rows"][0]["minutes"] == 3 and v["terminal"]["minutes"] == 16
    t[0] += 60                                                 # one minute later
    assert lcd_view(e)["terminal"]["minutes"] == 15
    e.event("end")
    v = lcd_view(e)
    assert v["first"]["name"] == "Českomoravská" and v["rows"] == [] and v["terminal"] is None


def test_stop_minutes_roundtrip():
    r = _pid_route()
    assert r.stops[1].minutes == 1 and r.to_dict()["stops"][1]["minutes"] == 1
    assert "minutes" not in Route.from_dict({"line": "1", "stops": [{"name": "A"}]}).to_dict()["stops"][0]


def test_led_ignores_lcd_icon_details(univ):
    e = SimEngine(_pid_route(), clock=lambda: 0.0)
    e.event("start"); e.event("depart")
    icons = e._context(0.0)["icons"]
    assert icons == " {icon:metro}"
    d = {"line": None, "text": "Ládví" + icons, "font": 3, "spacing": 1}
    tel = spec_telegrams(d, profile("BS120"), univ.fonts, {})       # encodes without error
    assert bytes([ICON_CODES["metro"]]) in tel[-1]


def test_lcd_server_runs_on_own_port():
    import json
    import socket
    import time
    import urllib.request
    from busepanel.extras.lcd.server import LcdServers
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = LcdServers()
    srv.sync({port: "127.0.0.1"})
    try:
        for _ in range(50):
            try:
                page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).read().decode()
                break
            except OSError:
                time.sleep(0.1)
        assert "lcd.js" in page
        st = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/lcd", timeout=2).read())
        assert "rows" in st
    finally:
        srv.stop_all()
    assert not srv.servers


def test_lcd_server_port_in_use_is_reported():
    import socket
    import time
    from busepanel.extras.lcd.server import LcdServers
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        srv = LcdServers()
        srv.sync({port: "127.0.0.1"})
        for _ in range(50):
            if srv.error(port):
                break
            time.sleep(0.1)
        assert "obsazený" in srv.error(port)
        srv.stop_all()
