"""Local web server for the graphical editor (FastAPI). Start with `busepanel serve`."""
from __future__ import annotations

import os
import threading
import webbrowser

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..extras.lcd.server import add_routes as _lcd_routes
from . import (
    bundle_api,
    editor_api,
    fonts_api,
    hardware_api,
    live_api,
    projects_api,
    sim_api,
)
from .state import S, WEB, State

# Re-export public endpoints and request models so callers/tests importing `busepanel.server.app` keep working
from .bundle_api import (  # noqa: F401
    BundleAddPanelReq,
    BundleMetaReq,
    BundleNewReq,
    BundleSelectReq,
    PreviewAllReq,
    PropagateReq,
    bundle_add_panel,
    bundle_apply_sim,
    bundle_export_zip,
    bundle_new,
    bundle_preview_all,
    bundle_propagate,
    bundle_remove_panel,
    bundle_select_panel,
    bundle_update_meta,
)
from .editor_api import (  # noqa: F401
    AutoformatReq,
    Compose0Req,
    ImportReq,
    MnemoReq,
    PreviewReq,
    SectionExportReq,
    SectionImportReq,
    do_import,
    gbuse0_compose,
    gbuse_autoformat,
    gbuse_section_export,
    gbuse_section_import,
    mnemo_convert,
    preview,
)
from .fonts_api import (  # noqa: F401
    CodeReq,
    FontManageReq,
    GlyphDel,
    GlyphReq,
    ImportFntReq,
    fonts_add_icons,
    fonts_codetable,
    fonts_export_fnt,
    fonts_get,
    fonts_glyph,
    fonts_glyph_delete,
    fonts_import_fnt,
    fonts_manage,
    icons,
    profiles,
)
from .hardware_api import (  # noqa: F401
    BackupReq,
    PanelCfg,
    SendReq,
    TextReq,
    devices,
    job_get,
    panel_backup,
    panel_config,
    panel_ports,
    panel_scan,
    panel_send,
    panel_text,
    panel_upload,
    ws_device,
)
from .live_api import (  # noqa: F401
    LiveBuild,
    LiveStart,
    LiveTemplateReq,
    live_build,
    live_start,
    live_status,
    live_stop,
    live_template_save,
    live_templates,
)
from .projects_api import (  # noqa: F401
    DeleteReq,
    DuplicateReq,
    NewReq,
    OpenReq,
    SaveAsReq,
    SaveReq,
    UploadOpenReq,
    files,
    project_bin,
    project_check,
    project_close,
    project_delete,
    project_duplicate,
    project_get,
    project_hex,
    project_new,
    project_open,
    project_save,
    project_save_as,
    project_update,
    project_upload_open,
)

app = FastAPI(title="BUSE Studio")
app.include_router(sim_api.router)
app.include_router(projects_api.router)
app.include_router(bundle_api.router)
app.include_router(editor_api.router)
app.include_router(fonts_api.router)
app.include_router(hardware_api.router)
app.include_router(live_api.router)


@app.middleware("http")
async def no_stale_ui(request, call_next):
    """Browsers must revalidate the UI files, otherwise an updated app mixes old and new modules."""
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))


_lcd_routes(app, "/lcd")
app.mount("/static", StaticFiles(directory=WEB), name="static")


def serve(host="127.0.0.1", port=8090, workdir=".", open_browser=True):
    import uvicorn
    S.reset(workdir)
    sim_api.init(workdir)
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{host}:{port}/")).start()
    print(f"BUSE Studio běží na http://{host}:{port}/  (pracovní složka {S.workdir})")
    uvicorn.run(app, host=host, port=port, log_level="warning")
