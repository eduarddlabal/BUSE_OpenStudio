// Simulation module – route, vehicle with several panels, manual control (OMSI etc.)
import { $, $$, api, toast, guard, esc } from "../common/core.js";
import { LedView } from "../common/led.js";
import { setProject, loadFiles } from "../gbuse/gbuse.js";

const sim = { route: null, vehicle: null, views: {}, active: false, polling: null, profiles: [] };
const ROLES = { interior: "vnitřní", front: "čelní", side: "boční", rear: "zadní" };
const OUTPUTS = { virtual: "virtuální (náhled)", serial: "COM port", esp: "ESP (WebSocket)" };

// ------------------------------------------------------------------ files
async function loadSimFiles() {
  const f = await api("/api/sim/files");
  const opt = (list) => list.map((x) => `<option>${esc(x)}</option>`).join("");
  $("#routeSelect").innerHTML = opt(f.routes) || "<option value=''>(žádné trasy)</option>";
  $("#vehSelect").innerHTML = opt(f.vehicles) || "<option value=''>(žádná vozidla)</option>";
  $("#dbSelect").innerHTML = opt(f.databases) || "<option value=''>(žádné databáze)</option>";
  const all = await api("/api/files");
  $("#dbBase").innerHTML = `<option value="gbuse">originální font gBUSE</option><option value="builtin">vestavěný font (BUSE Studio)</option>` + opt(all.files.filter((x) => x.endsWith(".hex") || x.endsWith(".yaml")));
  if (f.route_path) $("#routePath").value = f.route_path;
  if (f.vehicle_path) $("#vehPath").value = f.vehicle_path;
  $("#simDb").textContent = f.db_path ? "databáze: " + f.db_path : "⚠ není vybraná databáze panelu (vpravo dole)";
}

// ------------------------------------------------------------------ route editor
function renderRoute() {
  const r = sim.route;
  $("#rLine").value = r.line; $("#rDest").value = r.destination; $("#rVia").value = r.via || "";
  $("#rStops").innerHTML = r.stops.map((s, i) => `<tr data-i="${i}">
    <td class="muted">${i + 1}</td>
    <td><input data-f="name" value="${esc(s.name)}"></td>
    <td><input data-f="zone" value="${esc(s.zone || "")}" style="width:3.5em"></td>
    <td><input data-f="icons" value="${esc((s.icons || []).join(" "))}" style="width:8em"></td>
    <td><input type="checkbox" data-f="request" ${s.request ? "checked" : ""}></td>
    <td class="nowrap"><button data-a="up" title="nahoru">↑</button><button data-a="go" title="skočit sem">⤷</button><button data-a="del" class="danger">×</button></td></tr>`).join("");
  $("#rStops").querySelectorAll("input").forEach((inp) => inp.onchange = () => {
    const i = +inp.closest("tr").dataset.i, f = inp.dataset.f, s = r.stops[i];
    if (f === "request") s.request = inp.checked;
    else if (f === "icons") s.icons = inp.value.split(/[\s,]+/).filter(Boolean);
    else s[f] = inp.value;
    pushRoute();
  });
  $("#rStops").querySelectorAll("button").forEach((b) => b.onclick = guard(async () => {
    const i = +b.closest("tr").dataset.i;
    if (b.dataset.a === "del") r.stops.splice(i, 1);
    if (b.dataset.a === "up" && i > 0) [r.stops[i - 1], r.stops[i]] = [r.stops[i], r.stops[i - 1]];
    if (b.dataset.a === "go") { await ev("goto", { index: i, phase: "at_stop" }); return; }
    renderRoute(); pushRoute();
  }));
  $("#rMsgs").innerHTML = Object.entries(r.messages || {}).map(([k, v]) => `<div class="row" data-k="${esc(k)}">
    <input data-f="key" value="${esc(k)}" style="width:7em"><input data-f="text" value="${esc(v)}" style="flex:1"><button class="danger">×</button></div>`).join("");
  $("#rMsgs").querySelectorAll(".row").forEach((row) => {
    const [ik, it] = row.querySelectorAll("input");
    const upd = () => {
      const entries = Object.entries(r.messages).map(([k, v]) => k === row.dataset.k ? [ik.value, it.value] : [k, v]);
      r.messages = Object.fromEntries(entries); row.dataset.k = ik.value; pushRoute(); renderMsgButtons();
    };
    ik.onchange = upd; it.onchange = upd;
    row.querySelector("button").onclick = () => { delete r.messages[row.dataset.k]; renderRoute(); pushRoute(); };
  });
  renderMsgButtons();
}
function renderMsgButtons() {
  $("#simMsgButtons").innerHTML = Object.keys(sim.route.messages || {}).map((k) => `<button data-msg="${esc(k)}">${esc(k)}</button>`).join("");
  $("#simMsgButtons").querySelectorAll("button").forEach((b) => b.onclick = guard(() => ev("message", { key: b.dataset.msg })));
}
const pushRoute = guard(async () => {
  const r = sim.route;
  r.line = $("#rLine").value; r.destination = $("#rDest").value; r.via = $("#rVia").value;
  await api("/api/sim/route/update", { path: "", route: r });
});
["#rLine", "#rDest", "#rVia"].forEach((s) => $(s).onchange = pushRoute);
$("#rAddStop").onclick = () => { sim.route.stops.push({ name: "Nová zastávka", zone: "", icons: [], request: false }); renderRoute(); pushRoute(); };
$("#rAddMsg").onclick = () => { sim.route.messages = sim.route.messages || {}; let n = 1; while (sim.route.messages["zprava" + n]) n++;
  sim.route.messages["zprava" + n] = "Text mimořádné informace"; renderRoute(); pushRoute(); };
$("#routeLoad").onclick = guard(async () => {
  const p = $("#routeSelect").value; if (!p) return;
  sim.route = (await api("/api/sim/route/load", { path: p })).route; $("#routePath").value = p; renderRoute(); toast("Trasa načtena");
});
$("#routeSave").onclick = guard(async () => {
  await pushRoute();
  await api("/api/sim/route/save", { path: $("#routePath").value, route: sim.route }); toast("Trasa uložena"); loadSimFiles();
});
$("#rCsvImport").onclick = guard(async () => {
  sim.route = (await api("/api/sim/route/import-csv", { csv: $("#rCsv").value, line: $("#rLine").value, destination: $("#rDest").value })).route;
  renderRoute(); toast("Trasa importována – nezapomeňte uložit");
});

// ------------------------------------------------------------------ vehicle editor
function renderVehicle() {
  const v = sim.vehicle;
  const profOpts = (sel) => sim.profiles.map((p) => `<option value="${p.name}" ${p.name === sel ? "selected" : ""}>${esc(p.title)}</option>`).join("");
  const opts = (obj, sel) => Object.entries(obj).map(([k, t]) => `<option value="${k}" ${k === sel ? "selected" : ""}>${t}</option>`).join("");
  $("#vehPanels").innerHTML = (v.panels || []).map((p, i) => `<div class="vpanel" data-i="${i}">
    <div class="row"><label class="chk"><input type="checkbox" data-f="enabled" ${p.enabled === false ? "" : "checked"}></label>
      <input data-f="name" value="${esc(p.name || "")}" style="flex:1"><button class="danger" data-a="del">×</button></div>
    <div class="grid2">
      <label>Role <select data-f="role">${opts(ROLES, p.role)}</select></label>
      <label>Typ panelu <select data-f="profile">${profOpts(p.profile || "BS120")}</select></label>
      <label>Výstup <select data-f="output">${opts(OUTPUTS, p.output || "virtual")}</select></label>
      ${p.output === "serial" ? `<label>Port / adresa <span class="row"><input data-f="port" value="${esc(p.port || "")}" placeholder="auto" style="width:8em"><input data-f="address" type="number" value="${p.address ?? 10}" style="width:4em"></span></label>` : ""}
      ${p.output === "esp" ? `<label>ID zařízení ESP <input data-f="device" value="${esc(p.device || "")}"></label>` : ""}
    </div></div>`).join("");
  $("#vehPanels").querySelectorAll("[data-f]").forEach((el) => el.onchange = () => {
    const p = v.panels[+el.closest(".vpanel").dataset.i], f = el.dataset.f;
    p[f] = el.type === "checkbox" ? el.checked : el.type === "number" ? +el.value : el.value;
    if (f === "output") renderVehicle();
  });
  $("#vehPanels").querySelectorAll("[data-a=del]").forEach((b) => b.onclick = () => { v.panels.splice(+b.closest(".vpanel").dataset.i, 1); renderVehicle(); });
  const s = v.settings || (v.settings = {}), i = s.interior || (s.interior = {}), e = s.exterior || (s.exterior = {});
  $$("[data-item]").forEach((c) => c.checked = (i.items || ["next", "time", "zone"]).includes(c.dataset.item));
  $("#sSeconds").value = i.seconds ?? 4; $("#sAtStop").value = i.at_stop || "line_dest"; $("#sMsgMode").value = i.message_mode || "cycle";
  $("#sFont").value = i.font ?? 3; $("#sSpacing").value = i.spacing ?? 1;
  $("#sTime").value = i.time_format ?? "%H:%M";
  if (!i.sequences) i.sequences = JSON.parse(JSON.stringify(sim.defaultSeqs || {}));
  delete i.next_format; delete i.request_suffix; delete i.zone_format; delete i.final_text; delete e.format;
  renderSeq();
}
function readSettings() {
  const s = sim.vehicle.settings;
  s.interior = { ...s.interior, items: $$("[data-item]").filter((c) => c.checked).map((c) => c.dataset.item),
    seconds: +$("#sSeconds").value, at_stop: $("#sAtStop").value, message_mode: $("#sMsgMode").value,
    font: +$("#sFont").value, spacing: +$("#sSpacing").value, time_format: $("#sTime").value };
  s.exterior = { ...s.exterior, font: +$("#sFont").value, spacing: +$("#sSpacing").value };
}
$("#vehAdd").onclick = () => { sim.vehicle.panels.push({ name: "Nový panel", role: "interior", profile: "BS120", output: "virtual" }); renderVehicle(); };
const applyVehicle = guard(async (path) => {
  readSettings();
  sim.vehicle = (await api("/api/sim/vehicle/save", { path: path || null, config: sim.vehicle })).config;
  sim.views = {}; $("#simPanels").innerHTML = "";
  renderSeq();                       // placeholders show the current default font / spacing
  toast(path ? "Vozidlo uloženo" : "Nastavení použito");
});
$("#vehApply").onclick = () => applyVehicle();
$$("#tab-sim [data-item], #sSeconds, #sAtStop, #sMsgMode, #sFont, #sSpacing, #sTime")
  .forEach((el) => el.addEventListener("change", () => applyVehicle()));
$("#vehSave").onclick = guard(async () => { await applyVehicle($("#vehPath").value); loadSimFiles(); });
$("#vehLoad").onclick = guard(async () => {
  const p = $("#vehSelect").value; if (!p) return;
  sim.vehicle = (await api("/api/sim/vehicle/load", { path: p })).config; $("#vehPath").value = p;
  sim.views = {}; $("#simPanels").innerHTML = ""; renderVehicle();
});

// ------------------------------------------------------------------ sequences (pages)
const WHEN = { always: "vždy", request: "na znamení", not_request: "ne na znamení", via: "je „přes“", zone: "má zónu" };
const ROLE = { text: "text", heading: "nadpis" };
function seqList() {
  const i = sim.vehicle.settings.interior, name = $("#seqSelect").value;
  i.sequences = i.sequences || {};
  if (!i.sequences[name]) i.sequences[name] = JSON.parse(JSON.stringify((sim.defaultSeqs || {})[name] || []));
  return i.sequences[name];
}
function renderSeq() {
  if (!sim.vehicle) return;
  const pages = seqList();
  const opts = (obj, sel) => Object.entries(obj).map(([k, t]) => `<option value="${k}" ${k === (sel || Object.keys(obj)[0]) ? "selected" : ""}>${t}</option>`).join("");
  $("#seqPages").innerHTML = pages.map((p, i) => `<tr data-i="${i}">
    <td><input data-f="text" value="${esc(p.text || "")}" class="mono" style="min-width:14em;width:100%"></td>
    <td><input data-f="font" type="number" min="0" max="15" value="${p.font ?? ""}" placeholder="${sim.vehicle.settings.interior.font ?? 3}" style="width:3.5em"></td>
    <td><input data-f="spacing" type="number" min="0" max="15" value="${p.spacing ?? ""}" placeholder="${sim.vehicle.settings.interior.spacing ?? 1}" style="width:3.5em"></td>
    <td><input data-f="seconds" type="number" min="0.5" step="0.5" value="${p.seconds ?? ""}" style="width:4em"></td>
    <td><select data-f="when">${opts(WHEN, p.when)}</select></td>
    <td><select data-f="role">${opts(ROLE, p.role)}</select></td>
    <td class="nowrap"><button data-a="up">↑</button><button data-a="del" class="danger">×</button></td></tr>`).join("");
  $("#seqPages").querySelectorAll("[data-f]").forEach((el) => el.onchange = () => {
    const p = pages[+el.closest("tr").dataset.i], f = el.dataset.f;
    if (el.type === "number") { if (el.value === "") delete p[f]; else p[f] = +el.value; }
    else if ((f === "when" && el.value === "always") || (f === "role" && el.value === "text")) delete p[f];
    else p[f] = el.value;
    applyVehicle();
  });
  $("#seqPages").querySelectorAll("[data-a]").forEach((b) => b.onclick = () => {
    const i = +b.closest("tr").dataset.i;
    if (b.dataset.a === "del") pages.splice(i, 1);
    if (b.dataset.a === "up" && i > 0) [pages[i - 1], pages[i]] = [pages[i], pages[i - 1]];
    renderSeq(); applyVehicle();
  });
}
$("#seqSelect").onchange = renderSeq;
$("#seqAdd").onclick = () => { seqList().push({ text: "" }); renderSeq(); };
$("#seqDefault").onclick = () => {
  sim.vehicle.settings.interior.sequences[$("#seqSelect").value] = JSON.parse(JSON.stringify((sim.defaultSeqs || {})[$("#seqSelect").value] || []));
  renderSeq(); applyVehicle();
};

// ------------------------------------------------------------------ database
$("#dbBuild").onclick = guard(async () => {
  const r = await api("/api/sim/db/build", { base: $("#dbBase").value, out: $("#dbOut").value });
  toast("Univerzální databáze sestavena: " + r.path); loadSimFiles();
});
$("#dbOpen").onclick = guard(async () => {
  const p = $("#dbSelect").value; if (!p) return;
  await api("/api/sim/db/open", { path: p }); toast("Databáze: " + p); loadSimFiles();
});
$("#dbToLoader").onclick = guard(async () => {
  const f = await api("/api/sim/files");
  if (!f.db_path) { toast("Nejdřív sestavte nebo vyberte databázi", true); return; }
  setProject(await api("/api/project/open", { path: f.db_path }));
  await loadFiles(f.db_path);
  document.querySelector('[data-module="loader"]').click();
  toast("Databáze otevřena – klikněte „Nahrát projekt do panelu“");
});

// ------------------------------------------------------------------ control
async function ev(type, extra = {}) { render(await api("/api/sim/event", { type, ...extra })); }
$$("#tab-sim [data-ev]").forEach((b) => b.onclick = guard(() => ev(b.dataset.ev)));
$("#simMsgSend").onclick = guard(() => ev("message", { text: $("#simMsgText").value }));
$("#simStart").onclick = guard(async () => render(await api("/api/sim/start", {})));
$("#simStop").onclick = guard(async () => render(await api("/api/sim/stop", {})));
document.addEventListener("keydown", (e) => {
  if (!sim.active || ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
  const map = { " ": "next", ArrowRight: "next", ArrowLeft: "prev", a: "arrive", d: "depart", Escape: "clear_message" };
  const k = map[e.key];
  if (k) { e.preventDefault(); guard(() => ev(k))(); }
  if (e.key === "m") { const first = Object.keys(sim.route.messages || {})[0]; if (first) guard(() => ev("message", { key: first }))(); }
});

const PHASES = { idle: "nezahájeno", at_stop: "v zastávce", driving: "na trase", ended: "konečná" };
function render(s) {
  const e = s.engine;
  $("#simStatus").innerHTML = e ? `<b>${esc(PHASES[e.phase] || e.phase)}</b> · ${e.phase === "driving" ? "příští: " : "zastávka: "}
    <b>${esc(e.stop || "–")}</b> · linka ${esc(e.line)} → ${esc(e.destination)}
    ${e.message ? ` · <span class="warn">mimořádnost: ${esc(e.message)}</span>` : ""}
    · odesílání na panely: <b>${s.running ? "běží" : "zastaveno"}</b>`
    : `<span class="warn"><b>Není vybraná databáze panelu.</b> Vpravo dole v kartě „Univerzální databáze panelu“ klikněte
       na „Sestavit“ (nebo vyberte existující a „Použít existující“). Pak se zobrazí panely vozidla.</span>`;
  for (const p of s.panels || []) {
    let box = sim.views[p.name];
    if (!box) {
      const el = document.createElement("div"); el.className = "simpanel";
      el.innerHTML = `<div class="row"><b>${esc(p.name)}</b><span class="muted small"></span></div><canvas width="1080" height="70"></canvas>`;
      $("#simPanels").append(el);
      box = sim.views[p.name] = { el, view: new LedView(el.querySelector("canvas")) };
    }
    const info = [ROLES[p.role] || p.role, p.profile, OUTPUTS[p.kind === "esp" ? "esp" : p.kind] || p.kind];
    if (p.frame) info.push("režim: " + p.frame.mode_title);
    if (p.frame && p.frame.mirror) info.push("náhled podle odeslaných povelů");
    if (!p.frame && p.kind !== "virtual") info.push("zatím nic neodesláno – spusťte ▶ Start");
    if (p.kind === "esp") info.push(p.connected ? "připojeno" : "NEPŘIPOJENO");
    if (p.error) info.push("⚠ " + p.error);
    box.el.querySelector(".muted").textContent = info.join(" · ");
    const cv = box.el.querySelector("canvas");
    cv.hidden = !p.frame;
    if (p.frame) box.view.show(p.frame, p.frame.geometry);
  }
  $("#simLog").textContent = (s.log || []).join("\n");
}

async function poll() {
  if (!sim.active) return;
  try { render(await api("/api/sim/state")); } catch (e) { /* server busy */ }
  sim.polling = setTimeout(poll, 400);
}

export async function activateSim(on) {
  sim.active = on;
  clearTimeout(sim.polling);
  if (!on) return;
  if (!sim.route) {
    sim.profiles = (await api("/api/profiles")).profiles;
    sim.route = (await api("/api/sim/route")).route;
    sim.vehicle = (await api("/api/sim/vehicle")).config;
    sim.defaultSeqs = (await api("/api/sim/defaults")).sequences;
    const ic = await api("/api/icons");
    $("#iconNames").textContent = ic.icons.map((i) => i.name).join(", ");
    renderRoute(); renderVehicle();
  }
  await loadSimFiles();
  poll();
}
