// gBUSE module – unified editor for gBUSE1 (internal BS120) and gBUSE0 (external BS210 DOT-LED)
import { $, $$, api, toast, guard, esc, st } from "../common/core.js";

const SECTION_CMD_1 = {
  lines: ["l", 3], destinations: ["z", 3], destinations4: ["zC", 4],
  stops: ["x", 4], messages: ["xA", 4], supplementary: ["DOP", 1], format_strings: ["STR", 1],
};
const SECTION_CMD_0 = {
  lines: ["l", 3], destinations: ["z", 3], destinations4: ["zC", 4],
  stops: ["zD", 4], format_strings: ["STR", 1],
};
const ALL_CMDS_1 = ["l", "z", "x", "xA", "xB", "zC", "zD", "v", "zI", "zM", "zN", "aA", "u", "e", "xC", "a", "aV", "lA"];

function isG0() { return st.project?.format === "gbuse0"; }
function secMeta() { return isG0() ? SECTION_CMD_0 : SECTION_CMD_1; }
function isSpecialSec(sec = st.section) { return sec === "supplementary" || sec === "format_strings"; }

let cyclesState = { id: "0", fmt: "ident", playing: false };
let simState = { mode: "static", cycleId: "0", stepIdx: 0, playing: false, paused0: false };

// ---- LED canvas
const led = $("#led"), ctx = led.getContext("2d");

function drawToCanvas(canvas, cols, columns = 135, rows = 8, isDot = false) {
  const c = canvas.getContext("2d");
  const s = Math.max(3, Math.floor(canvas.width / Math.max(1, columns)));
  const r = s * (isDot ? 0.38 : 0.36);
  canvas.height = s * rows + 4;
  c.fillStyle = "#0b0907";
  c.fillRect(0, 0, canvas.width, canvas.height);
  for (let x = 0; x < columns; x++) {
    const colVal = cols[x] || 0;
    for (let y = 0; y < rows; y++) {
      const on = isDot ? ((colVal >>> y) & 1) : ((colVal >> (7 - y)) & 1);
      c.fillStyle = on ? (isDot ? "#ffd12e" : "#ffb02e") : "#2a2118";
      if (on) {
        c.shadowColor = isDot ? "#ffb800" : "#ff9d00";
        c.shadowBlur = s * 0.55;
      } else {
        c.shadowBlur = 0;
      }
      c.beginPath();
      c.arc(x * s + s / 2 + 2, y * s + s / 2 + 2, r, 0, 7);
      c.fill();
    }
  }
  c.shadowBlur = 0;
}

export function drawFrame(cols, columns = 135, rows = 8, isDot = false) {
  drawToCanvas(led, cols, columns, rows, isDot);
}

async function previewAllPanels() {
  const wrap = $("#ledAllWrap");
  if (!wrap || !st.project) return;
  const lineKey = st.section === "lines" ? st.key : ($("#ed0PreviewLine")?.value || $("#edPreviewLine")?.value || "");
  const destKey = (st.section === "destinations" || st.section === "destinations4") ? st.key : "";
  const stopKey = st.section === "stops" ? st.key : ($("#ed0PreviewStop")?.value || "");
  let res;
  try {
    res = await api("/api/bundle/preview-all", {
      section: st.section,
      line_key: lineKey || "",
      dest_key: destKey || "",
      stop_key: stopKey || "",
      value: currentValue(),
    });
  } catch (e) { $("#ledInfo").textContent = e.message; return; }
  wrap.innerHTML = "";
  const ovList = [];
  for (const p of res.panels || []) {
    const item = document.createElement("div");
    item.className = "ledall-item";
    const ov = Object.entries(p.overflow || {}).filter(([, v]) => v).map(([k]) => k);
    if (ov.length) ovList.push(`${p.name} (${ov.join(",")})`);
    const hdr = document.createElement("div");
    hdr.className = "ledall-title";
    hdr.innerHTML = `<span><b>${esc(p.name)}</b> (${esc(p.format.toUpperCase())} ${p.columns}×${p.rows})</span>` +
      `<span>${ov.length ? "⚠ přetečení" : "OK"}</span>`;
    const cv = document.createElement("canvas");
    cv.width = 1350;
    if (p.format === "gbuse0") {
      const fr = (p.frames && p.frames[0]) || { bits: [] };
      drawToCanvas(cv, fr.bits || [], p.columns, p.rows, true);
    } else {
      const f = new Array(p.columns).fill(0);
      if (p.line) blit(f, p.line, p.line.start, p.line.lo, p.line.hi);
      const fr = (p.frames && p.frames[0]);
      if (fr) blit(f, fr, fr.start, fr.lo, fr.hi);
      drawToCanvas(cv, f, p.columns, 8, false);
    }
    item.append(hdr, cv);
    wrap.append(item);
  }
  $("#ledInfo").textContent = `Společný náhled (${(res.panels || []).length} panely)` +
    (ovList.length ? ` — ⚠ přetečení: ${ovList.join(", ")}` : "");
}

function blit(frame, item, start, lo, hi) {
  item.cols.forEach((v, i) => {
    const x = start + i;
    if (x >= lo && x <= hi && x >= 0 && x < frame.length) frame[x] = item.inv[i] ? (~v & 0xff) : v;
  });
}

function stopAnim() {
  if (st.anim) cancelAnimationFrame(st.anim);
  if (st.animTimer) clearTimeout(st.animTimer);
  st.anim = null;
  st.animTimer = null;
}

export async function preview() {
  if (!st.project) return;
  stopAnim();
  if (st.viewAll && st.project.bundle) {
    $("#led").hidden = true;
    $("#ledAllWrap").hidden = false;
    return previewAllPanels();
  }
  $("#led").hidden = false;
  $("#ledAllWrap").hidden = true;
  const g = st.geometry;

  if (isG0()) {
    const val = currentValue();
    const lineKey = $("#ed0PreviewLine")?.value || "";
    const stopKey = $("#ed0PreviewStop")?.value || "";
    const lineVal = st.section === "lines" ? null : (lineKey ? (st.project.data.lines || {})[lineKey] : null);
    const stopVal = st.section === "stops" ? null : (stopKey ? (st.project.data.stops || {})[stopKey] : null);
    let res;
    try {
      res = await api("/api/preview", {
        section: st.section,
        value: val,
        line: lineVal ?? null,
        stop: stopVal ?? null,
      });
    } catch (e) { $("#ledInfo").textContent = e.message; return; }

    const frames = res.frames || [];
    const colsN = res.columns || g.columns || 140;
    const rowsN = res.rows || g.rows || 19;
    let fi = 0;
    const renderIdx = () => {
      if (!frames.length) { drawFrame([], colsN, rowsN, true); return; }
      const fr = frames[fi % frames.length];
      drawFrame(fr.bits, colsN, rowsN, true);
      if (frames.length > 1 && !simState.paused0 && fr.seconds) {
        st.animTimer = setTimeout(() => { fi = (fi + 1) % frames.length; renderIdx(); }, Math.max(100, fr.seconds * 1000));
      }
    };
    renderIdx();

    const ov = Object.entries(res.overflow || {}).filter(([, v]) => v).map(([k]) => k);
    $("#ledInfo").textContent = `gBUSE0 ${colsN}×${rowsN}` +
      (frames.length > 1 ? ` · ${frames.length} stránek` : "") +
      (res.block ? " · celý panel (b)" : "") +
      (res.suppress_stops ? " · bez zastávek (w)" : "") +
      (ov.length ? ` — ⚠ přetečení (${ov.join(", ")})` : "");
    $("#ed0Bytes").textContent = res.bytes || "";
    if ($("#ed0Mode").value !== "mnemo" && res.mnemo !== undefined && document.activeElement !== $("#ed0Mnemo")) {
      $("#ed0Mnemo").value = res.mnemo;
    }
    const probs = [...(res.problems || [])];
    if (ov.length) probs.push("Text v poli (" + ov.join(", ") + ") se nevejde na šířku okna.");
    $("#edProblems").innerHTML = probs.map((p) => `<div class="${p.includes("nevejde") ? "warning" : ""}">${esc(p)}</div>`).join("");
    return;
  }

  // ---- gBUSE1 preview (static, effects t1/t2/t3, blink, or typ 2 cycle simulation)
  if (simState.mode === "cycle") {
    const steps = (st.project.data.cycles || {})[simState.cycleId] || [];
    const idx = steps.length ? Math.min(simState.stepIdx, steps.length - 1) : 0;
    $("#simStepLabel").textContent = `${steps.length ? idx + 1 : 0}/${steps.length}`;
    if (!steps.length) { drawFrame([], g.columns, 8, false); $("#ledInfo").textContent = "Vybraný cyklus je prázdný"; return; }
    let res;
    try {
      res = await api("/api/preview", {
        cycle_id: +simState.cycleId,
        step_idx: idx,
        line: $("#edPreviewLine")?.value || "",
        free_text: $("#simFree").value,
        time_str: $("#simTime").value,
        zone_str: $("#simZone").value,
      });
    } catch (e) { $("#ledInfo").textContent = e.message; return; }
    const cf = res.cycle_frame;
    drawFrame(cf.cols, g.columns, 8, false);
    $("#ledInfo").textContent = `Cyklus ${simState.cycleId}, krok ${idx + 1}/${steps.length} (${cf.duration} s)`;
    if (simState.playing) {
      st.animTimer = setTimeout(() => {
        simState.stepIdx = (idx + 1) % steps.length;
        preview();
      }, Math.max(200, (cf.duration || 2) * 1000));
    }
    return;
  }

  const value = currentValue();
  const lineVal = st.section === "lines" ? null : (st.previewLine !== "" ? (st.project.data.lines || {})[st.previewLine] : null);
  let res;
  try { res = await api("/api/preview", { section: st.section, value, line: lineVal ?? null }); }
  catch (e) { $("#ledInfo").textContent = e.message; return; }

  const base = () => new Array(g.columns).fill(0);
  const withLine = (f) => {
    if (st.section !== "lines" && !isSpecialSec()) {
      for (let x = g.line_left; x <= g.line_right; x++) f[x] = 0;
      if (res.line) {
        const w = g.line_right - g.line_left + 1;
        blit(f, res.line, g.line_left + Math.max(0, Math.floor((w - res.line.width) / 2)), g.line_left, g.line_right);
      }
    }
    return f;
  };

  const d = res.dest;
  if ($("#edMnemoText") && document.activeElement !== $("#edMnemoText") && res.mnemo !== undefined && !$("#edMnemoChk").checked) {
    $("#edMnemoText").value = res.mnemo;
  }

  let info = "";
  const frames = res.frames || [];
  if (d && (d.run || d.effect === 1)) {
    const span = (d.hi - d.lo + 1) || (g.dest_right - g.dest_left + 1), period = d.width + span;
    let off = 0, last = performance.now();
    const tick = (t) => {
      const step = g.scroll_ms || 20;
      if (t - last >= step) {
        off = (off + Math.max(1, Math.floor((t - last) / step))) % period;
        last = t;
        const f = base();
        blit(f, d, d.lo + span - off, d.lo, d.hi);
        drawFrame(withLine(f), g.columns, 8, false);
      }
      st.anim = requestAnimationFrame(tick);
    };
    st.anim = requestAnimationFrame(tick);
    info = `běžící text (t1), šířka ${d.width} sl.`;
  } else if (d && (d.effect === 2 || d.effect === 3)) {
    const target = d.start, span = d.hi - d.lo + 1;
    let curX = d.effect === 2 ? d.hi + 1 : d.lo - d.width;
    let last = performance.now();
    const tick = (t) => {
      if (t - last >= 18) {
        last = t;
        if (d.effect === 2) curX = curX > target ? curX - 2 : d.hi + 1;
        else curX = curX < target ? curX + 2 : d.lo - d.width;
        const f = base();
        blit(f, d, curX, d.lo, d.hi);
        drawFrame(withLine(f), g.columns, 8, false);
      }
      st.anim = requestAnimationFrame(tick);
    };
    st.anim = requestAnimationFrame(tick);
    info = `efekt t${d.effect}, šířka ${d.width} sl. (${span} okno)`;
  } else if (d && d.blink) {
    let on = true;
    const toggle = () => {
      const f = base();
      if (on) blit(f, d, d.start, d.lo, d.hi);
      drawFrame(withLine(f), g.columns, 8, false);
      on = !on;
      st.animTimer = setTimeout(toggle, 500);
    };
    toggle();
    info = `blikání (b), šířka ${d.width} sl.`;
  } else if (frames.length > 1) {
    let fi = 0;
    const cyclePage = () => {
      const fr = frames[fi % frames.length];
      const f = base();
      blit(f, fr, fr.start, fr.lo, fr.hi);
      drawFrame(withLine(f), g.columns, 8, false);
      st.animTimer = setTimeout(() => { fi = (fi + 1) % frames.length; cyclePage(); }, Math.max(250, (fr.seconds || 1) * 1000));
    };
    cyclePage();
    info = `${frames.length} stránek`;
  } else {
    const f = base();
    if (d) blit(f, d, d.start, d.lo, d.hi);
    drawFrame(withLine(f), g.columns, 8, false);
    if (d) info = `šířka ${d.width} sl., začátek ${d.start}, viditelné ${d.lo}–${d.hi}` + (d.overflow ? " — ⚠ nevejde se, panel text ořízne" : "");
  }
  $("#ledInfo").textContent = info;
  $("#edBytes").textContent = d ? d.bytes : "";
  const probs = res.problems || [];
  $("#edProblems").innerHTML = probs.map((p) => `<div>${esc(p)}</div>`).join("") +
    (d && d.overflow ? `<div class="warning">Text se nevejde – zkraťte ho, zmenšete mezeru/font nebo zapněte běžící text.</div>` : "");
}

// ---- files & project manager
let projFilterState = "all";
let allProjects = [];
let allTemplates = {};
let newPathManual = false;
let saveAsPathManual = false;
let projDelArmed = { path: null, ts: 0 };
let panelDelArmed = 0;

function slugify(s) {
  return (s || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
    .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "projekt";
}

function setDirty(v) {
  st.dirty = !!v;
  if ($("#dirtyBadge")) $("#dirtyBadge").hidden = !st.dirty;
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
  const filtered = allProjects.filter((item) => {
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
  allProjects = r.projects || [];
  allTemplates = r.templates || {};
  if ($("#projWorkdir")) $("#projWorkdir").textContent = r.workdir || "";
  const labelMap = new Map(allProjects.map((it) => [it.path, it]));
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

function updateModeChrome() {
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
  st.key = null;
  showEditor();
}

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

let discardArmed = 0;
function confirmDiscard() {
  if (Date.now() - discardArmed < 4000) return true;
  discardArmed = Date.now();
  toast("Neuložené změny – klikněte znovu pro zahození");
  return false;
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
  const blob = await r.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = slugify(st.project?.name || "panel") + ".bin";
  a.click();
});

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
$("#btnNew").onclick = () => openNewDialog(isG0() ? "gbuse0" : "gbuse1");
$("#btnNew1").onclick = () => openNewDialog("gbuse1");
$("#btnNew0").onclick = () => openNewDialog("gbuse0");

async function createBuiltinBundle(template) {
  if (st.dirty && !confirmDiscard()) return;
  const res = await api("/api/bundle/new", { template, save_now: true });
  setProject(res);
  await loadFiles(res.rel_path);
  toast("Vytvořen projekt: " + res.name);
  window.dispatchEvent(new CustomEvent("buse:gbuse-tab", { detail: "texts" }));
}
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
$("#btnProp0").onclick = guard(propagateCurrentRecord);
$("#btnProp1").onclick = guard(propagateCurrentRecord);

$("#newPath").oninput = () => { newPathManual = true; };
$("#newName").oninput = () => {
  if (!newPathManual) $("#newPath").value = `projekty/${slugify($("#newName").value)}/projekt.yaml`;
};
function toggleNewDlg() {
  const g0 = $("#newFormat").value === "gbuse0";
  $("#newOpt0").hidden = !g0;
  $("#newOpt1").hidden = g0;
  $("#newCustom0").hidden = !g0 || $("#newPreset0").value !== "custom";
  if ($("#newFontsFrom").value === "gbuse1" || $("#newFontsFrom").value === "gbuse0") {
    $("#newFontsFrom").value = g0 ? "gbuse0" : "gbuse1";
  }
}
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

export async function syncProject() {
  if (st.project) await api("/api/project/update", { data: st.project.data });
}
function touch() {
  setDirty(true);
  clearTimeout(touch.t);
  touch.t = setTimeout(guard(syncProject), 350);
}

// ---- simulation bar
$("#simMode").onchange = () => { simState.mode = $("#simMode").value; simState.playing = false; preview(); };
$("#simCykId").onchange = () => { simState.cycleId = $("#simCykId").value; simState.stepIdx = 0; preview(); };
$("#simStepPrev").onclick = () => {
  const n = ((st.project?.data?.cycles || {})[simState.cycleId] || []).length || 1;
  simState.stepIdx = (simState.stepIdx - 1 + n) % n;
  simState.mode = "cycle"; $("#simMode").value = "cycle"; preview();
};
$("#simStepNext").onclick = () => {
  const n = ((st.project?.data?.cycles || {})[simState.cycleId] || []).length || 1;
  simState.stepIdx = (simState.stepIdx + 1) % n;
  simState.mode = "cycle"; $("#simMode").value = "cycle"; preview();
};
$("#simStepPlay").onclick = () => {
  simState.mode = "cycle"; $("#simMode").value = "cycle";
  simState.playing = !simState.playing;
  $("#simStepPlay").textContent = simState.playing ? "⏸ Pauza" : "▶ Přehrát";
  preview();
};
["#simFree", "#simTime", "#simZone"].forEach((s) => $(s).oninput = () => { if (simState.mode === "cycle") preview(); });
$("#sim0AnimToggle").onclick = () => {
  simState.paused0 = !simState.paused0;
  $("#sim0AnimToggle").textContent = simState.paused0 ? "▶ Spustit střídání" : "⏸ Pozastavit střídání";
  preview();
};

// ---- record list
$("#secSelect").onchange = () => { st.section = $("#secSelect").value; st.key = null; renderList(); showEditor(); };
$("#search").oninput = () => renderList();

function plain(v) {
  if (v && typeof v === "object") return v.text ?? "";
  let s = String(v ?? "");
  if (isG0() || !s.includes("{")) {
    // Strip MNEMO control tokens to show clean text in the list
    const m = [...s.matchAll(/'([^']*)'/g)].map((x) => x[1]).join(" | ");
    if (m) return m;
  }
  return s.replace(/\{\{/g, "{").replace(/\{[^{}]*\}/g, "");
}

function badges(v) {
  const b = [];
  if (v && typeof v === "object") {
    if (v.font !== undefined) b.push("f" + v.font);
    if (v.effect) b.push("t" + v.effect);
    else if (v.run) b.push("t1");
    if (v.blink) b.push("blik");
    if (v.inverse) b.push("inv");
    if (v.align && v.align !== "center") b.push(v.align === "left" ? "vlevo" : "vpravo");
    if (v.x !== undefined && v.x !== null) b.push("x" + v.x);
  } else {
    const s = String(v ?? "");
    if (/\{/.test(s)) b.push("značky");
    else if (isG0()) {
      if (/\bLF\b/.test(s)) b.push("2ř");
      if (/\bVT\b/.test(s)) b.push("střídá");
      if (/\bb\b/.test(s)) b.push("b");
    }
  }
  return b.map((x) => `<span class="badge">${x}</span>`).join("");
}

export function renderList() {
  const list = $("#recList");
  if (!st.project) { list.innerHTML = ""; return; }
  const recs = st.project.data[st.section] || {};
  const q = $("#search").value.toLowerCase();
  list.innerHTML = Object.entries(recs)
    .filter(([k, v]) => !q || k.toLowerCase().includes(q) || plain(v).toLowerCase().includes(q) || String(v).toLowerCase().includes(q))
    .map(([k, v]) => `<div class="rec${k === st.key ? " sel" : ""}" data-k="${esc(k)}"><span class="k">${esc(k)}</span>` +
      `<span class="t">${esc(plain(v)) || '<span class="muted">(prázdné)</span>'}</span><span>${badges(v)}</span></div>`).join("");
  list.querySelectorAll(".rec").forEach((el) => el.onclick = () => { st.key = el.dataset.k; renderList(); showEditor(); });
}

// ---- editors (gBUSE1 + gBUSE0)
function fillFontSelects() {
  const opts = st.project.fonts.map((f) => `<option value="${f}">${f}</option>`).join("");
  $("#edFont").innerHTML = `<option value="">výchozí</option>` + opts;
  $("#ed0F1").innerHTML = `<option value="">výchozí</option>` + opts;
  $("#ed0F2").innerHTML = `<option value="">výchozí</option>` + opts;
  $("#fontSelect").innerHTML = opts;
}

function fillPreviewLines() {
  const lines = st.project.data.lines || {};
  const lOpts = `<option value="">(bez linky)</option>` +
    Object.entries(lines).map(([k, v]) => `<option value="${esc(k)}">${esc(k)} – ${esc(plain(v))}</option>`).join("");
  $("#edPreviewLine").innerHTML = lOpts;
  $("#edPreviewLine").value = st.previewLine;
  $("#ed0PreviewLine").innerHTML = lOpts;
  $("#ed0PreviewLine").value = st.previewLine;
  const stops = st.project.data.stops || {};
  $("#ed0PreviewStop").innerHTML = `<option value="">(bez zastávky)</option>` +
    Object.entries(stops).map(([k, v]) => `<option value="${esc(k)}">${esc(k)} – ${esc(plain(v))}</option>`).join("");
}

$("#edPreviewLine").onchange = () => { st.previewLine = $("#edPreviewLine").value; preview(); };
$("#ed0PreviewLine").onchange = () => { st.previewLine = $("#ed0PreviewLine").value; preview(); };
$("#ed0PreviewStop").onchange = () => preview();

function currentValue() {
  if (!st.project || st.key === null) return null;
  return (st.project.data[st.section] || {})[st.key];
}

function showEditor() {
  const v = currentValue();
  const none = v === undefined || v === null;
  $("#noSel").hidden = !none;
  if (none) {
    $("#edForm").hidden = true;
    $("#edForm0").hidden = true;
    drawFrame([], st.geometry?.columns || 135, st.geometry?.rows || 8, isG0());
    return;
  }
  if (isG0()) {
    $("#edForm").hidden = true;
    $("#edForm0").hidden = false;
    loadEditor0(v);
    return;
  }
  $("#edForm0").hidden = true;
  $("#edForm").hidden = false;
  $("#edKey").value = st.key;
  const isMarkup = typeof v === "string" && /\{/.test(v);
  $("#edMarkup").checked = isMarkup;
  $("#markupWrap").hidden = !isMarkup;
  $("#edMarkupText").value = typeof v === "string" ? v : "";
  $("#edMnemoChk").checked = false;
  $("#edMnemoWrap").hidden = true;
  const o = typeof v === "object" ? v : { text: isMarkup ? "" : v };
  $("#edText").value = o.text ?? "";
  $("#edText").disabled = isMarkup;
  $("#edFont").value = o.font ?? "";
  $("#edSpacing").value = o.spacing ?? "";
  $("#edAlign").value = o.align || "center";
  $("#edX").value = o.x ?? "";
  $("#edEffect").value = o.effect ?? (o.run ? "1" : "");
  $("#edRun").checked = !!o.run || o.effect === 1;
  $("#edBlink").checked = !!o.blink;
  $("#edInterval").value = o.interval ?? "";
  $("#edInv").checked = !!o.inverse;
  preview();
}

function readEditor() {
  if ($("#edMarkup").checked) return $("#edMarkupText").value;
  const o = { text: $("#edText").value };
  if ($("#edFont").value !== "") o.font = +$("#edFont").value;
  if ($("#edSpacing").value !== "") o.spacing = +$("#edSpacing").value;
  const eff = $("#edEffect").value;
  if (eff !== "") {
    if (+eff === 1) o.run = true;
    else o.effect = +eff;
  } else if ($("#edRun").checked) o.run = true;
  if ($("#edBlink").checked) o.blink = true;
  if ($("#edInterval").value !== "") o.interval = +$("#edInterval").value;
  if ($("#edInv").checked) o.inverse = true;
  if ($("#edX").value !== "") o.x = +$("#edX").value;
  else if ($("#edAlign").value !== "center") o.align = $("#edAlign").value;
  return Object.keys(o).length === 1 ? o.text : o;
}

const onEdit = () => {
  if (st.key === null) return;
  st.project.data[st.section][st.key] = readEditor();
  touch(); renderList(); preview();
};
["#edText", "#edSpacing", "#edX", "#edInterval", "#edMarkupText"].forEach((s) => $(s).oninput = onEdit);
["#edFont", "#edAlign", "#edInv", "#edBlink"].forEach((s) => $(s).onchange = onEdit);
$("#edRun").onchange = () => {
  if ($("#edRun").checked) $("#edEffect").value = "1";
  else if ($("#edEffect").value === "1") $("#edEffect").value = "";
  onEdit();
};
$("#edEffect").onchange = () => {
  $("#edRun").checked = $("#edEffect").value === "1";
  onEdit();
};

$("#edMarkup").onchange = () => {
  const v = currentValue();
  if ($("#edMarkup").checked) {
    $("#edMnemoChk").checked = false;
    $("#edMnemoWrap").hidden = true;
    $("#edMarkupText").value = typeof v === "string" ? v : toMarkup(v);
    $("#markupWrap").hidden = false; $("#edText").disabled = true;
  } else {
    $("#markupWrap").hidden = true; $("#edText").disabled = false;
    $("#edText").value = plain(v);
  }
  onEdit();
};

$("#edMnemoChk").onchange = guard(async () => {
  const show = $("#edMnemoChk").checked;
  $("#edMnemoWrap").hidden = !show;
  if (show) {
    const r = await api("/api/mnemo/convert", { direction: "to_mnemo", value: currentValue(), section: st.section });
    $("#edMnemoText").value = r.mnemo || "";
  }
});

$("#btnMnemoApply").onclick = guard(async () => {
  const r = await api("/api/mnemo/convert", { direction: "from_mnemo", value: $("#edMnemoText").value, section: st.section });
  if (r.problems?.length) toast(r.problems.join("; "), true);
  if (r.structured !== undefined && r.structured !== null) {
    st.project.data[st.section][st.key] = r.structured;
    touch(); renderList(); showEditor();
  }
});

$("#btnAutoformat1").onclick = guard(async () => {
  const txt = $("#edMarkup").checked ? plain($("#edMarkupText").value) : $("#edText").value;
  const r = await api("/api/gbuse/autoformat", { text: txt, section: st.section });
  $("#edFont").value = String(r.font);
  $("#edSpacing").value = String(r.spacing);
  onEdit();
});

function toMarkup(v) {
  if (!v || typeof v !== "object") return String(v ?? "").replace(/\{/g, "{{");
  let s = "";
  if (v.font !== undefined) s += `{f${v.font}}`;
  if (v.spacing !== undefined) s += `{m${v.spacing}}`;
  if (v.effect !== undefined) s += `{t${v.effect}}`;
  else if (v.run) s += "{run}";
  if (v.blink) s += "{blink}";
  if (v.interval !== undefined) s += `{z:${Math.round(v.interval * 10)}}`;
  if (v.inverse) s += "{inv}";
  if (v.x !== undefined && !v.run && !v.effect) s += `{x:${v.x}}`;
  s += (v.text || "").replace(/\{/g, "{{");
  if (v.inverse) s += "{inv}";
  if (v.run && v.effect === undefined) s += "{/run}";
  return s;
}

// ---- gBUSE0 structured + MNEMO editor
async function loadEditor0(v) {
  $("#ed0Key").value = st.key;
  $("#ed0Equivalent").value = (st.project.data.equivalents || {})[st.key] || "";
  const r = await api("/api/mnemo/convert", { direction: "to_mnemo", value: v, section: st.section });
  $("#ed0Mnemo").value = typeof v === "string" ? v : (r.mnemo || "");
  const s = r.structured;
  if (!s) {
    $("#ed0Mode").value = "mnemo";
    toggleMode0();
    preview();
    return;
  }
  $("#ed0Mode").value = s.mode === "2" ? "2" : "1";
  $("#ed0Auto").checked = false;
  $("#ed0Full").checked = !!s.full_panel;
  $("#ed0Suppress").checked = !!s.suppress_stops;
  $("#ed0Interval").value = s.interval ?? "";
  fillLine0(1, s.line1 || {});
  fillLine0(2, s.line2 || {});
  toggleMode0();
  preview();
}

function fillLine0(n, l) {
  $(`#ed0T${n}`).value = l.text ?? "";
  $(`#ed0F${n}`).value = l.font ?? "";
  $(`#ed0M${n}`).value = l.spacing ?? "";
  $(`#ed0Y${n}`).value = l.shift ?? "";
  $(`#ed0A${n}`).value = l.align || "center";
  $(`#ed0X${n}`).value = l.x ?? "";
  $(`#ed0I${n}`).checked = !!l.inverse;
}

function readLine0(n) {
  const o = { text: $(`#ed0T${n}`).value, align: $(`#ed0A${n}`).value, inverse: $(`#ed0I${n}`).checked };
  if ($(`#ed0F${n}`).value !== "") o.font = +$(`#ed0F${n}`).value;
  if ($(`#ed0M${n}`).value !== "") o.spacing = +$(`#ed0M${n}`).value;
  if ($(`#ed0Y${n}`).value !== "") o.shift = +$(`#ed0Y${n}`).value;
  if ($(`#ed0X${n}`).value !== "") o.x = +$(`#ed0X${n}`).value;
  return o;
}

function toggleMode0() {
  const m = $("#ed0Mode").value;
  $("#ed0L1Wrap").hidden = m === "mnemo";
  $("#ed0L2Wrap").hidden = m !== "2";
  $("#ed0MnemoDetails").open = m === "mnemo";
}

let compose0Timer = null;
function scheduleCompose0() {
  if (st.key === null || !isG0()) return;
  if ($("#ed0Mode").value === "mnemo") {
    st.project.data[st.section][st.key] = $("#ed0Mnemo").value;
    touch(); renderList(); preview();
    return;
  }
  clearTimeout(compose0Timer);
  compose0Timer = setTimeout(guard(async () => {
    const spec = {
      mode: $("#ed0Mode").value,
      autoformat: $("#ed0Auto").checked,
      full_panel: $("#ed0Full").checked,
      suppress_stops: $("#ed0Suppress").checked,
      interval: $("#ed0Interval").value !== "" ? +$("#ed0Interval").value : null,
      line1: readLine0(1),
      line2: readLine0(2),
    };
    const r = await api("/api/gbuse0/compose", { spec, section: st.section });
    st.project.data[st.section][st.key] = r.mnemo;
    $("#ed0Mnemo").value = r.mnemo;
    touch(); renderList(); preview();
  }), 120);
}

$("#ed0Mode").onchange = () => { toggleMode0(); scheduleCompose0(); };
["#ed0Auto", "#ed0Full", "#ed0Suppress", "#ed0F1", "#ed0A1", "#ed0I1", "#ed0F2", "#ed0A2", "#ed0I2"].forEach((s) => $(s).onchange = scheduleCompose0);
["#ed0Interval", "#ed0T1", "#ed0M1", "#ed0Y1", "#ed0X1", "#ed0T2", "#ed0M2", "#ed0Y2", "#ed0X2"].forEach((s) => $(s).oninput = scheduleCompose0);

$("#ed0Mnemo").oninput = () => {
  if (st.key === null || !isG0()) return;
  st.project.data[st.section][st.key] = $("#ed0Mnemo").value;
  touch(); renderList(); preview();
};

$("#btn0ApplyMnemo").onclick = guard(async () => {
  st.project.data[st.section][st.key] = $("#ed0Mnemo").value;
  touch(); renderList();
  await loadEditor0($("#ed0Mnemo").value);
});

$("#btn0Auto").onclick = guard(async () => {
  const two = $("#ed0Mode").value === "2";
  const r1 = await api("/api/gbuse/autoformat", { text: $("#ed0T1").value, two_line: two, section: st.section });
  $("#ed0F1").value = String(r1.font); $("#ed0M1").value = String(r1.spacing); $("#ed0Y1").value = String(r1.shift);
  if (two) {
    const firstAlt = ($("#ed0T2").value || "").split("|")[0];
    const r2 = await api("/api/gbuse/autoformat", { text: firstAlt, two_line: true, section: st.section });
    $("#ed0F2").value = String(r2.font); $("#ed0M2").value = String(r2.spacing);
  }
  scheduleCompose0();
});

$("#btn0Swap").onclick = () => {
  const a = readLine0(1), b = readLine0(2);
  fillLine0(1, b); fillLine0(2, a);
  if ($("#ed0Mode").value !== "2") $("#ed0Mode").value = "2";
  toggleMode0();
  scheduleCompose0();
};

$("#ed0Equivalent").oninput = () => {
  if (st.key === null) return;
  const eq = st.project.data.equivalents = st.project.data.equivalents || {};
  const val = $("#ed0Equivalent").value;
  if (val) eq[st.key] = val; else delete eq[st.key];
  touch();
};

function renameKey(newRaw, inputEl) {
  const nk = newRaw.trim(), len = secMeta()[st.section][1];
  if (!/^\d+$/.test(nk) || nk.length > len) {
    toast(`Číslo musí mít nejvýše ${len} číslic`, true);
    inputEl.value = st.key;
    return;
  }
  const k = isSpecialSec() ? nk : nk.padStart(len, "0");
  const recs = st.project.data[st.section];
  if (k !== st.key && recs[k] !== undefined) {
    toast("Toto číslo už existuje", true);
    inputEl.value = st.key;
    return;
  }
  const entries = Object.entries(recs).map(([kk, vv]) => [kk === st.key ? k : kk, vv]).sort((a, b) => a[0].localeCompare(b[0]));
  st.project.data[st.section] = Object.fromEntries(entries);
  st.key = k;
  touch(); renderList(); showEditor();
}
$("#edKey").onchange = () => renameKey($("#edKey").value, $("#edKey"));
$("#ed0Key").onchange = () => renameKey($("#ed0Key").value, $("#ed0Key"));

$("#btnAdd").onclick = () => {
  if (!st.project) return;
  const len = secMeta()[st.section][1];
  const recs = st.project.data[st.section] = st.project.data[st.section] || {};
  let n = isSpecialSec() ? 0 : 1;
  const fmtKey = (num) => isSpecialSec() ? String(num) : String(num).padStart(len, "0");
  while (recs[fmtKey(n)] !== undefined) n++;
  const k = fmtKey(n);
  recs[k] = isG0() ? "'NOVÝ TEXT' CR" : "NOVÝ TEXT";
  st.project.data[st.section] = Object.fromEntries(Object.entries(recs).sort((a, b) => a[0].localeCompare(b[0])));
  st.key = k;
  touch(); renderList(); showEditor();
};

let delArmed = 0;
function deleteRecord() {
  if (Date.now() - delArmed > 4000) { delArmed = Date.now(); toast("Klikněte znovu pro smazání " + st.key); return; }
  delete st.project.data[st.section][st.key];
  st.key = null;
  touch(); renderList(); showEditor();
}
$("#btnDelete").onclick = deleteRecord;
$("#btn0Delete").onclick = deleteRecord;

async function sendCurrentToPanel() {
  if (isSpecialSec()) { toast("Tuto sekci nelze poslat jako samostatný IBIS telegram", true); return; }
  const [cmd, len] = secMeta()[st.section];
  const tel = [];
  if (st.section !== "lines" && st.previewLine !== "") tel.push("l" + st.previewLine.padStart(3, "0"));
  tel.push(cmd + st.key.padStart(len, "0"));
  await api("/api/panel/send", { telegrams: tel });
  toast("Odesláno: " + tel.join(" "));
}
$("#btnShow").onclick = guard(sendCurrentToPanel);
$("#btn0Show").onclick = guard(sendCurrentToPanel);

$("#btnCheck").onclick = guard(async () => {
  await syncProject();
  const r = await api("/api/project/check");
  $("#checkOut").innerHTML = r.items.length
    ? r.items.map((i) => `<div class="${i.level}">${esc(i.section)} ${esc(i.key)}: ${esc(i.msg)}</div>`).join("")
    : `<div style="color:var(--ok)">Projekt je v pořádku.</div>`;
});

// ---- cycles editor (gBUSE1 typ 2)
function ensureCycles() {
  if (!st.project) return {};
  return st.project.data.cycles = st.project.data.cycles || {};
}

export function showCycles() {
  if (!st.project || isG0()) return;
  const cycles = ensureCycles();
  const tabs = [];
  for (let i = 0; i <= 9; i++) {
    const cid = String(i);
    const cnt = (cycles[cid] || []).length;
    tabs.push(`<button data-cid="${cid}" class="${cyclesState.id === cid ? "cur" : ""}">${cid}${cnt ? ` (${cnt})` : ""}</button>`);
  }
  $("#cykTabs").innerHTML = tabs.join("");
  $("#cykTabs").querySelectorAll("button").forEach((b) => b.onclick = () => {
    cyclesState.id = b.dataset.cid;
    showCycles();
  });
  $("#cykFmt").value = cyclesState.fmt;

  const steps = cycles[cyclesState.id] || [];
  const meta = st.project.cyk_meta || { sources: {}, enter: {}, leave: {} };
  const fmtVal = (map, val) => {
    const n = Number(val || 0);
    if (cyclesState.fmt === "hex") return "0x" + n.toString(16).toUpperCase().padStart(2, "0");
    if (cyclesState.fmt === "dec") return String(n);
    return `${n}: ${map[String(n)] || "?"}`;
  };
  const optSel = (cls, map, val) => `<select class="${cls}">` +
    Object.entries(map).map(([k, lbl]) =>
      `<option value="${esc(k)}" ${Number(k) === Number(val || 0) ? "selected" : ""}>${esc(fmtVal(map, k))}</option>`
    ).join("") + `</select>`;

  $("#cykSteps").innerHTML = steps.map((step, i) => {
    const l = step.left || { width: 0, source: 0, dop: 0, duration: 3, enter: 0, leave: 0, id: "" };
    const r = step.right || { width: 0, source: 2, dop: 0, duration: 3, enter: 0, leave: 0, id: "" };
    const dur = l.duration || r.duration || 3;
    return `<tr data-i="${i}">
      <td class="mono">${i + 1}</td>
      <td><input class="cw" data-side="left" type="number" min="0" max="240" value="${l.width ?? 0}" style="width:4em"></td>
      <td>${optSel("csrc-l", meta.sources, l.source)}</td>
      <td><input class="cdop" data-side="left" type="number" min="0" max="255" value="${l.dop ?? 0}" style="width:3.5em"></td>
      <td class="nowrap">${optSel("cent-l", meta.enter, l.enter)} / ${optSel("clv-l", meta.leave, l.leave)}</td>
      <td><input class="cid" data-side="left" value="${esc(l.id ?? "")}" style="width:6em"></td>
      <td><input class="cw" data-side="right" type="number" min="0" max="240" value="${r.width ?? 0}" style="width:4em"></td>
      <td>${optSel("csrc-r", meta.sources, r.source)}</td>
      <td><input class="cdop" data-side="right" type="number" min="0" max="255" value="${r.dop ?? 0}" style="width:3.5em"></td>
      <td class="nowrap">${optSel("cent-r", meta.enter, r.enter)} / ${optSel("clv-r", meta.leave, r.leave)}</td>
      <td><input class="cid" data-side="right" value="${esc(r.id ?? "")}" style="width:6em"></td>
      <td><input class="cdur" type="number" min="1" max="255" value="${dur}" style="width:3.5em"></td>
      <td><button class="cdel danger">✕</button></td>
    </tr>`;
  }).join("");

  $("#cykSteps").querySelectorAll("tr").forEach((tr) => {
    const idx = +tr.dataset.i;
    const readRow = () => {
      const dur = +tr.querySelector(".cdur").value || 3;
      const side = (s, sc) => ({
        width: +tr.querySelector(`.cw[data-side="${s}"]`).value || 0,
        source: +tr.querySelector(`.csrc-${sc}`).value || 0,
        dop: +tr.querySelector(`.cdop[data-side="${s}"]`).value || 0,
        duration: dur,
        enter: +tr.querySelector(`.cent-${sc}`).value || 0,
        leave: +tr.querySelector(`.clv-${sc}`).value || 0,
        id: tr.querySelector(`.cid[data-side="${s}"]`).value,
      });
      steps[idx] = { left: side("left", "l"), right: side("right", "r") };
      touch();
    };
    tr.querySelectorAll("input, select").forEach((el) => el.onchange = readRow);
    tr.querySelector(".cdel").onclick = () => { steps.splice(idx, 1); touch(); showCycles(); };
  });
}

$("#cykFmt").onchange = () => { cyclesState.fmt = $("#cykFmt").value; showCycles(); };
$("#cykAdd").onclick = () => {
  const c = ensureCycles();
  const list = c[cyclesState.id] = c[cyclesState.id] || [];
  list.push({
    left: { width: 22, source: 1, dop: 0, duration: 3, enter: 0, leave: 0, id: "linka" },
    right: { width: 113, source: 2, dop: 0, duration: 3, enter: 0, leave: 0, id: "cíl" },
  });
  updateModeChrome(); touch(); showCycles();
};
$("#cykClear").onclick = () => {
  const c = ensureCycles();
  delete c[cyclesState.id];
  updateModeChrome(); touch(); showCycles();
};
$("#cykPlay").onclick = () => {
  simState.mode = "cycle";
  simState.cycleId = cyclesState.id;
  simState.stepIdx = 0;
  simState.playing = true;
  if ($("#simMode")) $("#simMode").value = "cycle";
  if ($("#simCykId")) $("#simCykId").value = cyclesState.id;
  $("#simStepPlay").textContent = "⏸ Pauza";
  preview();
};
$("#cykExport").onclick = guard(async () => {
  await syncProject();
  await downloadSectionExport(".cyk");
});
$("#cykImportFile").onchange = guard(async () => {
  const f = $("#cykImportFile").files[0];
  if (!f) return;
  await uploadSectionImport(f, "destinations", true);
  showCycles();
});

// ---- config
export function showConfig() {
  if (!st.project) return;
  const g0 = isG0();
  const d = st.project.data, c = d.config || (d.config = {}), p = d.panel || (d.panel = {});
  const df = c.defaults || (c.defaults = {}), meta = d.meta || {};
  const b = st.project.bundle;
  $("#cfgMetaName").value = b ? b.name : (meta.name ?? st.project.name ?? "");
  $("#cfgMetaDesc").value = b ? b.description : (meta.description ?? "");
  $("#cfgBundleWrap").hidden = !b;
  if (b) {
    const sysOpts = [`<option value="default">Výchozí systém</option>`].concat(
      (allTemplates.systemy || []).map((s) => `<option value="template:${esc(s)}">Šablona: ${esc(s)}</option>`)
    );
    $("#cfgBundleSys").innerHTML = sysOpts.join("");
    if (b.simulation?.system) $("#cfgBundleSys").value = b.simulation.system;
    const lineOpts = [`<option value="">— žádná —</option>`].concat(
      (allTemplates.trasy || []).map((s) => `<option value="template:${esc(s)}">Šablona: ${esc(s)}</option>`)
    );
    $("#cfgBundleLine").innerHTML = lineOpts.join("");
    if (b.simulation?.default_line) $("#cfgBundleLine").value = b.simulation.default_line;
  }
  $("#cfgFormat").value = g0 ? "gBUSE0" : "gBUSE1";
  $("#cfgType").value = p.type || (g0 ? "BS210" : "BS120");
  $("#cfgAddr").value = p.ibis_address ?? 10;
  $("#cfgColumns").value = c.columns ?? (g0 ? 140 : 135);
  $("#cfgRowsWrap").hidden = !g0;
  $("#cfgRows").value = c.rows ?? 19;
  $("#cfgVersion").value = c.version ?? "";

  const fwList = g0 ? ["1.6", "2.15", "3.08", "3.18"] : ["1.xx", "2.06", "2.12"];
  $("#cfgFw").innerHTML = fwList.map((f) => `<option value="${f}">${f}</option>`).join("");
  $("#cfgFw").value = fwList.includes(String(c.fw)) ? String(c.fw) : fwList[1];

  $("#cfg1Wrap").hidden = g0;
  $("#cfg0Wrap").hidden = !g0;

  if (!g0) {
    $("#cfgFont").value = df.font ?? 3;
    $("#cfgSpacing").value = df.spacing ?? 1;
    $("#cfgInterval").value = df.interval ?? 1;
    $("#cfgCycle").value = c.cycle_delay ?? 10;
    $("#cfgKind").value = String(c.panel_kind ?? 1);
    $("#cfgTariff").value = c.tariff_digits ?? 1;
  } else {
    $("#cfg0Num").value = p.panel_number ?? 0;
    $("#cfg0Bright").value = p.brightness ?? 0;
    $("#cfg0Sep").value = c.fnt_sep || ":";
    $("#cfg0Preset").value = "";
    const props = new Set(c.properties || []);
    const propMeta = st.project.gbuse0_meta?.properties || [];
    $("#cfg0Props").innerHTML = propMeta.map(([, name, label]) =>
      `<label class="chk"><input type="checkbox" value="${esc(name)}" ${props.has(name) ? "checked" : ""}> ${esc(label)}</label>`
    ).join("");
    $("#cfg0Props").querySelectorAll("input").forEach((el) => el.onchange = readConfig);

    const fields = c.fields || (c.fields = {});
    const fNames = [["line", "Pole linky (LIN)"], ["dest", "Pole cíle (CIL)"], ["stop", "Pole nácestné (DRU)"]];
    $("#cfg0Fields").innerHTML = fNames.map(([fk, title]) => {
      const f = fields[fk] || { r0: 0, r1: 0, c0: 0, c1: 0, font: 3, spacing: 1, shift: 0, interval: 1.0 };
      return `<div class="card0" data-fk="${fk}">
        <b>${title}</b>
        <div class="grid2">
          <label>r0 <input data-p="r0" type="number" value="${f.r0 ?? 0}"></label>
          <label>r1 <input data-p="r1" type="number" value="${f.r1 ?? 0}"></label>
          <label>c0 <input data-p="c0" type="number" value="${f.c0 ?? 0}"></label>
          <label>c1 <input data-p="c1" type="number" value="${f.c1 ?? 0}"></label>
          <label>font <input data-p="font" type="number" value="${f.font ?? 3}"></label>
          <label>mez. <input data-p="spacing" type="number" value="${f.spacing ?? 1}"></label>
          <label>posun <input data-p="shift" type="number" value="${f.shift ?? 0}"></label>
          <label>s <input data-p="interval" type="number" step="0.1" value="${f.interval ?? 1.0}"></label>
        </div>
      </div>`;
    }).join("");
    $("#cfg0Fields").querySelectorAll("input").forEach((el) => el.onchange = readConfig);
    $("#cfg0Afc").value = (c.afc || []).map((row) => row.join(", ")).join("\n");
    $("#cfg0Afd").value = (c.afd || []).map((row) => row.join(", ")).join("\n");
  }

  const allCmds = g0 ? (st.project.gbuse0_meta?.commands || []) : ALL_CMDS_1;
  const cmds = new Set(c.commands || []);
  $("#cfgCommands").innerHTML = allCmds.map((x) =>
    `<label class="chk"><input type="checkbox" value="${esc(x)}" ${cmds.has(x) ? "checked" : ""}> ${esc(x)}</label>`
  ).join("");
  $("#cfgCommands").querySelectorAll("input").forEach((i) => i.onchange = readConfig);
  $("#cfgKod").value = d.kod_table || "";
}

function parseAfText(txt) {
  return txt.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
    .map((l) => l.split(/[,;\s]+/).map((n) => +n || 0).slice(0, 3))
    .map((row) => [row[0] ?? 0, row[1] ?? 1, row[2] ?? 0]);
}

function readConfig() {
  if (!st.project) return;
  const g0 = isG0(), d = st.project.data, c = d.config, p = d.panel;
  const mn = $("#cfgMetaName").value.trim(), md = $("#cfgMetaDesc").value.trim();
  if (st.project.bundle) {
    st.project.bundle.name = mn;
    st.project.bundle.description = md;
    if (mn) st.project.name = mn;
    api("/api/bundle/update-meta", {
      name: mn,
      description: md,
      simulation: { ...(st.project.bundle.simulation || {}), system: $("#cfgBundleSys").value, default_line: $("#cfgBundleLine").value },
    }).catch(() => {});
  } else {
    d.meta = { name: mn, description: md };
    if (mn) st.project.name = mn;
  }
  p.type = $("#cfgType").value;
  p.ibis_address = +$("#cfgAddr").value;
  c.columns = +$("#cfgColumns").value;
  c.version = $("#cfgVersion").value;
  c.fw = $("#cfgFw").value;
  if (!g0) {
    c.magic = c.fw.startsWith("1") ? 0xAA : 0xAB;
    c.defaults = { font: +$("#cfgFont").value, spacing: +$("#cfgSpacing").value, interval: +$("#cfgInterval").value };
    c.cycle_delay = +$("#cfgCycle").value;
    c.panel_kind = +$("#cfgKind").value;
    c.tariff_digits = +$("#cfgTariff").value;
  } else {
    c.rows = +$("#cfgRows").value;
    c.magic = c.fw.startsWith("1") ? 0x55 : 0x57;
    c.fw_3x = c.fw.startsWith("3");
    p.panel_number = +$("#cfg0Num").value;
    p.brightness = +$("#cfg0Bright").value;
    c.fnt_sep = $("#cfg0Sep").value;
    c.properties = $$("#cfg0Props input:checked").map((el) => el.value);
    const fields = {};
    $$("#cfg0Fields [data-fk]").forEach((box) => {
      const f = {};
      box.querySelectorAll("input").forEach((inp) => { f[inp.dataset.p] = inp.dataset.p === "interval" ? +inp.value : Math.round(+inp.value); });
      if (f.r1 > 0 && f.c1 > 0) fields[box.dataset.fk] = f;
    });
    c.fields = fields;
    c.afc = parseAfText($("#cfg0Afc").value);
    c.afd = parseAfText($("#cfg0Afd").value);
  }
  c.commands = $$("#cfgCommands input:checked").map((i) => i.value);
  const kod = $("#cfgKod").value.trim();
  if (kod) d.kod_table = kod; else delete d.kod_table;
  st.geometry = { ...st.geometry, columns: c.columns, rows: g0 ? c.rows : 8 };
  updateModeChrome();
  touch();
}

["#cfgMetaName", "#cfgMetaDesc", "#cfgBundleSys", "#cfgBundleLine", "#cfgType", "#cfgFw", "#cfgAddr", "#cfgColumns", "#cfgRows", "#cfgVersion",
  "#cfgFont", "#cfgSpacing", "#cfgInterval", "#cfgCycle", "#cfgKind", "#cfgTariff",
  "#cfg0Num", "#cfg0Bright", "#cfg0Sep", "#cfg0Afc", "#cfg0Afd", "#cfgKod"].forEach((s) => $(s).onchange = readConfig);
$("#btnBundleApplySim").onclick = guard(async () => {
  await syncProject();
  const r = await api("/api/bundle/apply-sim", {});
  toast("Panely projektu načteny do simulace: " + (r.vehicle || "OK"));
});

$("#cfg0Preset").onchange = () => {
  const pr = $("#cfg0Preset").value;
  if (!pr || !isG0()) return;
  const presets = {
    front: [140, 19, ":", { line: { r0: 1, r1: 19, c0: 1, c1: 28, font: 8, spacing: 1, shift: 0, interval: 1 }, dest: { r0: 1, r1: 19, c0: 29, c1: 140, font: 7, spacing: 1, shift: 0, interval: 1 } }],
    side: [112, 19, " ", { line: { r0: 1, r1: 19, c0: 1, c1: 28, font: 8, spacing: 1, shift: 0, interval: 1 }, dest: { r0: 1, r1: 19, c0: 29, c1: 112, font: 7, spacing: 1, shift: 0, interval: 1 } }],
    side_via: [112, 19, " ", { line: { r0: 1, r1: 19, c0: 1, c1: 28, font: 8, spacing: 1, shift: 0, interval: 1 }, dest: { r0: 9, r1: 19, c0: 29, c1: 112, font: 3, spacing: 1, shift: 0, interval: 1 }, stop: { r0: 1, r1: 8, c0: 29, c1: 112, font: 11, spacing: 1, shift: 0, interval: 3 } }],
    rear: [28, 19, ".", { line: { r0: 1, r1: 19, c0: 1, c1: 28, font: 8, spacing: 1, shift: 0, interval: 1 } }],
  };
  const [cols, rows, sep, fields] = presets[pr];
  Object.assign(st.project.data.config, { columns: cols, rows, fnt_sep: sep, fields });
  touch(); showConfig(); updateModeChrome();
};

// ---- fonts
export async function loadFonts() {
  if (!st.project) return;
  st.fonts = await api("/api/fonts");
  st.project.fonts = st.fonts.fonts.map((f) => f.id);
  fillFontSelects();
  if (st.font === null || !st.fonts.fonts.find((f) => f.id === st.font)) st.font = st.fonts.fonts[0]?.id ?? null;
  $("#fontSelect").value = st.font;
  renderGlyphs(); renderCodeTable();
}

$("#fontSelect").onchange = () => { st.font = +$("#fontSelect").value; st.glyph = null; renderGlyphs(); $("#glyphEditor").hidden = true; };
$("#glyphFilter").oninput = () => renderGlyphs();

function glyphThumb(rows, scale = 3) {
  const h = rows.length || 8;
  const sc = h > 12 ? 2 : scale;
  const w = Math.max(1, rows[0]?.length || 1);
  const c = document.createElement("canvas");
  c.width = w * sc; c.height = h * sc;
  const g = c.getContext("2d");
  g.fillStyle = "#2a2118"; g.fillRect(0, 0, c.width, c.height);
  g.fillStyle = "#ffb02e";
  rows.forEach((r, y) => [...r].forEach((ch, x) => ch === "#" && g.fillRect(x * sc, y * sc, sc, sc)));
  return c;
}

function renderGlyphs() {
  const f = st.fonts?.fonts.find((x) => x.id === st.font);
  const list = $("#glyphList"); list.innerHTML = "";
  if (!f) return;
  const q = $("#glyphFilter").value;
  f.glyphs.filter((g) => !q || g.char === q).forEach((g) => {
    const d = document.createElement("div");
    d.className = "glyph" + (st.glyph?.code === g.code ? " sel" : "");
    if (g.rows) d.append(glyphThumb(g.rows));
    d.append(document.createTextNode(g.char === " " ? "␣" : (g.char ?? "#" + g.code.toString(16))));
    d.onclick = () => editGlyph(g);
    list.append(d);
  });
}

function editGlyph(g) {
  const rows = g.rows && g.rows.length ? g.rows : Array(g.height || 8).fill("....");
  st.glyph = { code: g.code, char: g.char, rows: rows.map((r) => [...r].map((c) => c === "#")) };
  $("#glyphEditor").hidden = false;
  drawGlyph(); renderGlyphs();
}

const gc = $("#glyphCanvas"), gx = gc.getContext("2d"), CELL = 20;
function drawGlyph() {
  const rows = st.glyph.rows, h = rows.length, w = rows[0]?.length || 1;
  $("#glyphTitle").textContent = `Font ${st.font} – znak „${st.glyph.char ?? "?"}“ (0x${st.glyph.code.toString(16).toUpperCase()}, ${w}×${h})`;
  gc.width = Math.max(1, w) * CELL + 1; gc.height = Math.max(1, h) * CELL + 1;
  gx.fillStyle = "#0b0907"; gx.fillRect(0, 0, gc.width, gc.height);
  rows.forEach((r, y) => r.forEach((on, x) => {
    gx.fillStyle = on ? "#ffb02e" : "#2a2118";
    gx.beginPath(); gx.arc(x * CELL + CELL / 2, y * CELL + CELL / 2, CELL * 0.4, 0, 7); gx.fill();
  }));
}

let painting = null;
gc.onmousedown = (e) => { const p = cell(e); if (!p) return; painting = !st.glyph.rows[p.y][p.x]; setCell(p); };
gc.onmousemove = (e) => { if (painting !== null) { const p = cell(e); if (p) setCell(p); } };
window.addEventListener("mouseup", () => painting = null);
function cell(e) {
  const r = gc.getBoundingClientRect(), sx = gc.width / r.width;
  const x = Math.floor((e.clientX - r.left) * sx / CELL), y = Math.floor((e.clientY - r.top) * sx / CELL);
  return st.glyph && y >= 0 && y < st.glyph.rows.length && x >= 0 && x < (st.glyph.rows[0]?.length || 0) ? { x, y } : null;
}
function setCell(p) { st.glyph.rows[p.y][p.x] = painting; drawGlyph(); }

const gmod = (fn) => () => { if (st.glyph) { st.glyph.rows = fn(st.glyph.rows); drawGlyph(); } };
$("#gWidthPlus").onclick = gmod((r) => r.map((row) => [...row, false]));
$("#gWidthMinus").onclick = gmod((r) => (r[0]?.length || 0) > 0 ? r.map((row) => row.slice(0, -1)) : r);
$("#gHeightPlus").onclick = gmod((r) => r.length < 28 ? [...r, Array(r[0]?.length || 4).fill(false)] : r);
$("#gHeightMinus").onclick = gmod((r) => r.length > 1 ? r.slice(0, -1) : r);
$("#gClear").onclick = gmod((r) => r.map((row) => row.map(() => false)));
$("#gInvert").onclick = gmod((r) => r.map((row) => row.map((v) => !v)));
$("#gShiftL").onclick = gmod((r) => r.map((row) => [...row.slice(1), false]));
$("#gShiftR").onclick = gmod((r) => r.map((row) => [false, ...row.slice(0, -1)]));
$("#gShiftU").onclick = gmod((r) => [...r.slice(1), r[0].map(() => false)]);
$("#gShiftD").onclick = gmod((r) => [r[0].map(() => false), ...r.slice(0, -1)]);

$("#gSave").onclick = guard(async () => {
  const rows = st.glyph.rows.map((r) => r.map((v) => v ? "#" : ".").join(""));
  await api("/api/fonts/glyph", { font: st.font, code: st.glyph.code, rows, height: rows.length });
  st.dirty = true; toast("Znak uložen do projektu (nezapomeňte Uložit)");
  await loadFonts(); preview();
});

let gdelArmed = 0;
$("#gDelete").onclick = guard(async () => {
  if (Date.now() - gdelArmed > 4000) { gdelArmed = Date.now(); toast("Klikněte znovu pro smazání znaku"); return; }
  await api("/api/fonts/glyph/delete", { font: st.font, code: st.glyph.code });
  $("#glyphEditor").hidden = true; st.glyph = null; st.dirty = true; loadFonts();
});

$("#btnNewGlyph").onclick = guard(async () => {
  const ch = $("#newGlyphChar").value;
  const e = /^#[0-9a-fA-F]{2}$/.test(ch) ? { code: parseInt(ch.slice(1), 16) } : st.fonts.codetable.find((c) => c.char === ch);
  if (!e) { toast("Znak není v kódové tabulce – přiřaďte ho kódu níže, nebo zadejte kód jako #C1", true); return; }
  const defH = isG0() ? (st.fonts.fonts.find((f) => f.id === st.font)?.glyphs[0]?.height || 8) : 8;
  await api("/api/fonts/glyph", { font: st.font, code: e.code, rows: Array(defH).fill("...."), height: defH });
  await loadFonts();
  editGlyph(st.fonts.fonts.find((f) => f.id === st.font).glyphs.find((g) => g.code === e.code));
});

function parseFontCode(val) {
  const s = (val || "").trim();
  if (/^0x[0-9a-fA-F]{2}$/i.test(s)) return parseInt(s, 16);
  if (/^#[0-9a-fA-F]{2}$/.test(s)) return parseInt(s.slice(1), 16);
  const found = st.fonts?.codetable.find((c) => c.char === s);
  return found ? found.code : null;
}

async function fontManage(action, extra = {}) {
  const r = await api("/api/fonts/manage", {
    action,
    source: $("#fmSource").value,
    src_font: +$("#fmSrcFont").value,
    dst_font: +$("#fmDstFont").value,
    ...extra,
  });
  st.dirty = true;
  if (extra.dst_font !== undefined) st.font = extra.dst_font;
  await loadFonts();
  toast("Operace nad fonty dokončena" + (r.copied !== undefined ? ` (${r.copied} znaků)` : ""));
}
$("#fmNewFont").onclick = guard(() => fontManage("new_font", { dst_font: +$("#fmDstFont").value }));
$("#fmCopyGlyph").onclick = guard(() => {
  const c = parseFontCode($("#fmCode").value);
  if (c === null) { toast("Neplatný znak / kód", true); return; }
  return fontManage("copy_glyph", { src_code: c, dst_code: c });
});
$("#fmCopyFont").onclick = guard(() => fontManage("copy_font"));
$("#fmCopyAll").onclick = guard(() => fontManage("copy_all"));
$("#fmDelFont").onclick = guard(() => fontManage("delete_font"));
$("#fmDelCodeAll").onclick = guard(() => {
  const c = parseFontCode($("#fmCode").value);
  if (c === null) { toast("Neplatný znak / kód", true); return; }
  return fontManage("delete_code_all", { dst_code: c });
});
$("#fmExportFnt").onclick = guard(async () => {
  const r = await fetch("/api/fonts/export-fnt");
  if (!r.ok) throw new Error(await r.text());
  const a = document.createElement("a");
  a.href = URL.createObjectURL(await r.blob());
  a.download = "fonty.fnt";
  a.click();
});
$("#fmImportFnt").onchange = guard(async () => {
  const f = $("#fmImportFnt").files[0];
  if (!f) return;
  const b64 = await readB64(f);
  await api("/api/fonts/import-fnt", { content_b64: b64 });
  st.dirty = true;
  await loadFonts();
  toast("Fonty načteny z .fnt");
});

function renderCodeTable() {
  $("#codeTable").innerHTML = st.fonts.codetable.filter((c) => c.code >= 0x21).map((c) =>
    `<label>${c.code.toString(16).toUpperCase()}<input data-code="${c.code}" value="${esc(c.char ?? "")}" maxlength="1"></label>`).join("");
  $("#codeTable").querySelectorAll("input").forEach((i) => i.onchange = guard(async () => {
    await api("/api/fonts/codetable", { code: +i.dataset.code, char: i.value });
    st.dirty = true; loadFonts();
  }));
}

// ---- import / export section files
function readB64(f) {
  return new Promise((res) => { const r = new FileReader(); r.onload = () => res(r.result.split(",")[1]); r.readAsDataURL(f); });
}

$("#btnImport").onclick = guard(async () => {
  const f = $("#impFile").files[0];
  if (!f) { toast("Vyberte soubor", true); return; }
  const b64 = await readB64(f);
  const defaults = {};
  if ($("#impFont").value !== "") defaults.font = +$("#impFont").value;
  if ($("#impSpacing").value !== "") defaults.spacing = +$("#impSpacing").value;
  await syncProject();
  const r = await api("/api/import", { name: f.name, content_b64: b64, section: $("#impSection").value, replace: $("#impReplace").checked, defaults });
  st.project.data = r.data; st.dirty = true; renderList();
  $("#impOut").textContent = "Importováno: " + JSON.stringify(r.counts) + (r.problems.length ? "\n\nProblémy:\n" + r.problems.join("\n") : "");
});

$("#btnTemplate").onclick = () => {
  const csv = "sekce;cislo;text;font;mezera;zarovnani;bezici;inverze\ncile;101;HLAVNÍ NÁDRAŽÍ;3;2;;;\ncile;102;Dlouhý text, který poběží;3;1;;ano;\nlinky;10;1;3;3;;;\n";
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv" })); a.download = "vzor_importu.csv"; a.click();
};

async function uploadSectionImport(f, section, replace) {
  await syncProject();
  const b64 = await readB64(f);
  const r = await api("/api/gbuse/section/import", { name: f.name, content_b64: b64, section, replace });
  st.project.data = r.data;
  st.dirty = true;
  renderList();
  showConfig();
  showCycles();
  return r;
}

async function downloadSectionExport(ext) {
  const r = await fetch("/api/gbuse/section/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ext, section: $("#secImpSection").value, sort_by: $("#secBbdSort").value }),
  });
  if (!r.ok) throw new Error(await r.text());
  const a = document.createElement("a");
  a.href = URL.createObjectURL(await r.blob());
  a.download = "databaze" + ext;
  a.click();
}

$("#btnSecImport").onclick = guard(async () => {
  const f = $("#secImpFile").files[0];
  if (!f) { toast("Vyberte soubor", true); return; }
  const r = await uploadSectionImport(f, $("#secImpSection").value, $("#secImpReplace").checked);
  $("#secImpOut").textContent = "Načteno: " + JSON.stringify(r.counts) +
    (r.problems?.length ? "\n\nPoznámky:\n" + r.problems.join("\n") : "");
  toast("Soubor sekce načten");
});

$("#btnSecExport").onclick = guard(async () => {
  await syncProject();
  await downloadSectionExport($("#secExpExt").value);
});

$("#btnAddIcons").onclick = guard(async () => {
  await api("/api/fonts/add-icons", {});
  st.dirty = true; await loadFonts();
  if (st.project && !st.project.fonts.includes(14)) st.project.fonts.push(14);
  toast("Ikony vloženy do fontu 14 – uložte projekt");
});
