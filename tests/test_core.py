import os
import pathlib

import pytest

from busepanel.core import ibis, image as I, text as T
from busepanel.live import modes as live
from busepanel.core.importers import to_records, rows_from_csv
from busepanel.panels.profile import Geometry
from busepanel.core.link import frame
from busepanel.core.project import Project
from busepanel.core.render import compose

# Operator data (HEX files from gBUSE) are NOT part of the repository. Point BUSE_SAMPLE_DIR to a
# folder with your own files to run the round-trip tests against them; otherwise they are skipped.
ROOT = pathlib.Path(os.environ.get("BUSE_SAMPLE_DIR", "/nonexistent"))
HEXES = sorted(p for p in ROOT.rglob("*") if p.suffix.lower() == ".hex") if ROOT.is_dir() else []
PCE = ROOT / "pce_trolejbusy.hex"


@pytest.fixture(scope="module")
def pce():
    if not PCE.exists():
        pytest.skip("sample operator data not available (set BUSE_SAMPLE_DIR)")
    return Project.from_hex(str(PCE))


@pytest.fixture(scope="module")
def builtin():
    from busepanel.sim.universal_db import build_universal
    return build_universal(None, font="builtin")


# ---------------------------------------------------------------- protocol
def test_ibis_checksum():
    assert ibis.encode("aV1") == bytes.fromhex("61 56 31 0d 74")
    assert ibis.encode("l012") == bytes.fromhex("6c 30 31 32 0d 2d")
    assert ibis.decode(b"aVNOVa\rs") == ("aVNOVa", True)


def test_binary_frame():
    assert frame(b"\x0f") == bytes.fromhex("01 0f f0")
    assert sum(frame(bytes([5, 0, 0, 0]) + b"x" * 32)) & 0xFF == 0


# ---------------------------------------------------------------- formats
def _gbuse1(paths):
    out = []
    for p in paths:
        try:
            if I.hex_to_image(p.read_bytes().decode("ascii"))[:1] == b"\xab":
                out.append(p)
        except Exception:      # noqa: BLE001
            pass
    return out


HEXES = _gbuse1(HEXES)


@pytest.mark.parametrize("path", HEXES, ids=lambda p: p.name)
def test_image_roundtrip(path):
    txt = path.read_bytes().decode("ascii")
    assert I.blocks_to_hex(I.build(I.parse(I.hex_to_image(txt)))) == txt


@pytest.mark.parametrize("path", HEXES, ids=lambda p: p.name)
def test_yaml_roundtrip(path, tmp_path):
    raw = path.read_bytes().decode("ascii")
    orig_img = I.hex_to_image(raw)
    p = Project.from_hex(str(path))
    p.save(str(tmp_path / "p.yaml"))
    q = Project.load(str(tmp_path / "p.yaml"))
    out, problems = q.to_hex()
    if b"gBUSE1 - 1.00" in orig_img:
        assert problems == []
    assert I.hex_to_image(out) == orig_img


def test_header_fields(pce):
    im, _ = pce.to_image()
    assert im.columns == 135
    assert im.commands == ["l", "z", "x", "u", "a", "aV"]
    assert im.version == "NOVá"


def test_text_structured(pce):
    v = pce.data["destinations"]["010"]
    assert v["run"] is True and v["font"] == 3 and "Bělehradskou" in v["text"]
    b, probs = T.encode(v, pce.fonts)
    assert b.startswith(b"\xe3\xb1\x1bt\x11") and b.endswith(b"\x0b\r") and not probs


def test_markup_roundtrip(pce):
    raw = bytes([0xE3, 0xB2, 0x1B, ord("a"), 0x15]) + b"AB{" + bytes([0x1B, ord("i")]) + b"C\r"
    m = T.decode(raw, pce.fonts)
    assert isinstance(m, str) and "{1B}" in m
    assert T.encode(m, pce.fonts)[0] == raw


def test_missing_glyph_reported(pce):
    _, probs = T.encode({"text": "€", "font": 3}, pce.fonts)
    assert probs


# ---------------------------------------------------------------- layout (measured on BS120)
def test_alignment_rules():
    g = Geometry()
    assert g.x_for_align("left", 30) == 24
    assert g.x_for_align("right", 43) == 69          # "VPRAVO" ended at column 111
    assert g.x_for_align("center", 43) is None


def test_render_overflow(pce):
    b, _ = T.encode({"text": "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "font": 3, "spacing": 1, "x": 24}, pce.fonts)
    _fr, rep = compose(pce.geometry, pce.fonts, None, b)
    assert rep["overflow"] and rep["visible"] == (24, 111)


def test_align_in_project(pce):
    p = pce.copy()
    p.data["destinations"] = {"003": {"text": "VPRAVO", "font": 3, "spacing": 1, "align": "right"}}
    b, _ = p.encode_value(p.data["destinations"]["003"])
    assert b[2:5] == bytes([0x1B, ord("s"), 0x10 + 69])


# ---------------------------------------------------------------- import / live
def test_csv_import():
    rows = rows_from_csv("sekce;cislo;text;font;bezici\ncile;7;Hlavní nádraží;3;ano\nlinky;12;12;;\n")
    recs, probs = to_records(rows)
    assert not probs
    assert recs["destinations"]["007"] == {"text": "Hlavní nádraží", "font": 3, "run": True}
    assert recs["lines"]["012"] == "12"


def test_clock_build(pce):
    p = pce.copy()
    p.data.pop("stops", None)
    live.add_clock(p, "stops")
    assert len(p.data["stops"]) == 1440
    assert "x" in p.data["config"]["commands"]
    import datetime as dt
    assert live.clock_telegrams(dt.datetime(2026, 1, 1, 7, 5)) == ["x0705"]
    out, problems = p.to_hex()
    assert not problems and len(I.hex_to_image(out)) < 0x10000


# ---------------------------------------------------------------- built-in fonts (no operator data)
def test_builtin_roundtrip(builtin, tmp_path):
    out, problems = builtin.to_hex()
    assert not problems
    builtin.save(str(tmp_path / "u.yaml"))
    again, _ = Project.load(str(tmp_path / "u.yaml")).to_hex()
    assert again == out
    im = I.parse(I.hex_to_image(out))
    assert I.blocks_to_hex(I.build(im)) == out


def test_builtin_czech_text(builtin):
    b, probs = T.encode({"text": "Příští zastávka: Žluťoučký kůň", "font": 0}, builtin.fonts)
    assert not probs and b.startswith(b"\xe0")
    assert T.decode(b, builtin.fonts)["text"] == "Příští zastávka: Žluťoučký kůň"


def test_builtin_overflow_and_align(builtin):
    b, _ = T.encode({"text": "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "font": 3, "spacing": 1, "x": 24}, builtin.fonts)
    _fr, rep = compose(builtin.geometry, builtin.fonts, None, b)
    assert rep["overflow"] and rep["visible"] == (24, 111)
    p = builtin.copy()
    p.data["destinations"] = {"003": {"text": "VPRAVO", "font": 3, "spacing": 1, "align": "right"}}
    b, _ = p.encode_value(p.data["destinations"]["003"])
    w = len(compose(p.geometry, p.fonts, None, T.encode({"text": "VPRAVO", "font": 3, "spacing": 1}, p.fonts)[0])[0].cols)
    assert b[2:4] == bytes([0x1B, ord("s")]) and b[4] - 0x10 + 1 > 24


def test_gbuse_font_universal():
    from busepanel.sim.universal_db import build_universal
    db = build_universal(None)                      # default = original gBUSE fonts
    assert [f.id for f in db.fonts.fonts][:4] == [0, 1, 2, 3]
    b, probs = T.encode({"text": "Příští zastávka: Žluťoučký kůň", "font": 0}, db.fonts)
    assert not probs
