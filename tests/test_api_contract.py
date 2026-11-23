"""Zero-dependency HTTP/ASGI contract tests & frontend ES-module/endpoint parity tests."""
from __future__ import annotations

import asyncio
import json
import pathlib
import re

from busepanel.server import app as srv, sim_api

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent / "busepanel" / "web"


def asgi_request(method: str, path: str, json_body: dict | None = None) -> tuple[int, dict[str, str], bytes]:
    """Invoke the FastAPI ASGI app in-process without httpx."""
    raw_body = b"" if json_body is None else json.dumps(json_body).encode("utf-8")
    headers = [(b"host", b"testserver")]
    if json_body is not None:
        headers.append((b"content-type", b"application/json"))
        headers.append((b"content-length", str(len(raw_body)).encode("ascii")))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8090),
    }

    sent_body = False
    status = 500
    resp_headers: dict[str, str] = {}
    chunks: list[bytes] = []

    async def run():
        disconnect = asyncio.Event()

        async def receive():
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": raw_body, "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(msg):
            nonlocal status, resp_headers
            if msg["type"] == "http.response.start":
                status = msg["status"]
                resp_headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in msg.get("headers", [])}
            elif msg["type"] == "http.response.body":
                chunks.append(msg.get("body", b""))
                if not msg.get("more_body", False):
                    disconnect.set()

        await srv.app(scope, receive, send)

    asyncio.run(run())
    return status, resp_headers, b"".join(chunks)


def _json(method: str, path: str, body: dict | None = None) -> tuple[int, dict[str, str], dict]:
    st_code, hdrs, raw = asgi_request(method, path, body)
    return st_code, hdrs, json.loads(raw.decode("utf-8"))


def test_http_static_and_end_to_end_contracts(tmp_path):
    old_s = srv.S.workdir
    old_sim = (sim_api.SIM.workdir, sim_api.SIM.db, sim_api.SIM.db_path, sim_api.SIM.vehicle_cfg, sim_api.SIM.vehicle_path)
    srv.S.reset(str(tmp_path))
    sim_api.SIM.vehicle_path = None
    sim_api.init(str(tmp_path))
    try:
        # 1. Static HTML + modular JS + no-cache headers
        for p in ("/", "/static/main.js", "/static/gbuse/gbuse.js", "/static/gbuse/projects.js", "/static/sim/sim.js"):
            code, hdrs, body = asgi_request("GET", p)
            assert code == 200, f"GET {p} -> {code}"
            assert hdrs.get("cache-control") == "no-cache"
            assert len(body) > 50

        # 2. Bundle create, open, preview-all, ZIP export & apply-sim
        code, _, b_res = _json("POST", "/api/bundle/new", {"template": "demo_bus", "save_now": True})
        assert code == 200 and b_res["bundle"] is not None and len(b_res["bundle"]["panels"]) == 3
        rel = b_res["rel_path"]

        code, _, f_res = _json("GET", "/api/files")
        assert code == 200
        assert set(f_res) >= {"workdir", "files", "projects", "templates"}
        assert rel in f_res["files"]
        assert any(x["path"] == rel and x["kind"] == "bundle" for x in f_res["projects"])

        code, _, sel_res = _json("POST", "/api/bundle/select-panel", {"panel_id": "vnitrni"})
        assert code == 200 and sel_res["format"] == "gbuse1"

        code, _, pa_res = _json("POST", "/api/bundle/preview-all", {"line_key": "001", "dest_key": "001"})
        assert code == 200 and len(pa_res["panels"]) == 3

        code, hdrs, zbody = asgi_request("GET", "/api/bundle/export-zip")
        assert code == 200 and zbody[:2] == b"PK"

        code, _, apply_res = _json("POST", "/api/bundle/apply-sim", {})
        assert code == 200 and apply_res["ok"] is True

        # 3. Single-panel CRUD, check, hex, bin, delete
        code, _, n_res = _json("POST", "/api/project/new", {"name": "Test1", "format": "gbuse1", "save_now": True})
        assert code == 200 and n_res["format"] == "gbuse1"
        s_rel = n_res["rel_path"]

        code, _, o_res = _json("POST", "/api/project/open", {"path": s_rel})
        assert code == 200 and o_res["rel_path"] == s_rel

        code, _, sv_res = _json("POST", "/api/project/save", {"data": o_res["data"]})
        assert code == 200 and "saved" in sv_res

        code, _, ck_res = _json("GET", "/api/project/check")
        assert code == 200 and "items" in ck_res

        code, _, hx_raw = asgi_request("GET", "/api/project/hex")
        assert code == 200 and b":" in hx_raw

        code, _, bn_raw = asgi_request("GET", "/api/project/bin")
        assert code == 200 and bn_raw[0] in (0xAA, 0xAB)

        # 4. Editor preview, autoformat, mnemo
        code, _, pv_res = _json("POST", "/api/preview", {"section": "destinations", "value": "CENTRUM"})
        assert code == 200 and pv_res["format"] == "gbuse1" and "dest" in pv_res

        code, _, af_res = _json("POST", "/api/gbuse/autoformat", {"text": "CENTRUM", "section": "destinations"})
        assert code == 200 and set(af_res) >= {"font", "spacing"}

        code, _, mn_res = _json("POST", "/api/mnemo/convert", {"direction": "to_mnemo", "value": "CENTRUM"})
        assert code == 200 and "mnemo" in mn_res

        # 5. Fonts, icons, profiles
        code, _, fnt_res = _json("GET", "/api/fonts")
        assert code == 200 and fnt_res["fonts"] and fnt_res["codetable"]

        code, _, ic_add = _json("POST", "/api/fonts/add-icons", {})
        assert code == 200 and ic_add["ok"] is True

        code, _, ic_res = _json("GET", "/api/icons")
        assert code == 200 and ic_res["icons"]

        code, _, pr_res = _json("GET", "/api/profiles")
        assert code == 200 and pr_res["profiles"]

        # gBUSE0 compose
        code, _, _ = _json("POST", "/api/project/new", {"name": "Test0", "format": "gbuse0", "save_now": False})
        assert code == 200
        code, _, cmp_res = _json("POST", "/api/gbuse0/compose", {"section": "destinations", "spec": {"mode": "1", "line1": {"text": "Nádraží"}}})
        assert code == 200 and "mnemo" in cmp_res

        # Delete saved single project
        code, _, del_res = _json("POST", "/api/project/delete", {"path": s_rel})
        assert code == 200 and del_res["ok"] is True

        # 6. Hardware, Live & Sim HTTP contracts
        code, _, pt_res = _json("GET", "/api/panel/ports")
        assert code == 200 and set(pt_res) >= {"ports", "port", "address"}

        code, _, dv_res = _json("GET", "/api/devices")
        assert code == 200 and "devices" in dv_res

        code, _, ls_res = _json("GET", "/api/live/status")
        assert code == 200 and set(ls_res) >= {"running", "log"}

        code, _, lt_res = _json("GET", "/api/live/templates")
        assert code == 200 and "presets" in lt_res

        code, _, db_res = _json("POST", "/api/sim/db/build", {
            "base": "gbuse", "out": "projekty/univ_http/projekt.yaml", "name": "HTTP Sada", "bundle": True,
        })
        assert code == 200 and db_res["kind"] == "bundle" and len(db_res["panels"]) == 3

        code, _, sf_res = _json("GET", "/api/sim/files")
        assert code == 200 and set(sf_res) >= {"routes", "vehicles", "databases", "projects", "db_path"}
        assert any(p["path"] == "projekty/univ_http/projekt.yaml" and p["kind"] == "bundle" for p in sf_res["projects"])

        code, _, so_res = _json("POST", "/api/sim/db/open", {"path": "projekty/univ_http/projekt.yaml", "apply_vehicle": True})
        assert code == 200 and so_res["kind"] == "bundle"

        code, _, svh_res = _json("GET", "/api/sim/vehicle")
        assert code == 200 and set(svh_res) >= {"path", "config", "system"}

        code, _, sdf_res = _json("GET", "/api/sim/defaults")
        assert code == 200 and set(sdf_res) >= {"sequences", "peripherals", "sections"}
    finally:
        srv.S.reset(old_s)
        sim_api.SIM.workdir, sim_api.SIM.db, sim_api.SIM.db_path, sim_api.SIM.vehicle_cfg, sim_api.SIM.vehicle_path = old_sim
        sim_api._rebuild()


def _exported_symbols(js_path: pathlib.Path, seen: set[pathlib.Path] | None = None) -> set[str]:
    seen = seen or set()
    if js_path in seen or not js_path.exists():
        return set()
    seen.add(js_path)
    src = js_path.read_text(encoding="utf-8")
    names: set[str] = set()
    for m in re.finditer(r"export\s+(?:async\s+)?(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)", src):
        names.add(m.group(1))
    for m in re.finditer(r"export\s*\{([^}]+)\}(?:\s*from\s*[\"']([^\"']+)[\"'])?", src):
        for part in m.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            alias = part.split(" as ")[-1].strip()
            names.add(alias)
    return names


def test_web_es_module_imports_resolve():
    js_files = sorted(WEB_DIR.rglob("*.js"))
    assert js_files
    imp_re = re.compile(r"import\s*(?:\{([^}]*)\}\s*from\s*)?[\"'](\.[^\"']+)[\"']\s*;?")
    for f in js_files:
        src = f.read_text(encoding="utf-8")
        for m in imp_re.finditer(src):
            syms, rel = m.group(1), m.group(2)
            target = (f.parent / rel).resolve()
            assert target.exists(), f"{f.relative_to(WEB_DIR)} imports missing module {rel}"
            if syms:
                exported = _exported_symbols(target)
                for part in syms.split(","):
                    part = part.strip()
                    if not part:
                        continue
                    orig = part.split(" as ")[0].strip()
                    assert orig in exported, f"{f.relative_to(WEB_DIR)} imports missing symbol {orig!r} from {rel}"


def test_web_api_endpoints_exist_in_fastapi_routes():
    openapi_paths = sorted(p for p in srv.app.openapi().get("paths", {}) if p.startswith("/api/"))
    route_patterns: list[re.Pattern[str]] = []
    for path in openapi_paths:
        regex = "^" + re.sub(r"\{[^}]+\}", r"[^/]+", re.escape(path).replace(r"\{", "{").replace(r"\}", "}")) + "$"
        route_patterns.append(re.compile(regex))

    js_files = sorted(WEB_DIR.rglob("*.js"))
    lit_re = re.compile(r"[\"'](/api/[a-zA-Z0-9_/\-]+)[\"']")
    tpl_re = re.compile(r"`(/api/[a-zA-Z0-9_/\-${}\(\)\.a-zA-Z]+)`")
    checked = 0
    for f in js_files:
        src = f.read_text(encoding="utf-8")
        for m in lit_re.finditer(src):
            url = m.group(1)
            candidate = url + "placeholder" if url.endswith("/") else url
            assert any(pat.match(candidate) for pat in route_patterns), f"Unknown API URL {url!r} in {f.name}"
            checked += 1
        for m in tpl_re.finditer(src):
            sample = re.sub(r"\$\{[^}]+\}", "placeholder", m.group(1))
            assert any(pat.match(sample) for pat in route_patterns), f"Unknown API template URL {m.group(1)!r} in {f.name}"
            checked += 1
    assert checked > 20
