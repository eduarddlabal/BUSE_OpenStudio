// Project manager, multi-panel bundle bar, save/export & dialogs
import { $, api, esc, guard, st, toast } from "../common/core.js";
import { showConfig } from "./config.js";
import { showCycles } from "./cycles.js";
import { preview } from "./preview.js";
import { fillFontSelects, fillPreviewLines, renderList, showEditor } from "./records.js";
import { isG0, metaCache, readB64, setDirty, simState, slugify, syncProject } from "./shared.js";

let projFilterState = "all";
let newPathManual = false;
let saveAsPathManual = false;
let projDelArmed = { path: null, ts: 0 };
let panelDelArmed = 0;
let discardArmed = 0;

function confirmDiscard() {
  if (Date.now() - discardArmed < 4000) return true;
  discardArmed = Date.now();
  toast("Neuložené změny – klikněte znovu pro zahození");
  return false;
}

function fmtCounts(cnt) {
  if (!cnt) return "";
  const labels = [["lines", "linek"], ["destinations", "cílů"], ["destinations4", "cílů 4m"],
                  ["stops", "zastávek"], ["messages", "mimoř."], ["supplementary", "DOP"], ["cycles", "cyklů"]];
  return labels.filter(([k]) => (cnt[k] || 0) > 0).map(([k, l]) => `${cnt[k]} ${l}`).join(" · ");
}

function renderProjectCards() {
  const wrap = $("#projCards");
  if (!wrap) return;
  const q = ($("#projSearch")?.value || "").trim().toLowerCase();
  const activeRel = st.project?.rel_path || null;
  const filtered = metaCache.projects.filter((item) => {
    if (projFilterState === "bundle" && item.kind !== "bundle") return false;
    if (projFilterState === "yaml" && item.kind !== "yaml") return false;
    if (projFilterState === "hexbin" && !["hex", "bin"].includes(item.kind)) return false;
    if (projFilterState === "gbuse0" && item.format !== "gbuse0" && !(item.panels || []).some((p) => p.format === "gbuse0")) return false;
    if (projFilterState === "gbuse1" && item.format !== "gbuse1" && !(item.panels || []).some((p) => p.format === "gbuse1")) return false;
    if (!q) return true;
    return [item.name, item.description, item.path, item.version, item.format]
      .some((x) => (x || "").toLowerCase().includes(q));
  });
  if (!filtered.length) {
    wrap.innerHTML = `<div class="muted" style="grid-column:1/-1;padding:12px 0">Zatím zde nejsou žádné odpovídající projekty. Klikněte na <b>★ Ukázkový autobus</b> nebo sem přetáhněte soubor <code>.hex</code> / <code>.bin</code>.</div>`;
    return;
  }
  wrap.innerHTML = filtered.map((it) => {
    const isAct = activeRel && it.path === activeRel;
    const isBundle = it.kind === "bundle";
    const fmtCls = isBundle ? "multi" : (it.format === "gbuse0" ? "g0" : (it.format === "gbuse1" ? "g1" : ""));
    const stats = fmtCounts(it.counts) || "prázdná databáze";
    const delBtn = (it.kind === "yaml" || isBundle) ? `<button class="p-del danger" data-p="${esc(it.path)}">Smazat</button>` : "";
    const panelsChips = isBundle
      ? (it.panels || []).map((p) => `<span class="badge">${esc(p.name)} · ${esc(p.format.toUpperCase())} ${p.columns}×${p.rows}</span>`).join(" ")
      : "";
    return `<div class="projcard${isAct ? " active" : ""}">
      <div class="projhead">
        <div class="projtitle">${esc(it.name)}</div>
        <div class="row" style="gap:4px">
          <span class="pill ${fmtCls}">${isBundle ? `SADA ${(it.panels || []).length} PANELŮ` : esc(it.format.toUpperCase())}</span>
          ${!isBundle ? `<span class="pill">${esc(it.kind.toUpperCase())}</span>` : ""}
          ${isAct ? '<span class="pill ok">otevřeno</span>' : ""}
        </div>
      </div>
      ${it.description ? `<div class="projdesc">${esc(it.description)}</div>` : ""}
      ${panelsChips ? `<div class="row" style="gap:4px">${panelsChips}</div>` : `<div class="projstats">${it.columns ? `${it.columns}×${it.rows}` : ""} ${it.fw ? `· FW ${esc(it.fw)}` : ""} ${it.version ? `· verze <code>${esc(it.version)}</code>` : ""}</div>`}
      <div class="projstats">${esc(stats)}${it.has_sim ? " · Simulace" : ""}${it.has_live ? " · Živě" : ""}</div>
      <div class="projmeta"><span><code>${esc(it.path)}</code></span><span>${esc(it.mtime || "")}</span></div>
      <div class="projactions">
        <button class="p-open primary" data-p="${esc(it.path)}" ${it.format === "gbuse2b" ? "disabled" : ""}>Otevřít</button>
        ${it.format !== "gbuse2b" ? `<button class="p-dup" data-p="${esc(it.path)}">Duplikovat</button>` : ""}
        ${delBtn}
      </div>
    </div>`;
  }).join("");

  wrap.querySelectorAll(".p-open").forEach((b) => b.onclick = guard(() => openProjectPath(b.dataset.p, true)));
  wrap.querySelectorAll(".p-dup").forEach((b) => b.onclick = guard(async () => {
    await api("/api/project/duplicate", { source_path: b.dataset.p });
    toast("Kopie projektu vytvořena");
    await loadFiles();
  }));
  wrap.querySelectorAll(".p-del").forEach((b) => b.onclick = guard(async () => {
    const p = b.dataset.p;
    if (projDelArmed.path !== p || Date.now() - projDelArmed.ts > 4000) {
      projDelArmed = { path: p, ts: Date.now() };
      toast("Klikněte znovu pro smazání projektu " + p);
      return;
    }
    const res = await api("/api/project/delete", { path: p });
    if (res.closed) { st.project = null; setDirty(false); updateModeChrome(); }
    toast("Projekt smazán");
    await loadFiles();
  }));
}

async function openProjectPath(path, switchTab = false) {
  if (!path) return;
  if (st.dirty && !confirmDiscard()) return;
  setProject(await api("/api/project/open", { path }));
  toast("Otevřeno: " + (st.project.name || path));
  await loadFiles(st.project.rel_path || path);
  if (switchTab) window.dispatchEvent(new CustomEvent("buse:gbuse-tab", { detail: "texts" }));
}

export async function loadFiles(select) {
  const r = await api("/api/files");
  metaCache.projects = r.projects || [];
  metaCache.templates = r.templates || {};
  if ($("#projWorkdir")) $("#projWorkdir").textContent = r.workdir || "";
  const labelMap = new Map(metaCache.projects.map((it) => [it.path, it]));
  const opts = r.files.map((f) => {
    const it = labelMap.get(f);
    const lbl = !it ? f : (it.kind === "bundle"
      ? `[SADA ${(it.panels || []).length} PANELŮ] ${it.name}`
      : `[${it.format.toUpperCase()} ${it.columns}×${it.rows}] ${it.name} (${it.kind})`);
    return `<option value="${esc(f)}">${esc(lbl)}</option>`;
  }).join("");
  $("#fileSelect").innerHTML = `<option value="">— vyberte projekt —</option>` + opts;
  const fontOpts = `<option value="gbuse1">Originální fonty gBUSE1</option>` +
    `<option value="gbuse0">Originální fonty gBUSE0</option>` +
    `<option value="builtin">Vestavěný font BUSE Studio</option>` + opts;
  $("#newFontsFrom").innerHTML = fontOpts;
  const fmOpts = `<option value="current">Aktuální projekt</option>` + fontOpts;
  if ($("#fmSource")) $("#fmSource").innerHTML = fmOpts;
  const targetSel = select || st.project?.rel_path;
  if (targetSel && r.files.includes(targetSel)) $("#fileSelect").value = targetSel;
  renderProjectCards();
}

function renderSectionSelect() {
  const opts = isG0()
    ? [
        ["destinations", "Cíle (z)"],
        ["lines", "Linky (l)"],
        ["stops", "Nácestné zastávky (zD)"],
        ["destinations4", "Cíle 4místné (zC)"],
        ["format_strings", "Formátovací řetězce (STR)"],
      ]
    : [
        ["destinations", "Cíle (z)"],
        ["lines", "Linky (l)"],
        ["stops", "Zastávky (x)"],
        ["messages", "Mimořádné (xA)"],
        ["destinations4", "Cíle 4místné (zC)"],
        ["supplementary", "Doplňkové texty (DOP)"],
        ["format_strings", "Formátovací řetězce (STR)"],
      ];
  $("#secSelect").innerHTML = opts.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
  if (!opts.some(([v]) => v === st.section)) st.section = "destinations";
  $("#secSelect").value = st.section;
}

function renderPanelBar() {
  const bar = $("#panelBar");
  if (!bar) return;
  if (!st.project) { bar.hidden = true; return; }
  bar.hidden = false;
  const b = st.project.bundle;
  const hasMulti = !!(b && b.panels && b.panels.length > 1);
  if ($("#btnZip")) $("#btnZip").hidden = !b;
  if ($("#btnViewAll")) $("#btnViewAll").hidden = !hasMulti;
  if ($("#btnRemovePanel")) $("#btnRemovePanel").hidden = !hasMulti;
  if ($("#btnProp0")) $("#btnProp0").hidden = !hasMulti;
  if ($("#btnProp1")) $("#btnProp1").hidden = !hasMulti;
  if (!b) {
    st.viewAll = false;
    $("#panelTabs").innerHTML = `<span class="muted small">Jednopanelový projekt (<b>${esc(st.project.name || "")}</b>)</span>`;
    return;
  }
  $("#panelTabs").innerHTML = b.panels.map((pe) =>
    `<button data-pid="${esc(pe.id)}" class="${pe.id === b.active_panel ? "cur" : ""}">${esc(pe.name)} (${esc(pe.format.toUpperCase())} ${pe.columns}×${pe.rows})</button>`
  ).join("");
  $("#panelTabs").querySelectorAll("button").forEach((btn) => btn.onclick = guard(async () => {
    if (btn.dataset.pid === b.active_panel) return;
    await syncProject();
    const res = await api("/api/bundle/select-panel", { panel_id: btn.dataset.pid, data: st.project.data });
    setProject(res);
  }));
}

export function updateModeChrome() {
  const g0 = isG0();
  const c = st.project?.data?.config || {};
  $("#projectFmt").hidden = !st.project;
  if (st.project) {
    $("#projectFmt").textContent = g0
      ? `gBUSE0 · ${c.columns || 140}×${c.rows || 19} · FW ${c.fw || "3.08"}`
      : `gBUSE1 · ${c.columns || 135}×8 · typ ${c.panel_kind ?? 1}`;
    $("#projectTitle").textContent = st.project.name || "";
    $("#projectPath").textContent = st.project.rel_path || st.project.path || "";
  } else {
    $("#projectTitle").textContent = "";
    $("#projectPath").textContent = "";
  }
  $("#tabBtnCycles").hidden = g0;
  $("#ledSimBar").hidden = !st.project;
  $("#ledSim0Wrap").hidden = !g0;
  $("#ledSim1Wrap").hidden = g0;
  if (!g0 && st.project) {
    const cyks = Object.keys(st.project?.data?.cycles || {});
    $("#simCykId").innerHTML = (cyks.length ? cyks : ["0"]).map((k) => `<option value="${esc(k)}">${esc(k)}</option>`).join("");
    if (cyks.length && !cyks.includes(simState.cycleId)) simState.cycleId = cyks[0];
    $("#simCykId").value = simState.cycleId;
  }
  renderPanelBar();
}

export function setProject(r) {
  st.project = r;
  st.geometry = r.geometry;
  setDirty(false);
  simState.mode = "static";
  if ($("#simMode")) $("#simMode").value = "static";
  simState.playing = false;
  renderSectionSelect();
  updateModeChrome();
  fillFontSelects();
  fillPreviewLines();
  renderList();
  showConfig();
  showCycles();
  renderProjectCards();
  if (r.rel_path && $("#fileSelect")) $("#fileSelect").value = r.rel_path;
  // the simulation (Jízda, Linky, Vozidla, Systémy) follows the project opened here
  if (r.rel_path) window.dispatchEvent(new CustomEvent("buse:project-opened", { detail: r.rel_path }));
  st.key = null;
  showEditor();
}

async function handleProjectUpload(file) {
  if (!file) return;
  if (st.dirty && !confirmDiscard()) return;
  const b64 = await readB64(file);
  const res = await api("/api/project/upload-open", { filename: file.name, content_b64: b64, save_now: true });
  setProject(res);
  toast("Nahráno a uloženo jako projekt: " + res.name);
  await loadFiles(res.rel_path);
  window.dispatchEvent(new CustomEvent("buse:gbuse-tab", { detail: "texts" }));
}

async function saveCurrentProject() {
  if (!st.project) return;
  const r = await api("/api/project/save", { data: st.project.data });
  st.project = { ...st.project, ...r };
  setDirty(false);
  updateModeChrome();
  toast("Uloženo: " + (r.rel_path || r.saved));
  loadFiles(r.rel_path);
}

function openNewDialog(fmt = "gbuse1") {
  $("#newFormat").value = fmt;
  $("#newName").value = fmt === "gbuse0" ? "Nový vnější panel" : "Nový vnitřní panel";
  $("#newDesc").value = "";
  newPathManual = false;
  $("#newPath").value = `projekty/${slugify($("#newName").value)}/projekt.yaml`;
  $("#newSaveNow").checked = true;
  $("#dlgNew").showModal();
  toggleNewDlg();
}

async function createBuiltinBundle(template) {
  if (st.dirty && !confirmDiscard()) return;
  const res = await api("/api/bundle/new", { template, save_now: true });
  setProject(res);
  await loadFiles(res.rel_path);
  toast("Vytvořen projekt: " + res.name);
  window.dispatchEvent(new CustomEvent("buse:gbuse-tab", { detail: "texts" }));
}

async function propagateCurrentRecord() {
  if (!st.project?.bundle || st.key === null) return;
  const r = await api("/api/bundle/propagate", {
    section: st.section,
    key: st.key,
    overwrite: true,
    data: st.project.data,
  });
  st.project = r.project;
  setDirty(true);
  const msg = r.updated?.length ? `Rozkopírováno na: ${r.updated.join(", ")}` : "Žádný jiný panel nebyl aktualizován";
  const skip = r.skipped?.length ? ` (přeskočeno: ${r.skipped.map(([n, why]) => `${n}: ${why}`).join("; ")})` : "";
  toast(msg + skip);
}

function toggleNewDlg() {
  const g0 = $("#newFormat").value === "gbuse0";
  $("#newOpt0").hidden = !g0;
  $("#newOpt1").hidden = g0;
  $("#newCustom0").hidden = !g0 || $("#newPreset0").value !== "custom";
  if ($("#newFontsFrom").value === "gbuse1" || $("#newFontsFrom").value === "gbuse0") {
    $("#newFontsFrom").value = g0 ? "gbuse0" : "gbuse1";
  }
}

// DOM listeners
$("#btnProjects").onclick = () => window.dispatchEvent(new CustomEvent("buse:gbuse-tab", { detail: "projects" }));
$("#btnOpen").onclick = guard(() => openProjectPath($("#fileSelect").value, true));
$("#fileSelect").onchange = guard(() => { if ($("#fileSelect").value) return openProjectPath($("#fileSelect").value); });
$("#projSearch").oninput = renderProjectCards;
$("#btnProjRefresh").onclick = guard(() => loadFiles());
$("#projFilter").querySelectorAll("button").forEach((b) => b.onclick = () => {
  projFilterState = b.dataset.f;
  $("#projFilter").querySelectorAll("button").forEach((x) => x.classList.toggle("cur", x === b));
  renderProjectCards();
});

$("#projUploadInput").onchange = guard(async () => {
  await handleProjectUpload($("#projUploadInput").files[0]);
  $("#projUploadInput").value = "";
});
const dropZone = $("#projDropZone");
dropZone.ondragover = (e) => { e.preventDefault(); dropZone.classList.add("dragover"); };
dropZone.ondragleave = () => dropZone.classList.remove("dragover");
dropZone.ondrop = guard(async (e) => {
  e.preventDefault();
  dropZone.classList.remove("dragover");
  const file = e.dataTransfer?.files?.[0];
  if (file) await handleProjectUpload(file);
});

$("#btnSave").onclick = guard(saveCurrentProject);
window.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && !$("nav.tabs[data-for='gbuse']")?.hidden && st.project) {
    e.preventDefault();
    guard(saveCurrentProject)();
  }
});

$("#btnSaveAs").onclick = () => {
  if (!st.project) { toast("Nejprve otevřete projekt", true); return; }
  const curName = (st.project.data?.meta?.name || st.project.name || "Kopie projektu");
  const copyName = curName.includes("(kopie)") ? curName : `${curName} (kopie)`;
  $("#saveAsName").value = copyName;
  $("#saveAsDesc").value = st.project.data?.meta?.description || "";
  saveAsPathManual = false;
  $("#saveAsPath").value = `projekty/${slugify(copyName)}/projekt.yaml`;
  $("#dlgSaveAs").showModal();
};
$("#saveAsPath").oninput = () => { saveAsPathManual = true; };
$("#saveAsName").oninput = () => {
  if (!saveAsPathManual) $("#saveAsPath").value = `projekty/${slugify($("#saveAsName").value)}/projekt.yaml`;
};
$("#saveAsOk").onclick = guard(async (e) => {
  e.preventDefault();
  const r = await api("/api/project/save-as", {
    name: $("#saveAsName").value,
    description: $("#saveAsDesc").value,
    path: $("#saveAsPath").value,
    data: st.project.data,
  });
  $("#dlgSaveAs").close();
  setProject(r);
  await loadFiles(r.rel_path);
  toast("Uloženo jako: " + (r.rel_path || r.name));
});

$("#btnHex").onclick = guard(async () => {
  await syncProject();
  const text = await api("/api/project/hex");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  a.download = slugify(st.project?.name || "panel") + ".hex";
  a.click();
});

$("#btnBin").onclick = guard(async () => {
  await syncProject();
  const r = await fetch("/api/project/bin");
  if (!r.ok) throw new Error(await r.text());
  const a = document.createElement("a");
  a.href = URL.createObjectURL(await r.blob());
  a.download = slugify(st.project?.name || "panel") + ".bin";
  a.click();
});

$("#btnNew").onclick = () => openNewDialog(isG0() ? "gbuse0" : "gbuse1");
$("#btnNew1").onclick = () => openNewDialog("gbuse1");
$("#btnNew0").onclick = () => openNewDialog("gbuse0");
$("#btnDemoBus").onclick = guard(() => createBuiltinBundle("demo_bus"));
$("#btnEmptyBus3").onclick = guard(() => createBuiltinBundle("empty_bus_3"));

$("#btnZip").onclick = guard(async () => {
  await syncProject();
  const r = await fetch("/api/bundle/export-zip");
  if (!r.ok) throw new Error(await r.text());
  const a = document.createElement("a");
  a.href = URL.createObjectURL(await r.blob());
  a.download = slugify(st.project?.name || "projekt") + ".zip";
  a.click();
});

$("#btnViewAll").onclick = () => {
  st.viewAll = !st.viewAll;
  $("#btnViewAll").textContent = st.viewAll ? "Aktivní panel" : "Zobrazit všechny panely";
  preview();
};

$("#btnAddPanel").onclick = () => {
  if (!st.project) { toast("Nejprve otevřete projekt", true); return; }
  $("#apFormat").value = "gbuse0";
  $("#apOpt0").hidden = false;
  $("#apOpt1").hidden = true;
  $("#dlgAddPanel").showModal();
};
$("#apFormat").onchange = () => {
  const g0 = $("#apFormat").value === "gbuse0";
  $("#apOpt0").hidden = !g0;
  $("#apOpt1").hidden = g0;
};
$("#apOk").onclick = guard(async (e) => {
  e.preventDefault();
  await syncProject();
  const res = await api("/api/bundle/add-panel", {
    name: $("#apName").value,
    role: $("#apRole").value,
    format: $("#apFormat").value,
    preset: $("#apPreset0").value,
    fw: $("#apFw0").value,
    panel_kind: +$("#apKind1").value,
    columns: +$("#apCols1").value,
    ibis_address: +$("#apAddr").value,
  });
  $("#dlgAddPanel").close();
  setProject(res);
  await loadFiles(res.rel_path);
  toast("Panel přidán do projektu");
});

$("#btnRemovePanel").onclick = guard(async () => {
  const b = st.project?.bundle;
  if (!b) return;
  if (Date.now() - panelDelArmed > 4000) {
    panelDelArmed = Date.now();
    toast("Klikněte znovu pro odebrání aktivního panelu");
    return;
  }
  const res = await api("/api/bundle/remove-panel", { panel_id: b.active_panel });
  setProject(res);
  await loadFiles(res.rel_path);
  toast("Panel odebrán z projektu");
});

$("#btnProp0").onclick = guard(propagateCurrentRecord);
$("#btnProp1").onclick = guard(propagateCurrentRecord);

$("#newPath").oninput = () => { newPathManual = true; };
$("#newName").oninput = () => {
  if (!newPathManual) $("#newPath").value = `projekty/${slugify($("#newName").value)}/projekt.yaml`;
};
$("#newFormat").onchange = toggleNewDlg;
$("#newPreset0").onchange = toggleNewDlg;

$("#newOk").onclick = guard(async (e) => {
  e.preventDefault();
  const g0 = $("#newFormat").value === "gbuse0";
  const saveNow = $("#newSaveNow").checked;
  const res = await api("/api/project/new", {
    path: $("#newPath").value,
    name: $("#newName").value,
    description: $("#newDesc").value,
    save_now: saveNow,
    fonts_from: $("#newFontsFrom").value,
    version: $("#newVersion").value,
    format: $("#newFormat").value,
    preset: $("#newPreset0").value,
    fw: g0 ? $("#newFw0").value : $("#newFw1").value,
    panel_kind: +$("#newKind1").value,
    columns: g0 ? +$("#newCols0").value : +$("#newCols1").value,
    rows: +$("#newRows0").value,
  });
  $("#dlgNew").close();
  setProject(res);
  setDirty(!saveNow);
  await loadFiles(res.rel_path);
  toast(saveNow ? "Nový projekt vytvořen a uložen" : "Nový projekt – nezapomeňte uložit");
  window.dispatchEvent(new CustomEvent("buse:gbuse-tab", { detail: "texts" }));
});
