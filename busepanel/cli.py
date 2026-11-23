"""Command line interface: busepanel <command> …"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

from . import __version__
from .core.project import Project, dump_yaml


def _say(msg):
    print(msg, flush=True)


def _progress():
    state = {}

    def cb(stage, done, total):
        pct = 100 * done // total if total else None
        key = (stage, pct // 10 if pct is not None else done // 4096)
        if key == state.get("last"):
            return
        state["last"] = key
        print(f"  {stage}: {pct} %" if pct is not None else f"  {stage}: {done} B", flush=True)
    return cb


def _port(a) -> str:
    if not getattr(a, "port", None):
        from .loader.device import default_port
        a.port = default_port()
    return a.port


def _panel(a):
    from .loader.device import Panel
    return Panel(_port(a), a.address, log=_say, progress=_progress())


def cmd_ports(a):
    from .loader.device import list_ports
    ports = list_ports()
    real = [p for p in ports if (p["description"] or "n/a").lower() != "n/a"]
    for p in real or ports:
        print(f"{p['port']:8} {p['description']}")
    if real and len(real) < len(ports):
        print(f"({len(ports) - len(real)} portů bez zařízení skryto)")


def cmd_scan(a):
    from .loader.device import Panel
    found = Panel.scan(_port(a), log=_say)
    if not found:
        print("Žádný panel neodpověděl (zkontrolujte port, napájení a že DTR není aktivní).")


def cmd_send(a):
    _panel(a).send(*a.telegrams)


def cmd_query(a):
    print(_panel(a).query(a.telegram))


def cmd_backup(a):
    r = _panel(a).backup(eprom=a.eprom)
    print()
    base = os.path.splitext(a.output)[0]
    with open(base + ".bin", "wb") as f:
        f.write(r["flash"])
    print(f"FLASH → {base}.bin ({len(r['flash'])} B)")
    if r["eprom"]:
        with open(base + "_eprom.bin", "wb") as f:
            f.write(r["eprom"])
        print(f"EPROM → {base}_eprom.bin")
    try:
        p = Project.from_bytes(r["flash"], ibis_address=a.address)
        p.save(base + ".yaml")
        hx, _ = p.to_hex()
        with open(base + ".hex", "w", newline="", encoding="ascii") as f:
            f.write(hx)
        print(f"projekt → {base}.yaml (+ fonts.yaml), HEX → {base}.hex")
    except Exception as e:      # noqa: BLE001
        print(f"data se nepodařilo převést na projekt: {e}")


def _load(path) -> Project:
    low = path.lower()
    if low.endswith(".hex"):
        return Project.from_hex(path)
    if low.endswith(".bin"):
        return Project.from_bin(path)
    return Project.load(path)


def cmd_import_hex(a):
    p = Project.from_hex(a.hex, ibis_address=a.address)
    p.save(a.output, fonts_file=a.fonts)
    print(f"{a.hex} → {a.output}")


def cmd_export_hex(a):
    p = Project.load(a.project)
    text, problems = p.to_hex()
    for x in problems:
        print("CHYBA:", x)
    if problems and not a.force:
        sys.exit("Export zastaven kvůli chybám (použijte --force).")
    with open(a.output, "w", newline="", encoding="ascii") as f:
        f.write(text)
    print(f"{a.project} → {a.output}")


def cmd_check(a):
    from .core.bundle import ProjectBundle
    if a.project.lower().endswith(".yaml") and ProjectBundle.is_bundle_yaml(a.project):
        b = ProjectBundle.load(a.project)
        total = 0
        for pe in b.panels:
            if not pe.project:
                continue
            rep = pe.project.check()
            total += len(rep)
            print(f"[{pe.id}: {pe.name}] ({pe.format.upper()})")
            for r in rep:
                print(f"  {r['level']:7} {r['section']} {r['key']}: {r['msg']}")
            if not rep:
                print("  v pořádku")
        print(f"Celkem {total} upozornění" if total else "Všechny panely v pořádku")
        return
    p = _load(a.project)
    rep = p.check()
    for r in rep:
        print(f"{r['level']:7} {r['section']} {r['key']}: {r['msg']}")
    print(f"{len(rep)} upozornění" if rep else "v pořádku")


def cmd_new_bundle(a):
    from .core.bundle import build_demo_bundle, build_empty_bundle
    if a.template == "demo_bus":
        b = build_demo_bundle(name=a.name or "Ukázkový autobus (3 panely)")
    else:
        preset_map = {"empty_bus_3": "bus_3", "empty_bus_4": "bus_4",
                      "empty_gbuse1": "single_gbuse1", "empty_gbuse0": "single_gbuse0"}
        b = build_empty_bundle(preset=preset_map.get(a.template, "bus_3"), name=a.name or "Prázdný projekt")
    out = a.output if a.output.lower().endswith(".yaml") else os.path.join(a.output, "projekt.yaml")
    b.save(out)
    print(f"nový vícepanelový projekt ({len(b.panels)} panely) → {out}")


def cmd_render(a):
    from .core.render import compose
    p = _load(a.project)
    if p.format == "gbuse0":
        from .gbuse0.program import frame_to_ascii, frame_to_png, render_combined
        db, _ = p.to_gbuse0()
        l_key = a.line.zfill(3) if a.line else None
        s_key = a.stop.zfill(4) if getattr(a, "stop", None) else None
        res = render_combined(
            db,
            line_key=l_key if a.section != "lines" else None,
            dest_key=a.key if a.section in ("destinations", "destinations4") else None,
            stop_key=a.key if a.section == "stops" else s_key,
            line_rec=db.records.get("LIN", {}).get(a.key) if a.section == "lines" else None,
        )
        bits = res["frames"][0]["bits"]
        if a.png:
            with open(a.png, "wb") as f:
                f.write(frame_to_png(bits, res["rows"]))
            print(f"→ {a.png}")
        else:
            print(frame_to_ascii(bits, res["rows"]))
        for fld, ov in (res.get("overflow") or {}).items():
            if ov:
                print(f"pozor: text v poli '{fld}' se nevejde a panel ho ořízne")
        return

    line = p.encode_value(p.data.get("lines", {}).get(a.line))[0] if a.line else None
    sec = p.data.get(a.section, {})
    if a.key not in sec:
        sys.exit(f"{a.section} {a.key} neexistuje")
    dest = p.encode_value(sec[a.key])[0]
    fr, rep = compose(p.geometry, p.fonts, line, dest, p.defaults(), a.scroll)
    if a.png:
        with open(a.png, "wb") as f:
            f.write(fr.png())
        print(f"→ {a.png}")
    else:
        print(fr.ascii())
    if rep.get("overflow"):
        print("pozor: text se nevejde a panel ho ořízne")


def cmd_upload(a):
    if a.project.lower().endswith(".hex"):
        with open(a.project, encoding="ascii") as f:
            text = f.read()
    else:
        from .core.bundle import ProjectBundle
        if ProjectBundle.is_bundle_yaml(a.project):
            b = ProjectBundle.load(a.project)
            if getattr(a, "panel", None):
                pe = b.panel(a.panel)
                if pe is None or pe.project is None:
                    avail = ", ".join(x.id for x in b.panels)
                    sys.exit(f"Panel '{a.panel}' v projektu není (dostupné: {avail}).")
            else:
                pe = next((x for x in b.panels if x.format == "gbuse1" and x.project is not None),
                          b.panels[0] if b.panels else None)
                if pe is None or pe.project is None:
                    sys.exit("Vícepanelový projekt neobsahuje žádný panel.")
            print(f"Panel z projektu: {pe.id} ({pe.name}, {pe.format})")
            p = pe.project
        elif a.project.lower().endswith(".bin"):
            p = Project.from_bin(a.project)
        else:
            p = Project.load(a.project)
        text, problems = p.to_hex()
        for x in problems:
            print("CHYBA:", x)
        if problems and not a.force:
            sys.exit("Nahrávání zastaveno kvůli chybám (použijte --force).")
    r = _panel(a).upload(text, verify=not a.no_verify)
    print()
    print(r)


def cmd_import(a):
    from .core.importers import import_file, merge
    p = Project.load(a.project)
    with open(a.file, "rb") as f:
        recs, problems = import_file(a.file, f.read(), default_section=a.section)
    for x in problems:
        print("CHYBA:", x)
    merge(p, recs, replace=a.replace)
    p.save(a.output or a.project)
    print({k: len(v) for k, v in recs.items()}, "→", a.output or a.project)


def cmd_new(a):
    fmt = getattr(a, "format", "gbuse1")
    if a.fonts_from:
        src = _load(a.fonts_from)
        fonts = src.fonts
    elif fmt == "gbuse0":
        import yaml
        from .core.fonts import FontSet, default_codetable
        yml = os.path.join(os.path.dirname(__file__), "peripherals", "gbuse0_font.yaml")
        with open(yml, encoding="utf-8") as fh:
            fonts = FontSet.from_yaml(yaml.safe_load(fh))
        fonts.codetable = default_codetable()
    else:
        from .core.gbuse_font import fontset
        fonts = fontset("gbuse")

    if fmt == "gbuse0":
        p = Project.new_gbuse0(fonts, preset=getattr(a, "preset", "front"),
                               version=a.version, ibis_address=a.address)
    else:
        p = Project.new_gbuse1(fonts, version=a.version, ibis_address=a.address)
    p.save(a.output)
    print(f"nový projekt ({fmt}) → {a.output}")


def cmd_live(a):
    from .live import modes as live
    from .loader.device import Panel
    panel = Panel(_port(a), a.address, log=_say)
    fonts = _load(a.project).fonts if a.project else None
    if a.mode in ("text", "departures") or (a.mode == "clock" and a.channel == "text"):
        if fonts is None:
            sys.exit("Pro volný text je potřeba --project (kvůli kódové tabulce a fontům).")
    if a.mode == "clock" and a.channel == "text":
        r = live.text_clock_runner(panel, fonts, a.format, log=_say)
    elif a.mode == "clock":
        r = live.clock_runner(panel, a.channel, a.offset, log=_say)
    elif a.mode == "departures":
        with open(a.schedule, encoding="utf-8-sig") as f:
            sched = live.read_schedule(f.read())
        r = live.departures_text_runner(panel, fonts, sched, a.dep_format, log=_say)
    elif a.mode == "text":
        panel.send(*live.text_telegrams(fonts, a.text, "v"))
        return
    else:
        r = live.playlist_runner(panel, a.keys.split(","), a.interval, log=_say)
    print("Ctrl+C ukončí")
    try:
        r.run()
    except KeyboardInterrupt:
        pass


def cmd_live_build(a):
    from .live import modes as live
    p = Project.load(a.project)
    if a.mode == "clock":
        live.add_clock(p, a.channel, a.format, offset=a.offset)
    else:
        with open(a.schedule, encoding="utf-8-sig") as f:
            live.add_departures(p, live.read_schedule(f.read()), offset=a.offset)
    p.data.setdefault("config", {})["version"] = a.version or p.data.get("config", {}).get("version", "")
    p.save(a.output)
    print(f"→ {a.output}")


def cmd_quickstart(a):
    """Prepare a working folder on a clean computer: universal project (gBUSE0 + gBUSE1),
    example route, vehicle with the detected USB adapter; optionally upload to the panel."""
    import shutil
    from .sim.route import Route
    from .core.gbuse_font import FONT_SOURCES
    from .sim.universal_db import build_universal, build_universal_bundle
    from .server.sim_api import EXAMPLE_VEHICLE
    wd = os.path.abspath(a.workdir)
    port = _port(a)
    ex = os.path.join(os.path.dirname(__file__), "examples")
    os.makedirs(os.path.join(wd, "trasy"), exist_ok=True)
    os.makedirs(os.path.join(wd, "vozidla"), exist_ok=True)
    lines = []
    for f in sorted(os.listdir(os.path.join(ex, "trasy"))):
        dst = os.path.join(wd, "trasy", f)
        if not os.path.exists(dst):
            shutil.copy(os.path.join(ex, "trasy", f), dst)
        lines.append(Route.load(dst).line)

    rel_proj = os.path.join("projekty", "univerzalni", "projekt.yaml")
    db_path = os.path.join(wd, rel_proj)
    if getattr(a, "single", False):
        db = build_universal(None, extra_lines=lines, ibis_address=a.address, font=a.font)
        db.save(db_path)
        panels_cfg = [
            {"name": "Panel (náhled)", "role": "interior", "profile": "BS120", "output": "virtual"},
            {"name": "Vnitřní BS120", "role": "interior", "profile": "BS120", "output": "serial",
             "port": port, "address": a.address, "enabled": True},
        ]
        line_map = db.data.get("line_map") or {}
        upload_proj = db
    else:
        bundle_panels = [
            {"id": "celni", "name": "Čelní BS210", "role": "front", "type": "BS210", "variant": "v1", "ibis_address": 1},
            {"id": "bocni", "name": "Boční BS210", "role": "side", "type": "BS210", "variant": "v2", "stop_field": "bottom", "ibis_address": 2},
            {"id": "vnitrni", "name": "Vnitřní BS120", "role": "interior", "type": "BS120", "profile": "BS120", "columns": 135, "ibis_address": a.address},
        ]
        bundle = build_universal_bundle(None, extra_lines=lines, font=a.font, panels=bundle_panels)
        bundle.save(db_path)
        panels_cfg = [
            {"name": "Čelní BS210", "type": "BS210", "variant": "v1", "role": "front", "output": "virtual",
             "database": "projekty/univerzalni/panely/celni/projekt.yaml"},
            {"name": "Boční BS210", "type": "BS210", "variant": "v2", "stop_field": "bottom", "role": "side", "output": "virtual",
             "database": "projekty/univerzalni/panely/bocni/projekt.yaml"},
            {"name": "Vnitřní BS120 (náhled)", "type": "BS120", "profile": "BS120", "role": "interior", "output": "virtual"},
            {"name": "Vnitřní BS120", "type": "BS120", "profile": "BS120", "role": "interior", "output": "serial",
             "port": port, "address": a.address, "enabled": True},
        ]
        vn = bundle.panel("vnitrni")
        upload_proj = vn.project if vn and vn.project is not None else bundle.panels[0].project
        line_map = (upload_proj.data.get("line_map") if upload_proj else None) or {}

    veh = {**dict(EXAMPLE_VEHICLE), "panels": panels_cfg}
    dump_yaml(veh, os.path.join(wd, "vozidla", "autobus.yaml"))
    kind_desc = "jednopanelová gBUSE1" if getattr(a, "single", False) else "vícepanelový gBUSE0 + gBUSE1"
    print(f"Pracovní složka: {wd}")
    print(f"  {rel_proj.replace(os.sep, '/')}  ({kind_desc}, {FONT_SOURCES[a.font]}, ikony, linky; mapa linek {line_map})")
    print(f"  trasy/  ({', '.join(lines)})")
    print(f"  vozidla/autobus.yaml  (panel na {port}, IBIS adresa {a.address})")
    if a.upload and upload_proj is not None:
        text, problems = upload_proj.to_hex()
        r = Panel_upload(port, a.address, text)
        print(r)
    print()
    print("Další kroky:")
    if not a.upload:
        print(f"  1. busepanel upload {os.path.relpath(db_path)} -p {port}     (nahraje databázi do panelu)")
    print("  2. VYPNOUT A ZAPNOUT panel (čísla linek se načtou až po restartu)")
    print(f"  3. busepanel serve --workdir {os.path.relpath(wd)}   -> Simulace: načíst trasu a vozidlo, ▶ Start")


def Panel_upload(port, address, text):
    from .loader.device import Panel
    return Panel(port, address, log=_say, progress=_progress()).upload(text)


def cmd_serve(a):
    from .server.app import serve
    serve(a.host, a.port_http, a.workdir, open_browser=not a.no_browser)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="busepanel", description="BUSE Studio – panely BUSE (BS120)")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def dev(p):
        p.add_argument("-p", "--port", default=None, help="sériový port (výchozí: BUSE_PORT nebo nalezený USB převodník)")
        p.add_argument("-a", "--address", type=int, default=int(os.environ.get("BUSE_ADDR", "10")))
        return p

    sub.add_parser("ports", help="seznam sériových portů").set_defaults(f=cmd_ports)
    dev(sub.add_parser("scan", help="najít panely (povel aV)")).set_defaults(f=cmd_scan)
    s = dev(sub.add_parser("send", help="poslat IBIS povely, např. l010 z001"))
    s.add_argument("telegrams", nargs="+"); s.set_defaults(f=cmd_send)
    s = dev(sub.add_parser("query", help="IBIS dotaz s odpovědí"))
    s.add_argument("telegram"); s.set_defaults(f=cmd_query)
    s = dev(sub.add_parser("backup", help="stáhnout data z panelu (bin + yaml + hex)"))
    s.add_argument("-o", "--output", default="zaloha_" + dt.datetime.now().strftime("%Y%m%d_%H%M"))
    s.add_argument("--eprom", action="store_true", help="i program (EPROM)")
    s.set_defaults(f=cmd_backup)
    s = sub.add_parser("import-hex", help="HEX → projekt YAML")
    s.add_argument("hex"); s.add_argument("output"); s.add_argument("--fonts", default="fonts.yaml")
    s.add_argument("-a", "--address", type=int, default=10); s.set_defaults(f=cmd_import_hex)
    s = sub.add_parser("export-hex", help="projekt YAML → HEX")
    s.add_argument("project"); s.add_argument("output"); s.add_argument("--force", action="store_true")
    s.set_defaults(f=cmd_export_hex)
    s = sub.add_parser("check", help="kontrola projektu"); s.add_argument("project"); s.set_defaults(f=cmd_check)
    s = sub.add_parser("render", help="náhled textu (ASCII nebo PNG)")
    s.add_argument("project"); s.add_argument("key"); s.add_argument("--section", default="destinations")
    s.add_argument("--line"); s.add_argument("--stop"); s.add_argument("--png"); s.add_argument("--scroll", type=int, default=0)
    s.set_defaults(f=cmd_render)
    s = dev(sub.add_parser("upload", help="nahrát projekt nebo HEX do panelu"))
    s.add_argument("project"); s.add_argument("--panel", help="ID panelu při nahrávání vícepanelového projektu")
    s.add_argument("--force", action="store_true")
    s.add_argument("--no-verify", action="store_true"); s.set_defaults(f=cmd_upload)
    s = sub.add_parser("import", help="hromadný import z CSV/XLSX do projektu")
    s.add_argument("project"); s.add_argument("file"); s.add_argument("-o", "--output")
    s.add_argument("--section", default="destinations"); s.add_argument("--replace", action="store_true")
    s.set_defaults(f=cmd_import)
    s = sub.add_parser("new", help="nový projekt (gBUSE1 nebo gBUSE0)")
    s.add_argument("output"); s.add_argument("--fonts-from", default="")
    s.add_argument("--format", choices=["gbuse1", "gbuse0"], default="gbuse1")
    s.add_argument("--preset", choices=["front", "side", "side_via", "rear"], default="front")
    s.add_argument("--version", default="NOVY"); s.add_argument("-a", "--address", type=int, default=10)
    s.set_defaults(f=cmd_new)
    s = sub.add_parser("new-bundle", help="nový vícepanelový projekt (gBUSE0 + gBUSE1)")
    s.add_argument("output"); s.add_argument("--template", choices=["demo_bus", "empty_bus_3", "empty_bus_4", "empty_gbuse1", "empty_gbuse0"], default="demo_bus")
    s.add_argument("--name", default=""); s.set_defaults(f=cmd_new_bundle)
    s = sub.add_parser("live-build", help="přidat do projektu texty pro hodiny/odjezdy")
    s.add_argument("mode", choices=["clock", "departures"]); s.add_argument("project"); s.add_argument("output")
    s.add_argument("--channel", default="stops", choices=["stops", "split", "dest4"])
    s.add_argument("--format", default="%H:%M"); s.add_argument("--schedule")
    s.add_argument("--offset", type=int, default=0); s.add_argument("--version")
    s.set_defaults(f=cmd_live_build)
    s = dev(sub.add_parser("live", help="živý režim: hodiny, odjezdy, střídání textů"))
    s.add_argument("mode", choices=["clock", "departures", "playlist", "text"])
    s.add_argument("--project", help="projekt nebo HEX (kódová tabulka pro volný text)")
    s.add_argument("--channel", default="text", choices=["text", "stops", "split", "dest4"])
    s.add_argument("--format", default="%H:%M")
    s.add_argument("--dep-format", default="{line}  {dest}  {za}")
    s.add_argument("--text", default="")
    s.add_argument("--schedule"); s.add_argument("--keys", default="001,002")
    s.add_argument("--interval", type=float, default=10.0); s.add_argument("--offset", type=int, default=0)
    s.set_defaults(f=cmd_live)
    s = dev(sub.add_parser("quickstart", help="připravit pracovní složku na čistém počítači (projekt, ukázková trasa, vozidlo)"))
    s.add_argument("--workdir", default=".")
    s.add_argument("--upload", action="store_true", help="rovnou nahrát univerzální databázi do panelu")
    s.add_argument("--single", action="store_true", help="vytvořit jen jednopanelovou gBUSE1 databázi místo vícepanelové")
    s.add_argument("--font", default="gbuse", choices=["gbuse", "builtin"],
                   help="fonty databáze: gbuse = originální font gBUSE (výchozí), builtin = vestavěný")
    s.set_defaults(f=cmd_quickstart)
    s = sub.add_parser("serve", help="spustit grafické rozhraní v prohlížeči")
    s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port-http", type=int, default=8090)
    s.add_argument("--workdir", default="."); s.add_argument("--no-browser", action="store_true")
    s.set_defaults(f=cmd_serve)

    a = ap.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
