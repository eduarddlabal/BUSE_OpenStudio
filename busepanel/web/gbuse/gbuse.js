// gBUSE module – texts, configuration, fonts, import (replacement of gBUSE1)
import { $, $$, api, toast, guard, esc, log, st } from "../common/core.js";
import { loadPorts } from "../loader/loader.js";

const SECTION_CMD = { lines: ["l", 3], destinations: ["z", 3], destinations4: ["zC", 4], stops: ["x", 4], messages: ["xA", 4] };
const ALL_CMDS = ["l", "z", "x", "xA", "xB", "zC", "zD", "v", "zI", "zM", "zN", "aA", "u", "e", "xC", "a", "aV", "lA"];

// ---- LED canvas
const led = $("#led"), ctx = led.getContext("2d");
export function drawFrame(cols, columns = 135) {
  const s = Math.floor(led.width / columns), r = s * 0.36;
  led.height = s * 8 + 4;
  ctx.fillStyle = "#0b0907"; ctx.fillRect(0, 0, led.width, led.height);
  for (let x = 0; x < columns; x++) for (let y = 0; y < 8; y++) {
    const on = (cols[x] >> (7 - y)) & 1;
    ctx.fillStyle = on ? "#ffb02e" : "#2a2118";
    if (on) { ctx.shadowColor = "#ff9d00"; ctx.shadowBlur = s * 0.6; } else ctx.shadowBlur = 0;
    ctx.beginPath(); ctx.arc(x * s + s / 2 + 2, y * s + s / 2 + 2, r, 0, 7); ctx.fill();
  }
  ctx.shadowBlur = 0;
}
function blit(frame, item, start, lo, hi) {
  item.cols.forEach((v, i) => {
    const x = start + i;
    if (x >= lo && x <= hi) frame[x] = item.inv[i] ? (~v & 0xff) : v;
  });
}
export async function preview() {
  if (!st.project) return;
  const g = st.geometry;
  const value = currentValue();
  const lineVal = st.previewLine !== "" ? (st.project.data.lines || {})[st.previewLine] : null;
  let res;
  try { res = await api("/api/preview", { value, line: lineVal ?? null }); }
  catch (e) { $("#ledInfo").textContent = e.message; return; }
  if (st.anim) cancelAnimationFrame(st.anim), st.anim = null;
  const base = () => {
    const f = new Array(g.columns).fill(0);
    return f;
  };
  const withLine = (f) => {
    for (let x = g.line_left; x <= g.line_right; x++) f[x] = 0;
    if (res.line) {
      const w = g.line_right - g.line_left + 1;
      blit(f, res.line, g.line_left + Math.max(0, Math.floor((w - res.line.width) / 2)), g.line_left, g.line_right);
    }
    return f;
  };
  const d = res.dest;
  let info = "";
  if (d && d.run) {
    const span = g.dest_right - g.dest_left + 1, period = d.width + span;
    let off = 0, last = performance.now();
    const tick = (t) => {
      const step = g.scroll_ms || 20;
      if (t - last >= step) { off = (off + Math.max(1, Math.floor((t - last) / step))) % period; last = t;
        const f = base(); blit(f, d, g.dest_left + span - off, g.dest_left, g.dest_right); drawFrame(withLine(f), g.columns); }
      st.anim = requestAnimationFrame(tick);
    };
    st.anim = requestAnimationFrame(tick);
    info = `běžící text, šířka ${d.width} sloupců`;
  } else {
    const f = base();
    if (d) blit(f, d, d.start, d.lo, d.hi);
    drawFrame(withLine(f), g.columns);
    if (d) info = `šířka ${d.width} sl., začátek ${d.start}, viditelné ${d.lo}–${d.hi}` + (d.overflow ? " — ⚠ nevejde se, panel text ořízne" : "");
  }
  $("#ledInfo").textContent = info;
  $("#edBytes").textContent = d ? d.bytes : "";
  const probs = res.problems || [];
  $("#edProblems").innerHTML = probs.map((p) => `<div>${esc(p)}</div>`).join("") +
    (d && d.overflow ? `<div class="warning">Text se nevejde – zkraťte ho, zmenšete mezeru/font nebo zapněte běžící text.</div>` : "");
}

// ---- files & project
export async function loadFiles(select) {
  const r = await api("/api/files");
  const opts = r.files.map((f) => `<option>${esc(f)}</option>`).join("");
  $("#fileSelect").innerHTML = opts;
  $("#newFontsFrom").innerHTML = opts;
  if (select) $("#fileSelect").value = select;
}
export function setProject(r) {
  st.project = r; st.geometry = r.geometry;
  $("#projectPath").textContent = r.path || "";
  st.dirty = false;
  fillFontSelects();
  fillPreviewLines();
  renderList();
  showConfig();
  st.key = null; showEditor();
}
$("#btnOpen").onclick = guard(async () => {
  const path = $("#fileSelect").value;
  if (!path) return;
  if (st.dirty && !confirmDiscard()) return;
  setProject(await api("/api/project/open", { path }));
  toast("Otevřeno: " + path + (path.endsWith(".hex") ? " (převedeno z HEX)" : ""));
});
let discardArmed = 0;
function confirmDiscard() {   // viewer has no confirm(): second click within 4 s confirms
  if (Date.now() - discardArmed < 4000) return true;
  discardArmed = Date.now(); toast("Neuložené změny – klikněte znovu pro zahození"); return false;
}
$("#btnSave").onclick = guard(async () => {
  if (!st.project) return;
  const r = await api("/api/project/save", { data: st.project.data });
  st.dirty = false; toast("Uloženo: " + r.saved); loadFiles();
});
$("#btnHex").onclick = guard(async () => {
  await syncProject();
  const text = await api("/api/project/hex");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  a.download = (st.project.path || "panel").split(/[\\/]/).pop().replace(/\.ya?ml$/, "") + ".hex";
  a.click();
});
$("#btnNew").onclick = () => $("#dlgNew").showModal();
$("#newOk").onclick = guard(async (e) => {
  e.preventDefault();
  setProject(await api("/api/project/new", { path: $("#newPath").value, fonts_from: $("#newFontsFrom").value, version: $("#newVersion").value }));
  $("#dlgNew").close(); toast("Nový projekt – nezapomeňte uložit");
  st.dirty = true;
});
export async function syncProject() { if (st.project) await api("/api/project/update", { data: st.project.data }); }
function touch() { st.dirty = true; clearTimeout(touch.t); touch.t = setTimeout(guard(syncProject), 400); }

// ---- record list
$("#secSelect").onchange = () => { st.section = $("#secSelect").value; st.key = null; renderList(); showEditor(); };
$("#search").oninput = () => renderList();
function plain(v) {
  if (v && typeof v === "object") return v.text ?? "";
  return String(v ?? "").replace(/\{\{/g, "{").replace(/\{[^{}]*\}/g, "");
}
function badges(v) {
  const b = [];
  if (v && typeof v === "object") {
    if (v.font !== undefined) b.push("f" + v.font);
    if (v.run) b.push("běží");
    if (v.inverse) b.push("inv");
    if (v.align && v.align !== "center") b.push(v.align === "left" ? "vlevo" : "vpravo");
    if (v.x !== undefined && v.x !== null) b.push("x" + v.x);
  } else if (/\{/.test(String(v ?? ""))) b.push("značky");
  return b.map((x) => `<span class="badge">${x}</span>`).join("");
}
export function renderList() {
  const list = $("#recList");
  if (!st.project) { list.innerHTML = ""; return; }
  const recs = st.project.data[st.section] || {};
  const q = $("#search").value.toLowerCase();
  list.innerHTML = Object.entries(recs)
    .filter(([k, v]) => !q || k.includes(q) || plain(v).toLowerCase().includes(q))
    .map(([k, v]) => `<div class="rec${k === st.key ? " sel" : ""}" data-k="${esc(k)}"><span class="k">${esc(k)}</span>` +
      `<span class="t">${esc(plain(v)) || '<span class="muted">(prázdné)</span>'}</span><span>${badges(v)}</span></div>`).join("");
  list.querySelectorAll(".rec").forEach((el) => el.onclick = () => { st.key = el.dataset.k; renderList(); showEditor(); });
}

// ---- editor
function fillFontSelects() {
  const opts = st.project.fonts.map((f) => `<option value="${f}">${f}</option>`).join("");
  $("#edFont").innerHTML = `<option value="">výchozí</option>` + opts;
  $("#fontSelect").innerHTML = opts;
}
function fillPreviewLines() {
  const lines = st.project.data.lines || {};
  $("#edPreviewLine").innerHTML = `<option value="">(bez linky)</option>` +
    Object.entries(lines).map(([k, v]) => `<option value="${esc(k)}">${esc(k)} – ${esc(plain(v))}</option>`).join("");
  $("#edPreviewLine").value = st.previewLine;
}
$("#edPreviewLine").onchange = () => { st.previewLine = $("#edPreviewLine").value; preview(); };

function currentValue() {
  if (!st.project || st.key === null) return null;
  return (st.project.data[st.section] || {})[st.key];
}
function showEditor() {
  const v = currentValue();
  $("#noSel").hidden = v !== undefined && v !== null;
  $("#edForm").hidden = !$("#noSel").hidden;
  if ($("#edForm").hidden) { drawFrame([], st.geometry?.columns || 135); return; }
  $("#edKey").value = st.key;
  const isMarkup = typeof v === "string" && /\{/.test(v);
  $("#edMarkup").checked = isMarkup;
  $("#markupWrap").hidden = !isMarkup;
  $("#edMarkupText").value = typeof v === "string" ? v : "";
  const o = typeof v === "object" ? v : { text: isMarkup ? "" : v };
  $("#edText").value = o.text ?? "";
  $("#edText").disabled = isMarkup;
  $("#edFont").value = o.font ?? "";
  $("#edSpacing").value = o.spacing ?? "";
  $("#edAlign").value = o.align || "center";
  $("#edX").value = o.x ?? "";
  $("#edRun").checked = !!o.run;
  $("#edInv").checked = !!o.inverse;
  preview();
}
function readEditor() {
  if ($("#edMarkup").checked) return $("#edMarkupText").value;
  const o = { text: $("#edText").value };
  if ($("#edFont").value !== "") o.font = +$("#edFont").value;
  if ($("#edSpacing").value !== "") o.spacing = +$("#edSpacing").value;
  if ($("#edRun").checked) o.run = true;
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
["#edText", "#edSpacing", "#edX", "#edMarkupText"].forEach((s) => $(s).oninput = onEdit);
["#edFont", "#edAlign", "#edRun", "#edInv"].forEach((s) => $(s).onchange = onEdit);
$("#edMarkup").onchange = () => {
  const v = currentValue();
  if ($("#edMarkup").checked) {
    $("#edMarkupText").value = typeof v === "string" ? v : toMarkup(v);
    $("#markupWrap").hidden = false; $("#edText").disabled = true;
  } else {
    $("#markupWrap").hidden = true; $("#edText").disabled = false;
    $("#edText").value = plain(v);
  }
  onEdit();
};
function toMarkup(v) {
  if (!v || typeof v !== "object") return String(v ?? "").replace(/\{/g, "{{");
  let s = "";
  if (v.font !== undefined) s += `{f${v.font}}`;
  if (v.spacing !== undefined) s += `{m${v.spacing}}`;
  if (v.run) s += "{run}";
  if (v.inverse) s += "{inv}";
  if (v.x !== undefined && !v.run) s += `{x:${v.x}}`;
  s += (v.text || "").replace(/\{/g, "{{");
  if (v.inverse) s += "{inv}";
  if (v.run) s += "{/run}";
  return s;
}
$("#edKey").onchange = () => {
  const nk = $("#edKey").value.trim(), len = SECTION_CMD[st.section][1];
  if (!/^\d+$/.test(nk) || nk.length > len) { toast(`Číslo musí mít nejvýše ${len} číslic`, true); $("#edKey").value = st.key; return; }
  const k = nk.padStart(len, "0"), recs = st.project.data[st.section];
  if (k !== st.key && recs[k] !== undefined) { toast("Toto číslo už existuje", true); $("#edKey").value = st.key; return; }
  const entries = Object.entries(recs).map(([kk, vv]) => [kk === st.key ? k : kk, vv]).sort((a, b) => a[0].localeCompare(b[0]));
  st.project.data[st.section] = Object.fromEntries(entries);
  st.key = k; touch(); renderList(); showEditor();
};
$("#btnAdd").onclick = () => {
  if (!st.project) return;
  const len = SECTION_CMD[st.section][1];
  const recs = st.project.data[st.section] = st.project.data[st.section] || {};
  let n = 1; while (recs[String(n).padStart(len, "0")] !== undefined) n++;
  const k = String(n).padStart(len, "0");
  recs[k] = "NOVÝ TEXT";
  st.project.data[st.section] = Object.fromEntries(Object.entries(recs).sort((a, b) => a[0].localeCompare(b[0])));
  st.key = k; touch(); renderList(); showEditor();
};
let delArmed = 0;
$("#btnDelete").onclick = () => {
  if (Date.now() - delArmed > 4000) { delArmed = Date.now(); toast("Klikněte znovu pro smazání " + st.key); return; }
  delete st.project.data[st.section][st.key];
  st.key = null; touch(); renderList(); showEditor();
};
$("#btnShow").onclick = guard(async () => {
  const [cmd, len] = SECTION_CMD[st.section];
  const tel = [];
  if (st.section !== "lines" && st.previewLine !== "") tel.push("l" + st.previewLine);
  tel.push(cmd + st.key.padStart(len, "0"));
  await api("/api/panel/send", { telegrams: tel });
  toast("Odesláno: " + tel.join(" ") + " (panel musí mít nahraná aktuální data)");
});
$("#btnCheck").onclick = guard(async () => {
  await syncProject();
  const r = await api("/api/project/check");
  $("#checkOut").innerHTML = r.items.length
    ? r.items.map((i) => `<div class="${i.level}">${esc(i.section)} ${esc(i.key)}: ${esc(i.msg)}</div>`).join("")
    : `<div style="color:var(--ok)">Projekt je v pořádku.</div>`;
});

// ---- config
export function showConfig() {
  if (!st.project) return;
  const d = st.project.data, c = d.config || (d.config = {}), p = d.panel || (d.panel = {});
  const df = c.defaults || (c.defaults = {});
  $("#cfgType").value = p.type || "BS120";
  $("#cfgAddr").value = p.ibis_address ?? 10;
  $("#cfgColumns").value = c.columns ?? 135;
  $("#cfgVersion").value = c.version ?? "";
  $("#cfgFont").value = df.font ?? 3;
  $("#cfgSpacing").value = df.spacing ?? 1;
  $("#cfgInterval").value = df.interval ?? 1;
  $("#cfgCycle").value = c.cycle_delay ?? 10;
  const cmds = new Set(c.commands || []);
  $("#cfgCommands").innerHTML = ALL_CMDS.map((x) => `<label class="chk"><input type="checkbox" value="${x}" ${cmds.has(x) ? "checked" : ""}> ${x}</label>`).join("");
  $("#cfgCommands").querySelectorAll("input").forEach((i) => i.onchange = readConfig);
}
function readConfig() {
  const d = st.project.data, c = d.config, p = d.panel;
  p.type = $("#cfgType").value; p.ibis_address = +$("#cfgAddr").value;
  c.columns = +$("#cfgColumns").value; c.version = $("#cfgVersion").value;
  c.defaults = { font: +$("#cfgFont").value, spacing: +$("#cfgSpacing").value, interval: +$("#cfgInterval").value };
  c.cycle_delay = +$("#cfgCycle").value;
  c.commands = $$("#cfgCommands input:checked").map((i) => i.value);
  touch();
}
["#cfgType", "#cfgAddr", "#cfgColumns", "#cfgVersion", "#cfgFont", "#cfgSpacing", "#cfgInterval", "#cfgCycle"].forEach((s) => $(s).onchange = readConfig);

// ---- fonts
export async function loadFonts() {
  if (!st.project) return;
  st.fonts = await api("/api/fonts");
  if (st.font === null || !st.fonts.fonts.find((f) => f.id === st.font)) st.font = st.fonts.fonts[0]?.id ?? null;
  $("#fontSelect").value = st.font;
  renderGlyphs(); renderCodeTable();
}
$("#fontSelect").onchange = () => { st.font = +$("#fontSelect").value; st.glyph = null; renderGlyphs(); $("#glyphEditor").hidden = true; };
$("#glyphFilter").oninput = () => renderGlyphs();
function glyphThumb(rows, scale = 3) {
  const w = Math.max(1, rows[0]?.length || 1);
  const c = document.createElement("canvas"); c.width = w * scale; c.height = 8 * scale;
  const g = c.getContext("2d"); g.fillStyle = "#2a2118"; g.fillRect(0, 0, c.width, c.height); g.fillStyle = "#ffb02e";
  rows.forEach((r, y) => [...r].forEach((ch, x) => ch === "#" && g.fillRect(x * scale, y * scale, scale, scale)));
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
  if (!g.rows) { toast("Tento znak má neobvyklou výšku, upravte ho v YAML", true); return; }
  st.glyph = { code: g.code, char: g.char, rows: g.rows.map((r) => [...r].map((c) => c === "#")) };
  $("#glyphEditor").hidden = false;
  $("#glyphTitle").textContent = `Font ${st.font} – znak „${g.char ?? "?"}“ (kód 0x${g.code.toString(16).toUpperCase()})`;
  drawGlyph(); renderGlyphs();
}
const gc = $("#glyphCanvas"), gx = gc.getContext("2d"), CELL = 22;
function drawGlyph() {
  const rows = st.glyph.rows, w = rows[0].length;
  gc.width = Math.max(1, w) * CELL + 1; gc.height = 8 * CELL + 1;
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
  return st.glyph && y >= 0 && y < 8 && x >= 0 && x < st.glyph.rows[0].length ? { x, y } : null;
}
function setCell(p) { st.glyph.rows[p.y][p.x] = painting; drawGlyph(); }
const gmod = (fn) => () => { if (st.glyph) { st.glyph.rows = fn(st.glyph.rows); drawGlyph(); } };
$("#gWidthPlus").onclick = gmod((r) => r.map((row) => [...row, false]));
$("#gWidthMinus").onclick = gmod((r) => r[0].length > 0 ? r.map((row) => row.slice(0, -1)) : r);
$("#gClear").onclick = gmod((r) => r.map((row) => row.map(() => false)));
$("#gInvert").onclick = gmod((r) => r.map((row) => row.map((v) => !v)));
$("#gShiftL").onclick = gmod((r) => r.map((row) => [...row.slice(1), false]));
$("#gShiftR").onclick = gmod((r) => r.map((row) => [false, ...row.slice(0, -1)]));
$("#gShiftU").onclick = gmod((r) => [...r.slice(1), r[0].map(() => false)]);
$("#gShiftD").onclick = gmod((r) => [r[0].map(() => false), ...r.slice(0, -1)]);
$("#gSave").onclick = guard(async () => {
  const rows = st.glyph.rows.map((r) => r.map((v) => v ? "#" : ".").join(""));
  await api("/api/fonts/glyph", { font: st.font, code: st.glyph.code, rows });
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
  await api("/api/fonts/glyph", { font: st.font, code: e.code, rows: Array(8).fill("....") });
  await loadFonts();
  editGlyph(st.fonts.fonts.find((f) => f.id === st.font).glyphs.find((g) => g.code === e.code));
});
function renderCodeTable() {
  $("#codeTable").innerHTML = st.fonts.codetable.filter((c) => c.code >= 0x21).map((c) =>
    `<label>${c.code.toString(16).toUpperCase()}<input data-code="${c.code}" value="${esc(c.char ?? "")}" maxlength="1"></label>`).join("");
  $("#codeTable").querySelectorAll("input").forEach((i) => i.onchange = guard(async () => {
    await api("/api/fonts/codetable", { code: +i.dataset.code, char: i.value });
    st.dirty = true; loadFonts();
  }));
}

// ---- import
$("#btnImport").onclick = guard(async () => {
  const f = $("#impFile").files[0];
  if (!f) { toast("Vyberte soubor", true); return; }
  const b64 = await new Promise((res) => { const r = new FileReader(); r.onload = () => res(r.result.split(",")[1]); r.readAsDataURL(f); });
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
  a.href = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv" })); a.download = "vzor_importu.csv"; a.click();
};

$("#btnAddIcons").onclick = guard(async () => {
  await api("/api/fonts/add-icons", {});
  st.dirty = true; await loadFonts();
  if (st.project && !st.project.fonts.includes(14)) st.project.fonts.push(14);
  toast("Ikony vloženy do fontu 14 – uložte projekt");
});
