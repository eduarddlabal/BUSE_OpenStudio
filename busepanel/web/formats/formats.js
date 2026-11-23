// Text formats (skladba textů): list, editor of the general settings and sequences, live preview on a panel
import { $, $$, api, toast, guard, esc } from "../common/core.js";
import { LedView } from "../common/led.js";

const fm = { list: [], name: null, data: null, saved: null, builtin: false, dirty: false, defaults: {}, active: false,
  pending: null, view: null, timeline: null, timer: null, stops: [] };

const PANELS = {
  interior: { title: "vnitřní BS120", panel: { type: "BS120", role: "interior" } },
  front: { title: "čelní BS 210.0C (v1)", panel: { type: "BS210", variant: "v1", role: "front" } },
  side_bottom: { title: "boční BS 210.0B (v2), nácestné dole", panel: { type: "BS210", variant: "v2", role: "side", stop_field: "bottom" } },
  side: { title: "boční BS 210.0B (v2)", panel: { type: "BS210", variant: "v2", role: "side" } },
  rear: { title: "zadní BS 210.0A (v4)", panel: { type: "BS210", variant: "v4", role: "rear" } },
};
const SITUATIONS = {
  driving: { title: "na trase (příští zastávka)", phase: "driving" },
  at_stop: { title: "v zastávce", phase: "at_stop" },
  idle: { title: "před odjezdem", phase: "idle" },
  ended: { title: "konečná", phase: "ended" },
  message: { title: "na trase s mimořádností", phase: "driving", message: true },
};
const WHEN = { always: "vždy", request: "na znamení", not_request: "ne na znamení", via: "je „přes“", zone: "má zónu" };
const ROLE = { text: "text", heading: "nadpis" };
const clone = (x) => JSON.parse(JSON.stringify(x));
const opts = (obj, sel) => Object.entries(obj).map(([k, t]) => `<option value="${k}" ${k === sel ? "selected" : ""}>${esc(typeof t === "string" ? t : t.title)}</option>`).join("");

// ------------------------------------------------------------------ list
async function loadList(select) {
  const r = await api("/api/sim/formats");
  fm.list = r.formats; fm.defaults = r.default_sequences || {};
  renderList();
  const want = select || fm.name || r.vehicle_format || (fm.list[0] && fm.list[0].name);
  if (want && want !== fm.name) await openName(want);
}
function renderList() {
  const groups = {};
  for (const f of fm.list) { const g = f.builtin ? "Vestavěné" : (f.group || "Vlastní"); (groups[g] = groups[g] || []).push(f); }
  const order = (g) => (g === "Vestavěné" ? 0 : g === "Vlastní" ? 1 : 2);
  $("#fmtList").innerHTML = Object.keys(groups).sort((a, b) => order(a) - order(b) || a.localeCompare(b)).map((g) =>
    `<div class="muted small fmtgroup">${esc(g)}</div>` + groups[g].map((f) =>
      `<div class="rec fmtrec ${f.name === fm.name ? "sel" : ""}" data-n="${esc(f.name)}"><span class="t">${esc(f.title)}</span>
        <span class="k small">${esc(f.name.split("/").pop())}</span></div>`).join("")).join("");
  $("#fmtList").querySelectorAll(".fmtrec").forEach((el) => el.onclick = () => openName(el.dataset.n));
}
const openName = guard(async (name) => {
  if (fm.dirty && name !== fm.name && !confirm("Formát má neuložené změny. Zahodit je?")) return;
  const f = await api("/api/sim/format?name=" + encodeURIComponent(name));
  fm.name = f.name; fm.builtin = f.builtin;
  fm.saved = { title: f.title, interior: f.interior, exterior: f.exterior || {} };
  fm.data = clone(fm.saved); fm.dirty = false;
  renderList(); renderEditor(); preview();
});

// ------------------------------------------------------------------ editor
function setDirty() {
  fm.dirty = true;
  $("#fmtState").textContent = "neuloženo";
  schedulePreview();
}
function renderEditor() {
  const d = fm.data, i = d.interior, e = d.exterior;
  $("#fmtTitle").value = d.title || "";
  $("#fmtFile").textContent = fm.builtin ? `vestavěný formát „${fm.name}“` : `formaty/${fm.name}.yaml`;
  $("#fmtSave").disabled = fm.builtin;
  $("#fmtDel").disabled = fm.builtin;
  $("#fmtState").textContent = fm.dirty ? "neuloženo" : "";
  $$("#tab-formaty [data-item]").forEach((c) => c.checked = (i.items || ["next", "time", "zone"]).includes(c.dataset.item));
  $("#sSeconds").value = i.seconds ?? 3; $("#sAtStop").value = i.at_stop || "line_dest"; $("#sMsgMode").value = i.message_mode || "cycle";
  $("#sLine").value = i.line || "at_stop"; $("#sRunTime").value = i.run_time || "once";
  $("#sFont").value = i.font ?? 3; $("#sSpacing").value = i.spacing ?? 1;
  $("#sTime").value = i.time_format ?? "%H:%M";
  $("#sExtSeconds").value = e.seconds ?? 4;
  renderSeq();
}
function readGeneral() {
  const i = fm.data.interior;
  Object.assign(i, { items: $$("#tab-formaty [data-item]").filter((c) => c.checked).map((c) => c.dataset.item),
    seconds: +$("#sSeconds").value, at_stop: $("#sAtStop").value, message_mode: $("#sMsgMode").value, line: $("#sLine").value,
    run_time: $("#sRunTime").value, font: +$("#sFont").value, spacing: +$("#sSpacing").value, time_format: $("#sTime").value });
  fm.data.exterior = { ...fm.data.exterior, seconds: +$("#sExtSeconds").value };
}
$$("#tab-formaty [data-item], #sSeconds, #sAtStop, #sMsgMode, #sLine, #sRunTime, #sFont, #sSpacing, #sTime, #sExtSeconds")
  .forEach((el) => el.addEventListener("change", () => { if (!fm.data) return; readGeneral(); renderSeq(); setDirty(); }));
$("#fmtTitle").onchange = () => { if (!fm.data) return; fm.data.title = $("#fmtTitle").value; setDirty(); };

// ---- sequences (pages)
function seqBase(name) {
  return (fm.saved && fm.saved.interior.sequences && fm.saved.interior.sequences[name]) || fm.defaults[name] || [];
}
function seqList() {
  const i = fm.data.interior, name = $("#seqSelect").value;
  i.sequences = i.sequences || {};
  if (!i.sequences[name]) i.sequences[name] = clone(seqBase(name));
  return i.sequences[name];
}
function renderSeq() {
  if (!fm.data) return;
  const pages = seqList(), i = fm.data.interior;
  const sel = (obj, v) => Object.entries(obj).map(([k, t]) => `<option value="${k}" ${k === (v || Object.keys(obj)[0]) ? "selected" : ""}>${t}</option>`).join("");
  $("#seqPages").innerHTML = pages.map((p, n) => `<tr data-i="${n}">
    <td><input data-f="text" value="${esc(p.text || "")}" class="mono" style="min-width:14em;width:100%"></td>
    <td><input data-f="font" type="number" min="0" max="15" value="${p.font ?? ""}" placeholder="${i.font ?? 3}" style="width:3.5em"></td>
    <td><input data-f="spacing" type="number" min="0" max="15" value="${p.spacing ?? ""}" placeholder="${i.spacing ?? 1}" style="width:3.5em"></td>
    <td><input data-f="seconds" type="number" min="0.5" step="0.5" value="${p.seconds ?? ""}" style="width:4em"></td>
    <td><select data-f="when">${sel(WHEN, p.when)}</select></td>
    <td><select data-f="role">${sel(ROLE, p.role)}</select></td>
    <td><input data-f="run" type="checkbox" ${p.run ? "checked" : ""}></td>
    <td class="nowrap"><button data-a="up">↑</button><button data-a="del" class="danger">×</button></td></tr>`).join("");
  $("#seqPages").querySelectorAll("[data-f]").forEach((el) => el.onchange = () => {
    const p = pages[+el.closest("tr").dataset.i], f = el.dataset.f;
    if (el.type === "checkbox") { if (el.checked) p[f] = true; else delete p[f]; }
    else if (el.type === "number") { if (el.value === "") delete p[f]; else p[f] = +el.value; }
    else if ((f === "when" && el.value === "always") || (f === "role" && el.value === "text")) delete p[f];
    else p[f] = el.value;
    setDirty();
  });
  $("#seqPages").querySelectorAll("[data-a]").forEach((b) => b.onclick = () => {
    const n = +b.closest("tr").dataset.i;
    if (b.dataset.a === "del") pages.splice(n, 1);
    if (b.dataset.a === "up" && n > 0) [pages[n - 1], pages[n]] = [pages[n], pages[n - 1]];
    renderSeq(); setDirty();
  });
}
$("#seqSelect").onchange = () => { renderSeq(); preferPanelFor($("#seqSelect").value); };
$("#seqAdd").onclick = () => { if (!fm.data) return; seqList().push({ text: "" }); renderSeq(); setDirty(); };
$("#seqDefault").onclick = () => {
  if (!fm.data) return;
  fm.data.interior.sequences[$("#seqSelect").value] = clone(seqBase($("#seqSelect").value));
  renderSeq(); setDirty();
};
// editing an exterior sequence -> show it on the matching panel
function preferPanelFor(seq) {
  const want = { exterior: "front", exterior_side: "side_bottom", exterior_rear: "rear", via: "side_bottom" }[seq];
  const cur = $("#pvPanel").value;
  if (want && cur === "interior") { $("#pvPanel").value = want; preview(); }
  if (!want && cur !== "interior") { $("#pvPanel").value = "interior"; preview(); }
}

// ---- save / delete
const payload = (name) => ({ name, title: fm.data.title, interior: fm.data.interior, exterior: fm.data.exterior });
$("#fmtSave").onclick = guard(async () => {
  if (!fm.data || fm.builtin) return;
  readGeneral();
  await api("/api/sim/format/save", payload(fm.name));
  fm.saved = clone(fm.data); fm.dirty = false; $("#fmtState").textContent = "uloženo";
  toast("Formát uložen – platí pro všechna vozidla, která ho používají");
  await loadList(fm.name); window.dispatchEvent(new CustomEvent("buse:formats-changed"));
});
$("#fmtSaveAs").onclick = guard(async () => {
  if (!fm.data) return;
  readGeneral();
  const base = fm.builtin ? fm.name + "_upraveny" : fm.name + "_kopie";
  const name = prompt("Název souboru formátu (ve složce formaty/, podsložky přes /):", base);
  if (!name) return;
  const title = prompt("Název formátu:", fm.data.title + (fm.builtin ? " (upravený)" : " (kopie)"));
  if (title === null) return;
  fm.data.title = title || fm.data.title;
  const r = await api("/api/sim/format/save", payload(name));
  fm.dirty = false; fm.name = null;
  await loadList(r.saved); toast("Formát uložen: " + r.saved);
  window.dispatchEvent(new CustomEvent("buse:formats-changed"));
});
$("#fmtRevert").onclick = () => { if (!fm.saved) return; fm.data = clone(fm.saved); fm.dirty = false; renderEditor(); preview(); };
$("#fmtDel").onclick = guard(async () => {
  if (fm.builtin || !fm.name || !confirm(`Smazat formát ${fm.name}? Soubor formaty/${fm.name}.yaml se odstraní.`)) return;
  await api("/api/sim/format/delete", { name: fm.name });
  fm.name = null; fm.dirty = false; await loadList(); toast("Formát smazán");
  window.dispatchEvent(new CustomEvent("buse:formats-changed"));
});
$("#fmtNew").onclick = guard(async () => {
  const name = prompt("Název souboru nového formátu (ve složce formaty/):", "novy_format");
  if (!name) return;
  const z = await api("/api/sim/format?name=zakladni");
  const r = await api("/api/sim/format/save", { name, title: name.split("/").pop(), interior: z.interior, exterior: z.exterior });
  fm.dirty = false; fm.name = null; await loadList(r.saved);
  toast("Nový formát vychází ze Základního");
  window.dispatchEvent(new CustomEvent("buse:formats-changed"));
});

// ------------------------------------------------------------------ preview
function schedulePreview() { clearTimeout(fm.pvTimer); fm.pvTimer = setTimeout(preview, 350); }
const preview = guard(async () => {
  if (!fm.data || !fm.active) return;
  const sit = SITUATIONS[$("#pvSit").value] || SITUATIONS.driving, pn = PANELS[$("#pvPanel").value] || PANELS.interior;
  let r;
  try {
    r = await api("/api/sim/format/preview", { interior: fm.data.interior, exterior: fm.data.exterior, panel: pn.panel,
      phase: sit.phase, message: !!sit.message, pos: +($("#pvStop").value || 1) });
  } catch (e) {
    $("#pvInfo").textContent = "náhled nejde vytvořit: " + e.message; stopPlay(); return;
  }
  fm.timeline = r; play(0);
});
function stopPlay() { clearTimeout(fm.timer); fm.timer = null; }
function play(i) {
  stopPlay();
  const pages = (fm.timeline && fm.timeline.pages) || [];
  if (!pages.length) { $("#pvPages").innerHTML = ""; return; }
  i %= pages.length;
  const p = pages[i];
  if (!fm.view) fm.view = new LedView($("#pvCanvas"));
  fm.view.show(p.frame, p.frame.geometry);
  $("#pvPages").innerHTML = pages.map((x, n) => `<button data-n="${n}" class="${n === i ? "cur" : ""}" title="${esc(x.frame.mode_title || "")}">${n + 1} · ${x.seconds.toFixed(1)} s</button>`).join("");
  $("#pvPages").querySelectorAll("button").forEach((b) => b.onclick = () => play(+b.dataset.n));
  $("#pvInfo").textContent = `linka ${fm.timeline.line} · ${fm.timeline.stop} · cyklus ${fm.timeline.total.toFixed(1)} s`;
  fm.timer = setTimeout(() => play(i + 1), Math.max(300, p.seconds * 1000));
}
$("#pvPanel").innerHTML = opts(PANELS, "interior");
$("#pvSit").innerHTML = opts(SITUATIONS, "driving");
["#pvPanel", "#pvSit", "#pvStop"].forEach((s) => $(s).onchange = () => preview());

async function loadStops() {
  const r = await api("/api/sim/route");
  const stops = (r.route && r.route.stops) || [];
  const cur = $("#pvStop").value;
  $("#pvStop").innerHTML = stops.map((s, n) => `<option value="${n}">${n + 1}. ${esc(s.name)}</option>`).join("");
  $("#pvStop").value = cur && +cur < stops.length ? cur : String(Math.min(1, Math.max(0, stops.length - 1)));
}

// ------------------------------------------------------------------ module
export function openFormat(name) { fm.pending = name; }
export async function activateFormats(on) {
  fm.active = on;
  if (!on) { stopPlay(); return; }
  await loadStops();
  const want = fm.pending; fm.pending = null;
  await loadList(want);
  if (!want || want === fm.name) preview();
}
