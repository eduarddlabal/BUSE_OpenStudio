// Cycles editor for gBUSE1 typ 2 panels
import { $, esc, guard, st } from "../common/core.js";
import { downloadSectionExport, uploadSectionImport } from "./import_export.js";
import { preview } from "./preview.js";
import { updateModeChrome } from "./projects.js";
import { cyclesState, isG0, simState, syncProject, touch } from "./shared.js";

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
    Object.entries(map).map(([k]) =>
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
