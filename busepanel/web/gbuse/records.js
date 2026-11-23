// Record list & gBUSE1/gBUSE0 text editors
import { $, api, esc, guard, st, toast } from "../common/core.js";
import { drawFrame, preview } from "./preview.js";
import { currentValue, isG0, isSpecialSec, plain, secMeta, syncProject, touch } from "./shared.js";

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

export function fillFontSelects() {
  if (!st.project) return;
  const opts = st.project.fonts.map((f) => `<option value="${f}">${f}</option>`).join("");
  $("#edFont").innerHTML = `<option value="">výchozí</option>` + opts;
  $("#ed0F1").innerHTML = `<option value="">výchozí</option>` + opts;
  $("#ed0F2").innerHTML = `<option value="">výchozí</option>` + opts;
  $("#fontSelect").innerHTML = opts;
}

export function fillPreviewLines() {
  if (!st.project) return;
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

export function showEditor() {
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

const onEdit = () => {
  if (st.key === null) return;
  st.project.data[st.section][st.key] = readEditor();
  touch(); renderList(); preview();
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

let delArmed = 0;
function deleteRecord() {
  if (Date.now() - delArmed > 4000) { delArmed = Date.now(); toast("Klikněte znovu pro smazání " + st.key); return; }
  delete st.project.data[st.section][st.key];
  st.key = null;
  touch(); renderList(); showEditor();
}

async function sendCurrentToPanel() {
  if (isSpecialSec()) { toast("Tuto sekci nelze poslat jako samostatný IBIS telegram", true); return; }
  const [cmd, len] = secMeta()[st.section];
  const tel = [];
  if (st.section !== "lines" && st.previewLine !== "") tel.push("l" + st.previewLine.padStart(3, "0"));
  tel.push(cmd + st.key.padStart(len, "0"));
  await api("/api/panel/send", { telegrams: tel });
  toast("Odesláno: " + tel.join(" "));
}

// DOM listeners
$("#secSelect").onchange = () => { st.section = $("#secSelect").value; st.key = null; renderList(); showEditor(); };
$("#search").oninput = () => renderList();
$("#edPreviewLine").onchange = () => { st.previewLine = $("#edPreviewLine").value; preview(); };
$("#ed0PreviewLine").onchange = () => { st.previewLine = $("#ed0PreviewLine").value; preview(); };
$("#ed0PreviewStop").onchange = () => preview();

["#edText", "#edSpacing", "#edX", "#edInterval", "#edMarkupText"].forEach((s) => $(s).oninput = onEdit);
["#edFont", "#edAlign", "#edInv", "#edBlink"].forEach((s) => $(s).onchange = onEdit);
$("#edRun").onchange = () => {
  if ($("#edRun").checked) $("#edEffect").value = "1";
  else if ($("#edEffect").value === "1") $("#edEffect").value = "";
  onEdit();
};
$("#edEffect").onchange = () => { $("#edRun").checked = $("#edEffect").value === "1"; onEdit(); };

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

$("#btnDelete").onclick = deleteRecord;
$("#btn0Delete").onclick = deleteRecord;
$("#btnShow").onclick = guard(sendCurrentToPanel);
$("#btn0Show").onclick = guard(sendCurrentToPanel);

$("#btnCheck").onclick = guard(async () => {
  await syncProject();
  const r = await api("/api/project/check");
  $("#checkOut").innerHTML = r.items.length
    ? r.items.map((i) => `<div class="${i.level}">${esc(i.section)} ${esc(i.key)}: ${esc(i.msg)}</div>`).join("")
    : `<div style="color:var(--ok)">Projekt je v pořádku.</div>`;
});
