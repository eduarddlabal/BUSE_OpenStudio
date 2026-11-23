// Font, glyph bitmap editor, and codetable UI
import { $, api, esc, guard, st, toast } from "../common/core.js";
import { preview } from "./preview.js";
import { fillFontSelects } from "./records.js";
import { isG0, readB64 } from "./shared.js";

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
function cell(e) {
  const r = gc.getBoundingClientRect(), sx = gc.width / r.width;
  const x = Math.floor((e.clientX - r.left) * sx / CELL), y = Math.floor((e.clientY - r.top) * sx / CELL);
  return st.glyph && y >= 0 && y < st.glyph.rows.length && x >= 0 && x < (st.glyph.rows[0]?.length || 0) ? { x, y } : null;
}
function setCell(p) { st.glyph.rows[p.y][p.x] = painting; drawGlyph(); }

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

function renderCodeTable() {
  $("#codeTable").innerHTML = st.fonts.codetable.filter((c) => c.code >= 0x21).map((c) =>
    `<label>${c.code.toString(16).toUpperCase()}<input data-code="${c.code}" value="${esc(c.char ?? "")}" maxlength="1"></label>`).join("");
  $("#codeTable").querySelectorAll("input").forEach((i) => i.onchange = guard(async () => {
    await api("/api/fonts/codetable", { code: +i.dataset.code, char: i.value });
    st.dirty = true; loadFonts();
  }));
}

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
gc.onmousedown = (e) => { const p = cell(e); if (!p) return; painting = !st.glyph.rows[p.y][p.x]; setCell(p); };
gc.onmousemove = (e) => { if (painting !== null) { const p = cell(e); if (p) setCell(p); } };
window.addEventListener("mouseup", () => painting = null);

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

$("#btnAddIcons").onclick = guard(async () => {
  await api("/api/fonts/add-icons", {});
  st.dirty = true; await loadFonts();
  if (st.project && !st.project.fonts.includes(14)) st.project.fonts.push(14);
  toast("Ikony vloženy do fontu 14 – uložte projekt");
});
