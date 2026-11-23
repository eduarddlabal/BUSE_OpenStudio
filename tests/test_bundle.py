"""Tests for multi-panel ProjectBundle, built-in demo/empty projects, propagation, preview-all, ZIP export & API."""
from __future__ import annotations

import io
import zipfile

from busepanel import cli
from busepanel.core import image as I
from busepanel.core.bundle import ProjectBundle, build_demo_bundle, build_empty_bundle
from busepanel.gbuse0 import database as D0
from busepanel.server import app as srv


def test_demo_and_empty_bundle_roundtrip_and_check(tmp_path):
    demo = build_demo_bundle()
    assert len(demo.panels) == 3
    assert [p.format for p in demo.panels] == ["gbuse0", "gbuse0", "gbuse1"]

    # Every panel builds clean HEX & BIN without glyph errors
    for pe in demo.panels:
        rep = pe.project.check()
        errs = [x for x in rep if x["level"] == "error"]
        assert errs == [], f"{pe.id}: {errs}"
        hx, probs = pe.project.to_hex()
        assert probs == [] and len(hx) > 100
        bn, _ = pe.project.to_bin()
        expected_magic = (D0.MAGIC, D0.MAGIC_OLD) if pe.format == "gbuse0" else (I.MAGIC_FW1, I.MAGIC_FW2)
        assert bn[0] in expected_magic

    manifest = tmp_path / "demo" / "projekt.yaml"
    demo.save(str(manifest))
    assert ProjectBundle.is_bundle_yaml(str(manifest))
    assert (tmp_path / "demo" / "zive.yaml").exists()
    assert (tmp_path / "demo" / "vozidlo.yaml").exists()

    reloaded = ProjectBundle.load(str(manifest))
    assert reloaded.meta["name"] == demo.meta["name"]
    assert [p.id for p in reloaded.panels] == ["celni", "bocni", "vnitrni"]
    assert reloaded.live["presets"][0]["name"] == "Služební jízda"

    empty = build_empty_bundle(preset="bus_4", name="Prázdný 4p")
    assert [p.id for p in empty.panels] == ["celni", "bocni", "zadni", "vnitrni"]
    for pe in empty.panels:
        assert pe.project.data["lines"] == {}
        assert pe.project.data["destinations"] == {}
        assert len(pe.project.fonts.fonts) >= 1


def test_bundle_propagate_and_export_zip():
    b = build_empty_bundle(preset="bus_4")
    # Add a long record to front gBUSE0 panel and propagate to side/rear/interior
    b.panel("celni").project.data["destinations"]["015"] = "f7 m1 'NÁDRAŽÍ CENTRUM - TERMINÁL SEVER' CR"
    b.panel("celni").project.data["lines"]["015"] = "f8 m1 '15' CR"
    b.panel("bocni").project.data["stops"]["0010"] = "f11 m1 'Křižovatka' CR"

    r_dest = b.propagate_record("celni", "destinations", "015")
    assert "Boční panel" in r_dest["updated"]
    assert "Vnitřní jednořádek" in r_dest["updated"]
    assert any("Zadní" in name for name, _ in r_dest["skipped"])
    assert "NÁDRAŽÍ CENTRUM - TERMINÁL SEVER" in b.panel("bocni").project.data["destinations"]["015"]
    vn_rec = b.panel("vnitrni").project.data["destinations"]["015"]
    assert vn_rec["text"] == "NÁDRAŽÍ CENTRUM - TERMINÁL SEVER"
    assert vn_rec.get("run") is True  # long destination automatically becomes running text on BS120

    r_line = b.propagate_record("celni", "lines", "015")
    assert "Zadní panel" in r_line["updated"]
    assert "15" in b.panel("zadni").project.data["lines"]["015"]

    r_stop = b.propagate_record("bocni", "stops", "0010")
    assert "Vnitřní jednořádek" in r_stop["updated"]
    assert b.panel("vnitrni").project.data["stops"]["0010"]["text"] == "Křižovatka"

    zdata = b.export_zip_bytes()
    with zipfile.ZipFile(io.BytesIO(zdata), "r") as zf:
        names = set(zf.namelist())
        assert {"celni.hex", "celni.bin", "bocni.hex", "bocni.bin",
                "zadni.hex", "zadni.bin", "vnitrni.hex", "vnitrni.bin",
                "ekvivalenty.bbd", "manifest.txt"} <= names
        assert zf.read("vnitrni.bin")[0] == I.MAGIC_FW2
        assert zf.read("celni.bin")[0] == D0.MAGIC


def test_bundle_server_apis_and_single_upgrade(tmp_path):
    old_wd = srv.S.workdir
    srv.S.workdir = str(tmp_path)
    srv.S.bundle = None
    srv.S.active_panel = None
    srv.S.project = None
    srv.S.path = None
    try:
        # 1. Start from demo bundle via API
        j = srv.bundle_new(srv.BundleNewReq(template="demo_bus", save_now=True))
        assert j["bundle"] is not None
        assert len(j["bundle"]["panels"]) == 3
        assert j["format"] == "gbuse0"

        # /api/files shows 1 bundle entry and does NOT expose panely/* sub-files
        fl = srv.files()
        assert fl["files"] == [j["rel_path"]]
        assert fl["projects"][0]["kind"] == "bundle"
        assert len(fl["projects"][0]["panels"]) == 3

        # 2. Switch active panel to interior gBUSE1 & run combined preview across all panels
        j2 = srv.bundle_select_panel(srv.BundleSelectReq(panel_id="vnitrni"))
        assert j2["format"] == "gbuse1"
        pa = srv.bundle_preview_all(srv.PreviewAllReq(line_key="001", dest_key="001", stop_key="0001"))
        assert len(pa["panels"]) == 3
        assert [x["format"] for x in pa["panels"]] == ["gbuse0", "gbuse0", "gbuse1"]

        # 3. Propagate edited destination from interior back to front/side & export ZIP
        srv.S.project.data["destinations"]["022"] = {"text": "NOVÁ ZASTÁVKA", "font": 3, "spacing": 1}
        pr = srv.bundle_propagate(srv.PropagateReq(section="destinations", key="022", data=srv.S.project.data))
        assert set(pr["updated"]) >= {"Čelní panel", "Boční panel"}
        zresp = srv.bundle_export_zip()
        assert zresp.body[:2] == b"PK"

        # 4. Duplicate & delete bundle
        dup = srv.project_duplicate(srv.DuplicateReq(source_path=j["rel_path"], name="Kopie Bus"))
        assert dup["ok"] is True and dup["project"]["kind"] == "bundle"
        del_r = srv.project_delete(srv.DeleteReq(path=dup["project"]["path"]))
        assert del_r == {"ok": True, "closed": False}

        # 5. Live template save & list
        srv.live_template_save(srv.LiveTemplateReq(name="Zkouška", text="ZKOUSKA", kind="v", scope="template"))
        srv.live_template_save(srv.LiveTemplateReq(name="Linková", text="SLUZEBNE", kind="v", scope="project"))
        lt = srv.live_templates()["presets"]
        assert any(x["name"] == "Zkouška" and x["scope"] == "template" for x in lt)
        assert any(x["name"] == "Linková" and x["scope"] == "project" for x in lt)

        # 6. Upgrade an existing single-panel project by adding a second panel
        s1 = srv.project_new(srv.NewReq(name="Samostatný čelní", format="gbuse0", preset="front", save_now=True))
        assert s1["bundle"] is None
        s2 = srv.bundle_add_panel(srv.BundleAddPanelReq(name="Vnitřní BS120", role="interior", format="gbuse1", panel_kind=1))
        assert s2["bundle"] is not None and len(s2["bundle"]["panels"]) == 2
        assert [p["format"] for p in s2["bundle"]["panels"]] == ["gbuse0", "gbuse1"]
    finally:
        srv.S.workdir = old_wd
        srv.S.bundle = None
        srv.S.active_panel = None
        srv.S.project = None
        srv.S.path = None


def test_cli_new_bundle_and_check(tmp_path):
    target = tmp_path / "cli_demo"
    cli.main(["new-bundle", str(target), "--template", "demo_bus", "--name", "CLI Sada"])
    manifest = target / "projekt.yaml"
    assert manifest.exists()
    cli.main(["check", str(manifest)])
