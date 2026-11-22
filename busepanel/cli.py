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
        from .core import image as I
        p = Project.from_image(I.parse(r["flash"]), ibis_address=a.address)
        p.save(base + ".yaml")
        from .core.image import blocks_to_hex, build
        with open(base + ".hex", "w", newline="", encoding="ascii") as f:
            f.write(blocks_to_hex(build(I.parse(r["flash"]))))
        print(f"projekt → {base}.yaml (+ fonts.yaml), HEX → {base}.hex")
    except Exception as e:      # noqa: BLE001
        print(f"data se nepodařilo převést na projekt: {e}")


def _load(path) -> Project:
    if path.lower().endswith(".hex"):
        return Project.from_hex(path)
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
    p = _load(a.project)
    rep = p.check()
    for r in rep:
        print(f"{r['level']:7} {r['section']} {r['key']}: {r['msg']}")
    print(f"{len(rep)} upozornění" if rep else "v pořádku")


def cmd_render(a):
    from .core.render import compose
    p = _load(a.project)
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
    src = Project.from_hex(a.fonts_from)
    data = {"panel": {"type": "BS120", "ibis_address": a.address},
            "config": {"version": a.version, "columns": 135, "commands": ["l", "z", "a", "aV"],
                       "defaults": {"font": 3, "spacing": 1, "interval": 1.0},
                       "tariff_digits": 1, "cycle_delay": 10, "panel_kind": 1},
            "fonts": None, "lines": {"000": ""}, "destinations": {"000": ""}}
    p = Project(data, src.fonts)
    p.save(a.output)
    print(f"nový projekt → {a.output}")


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
    """Prepare a working folder on a clean computer: universal database (built-in fonts),
    example route, vehicle with the detected USB adapter; optionally upload to the panel."""
    import shutil
    from .core.project import dump_yaml
    from .sim.route import Route
    from .sim.universal_db import build_universal
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
    db = build_universal(None, extra_lines=lines, ibis_address=a.address)
    db_path = os.path.join(wd, "databaze", "univerzalni", "projekt.yaml")
    db.save(db_path)
    veh = dict(EXAMPLE_VEHICLE)
    veh["panels"] = [{"name": "Panel (náhled)", "role": "interior", "profile": "BS120", "output": "virtual"},
                     {"name": "Vnitřní BS120", "role": "interior", "profile": "BS120", "output": "serial",
                      "port": port, "address": a.address, "enabled": True}]
    dump_yaml(veh, os.path.join(wd, "vozidla", "autobus.yaml"))
    print(f"Pracovní složka: {wd}")
    print(f"  databaze/univerzalni/projekt.yaml  (vestavěný font, ikony, linky; mapa linek {db.data['line_map']})")
    print(f"  trasy/  ({', '.join(lines)})")
    print(f"  vozidla/autobus.yaml  (panel na {port}, IBIS adresa {a.address})")
    if a.upload:
        text, problems = db.to_hex()
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
    s.add_argument("--line"); s.add_argument("--png"); s.add_argument("--scroll", type=int, default=0)
    s.set_defaults(f=cmd_render)
    s = dev(sub.add_parser("upload", help="nahrát projekt nebo HEX do panelu"))
    s.add_argument("project"); s.add_argument("--force", action="store_true")
    s.add_argument("--no-verify", action="store_true"); s.set_defaults(f=cmd_upload)
    s = sub.add_parser("import", help="hromadný import z CSV/XLSX do projektu")
    s.add_argument("project"); s.add_argument("file"); s.add_argument("-o", "--output")
    s.add_argument("--section", default="destinations"); s.add_argument("--replace", action="store_true")
    s.set_defaults(f=cmd_import)
    s = sub.add_parser("new", help="nový projekt (fonty převezme z HEX)")
    s.add_argument("output"); s.add_argument("--fonts-from", required=True)
    s.add_argument("--version", default="NOVY"); s.add_argument("-a", "--address", type=int, default=10)
    s.set_defaults(f=cmd_new)
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
    s = dev(sub.add_parser("quickstart", help="připravit pracovní složku na čistém počítači (databáze, ukázková trasa, vozidlo)"))
    s.add_argument("--workdir", default=".")
    s.add_argument("--upload", action="store_true", help="rovnou nahrát univerzální databázi do panelu")
    s.set_defaults(f=cmd_quickstart)
    s = sub.add_parser("serve", help="spustit grafické rozhraní v prohlížeči")
    s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port-http", type=int, default=8090)
    s.add_argument("--workdir", default="."); s.add_argument("--no-browser", action="store_true")
    s.set_defaults(f=cmd_serve)

    a = ap.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
