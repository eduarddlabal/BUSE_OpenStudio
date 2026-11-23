"""Comprehensive tests for gBUSE0 and gBUSE1 full functionality (core, formats, MNEMO, API, round-trip)."""
from __future__ import annotations

import base64
import os
import pathlib

import pytest

from busepanel import cli
from busepanel.core import image as I, text as T
from busepanel.core.fonts import CODETABLE_LEN, CodeTable, Font, FontSet, Glyph, default_codetable
from busepanel.core.project import Project
from busepanel.core.render import render_frames, simulate_cycle_step
from busepanel.gbuse0 import database as D0, formats as F0, mnemo as M0, program as P0
from busepanel.panels.profile import Geometry
from busepanel.server import app as srv

SAMPLE_DIRS = [
    pathlib.Path(p)
    for p in (os.environ.get("BUSE_SAMPLE_DIR", ""),)
    if p and pathlib.Path(p).is_dir()
]
ALL_SAMPLE_HEXES = sorted(
    p for d in SAMPLE_DIRS for p in d.rglob("*") if p.suffix.lower() == ".hex"
)


# ------------------------------------------------------------------ CodeTable & Fonts
def test_codetable_160_layout_and_high_codes():
    ct = default_codetable()
    assert len(ct.raw) == CODETABLE_LEN
    assert ct.raw[-1] == 0xFF
    raw = bytearray(ct.raw)
    # 0xF0 maps to index 143
    raw[143] = "§".encode("cp1250")[0]
    ct2 = CodeTable(bytes(raw))
    assert ct2.decode_code(0xF0) == "§"
    assert ct2.encode_char("§") == 0xF0
    assert ct2.decode(b"A\xf0") == "A§"


def test_variable_height_glyph_and_font_manager():
    rows14 = [
        "..##..",
        ".####.",
        "##..##",
        "######",
        "##..##",
        "##..##",
        "......",
        "......",
        "..##..",
        ".####.",
        "......",
        "......",
        "......",
        "......",
    ]
    g = Glyph.from_rows(0x41, rows14)
    assert g.height == 14
    assert g.width == 6
    assert g.nbytes_col == 2
    assert g.rows() == rows14
    cols = g.column_ints()
    assert len(cols) == 6 and (cols[2] & 1) == 1

    ct = default_codetable()
    fs1 = FontSet([Font(0, {0x41: g})], ct)
    fs2 = FontSet([Font(3, {})], ct)
    assert fs2.copy_glyph(fs1, 0, 0x41, 3, 0x42) is True
    assert fs2.font(3).glyphs[0x42].height == 14
    assert fs2.copy_font(fs1, 0, 5) == 1
    assert fs2.font(5).glyphs[0x41].rows() == rows14

    fnt = fs2.to_fnt_bytes()
    fs3 = FontSet.from_fnt_bytes(fnt)
    assert fs3.font(5).glyphs[0x41].rows() == rows14

    fs2.delete_code_all(0x41)
    assert 0x41 not in fs2.font(5).glyphs
    fs2.delete_font(5)
    assert fs2.font(5) is None


# ------------------------------------------------------------------ gBUSE0 Database, MNEMO, Program, Formats
def _make_sample_db0(magic: int = D0.MAGIC) -> D0.Database0:
    ct = default_codetable()
    f = Font(3, {
        0x31: Glyph.from_rows(0x31, ["..#.", ".##.", "..#.", "..#.", "..#.", "..#.", ".###", "...."]),
        0x41: Glyph.from_rows(0x41, [".##.", "#..#", "####", "#..#", "#..#", "#..#", "#..#", "...."]),
        0x42: Glyph.from_rows(0x42, ["###.", "#..#", "###.", "#..#", "#..#", "#..#", "###.", "...."]),
        0x43: Glyph.from_rows(0x43, [".###", "#...", "#...", "#...", "#...", "#...", ".###", "...."]),
    })
    db = D0.Database0(
        columns=140,
        rows=19,
        fields={
            "line": D0.Field(0, 27, 0, 18, font=3, spacing=1, interval=1.0),
            "dest": D0.Field(28, 139, 0, 18, font=3, spacing=1, interval=1.5),
            "stop": D0.Field(28, 139, 11, 18, font=3, spacing=1, interval=2.0),
        },
        fonts={3: f},
        codetable=ct.raw,
        magic=magic,
        fw_3x=(magic == D0.MAGIC),
        version="TEST0",
        afc=[(3, 1, 0)],
        afd=[(3, 1, 0)],
        kod=bytes(range(128)),
        format_strings={1: b"\xe3\xb1TEST\r"},
    )
    db.property_flags = ["flip_all", "check_voltage"]
    db.command_list = ["l", "z", "zD", "aV"]
    db.records["LIN"] = {"001": b"\xe3\xb11\r"}
    db.record_lists["LIN"] = [(b"001", b"\xe3\xb11\r")]
    db.records["CIL"] = {"001": b"\xe3\xb1ABC\r"}
    db.record_lists["CIL"] = [(b"001", b"\xe3\xb1ABC\r")]
    db.records["DRU"] = {"0001": b"\xe3\xb1CAB\r"}
    db.record_lists["DRU"] = [(b"0001", b"\xe3\xb1CAB\r")]
    return db


@pytest.mark.parametrize("magic", [D0.MAGIC, D0.MAGIC_OLD])
def test_gbuse0_build_parse_roundtrip(magic):
    db = _make_sample_db0(magic=magic)
    blocks = D0.build(db)
    raw = I.blocks_to_image(blocks)
    assert raw[0] == magic
    db2 = D0.parse(raw)
    assert db2.magic == magic
    assert db2.version == "TEST0"
    assert db2.records["LIN"]["001"] == b"\xe3\xb11\r"
    assert db2.records["CIL"]["001"] == b"\xe3\xb1ABC\r"
    assert db2.kod == db.kod
    if magic == D0.MAGIC:
        assert db2.afc == db.afc
        assert db2.afd == db.afd
        assert db2.records["DRU"]["0001"] == b"\xe3\xb1CAB\r"
        assert db2.format_strings[1] == b"\xe3\xb1TEST\r"
        assert set(db2.property_flags) == {"flip_all", "check_voltage"}
    assert D0.bs110_warnings(blocks, db) == []


def test_gbuse0_mnemo_compose_and_program():
    db = _make_sample_db0()
    ct = CodeTable(db.codetable)
    spec = {
        "mode": "2",
        "full_panel": True,
        "suppress_stops": True,
        "interval": 2.5,
        "line1": {"text": "AB", "font": 3, "spacing": 1, "shift": 0, "align": "left"},
        "line2": {"text": "BA|CA", "font": 3, "spacing": 1, "shift": 10, "inverse": True},
    }
    rec, probs = M0.compose_record(spec, db, "dest", ct)
    assert probs == []
    mn = M0.to_mnemo(rec, ct)
    assert "b" in mn and "w" in mn and "VT" in mn
    rec2, probs2 = M0.from_mnemo(mn, ct)
    assert probs2 == [] and rec2 == rec
    dec = M0.decompose_record(rec, db, "dest", ct)
    assert dec is not None and dec["mode"] == "2" and dec["line2"]["text"] == "BA|CA"
    assert dec["full_panel"] and dec["suppress_stops"] and dec["interval"] == 2.5

    # program rotate_up / rotate_left & render_combined
    rec_rot, _ = M0.from_mnemo("f3 m1 'ABC' FF n r CR", ct)
    prog = P0.run(rec_rot, db, db.fields["dest"])
    assert len(prog.frames) == 1
    res = P0.render_combined(db, line_key="001", dest_rec=rec)
    assert len(res["frames"]) == 2 and res["suppress_stops"] and res["block"]
    assert len(P0.frame_to_ascii(res["frames"][0]["bits"], res["rows"]).splitlines()) == 19
    assert P0.frame_to_png(res["frames"][0]["bits"], res["rows"]).startswith(b"\x89PNG")


def test_auxiliary_formats_roundtrip():
    ct = default_codetable()
    db = _make_sample_db0()
    # Section files
    raw_cil = F0.write_records_file({"001": b"\xe3\xb1ABC\r"}, 3)
    assert F0.read_records_file(raw_cil, 3) == {"001": b"\xe3\xb1ABC\r"}
    assert F0.read_kod_file(F0.write_kod_file(bytes(range(128)))) == bytes(range(128))
    assert F0.read_str_file(F0.write_str_file({1: b"AB\r"})) == {1: b"AB\r"}
    afc, afd = F0.read_af_file(F0.write_af_file([(3, 1, 0)], [(0, 1, 2)], ".afm"), ".afm")
    assert afc == [(3, 1, 0)] and afd == [(0, 1, 2)]

    # .txt with $ template & DOS Kamenický @ line
    txt_in = b"$ f3 m1 [$1] LF f3 m1 ($2) CR\r\n005\tAB\tCA\r\n"
    recs, probs = F0.import_txt_gbuse0(txt_in, db, "dest", 3, ct)
    assert probs == [] and "005" in recs and b"\n" in recs["005"]
    dos_in = b"@001\t\x80\x87\r\n"  # Kamenický Čč
    recs1, probs1 = F0.import_txt_gbuse1(dos_in, FontSet([Font(3, {})], ct), 3)
    assert probs1 == [] and recs1["001"]["text"] == "Čč"

    # .bbd equivalents
    bbd = F0.export_bbd({"001": "AB", "002": "AA"}, version="V1", sort_by="alpha")
    assert F0.import_bbd(bbd) == {"001": "AB", "002": "AA"}


# ------------------------------------------------------------------ gBUSE1 FW1, CYK/DOP, MNEMO1, effects
def test_gbuse1_fw1_cyk_dop_mnemo1_and_cycle_sim():
    fs = FontSet([Font(3, {
        0x41: Glyph.from_rows(0x41, [".##.", "#..#", "####", "#..#", "#..#", "#..#", "#..#", "...."]),
        0x42: Glyph.from_rows(0x42, ["###.", "#..#", "###.", "#..#", "#..#", "#..#", "###.", "...."]),
    })], default_codetable())
    # FW 1.xx (0xAA) round-trip
    p1 = Project.new_gbuse1(fs, version="FW1TEST", panel_kind=1, fw="1.xx")
    p1.data["config"]["magic"] = I.MAGIC_FW1
    p1.data["destinations"] = {"001": "AB"}
    p1.data["kod_table"] = bytes(range(128)).hex(" ").upper()
    raw1, probs1 = p1.to_hex()
    assert probs1 == []
    img1 = I.hex_to_image(raw1)
    assert img1[0] == I.MAGIC_FW1
    q1 = Project.from_bytes(img1)
    assert q1.data["config"]["magic"] == I.MAGIC_FW1
    assert q1.data["kod_table"] == p1.data["kod_table"]

    # FW 2.12 (0xAB, typ 2) with CYK + DOP + STR + KOD round-trip
    p = Project.new_gbuse1(fs, version="FW2TEST", panel_kind=2, fw="2.12")
    p.data["destinations"] = {"001": "{t2}{blink}{z:15}AB{lf}BA"}
    p.data["supplementary"] = {"1": "AB "}
    p.data["format_strings"] = {"2": "{f3}ABA"}
    p.data["kod_table"] = bytes(range(128)).hex(" ").upper()
    raw_hex, probs = p.to_hex()
    assert probs == []
    img = I.hex_to_image(raw_hex)
    assert img[0] == I.MAGIC_FW2
    q = Project.from_bytes(img)
    assert q.data["config"].get("magic", I.MAGIC_FW2) == I.MAGIC_FW2
    assert q.data["cycles"]["0"][0]["right"]["source"] == 2
    assert q.data["supplementary"]["1"] == "AB "
    assert q.data["format_strings"]["2"] == {"text": "ABA", "font": 3}
    assert q.data["kod_table"] == p.data["kod_table"]

    # MNEMO1 roundtrip
    b, pr = p.encode_value(p.data["destinations"]["001"])
    assert not pr
    mn = T.to_mnemo1(b, fs)
    assert "t2" in mn and "b" in mn and "z15" in mn
    b2, pr2 = T.from_mnemo1(mn, fs)
    assert not pr2 and b2 == b

    # Multi-frame effects + cycle simulation
    rf = render_frames(b, fs, Geometry(135, 8))
    assert len(rf["frames"]) == 2 and rf["frames"][0]["effect"] == 2 and rf["frames"][0]["blink"] is True
    cf = simulate_cycle_step(q, 0, 0)
    assert cf["width"] == 135 and len(cf["cols"]) == 135


# ------------------------------------------------------------------ Server API & CLI tests
def test_api_endpoints_gbuse0_and_gbuse1(tmp_path):
    old_wd = srv.S.workdir
    srv.S.workdir = str(tmp_path)
    try:
        p0_path = "ext/p0.yaml"
        j = srv.project_new(srv.NewReq(
            path=p0_path, format="gbuse0", preset="side_via", fw="3.08", version="TST0", fonts_from="gbuse0",
        ))
        assert j["format"] == "gbuse0" and j["columns"] == 112 and j["rows"] == 19

        # gBUSE0 compose / autoformat / preview / bin / hex
        rc = srv.gbuse0_compose(srv.Compose0Req(section="destinations", spec={"mode": "1", "line1": {"text": "Nádraží"}}))
        assert "Nádraží" in rc["mnemo"]
        ra = srv.gbuse_autoformat(srv.AutoformatReq(text="Centrum", two_line=False))
        assert "font" in ra
        rp = srv.preview(srv.PreviewReq(section="destinations", value="f3 m1 'Nádraží' CR"))
        assert rp["format"] == "gbuse0" and len(rp["frames"]) >= 1
        assert srv.project_bin().body[0] == D0.MAGIC
        assert ":" in srv.project_hex()

        # Variable height font glyph + codetable 0xF0 + Font Manager + FNT export/import
        rg = srv.fonts_glyph(srv.GlyphReq(font=3, code=0xF0, height=12, rows=["#." * 2] * 12))
        assert rg["height"] == 12
        assert srv.fonts_codetable(srv.CodeReq(code=0xF0, char="§"))["ok"] is True
        assert srv.fonts_manage(srv.FontManageReq(action="copy_font", source="current", src_font=3, dst_font=15))["ok"] is True
        fnt_bytes = srv.fonts_export_fnt().body
        assert srv.fonts_import_fnt(srv.ImportFntReq(content_b64=base64.b64encode(fnt_bytes).decode()))["ok"] is True

        # Section export/import (.bbd, .afm)
        bbd = srv.gbuse_section_export(srv.SectionExportReq(ext=".bbd")).body
        ri = srv.gbuse_section_import(srv.SectionImportReq(name="test.bbd", content_b64=base64.b64encode(bbd).decode()))
        assert ri["counts"]["equivalents"] >= 1

        # gBUSE1 typ 2 + CYK/DOP endpoints
        p1_path = "int/p1.yaml"
        j1 = srv.project_new(srv.NewReq(
            path=p1_path, format="gbuse1", panel_kind=2, fw="2.12", version="TST1", fonts_from="gbuse1",
        ))
        assert j1["format"] == "gbuse1"
        rm = srv.mnemo_convert(srv.MnemoReq(direction="to_mnemo", value={"text": "AHOJ", "font": 3, "blink": True}))
        assert "b" in rm["mnemo"]
        rpc = srv.preview(srv.PreviewReq(cycle_id=0, step_idx=0))
        assert rpc["cycle_frame"]["width"] == 135
        cyk_bytes = srv.gbuse_section_export(srv.SectionExportReq(ext=".cyk")).body
        ric = srv.gbuse_section_import(srv.SectionImportReq(name="test.cyk", content_b64=base64.b64encode(cyk_bytes).decode(), replace=True))
        assert ric["counts"]["cycles"] >= 1
    finally:
        srv.S.workdir = old_wd


def test_cli_new_and_render_gbuse0(tmp_path):
    proj = tmp_path / "cli0.yaml"
    png = tmp_path / "out.png"
    cli.main(["new", str(proj), "--format", "gbuse0", "--preset", "side_via", "--version", "CLI0"])
    cli.main(["check", str(proj)])
    cli.main(["render", str(proj), "001", "--line", "001", "--stop", "0001", "--png", str(png)])
    assert png.read_bytes().startswith(b"\x89PNG")


def test_project_manager_crud_and_upload(tmp_path):
    from fastapi import HTTPException
    old_wd = srv.S.workdir
    srv.S.workdir = str(tmp_path)
    srv.S.project = None
    srv.S.path = None
    try:
        # 1. Create with auto-slugged path, name & description, save_now=True
        j = srv.project_new(srv.NewReq(
            name="Brno Čelní BS210",
            description="Testovací popis",
            format="gbuse0",
            preset="front",
            fw="3.08",
            version="BRNO1",
            fonts_from="gbuse0",
            save_now=True,
        ))
        assert j["name"] == "Brno Čelní BS210"
        assert j["description"] == "Testovací popis"
        assert j["rel_path"] == "projekty/brno-celni-bs210/projekt.yaml"
        assert (tmp_path / "projekty/brno-celni-bs210/projekt.yaml").exists()
        assert (tmp_path / "projekty/brno-celni-bs210/fonts.yaml").exists()

        # Verify HEX/BIN round-trip ignores `meta` without error
        hx = srv.project_hex()
        raw_bin = srv.project_bin().body
        assert raw_bin[0] == D0.MAGIC and ":" in hx

        # 2. /api/files returns descriptive `projects` entries
        fl = srv.files()
        assert len(fl["projects"]) == 1
        card = fl["projects"][0]
        assert card["name"] == "Brno Čelní BS210"
        assert card["description"] == "Testovací popis"
        assert card["format"] == "gbuse0" and card["columns"] == 140 and card["rows"] == 19

        # 3. Upload HEX from computer via upload-open
        up = srv.project_upload_open(srv.UploadOpenReq(
            filename="Linka_99.hex",
            content_b64=base64.b64encode(hx.encode("ascii")).decode("ascii"),
            name="Nahraná 99",
        ))
        assert up["name"] == "Nahraná 99"
        assert (tmp_path / up["rel_path"]).exists()

        # 4. Duplicate & Save As
        dup = srv.project_duplicate(srv.DuplicateReq(source_path=up["rel_path"], name="Kopie devadesátdevítky"))
        assert dup["ok"] is True and dup["project"]["name"] == "Kopie devadesátdevítky"
        sa = srv.project_save_as(srv.SaveAsReq(name="Ulozeno Jako", description="Novy popis"))
        assert sa["name"] == "Ulozeno Jako" and sa["rel_path"] == "projekty/ulozeno-jako/projekt.yaml"

        # 5. Path traversal protection
        with pytest.raises(HTTPException) as ex:
            srv.project_delete(srv.DeleteReq(path="../outside.yaml"))
        assert ex.value.status_code == 400

        # 6. Delete active project & companion files
        del_res = srv.project_delete(srv.DeleteReq(path=sa["rel_path"]))
        assert del_res == {"ok": True, "closed": True}
        assert not (tmp_path / sa["rel_path"]).exists()
        assert not (tmp_path / "projekty/ulozeno-jako").exists()
        assert srv.S.project is None
    finally:
        srv.S.workdir = old_wd


# ------------------------------------------------------------------ Parametrized real sample HEX round-trip
@pytest.mark.parametrize("path", ALL_SAMPLE_HEXES, ids=lambda p: p.name)
def test_all_sample_hex_yaml_bin_roundtrip(path, tmp_path):
    raw = path.read_bytes().decode("ascii")
    orig_img = I.hex_to_image(raw)
    if orig_img[:2] == b"cT":
        pytest.skip("gBUSE2b (Perla) format")
    p = Project.from_hex(str(path))
    yml = tmp_path / "p.yaml"
    p.save(str(yml))
    q = Project.load(str(yml))
    out_hex, _probs = q.to_hex()
    assert I.hex_to_image(out_hex) == orig_img
    out_bin, _probs2 = q.to_bin()
    assert out_bin == orig_img
