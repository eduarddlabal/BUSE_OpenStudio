// Display systems (systémy zobrazení): what every kind of display shows. List, editor per display kind
// (single-line interior, two-line interior, exterior panels) and a live preview on a panel.
import { $, $$, api, toast, guard, esc } from "../common/core.js";
import { LedView } from "../common/led.js";

const fm = { list: [], name: null, data: null, saved: null, dirty: false, defaults: {}, active: false,
  pending: null, view: null, timeline: null, timer: null, stops: [], sec: "interior" };

// display kinds = sections of a system file; preview panel and sequences offered for each
const SECTIONS = {
  interior: { title: "Vnitřní jednořádkový", panel: "interior",
    note: "Vnitřní LED panel s jedním řádkem (BS120). Číslo linky vlevo, text vedle něj." },
  interior_2: { title: "Vnitřní dvouřádkový", panel: "interior_2",
    note: "Dvouřádkový vnitřní panel (PID: min. 16×128). Horní řádek: číslo linky + stránka s rolí „nadpis“, dolní řádek: text přes celou šířku." },
  exterior: { title: "Vnější panely", panel: "front",
    note: "Čelní, boční a zadní panel (BS 210). Bez fontu si panel text naformátuje sám." },
};
const SEQS_INT = { next: "Příští zastávka (na trase)", stop: "Název zastávky (v zastávce)", destination: "Směr / cíl (v zastávce, před odjezdem)",
  zone: "Tarifní zóna", time: "Čas", message: "Mimořádnost", final: "Konečná" };
const SEQS_EXT = { exterior: "Cíl – čelní (a ostatní)", exterior_side: "Cíl – boční", exterior_rear: "Cíl – zadní",
  via: "Řádek nácestných zastávek (boční)" };

const PANELS = {
  interior: { title: "vnitřní BS 120.0A 8×135", panel: { type: "BS120", role: "interior" } },
  interior_120: { title: "vnitřní BS120 8×120 (autobus)", panel: { type: "BS120", role: "interior", columns: 120 } },
  interior_2: { title: "vnitřní dvouřádkový 16×128 (odhad)", panel: { type: "BS220", role: "interior" } },
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
  for (const f of fm.list) { const g = f.group || ""; (groups[g] = groups[g] || []).push(f); }
  $("#fmtList").innerHTML = (fm.list.length ? "" : `<p class="muted small">Ve složce systemy/ zatím nic není – založte nový systém.</p>`) +
    Object.keys(groups).sort().map((g) =>
    (g ? `<div class="muted small fmtgroup">${esc(g)}</div>` : "") + groups[g].map((f) =>
      `<div class="rec fmtrec ${f.name === fm.name ? "sel" : ""}" data-n="${esc(f.name)}"><span class="t">${esc(f.title)}</span>
        <span class="k small">${esc(f.name.split("/").pop())}</span></div>`).join("")).join("");
  $("#fmtList").querySelectorAll(".fmtrec").forEach((el) => el.onclick = () => openName(el.dataset.n));
}
const openName = guard(async (name) => {
  if (fm.dirty && name !== fm.name && !confirm("Systém má neuložené změny. Zahodit je?")) return;
  const f = await api("/api/sim/format?name=" + encodeURIComponent(name));
  fm.name = f.name;
  fm.saved = { title: f.title, interior: f.interior, interior_2: f.interior_2 || {}, exterior: f.exterior || {} };
  fm.data = clone(fm.saved); fm.dirty = false;
  renderList(); renderEditor(); preview();
});

// ------------------------------------------------------------------ editor
function setDirty() {
  fm.dirty = true;
  $("#fmtState").textContent = "neuloženo";
  schedulePreview();
}
// settings of the section being edited (two-line without own content = the single-line ones, read only)
const own2 = () => !!(fm.data.interior_2 && Object.keys(fm.data.interior_2).length);
function cur() {
  if (fm.sec === "interior_2") return own2() ? fm.data.interior_2 : fm.data.interior;
  return fm.data[fm.sec] || (fm.data[fm.sec] = {});
}
function renderSections() {
  $("#fmtSection").innerHTML = Object.entries(SECTIONS).map(([k, v]) =>
    `<button data-s="${k}" class="${k === fm.sec ? "cur" : ""}">${esc(v.title)}</button>`).join("");
  $("#fmtSection").querySelectorAll("button").forEach((b) => b.onclick = () => setSection(b.dataset.s));
  $("#fmtSectionNote").textContent = SECTIONS[fm.sec].note;
  const ext = fm.sec === "exterior", two = fm.sec === "interior_2";
  $("#fmtOwnWrap").hidden = !two;
  $("#fmtOwn").checked = two && own2();
  $("#fmtGeneral").hidden = ext || (two && !own2());
  $("#fmtExtGeneral").hidden = !ext;
  $("#fmtSeqWrap").hidden = two && !own2();
  const seqs = ext ? SEQS_EXT : SEQS_INT, keep = $("#seqSelect").value;
  $("#seqSelect").innerHTML = Object.entries(seqs).map(([k, t]) => `<option value="${k}">${esc(t)}</option>`).join("");
  if (seqs[keep]) $("#seqSelect").value = keep;
}
function setSection(sec) {
  fm.sec = sec;
  renderEditor();
  $("#pvPanel").value = SECTIONS[sec].panel;
  preview();
}
$("#fmtOwn").onchange = () => {
  if (!fm.data) return;
  // own two-line content starts as a copy of the single-line one
  fm.data.interior_2 = $("#fmtOwn").checked ? clone(fm.data.interior) : {};
  renderEditor(); setDirty();
};
function renderEditor() {
  const d = fm.data, i = cur(), e = d.exterior || {};
  $("#fmtTitle").value = d.title || "";
  $("#fmtFile").textContent = `systemy/${fm.name}.yaml`;
  $("#fmtState").textContent = fm.dirty ? "neuloženo" : "";
  renderSections();
  $$("#tab-formaty [data-item]").forEach((c) => c.checked = (i.items || ["next", "time", "zone"]).includes(c.dataset.item));
  $("#sSeconds").value = i.seconds ?? 3; $("#sAtStop").value = i.at_stop || "line_dest"; $("#sMsgMode").value = i.message_mode || "cycle";
  $("#sLine").value = i.line || "at_stop"; $("#sRunTime").value = i.run_time || "once";
  $("#sFont").value = i.font ?? 3; $("#sSpacing").value = i.spacing ?? 1;
  $("#sTime").value = i.time_format ?? "%H:%M";
  $("#sExtSeconds").value = e.seconds ?? 4;
  renderSeq();
}
function readGeneral() {
  if (fm.sec === "exterior") { fm.data.exterior = { ...fm.data.exterior, seconds: +$("#sExtSeconds").value }; return; }
  if (fm.sec === "interior_2" && !own2()) return;
  const i = cur();
  Object.assign(i, { items: $$("#tab-formaty [data-item]").filter((c) => c.checked).map((c) => c.dataset.item),
    seconds: +$("#sSeconds").value, at_stop: $("#sAtStop").value, message_mode: $("#sMsgMode").value, line: $("#sLine").value,
    run_time: $("#sRunTime").value, font: +$("#sFont").value, spacing: +$("#sSpacing").value, time_format: $("#sTime").value });
}
$$("#tab-formaty [data-item], #sSeconds, #sAtStop, #sMsgMode, #sLine, #sRunTime, #sFont, #sSpacing, #sTime, #sExtSeconds")
  .forEach((el) => el.addEventListener("change", () => { if (!fm.data) return; readGeneral(); renderSeq(); setDirty(); }));
$("#fmtTitle").onchange = () => { if (!fm.data) return; fm.data.title = $("#fmtTitle").value; setDirty(); };

// ---- sequences (pages)
function seqBase(name) {
  const from = (sec) => (fm.saved && fm.saved[sec] && fm.saved[sec].sequences && fm.saved[sec].sequences[name]);
  return from(fm.sec) || from("interior") || fm.defaults[name] || [];
}
function seqList() {
  const i = cur(), name = $("#seqSelect").value;
  i.sequences = i.sequences || {};
  if (!i.sequences[name]) i.sequences[name] = clone(seqBase(name));
  return i.sequences[name];
}
function renderSeq() {
  if (!fm.data) return;
  const pages = seqList(), i = cur();
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
  const i = cur(); i.sequences = i.sequences || {};
  i.sequences[$("#seqSelect").value] = clone(seqBase($("#seqSelect").value));
  renderSeq(); setDirty();
};
// editing an exterior sequence -> show it on the matching panel
function preferPanelFor(seq) {
  const want = { exterior: "front", exterior_side: "side_bottom", exterior_rear: "rear", via: "side_bottom" }[seq];
  if (want && $("#pvPanel").value !== want) { $("#pvPanel").value = want; preview(); }
}

// ---- save / delete
const payload = (name) => ({ name, title: fm.data.title, interior: fm.data.interior,
  interior_2: own2() ? fm.data.interior_2 : {}, exterior: fm.data.exterior });
$("#fmtSave").onclick = guard(async () => {
  if (!fm.data) return;
  readGeneral();
  await api("/api/sim/format/save", payload(fm.name));
  fm.saved = clone(fm.data); fm.dirty = false; $("#fmtState").textContent = "uloženo";
  toast("Systém zobrazení uložen");
  await loadList(fm.name); window.dispatchEvent(new CustomEvent("buse:formats-changed"));
});
$("#fmtSaveAs").onclick = guard(async () => {
  if (!fm.data) return;
  readGeneral();
  const name = prompt("Název souboru systému (ve složce systemy/, podsložky přes /):", fm.name + "_kopie");
  if (!name) return;
  const title = prompt("Název systému:", fm.data.title + " (kopie)");
  if (title === null) return;
  fm.data.title = title || fm.data.title;
  const r = await api("/api/sim/format/save", payload(name));
  fm.dirty = false; fm.name = null;
  await loadList(r.saved); toast("Systém uložen: " + r.saved);
  window.dispatchEvent(new CustomEvent("buse:formats-changed"));
});
$("#fmtRevert").onclick = () => { if (!fm.saved) return; fm.data = clone(fm.saved); fm.dirty = false; renderEditor(); preview(); };
$("#fmtDel").onclick = guard(async () => {
  if (!fm.name || !confirm(`Smazat systém ${fm.name}? Soubor systemy/${fm.name}.yaml se odstraní.`)) return;
  await api("/api/sim/format/delete", { name: fm.name });
  fm.name = null; fm.data = null; fm.dirty = false; await loadList(); toast("Systém smazán");
  window.dispatchEvent(new CustomEvent("buse:formats-changed"));
});
$("#fmtNew").onclick = guard(async () => {
  const name = prompt("Název souboru nového systému (ve složce systemy/):", "novy_system");
  if (!name) return;
  // starts as a copy of the open system, or of the built-in default when none is open
  const base = fm.data ? clone(fm.data) : { interior: { sequences: clone(fm.defaults) }, interior_2: {}, exterior: { seconds: 4 } };
  const r = await api("/api/sim/format/save", { name, title: name.split("/").pop(), interior: base.interior,
    interior_2: base.interior_2 || {}, exterior: base.exterior || {} });
  fm.dirty = false; fm.name = null; await loadList(r.saved);
  toast(fm.data ? "Nový systém je kopií předchozího" : "Nový systém vychází z výchozího");
  window.dispatchEvent(new CustomEvent("buse:formats-changed"));
});

// ------------------------------------------------------------------ preview
function schedulePreview() { clearTimeout(fm.pvTimer); fm.pvTimer = setTimeout(preview, 350); }
const preview = guard(async () => {
  if (!fm.data || !fm.active) return;
  const sit = SITUATIONS[$("#pvSit").value] || SITUATIONS.driving, pn = PANELS[$("#pvPanel").value] || PANELS.interior;
  let r;
  try {
    r = await api("/api/sim/format/preview", { interior: fm.data.interior, interior_2: own2() ? fm.data.interior_2 : {},
      exterior: fm.data.exterior, panel: pn.panel,
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
