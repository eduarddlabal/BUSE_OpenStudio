// Simulation module – route, vehicle with several panels, manual control (OMSI etc.)
import { $, $$, api, toast, guard, esc } from "../common/core.js";
import { LedView } from "../common/led.js";
import { setProject, loadFiles } from "../gbuse/gbuse.js";

const sim = { route: null, line: null, sel: {}, data: null, dir: null, vehicle: null, views: {}, active: false, polling: null, profiles: [], ptypes: {}, variants: {} };
const ROLES = { interior: "vnitřní", front: "čelní", side: "boční", rear: "zadní" };
const OUTPUTS = { virtual: "virtuální náhled", serial: "fyzický – COM port, adresa", esp: "na síti (ESP)", lcd: "LCD panel – webová stránka (doplněk)" };

// ------------------------------------------------------------------ files
// files in subfolders of vozidla/ are grouped by the folder name (any folders, nothing predefined)
function vehOptions(list) {
  const groups = {};
  for (const p of list) { const parts = p.split("/"); const g = parts.length > 2 ? parts.slice(1, -1).join("/") : ""; (groups[g] = groups[g] || []).push(p); }
  return Object.keys(groups).sort().map((g) => {
    const o = groups[g].map((p) => `<option value="${esc(p)}">${esc(p.split("/").pop())}</option>`).join("");
    return g ? `<optgroup label="${esc(g)}">${o}</optgroup>` : o;
  }).join("");
}
async function loadSimFiles() {
  const f = await api("/api/sim/files");
  const opt = (list) => list.map((x) => `<option>${esc(x)}</option>`).join("");
  $("#routeSelect").innerHTML = opt(f.routes) || "<option value=''>(žádné trasy)</option>";
  $("#vehSelect").innerHTML = vehOptions(f.vehicles) || "<option value=''>(žádná vozidla)</option>";
  $("#dbSelect").innerHTML = opt(f.databases) || "<option value=''>(žádné databáze)</option>";
  const all = await api("/api/files");
  $("#dbBase").innerHTML = `<option value="gbuse">originální font gBUSE</option><option value="builtin">vestavěný font (BUSE Studio)</option>` + opt(all.files.filter((x) => x.endsWith(".hex") || x.endsWith(".yaml")));
  if (f.route_path) $("#routePath").value = f.route_path;
  if (f.vehicle_path) $("#vehPath").value = f.vehicle_path;
  $("#simDb").textContent = f.db_path ? "databáze: " + f.db_path : "⚠ není vybraná databáze panelu (vpravo dole)";
}

// ------------------------------------------------------------------ line: directions, courses, trips
// sim.data = the whole line as edited (line, messages, directions {id: {destination, dest_code, stops}},
// courses {id: [{direction, departure, from, to, destination}]}); sim.line = server summary;
// sim.route = the simulated trip; sim.sel = its selection; sim.dir = the direction shown in the editor.
function setRouteInfo(info) {
  sim.route = info.route; sim.line = info.line; sim.sel = info.selection || {}; sim.data = info.data;
  const dirs = sim.data.directions || {};
  if (!(sim.dir in dirs)) sim.dir = simDirection() || Object.keys(dirs)[0];
  renderLine();
}
function simDirection() {
  const s = sim.sel || {};
  if (s.course) { const t = ((sim.data.courses || {})[s.course] || [])[s.index || 0]; return t ? String(t.direction) : null; }
  return s.direction || (sim.route && sim.route.direction) || Object.keys(sim.data.directions || {})[0];
}
function renderLine() {
  $("#rLine").value = sim.data.line || "";
  renderTrips(); renderDirs(); renderCourses(); renderMsgs();
}
async function applyInfo(info, { courses = false } = {}) {
  sim.route = info.route; sim.line = info.line; sim.sel = info.selection || {};
  renderTrips(); renderMsgButtons(); markSimStops();
  if (courses) renderCourses(); else markSimCourse();
  if (info.state) render(info.state);
}
const pushLine = (opts) => guard(async () => {
  sim.data.line = $("#rLine").value;
  await applyInfo(await api("/api/sim/line/update", { line: sim.data, selection: sim.sel }), opts);
})();
$("#rLine").onchange = () => pushLine();

const selKey = (sel) => sel && sel.course ? `c:${sel.course}:${sel.index || 0}` : sel && sel.direction ? `d:${sel.direction}` : "";
function renderTrips() {
  const l = sim.line;
  const cur = selKey(sim.sel);
  let html = "";
  for (const [c, rows] of Object.entries(l.courses || {}))
    html += `<optgroup label="Kurz ${esc(c)}">` + rows.map((t) => `<option value="c:${esc(c)}:${t.index}" ${cur === `c:${c}:${t.index}` ? "selected" : ""}>
      ${t.index + 1}. ${esc(t.departure || "--:--")} · směr ${esc(t.direction)} · ${esc(t.from)} → ${esc(t.destination)} (${t.stops} zast.${t.full ? "" : ", zkrácený"})</option>`).join("") + "</optgroup>";
  html += `<optgroup label="Celý směr">` + (l.directions || []).map((d) => `<option value="d:${esc(d.id)}" ${cur === `d:${d.id}` || (!cur && l.flat) ? "selected" : ""}>
      směr ${esc(d.id)} · ${esc(d.first)} → ${esc(d.destination)} (${d.stops} zast.)</option>`).join("") + "</optgroup>";
  $("#rTrip").innerHTML = html;
  const t = sim.sel.course ? (l.courses[sim.sel.course] || [])[sim.sel.index || 0] : null;
  $("#rTripNote").hidden = !(t && !t.full);
  $("#rTripNote").textContent = t ? `Zkrácený spoj ${t.from} → ${t.to}: jede po zastávkách směru ${t.direction} (v editoru směru podbarvené).` : "";
}
const selectTrip = guard(async (sel) => {
  const r = await api("/api/sim/trip", sel);
  sim.dir = null; setRouteInfo(r); render(r.state);
});
$("#rTrip").onchange = () => {
  const [k, a, b] = $("#rTrip").value.split(":");
  selectTrip(k === "c" ? { course: a, index: +b } : { direction: a });
};

// ---- directions
const dirOf = () => sim.data.directions[sim.dir];
function freeId(obj, start = 1) { let n = start; while (String(n) in obj) n++; return String(n); }
function renameKey(obj, from, to) { return Object.fromEntries(Object.entries(obj).map(([k, v]) => [k === from ? to : k, v])); }
function eachTrip(fn) { for (const trips of Object.values(sim.data.courses || {})) trips.forEach(fn); }

function renderDirs() {
  const dirs = sim.data.directions || {};
  $("#rDirTabs").innerHTML = Object.entries(dirs).map(([id, d]) =>
    `<button data-d="${esc(id)}" class="${id === sim.dir ? "active" : ""}">${esc(id)}: ${esc(d.destination || "?")}</button>`).join("") +
    `<button data-d="" title="nový směr">+ Směr</button>`;
  $("#rDirTabs").querySelectorAll("button").forEach((b) => b.onclick = () => {
    if (b.dataset.d) { sim.dir = b.dataset.d; renderDirs(); return; }
    const id = freeId(dirs);
    dirs[id] = { destination: "", stops: [{ name: "Nová zastávka" }] };
    sim.dir = id; renderDirs(); pushLine({ courses: true });
  });
  const d = dirOf();
  $("#rDirEdit").hidden = !d;
  if (!d) return;
  $("#rDirId").value = sim.dir; $("#rDest").value = d.destination || ""; $("#rDestCode").value = d.dest_code || "";
  $("#rDirDel").disabled = Object.keys(dirs).length < 2;
  renderStops();
}
$("#rDirId").onchange = () => {
  const old = sim.dir, id = $("#rDirId").value.trim();
  if (!id || id === old) { $("#rDirId").value = old; return; }
  if (id in sim.data.directions) { toast("Směr " + id + " už existuje"); $("#rDirId").value = old; return; }
  sim.data.directions = renameKey(sim.data.directions, old, id);
  eachTrip((t) => { if (String(t.direction) === old) t.direction = id; });
  if (sim.sel.direction === old) sim.sel.direction = id;
  sim.dir = id; renderDirs(); pushLine({ courses: true });
};
$("#rDest").onchange = () => { dirOf().destination = $("#rDest").value; renderDirs(); pushLine({ courses: true }); };
$("#rDestCode").onchange = () => { const d = dirOf(); if ($("#rDestCode").value) d.dest_code = $("#rDestCode").value; else delete d.dest_code; pushLine(); };
$("#rDirDel").onclick = () => {
  const id = sim.dir, used = [];
  eachTrip((t) => { if (String(t.direction) === id) used.push(t); });
  if (!confirm(`Smazat směr ${id}?` + (used.length ? ` Smažou se i jeho spoje v kurzech (${used.length}).` : ""))) return;
  delete sim.data.directions[id];
  for (const c of Object.keys(sim.data.courses || {})) sim.data.courses[c] = sim.data.courses[c].filter((t) => String(t.direction) !== id);
  if (simDirection() === id || sim.sel.direction === id) sim.sel = {};
  sim.dir = Object.keys(sim.data.directions)[0];
  renderDirs(); renderCourses(); pushLine({ courses: true });
};
$("#rDirReverse").onclick = () => {
  const d = dirOf(), id = freeId(sim.data.directions);
  const stops = d.stops.map((s) => ({ ...s })).reverse();
  const mins = stops.map((s) => s.minutes).filter((m) => m != null);
  const max = mins.length ? Math.max(...mins) : null;
  stops.forEach((s) => { if (s.minutes != null) s.minutes = max - s.minutes; });
  sim.data.directions[id] = { destination: stops.length ? stops[stops.length - 1].name : "", stops };
  sim.dir = id; renderDirs(); pushLine({ courses: true });
  toast(`Směr ${id} vytvořen – zkontrolujte cíl a zastávky „Přes“`);
};

// the part of the edited direction the simulated trip runs on (row offset or -1)
function simSpan() {
  if (simDirection() !== sim.dir || !sim.route || !sim.route.stops.length) return null;
  const names = dirOf().stops.map((s) => s.name), first = sim.route.stops[0].name;
  const a = names.indexOf(first);
  return a < 0 ? null : [a, a + sim.route.stops.length - 1];
}
function markSimStops() {
  const sp = simSpan();
  $("#rStops").querySelectorAll("tr").forEach((tr) => {
    const i = +tr.dataset.i, on = sp && i >= sp[0] && i <= sp[1];
    tr.classList.toggle("sim", !!on);
    const go = tr.querySelector('[data-a="go"]'); if (go) go.disabled = !on;
  });
}
function renderStops() {
  const r = dirOf();
  $("#rStops").innerHTML = r.stops.map((s, i) => `<tr data-i="${i}">
    <td class="muted">${i + 1}</td>
    <td><input data-f="name" value="${esc(s.name)}"></td>
    <td><input data-f="zone" value="${esc(s.zone || "")}" style="width:3.5em"></td>
    <td><input data-f="icons" value="${esc((s.icons || []).join(" "))}" style="width:8em"></td>
    <td><input type="checkbox" data-f="request" ${s.request ? "checked" : ""}></td>
    <td><input data-f="minutes" type="number" min="0" step="1" value="${s.minutes ?? ""}" style="width:4em"></td>
    <td><input type="checkbox" data-f="via" ${s.via ? "checked" : ""}></td>
    <td><input data-f="code" value="${esc(s.code || "")}" style="width:4em"></td>
    <td class="nowrap"><button data-a="up" title="nahoru">↑</button><button data-a="go" title="simulace: skočit sem">⤷</button><button data-a="del" class="danger">×</button></td></tr>`).join("");
  $("#rStops").querySelectorAll("input").forEach((inp) => inp.onchange = () => {
    const i = +inp.closest("tr").dataset.i, f = inp.dataset.f, s = r.stops[i];
    let courses = false;
    if (f === "request" || f === "via") { if (inp.checked) s[f] = true; else delete s[f]; }
    else if (f === "code") { if (inp.value === "") delete s.code; else s.code = inp.value; }
    else if (f === "icons") s.icons = inp.value.split(/[\s,]+/).filter(Boolean);
    else if (f === "minutes") { if (inp.value === "") delete s.minutes; else s.minutes = +inp.value; }
    else if (f === "name") {
      const old = s.name; s.name = inp.value; courses = true;
      eachTrip((t) => { if (String(t.direction) !== sim.dir) return; for (const k of ["from", "to"]) if (t[k] === old) t[k] = s.name; });
    } else s[f] = inp.value;
    pushLine({ courses });
  });
  $("#rStops").querySelectorAll("button").forEach((b) => b.onclick = guard(async () => {
    const i = +b.closest("tr").dataset.i;
    if (b.dataset.a === "go") { const sp = simSpan(); if (sp) await ev("goto", { index: i - sp[0], phase: "at_stop" }); return; }
    if (b.dataset.a === "del") {
      const name = r.stops[i].name;
      r.stops.splice(i, 1);
      eachTrip((t) => { if (String(t.direction) !== sim.dir) return; for (const k of ["from", "to"]) if (t[k] === name) delete t[k]; });
    }
    if (b.dataset.a === "up" && i > 0) [r.stops[i - 1], r.stops[i]] = [r.stops[i], r.stops[i - 1]];
    renderStops(); pushLine({ courses: true });
  }));
  markSimStops();
}
$("#rAddStop").onclick = () => { dirOf().stops.push({ name: "Nová zastávka" }); renderStops(); pushLine({ courses: true }); };

// ---- courses
function stopIndex(stops, ref) {
  if (ref === undefined || ref === null || ref === "") return "";
  if (typeof ref === "number" || /^\d+$/.test(String(ref))) return +ref - 1;
  const i = stops.findIndex((s) => s.name === String(ref));
  return i < 0 ? "" : i;
}
function stopRef(stops, i) {
  if (i === "") return undefined;
  const name = stops[+i].name;
  return stops.filter((s) => s.name === name).length > 1 ? +i + 1 : name;
}
function markSimCourse() {
  $("#rCourses").querySelectorAll(".course").forEach((el) => el.classList.toggle("sim", el.dataset.c === sim.sel.course));
  $("#rCourses").querySelectorAll("tr[data-i]").forEach((tr) => tr.classList.toggle("sim",
    tr.closest(".course").dataset.c === sim.sel.course && +tr.dataset.i === (sim.sel.index || 0)));
}
function renderCourses() {
  const courses = sim.data.courses || {}, dirs = sim.data.directions || {}, sum = sim.line.courses || {};
  $("#rCourses").innerHTML = Object.entries(courses).map(([c, trips]) => `<div class="course" data-c="${esc(c)}">
    <div class="row"><label>Kurz <input data-cf="id" value="${esc(c)}" style="width:5em"></label>
      <span style="flex:1"></span><button data-ca="add">+ Spoj</button><button data-ca="del" class="danger">Smazat kurz</button></div>
    <div class="stopwrap"><table class="stops"><thead><tr><th>#</th><th>Směr</th><th>Odjezd</th><th>Od</th><th>Do</th>
      <th title="vlastní cíl zkráceného spoje (prázdné = automaticky)">Cíl</th><th></th></tr></thead><tbody>
    ${trips.map((t, i) => {
      const d = dirs[t.direction] || { stops: [] }, st = d.stops, s = (sum[c] || [])[i] || {};
      const opts = (ref, empty) => `<option value="">${empty}</option>` + st.map((x, k) =>
        `<option value="${k}" ${stopIndex(st, ref) === k ? "selected" : ""}>${k + 1}. ${esc(x.name)}</option>`).join("");
      return `<tr data-i="${i}"><td class="muted">${i + 1}</td>
        <td><select data-f="direction">${Object.entries(dirs).map(([id, x]) => `<option value="${esc(id)}" title="${esc(x.destination || "")}" ${id === String(t.direction) ? "selected" : ""}>${esc(id)}</option>`).join("")}</select></td>
        <td><input data-f="departure" value="${esc(t.departure || "")}" placeholder="hh:mm" style="width:4.5em"></td>
        <td><select data-f="from">${opts(t.from, "(začátek)")}</select></td>
        <td><select data-f="to">${opts(t.to, "(konec)")}</select></td>
        <td><input data-f="destination" value="${esc(t.destination || "")}" placeholder="${esc(s.destination || "")}" title="${esc(s.destination || "")}" style="width:7em"></td>
        <td class="nowrap"><button data-a="run" title="simulovat tento spoj">▶</button><button data-a="up" title="nahoru">↑</button><button data-a="del" class="danger">×</button></td></tr>`;
    }).join("")}</tbody></table></div></div>`).join("") || `<p class="muted small">Linka nemá kurzy – simuluje se celý směr.</p>`;
  $("#rCourses").querySelectorAll(".course").forEach((el) => {
    const c = el.dataset.c, trips = courses[c];
    el.querySelector('[data-cf="id"]').onchange = (e) => {
      const id = e.target.value.trim();
      if (!id || id in courses) { e.target.value = c; if (id && id !== c) toast("Kurz " + id + " už existuje"); return; }
      sim.data.courses = renameKey(courses, c, id);
      if (sim.sel.course === c) sim.sel.course = id;
      renderCourses(); pushLine({ courses: true });
    };
    el.querySelector('[data-ca="add"]').onclick = () => {
      const ids = Object.keys(dirs), last = trips[trips.length - 1];
      const dir = last && ids.length === 2 ? ids.find((x) => x !== String(last.direction)) : ids[0];
      trips.push({ direction: dir }); renderCourses(); pushLine({ courses: true });
    };
    el.querySelector('[data-ca="del"]').onclick = () => {
      if (!confirm(`Smazat kurz ${c}?`)) return;
      delete courses[c];
      if (sim.sel.course === c) sim.sel = {};
      renderCourses(); pushLine({ courses: true });
    };
    el.querySelectorAll("tr[data-i]").forEach((tr) => {
      const i = +tr.dataset.i, t = trips[i];
      tr.querySelectorAll("[data-f]").forEach((inp) => inp.onchange = () => {
        const f = inp.dataset.f, st = (dirs[t.direction] || { stops: [] }).stops;
        if (f === "direction") { t.direction = inp.value; delete t.from; delete t.to; }
        else if (f === "from" || f === "to") { const v = stopRef(st, inp.value); if (v === undefined) delete t[f]; else t[f] = v; }
        else if (inp.value === "") delete t[f]; else t[f] = inp.value;
        if (f !== "departure" && f !== "destination") renderCourses();
        pushLine({ courses: f !== "departure" });
      });
      tr.querySelectorAll("button").forEach((b) => b.onclick = () => {
        if (b.dataset.a === "run") { selectTrip({ course: c, index: i }); return; }
        if (b.dataset.a === "del") trips.splice(i, 1);
        if (b.dataset.a === "up" && i > 0) [trips[i - 1], trips[i]] = [trips[i], trips[i - 1]];
        renderCourses(); pushLine({ courses: true });
      });
    });
  });
  markSimCourse();
}
$("#rAddCourse").onclick = () => {
  sim.data.courses = sim.data.courses || {};
  const id = freeId(sim.data.courses);
  sim.data.courses[id] = [{ direction: sim.dir || Object.keys(sim.data.directions)[0] }];
  renderCourses(); pushLine({ courses: true });
};

// ---- messages (whole line)
function renderMsgs() {
  const m = sim.data.messages = sim.data.messages || {};
  $("#rMsgs").innerHTML = Object.entries(m).map(([k, v]) => `<div class="row" data-k="${esc(k)}">
    <input data-f="key" value="${esc(k)}" style="width:7em"><input data-f="text" value="${esc(v)}" style="flex:1"><button class="danger">×</button></div>`).join("");
  $("#rMsgs").querySelectorAll(".row").forEach((row) => {
    const [ik, it] = row.querySelectorAll("input");
    const upd = () => {
      sim.data.messages = Object.fromEntries(Object.entries(sim.data.messages).map(([k, v]) => k === row.dataset.k ? [ik.value, it.value] : [k, v]));
      row.dataset.k = ik.value; pushLine();
    };
    ik.onchange = upd; it.onchange = upd;
    row.querySelector("button").onclick = () => { delete sim.data.messages[row.dataset.k]; renderMsgs(); pushLine(); };
  });
  renderMsgButtons();
}
function renderMsgButtons() {
  $("#simMsgButtons").innerHTML = Object.keys(sim.route.messages || {}).map((k) => `<button data-msg="${esc(k)}">${esc(k)}</button>`).join("");
  $("#simMsgButtons").querySelectorAll("button").forEach((b) => b.onclick = guard(() => ev("message", { key: b.dataset.msg })));
}
$("#rAddMsg").onclick = () => { const m = sim.data.messages = sim.data.messages || {}; let n = 1; while (m["zprava" + n]) n++;
  m["zprava" + n] = "Text mimořádné informace"; renderMsgs(); pushLine(); };

// ---- files
$("#routeLoad").onclick = guard(async () => {
  const p = $("#routeSelect").value; if (!p) return;
  sim.dir = null; setRouteInfo(await api("/api/sim/route/load", { path: p })); $("#routePath").value = p; toast("Linka načtena");
});
$("#routeSave").onclick = guard(async () => {
  sim.data.line = $("#rLine").value;
  await applyInfo(await api("/api/sim/line/save", { path: $("#routePath").value, line: sim.data, selection: sim.sel }), { courses: true });
  toast("Linka uložena"); loadSimFiles();
});
$("#rCsvImport").onclick = guard(async () => {
  const d = dirOf();
  const r = await api("/api/sim/route/import-csv", { csv: $("#rCsv").value, line: sim.data.line, destination: d.destination || "" });
  d.stops = r.route.stops;
  if (!d.destination) d.destination = r.route.destination || (d.stops.length ? d.stops[d.stops.length - 1].name : "");
  renderDirs(); pushLine({ courses: true });
  toast(`Zastávky směru ${sim.dir} nahrazeny – nezapomeňte uložit`);
});

// ------------------------------------------------------------------ vehicle editor
const CONTROL = { text: "text (zI, zN) – bez databáze", code: "kódy (z / zC, zD) – podle databáze gBUSE0" };
const STOP_FIELD = { "": "ne", top: "nahoře", bottom: "dole" };
// type of a panel as the server decides it (peripherals/__init__.py: peripheral_type)
function ptype(p) {
  const t = (p.type || "").toUpperCase().replace(/\s/g, "");
  if (sim.ptypes[t]) return t;
  if (p.output === "lcd") return "LCD";
  const pr = (p.profile || "BS120").toUpperCase().replace(/\s/g, "");
  if (pr.startsWith("BS210") || pr.startsWith("BS110")) return "BS210";
  return sim.ptypes[pr] ? pr : "BS120";
}
// vehicle files with named buses: the UI only knows the panel's own output -> copy it into the panel
function inlineBuses(v) {
  const buses = v.buses || {};
  for (const p of v.panels || []) {
    const b = p.bus && buses[p.bus];
    if (b) for (const k of ["output", "port", "address", "device"]) if (b[k] !== undefined) p[k] = b[k];
    delete p.bus;
  }
  delete v.buses;
  for (const p of v.panels || []) if (p.stop_field === true) p.stop_field = "top";
}

function renderVehicle() {
  const v = sim.vehicle;
  inlineBuses(v);
  const opts = (obj, sel) => Object.entries(obj).map(([k, t]) => `<option value="${k}" ${k === sel ? "selected" : ""}>${t}</option>`).join("");
  const panelOutputs = Object.fromEntries(Object.entries(OUTPUTS).filter(([k]) => k !== "lcd"));
  $("#vehPanels").innerHTML = (v.panels || []).map((p, i) => {
    const t = ptype(p), lcd = t === "LCD", ext = t === "BS210";
    return `<div class="vpanel" data-i="${i}">
    <div class="row"><label class="chk"><input type="checkbox" data-f="enabled" ${p.enabled === false ? "" : "checked"}></label>
      <input data-f="name" value="${esc(p.name || "")}" style="flex:1"><button class="danger" data-a="del">×</button></div>
    <div class="grid2">
      <label>Typ <select data-f="type">${opts(sim.ptypes, t)}</select></label>
      <label>Role <select data-f="role">${opts(ROLES, p.role)}</select></label>
      ${ext ? `<label>Varianta <select data-f="variant">${opts(sim.variants, p.variant || { front: "v1", side: "v2", rear: "v4" }[p.role] || "v1")}</select></label>
        <label>Nácestné zastávky <select data-f="stop_field">${opts(STOP_FIELD, p.stop_field || "")}</select></label>
        <label>Ovládání <select data-f="control">${opts(CONTROL, p.control || "text")}</select></label>
        <label>Databáze gBUSE0 (.hex, volitelně) <input data-f="database" value="${esc(p.database || "")}" placeholder="bez databáze – originální font gBUSE0"></label>` : ""}
      ${!lcd && !ext ? `<label title="rychlost běžícího textu – změřte na panelu: (šířka textu + šířka pole) sloupců za naměřenou dobu">Posuv [ms/sloupec] <input data-f="scroll_ms" type="number" min="1" max="500" value="${p.scroll_ms ?? ""}" placeholder="podle profilu" style="width:6em"></label>` : ""}
      ${lcd ? "" : `<label>Panel <select data-f="output">${opts(panelOutputs, p.output || "virtual")}</select></label>`}
      ${!lcd && p.output === "serial" ? `<label>Port / adresa <span class="row"><input data-f="port" value="${esc(p.port || "")}" placeholder="auto" style="width:8em"><input data-f="address" type="number" value="${p.address ?? 10}" style="width:4em"></span></label>` : ""}
      ${!lcd && p.output === "esp" ? `<label>ID zařízení ESP <input data-f="device" value="${esc(p.device || "")}"></label>` : ""}
      ${lcd ? `<label>HTTP port / adresa <span class="row"><input data-f="http_port" type="number" min="1024" max="65535" value="${p.http_port ?? 8091}" style="width:6em"><select data-f="host"><option value="127.0.0.1" ${(p.host || "127.0.0.1") === "127.0.0.1" ? "selected" : ""}>jen tento počítač</option><option value="0.0.0.0" ${p.host === "0.0.0.0" ? "selected" : ""}>i z jiných zařízení v síti</option></select></span></label>` : ""}
    </div></div>`;
  }).join("");
  $("#vehPanels").querySelectorAll("[data-f]").forEach((el) => el.onchange = () => {
    const p = v.panels[+el.closest(".vpanel").dataset.i], f = el.dataset.f;
    let val = el.type === "checkbox" ? el.checked : el.type === "number" ? +el.value : el.value;
    if ((f === "database" || f === "stop_field") && !val) { delete p[f]; return; }
    if (f === "scroll_ms" && !el.value) { delete p[f]; return; }
    p[f] = val;
    if (f === "type") {                   // type decides the driver; keep profile in sync for the interior panels
      if (val === "LCD") { p.output = "lcd"; delete p.profile; }
      else {
        if (p.output === "lcd") p.output = "virtual";
        if (val === "BS210") { delete p.profile; if (p.role === "interior") p.role = "front"; }
        else p.profile = val;
      }
    }
    if (["type", "output", "role"].includes(f)) renderVehicle();
  });
  $("#vehPanels").querySelectorAll("[data-a=del]").forEach((b) => b.onclick = () => { v.panels.splice(+b.closest(".vpanel").dataset.i, 1); renderVehicle(); });
  const s = v.settings || (v.settings = {}), i = s.interior || (s.interior = {}), e = s.exterior || (s.exterior = {});
  $$("[data-item]").forEach((c) => c.checked = (i.items || ["next", "time", "zone"]).includes(c.dataset.item));
  $("#sSeconds").value = i.seconds ?? 4; $("#sAtStop").value = i.at_stop || "line_dest"; $("#sMsgMode").value = i.message_mode || "cycle";
  $("#sLine").value = i.line || "at_stop"; $("#sRunTime").value = i.run_time || "once";
  $("#sPreset").innerHTML = Object.entries(sim.presets || {}).map(([k, p]) =>
    `<option value="${k}" ${k === (i.format || "zakladni") ? "selected" : ""}>${esc(p.title)}</option>`).join("");
  $("#sFont").value = i.font ?? 3; $("#sSpacing").value = i.spacing ?? 1;
  $("#sTime").value = i.time_format ?? "%H:%M";
  if (!i.sequences) i.sequences = JSON.parse(JSON.stringify(sim.defaultSeqs || {}));
  delete i.next_format; delete i.request_suffix; delete i.zone_format; delete i.final_text; delete e.format;
  renderSeq();
}
function readSettings() {
  const s = sim.vehicle.settings;
  s.interior = { ...s.interior, items: $$("[data-item]").filter((c) => c.checked).map((c) => c.dataset.item),
    seconds: +$("#sSeconds").value, at_stop: $("#sAtStop").value, message_mode: $("#sMsgMode").value, line: $("#sLine").value, run_time: $("#sRunTime").value,
    font: +$("#sFont").value, spacing: +$("#sSpacing").value, time_format: $("#sTime").value };
  delete s.exterior.font; delete s.exterior.spacing;      // exterior: fonts only per page (else autoformat)
}
$("#vehAdd").onclick = () => {
  sim.vehicle.panels.push({ name: "Nový panel", type: "BS120", role: "interior", profile: "BS120", output: "virtual" });
  renderVehicle();
};
const applyVehicle = guard(async (path) => {
  readSettings();
  sim.vehicle = (await api("/api/sim/vehicle/save", { path: path || null, config: sim.vehicle })).config;
  sim.views = {}; $("#simPanels").innerHTML = "";
  renderSeq();                       // placeholders show the current default font / spacing
  toast(path ? "Vozidlo uloženo" : "Nastavení použito");
});
$("#vehApply").onclick = () => applyVehicle();
$("#sPresetApply").onclick = () => {
  const p = (sim.presets || {})[$("#sPreset").value]; if (!p) return;
  sim.vehicle.settings.interior = JSON.parse(JSON.stringify(p.interior));
  renderVehicle(); applyVehicle();
};
$$("#tab-sim [data-item], #sSeconds, #sAtStop, #sMsgMode, #sLine, #sFont, #sSpacing, #sTime")
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
// default pages of a sequence – from the selected format (preset), else the built-in defaults
const seqDefaults = () => (sim.presets?.[sim.vehicle.settings.interior.format]?.interior.sequences) || sim.defaultSeqs || {};
function seqList() {
  const i = sim.vehicle.settings.interior, name = $("#seqSelect").value;
  i.sequences = i.sequences || {};
  if (!i.sequences[name]) i.sequences[name] = JSON.parse(JSON.stringify(seqDefaults()[name] || []));
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
    <td><input data-f="run" type="checkbox" ${p.run ? "checked" : ""}></td>
    <td class="nowrap"><button data-a="up">↑</button><button data-a="del" class="danger">×</button></td></tr>`).join("");
  $("#seqPages").querySelectorAll("[data-f]").forEach((el) => el.onchange = () => {
    const p = pages[+el.closest("tr").dataset.i], f = el.dataset.f;
    if (el.type === "checkbox") { if (el.checked) p[f] = true; else delete p[f]; }
    else if (el.type === "number") { if (el.value === "") delete p[f]; else p[f] = +el.value; }
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
  sim.vehicle.settings.interior.sequences[$("#seqSelect").value] = JSON.parse(JSON.stringify(seqDefaults()[$("#seqSelect").value] || []));
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
async function ev(type, extra = {}) {
  render(await api("/api/sim/event", { type, ...extra }));
  if (type === "next_trip") setRouteInfo(await api("/api/sim/route"));
}
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
    ${e.course ? ` · kurz ${esc(e.course)}, spoj ${e.trip + 1}${e.departure ? " (" + esc(e.departure) + ")" : ""}` : e.direction ? ` · směr ${esc(e.direction)}` : ""}
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
    const info = [ROLES[p.role] || p.role, p.kind === "lcd" ? null : p.profile, OUTPUTS[p.kind] || p.kind].filter(Boolean);
    if (p.frame) info.push("režim: " + p.frame.mode_title);
    if (p.frame && p.frame.mirror) info.push("náhled podle odeslaných povelů");
    if (!p.frame && p.kind !== "virtual" && p.kind !== "lcd") info.push("zatím nic neodesláno – spusťte ▶ Start");
    if (p.kind === "esp") info.push(p.connected ? "připojeno" : "NEPŘIPOJENO");
    if (p.kind === "lcd") {
      let a = box.el.querySelector("a.lcdlink");
      if (!a) { a = document.createElement("a"); a.className = "lcdlink"; a.target = "_blank"; box.el.append(a); }
      a.href = p.url; a.textContent = "Otevřít LCD panel: " + p.url;
    }
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
    const ri = await api("/api/sim/route");
    sim.vehicle = (await api("/api/sim/vehicle")).config;
    const d = await api("/api/sim/defaults");
    sim.defaultSeqs = d.sequences; sim.presets = d.presets;
    sim.ptypes = d.peripherals.types; sim.variants = d.peripherals.variants;
    const ic = await api("/api/icons");
    $("#iconNames").textContent = ic.icons.map((i) => i.name).join(", ");
    setRouteInfo(ri); renderVehicle();
  }
  await loadSimFiles();
  poll();
}
