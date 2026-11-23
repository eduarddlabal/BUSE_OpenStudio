// Live module – free text, clock, departure board
import { $, $$, api, toast, guard, esc, log, st } from "../common/core.js";
import { syncProject, renderList } from "../gbuse/gbuse.js";

const liveOut = () => ({ output: $("#liveOutput").value, device: $("#liveDevice").value });

async function liveStatus() {
  try {
    const r = await api("/api/live/status");
    $("#liveState").textContent = r.running ? "● běží" : "zastaveno";
    $("#liveOut").textContent = r.log.join("\n");
  } catch { /* server restarting */ }
}
setInterval(liveStatus, 2000);
$("#clkBuild").onclick = guard(async () => {
  if ($("#clkChannel").value === "text") return;
  await syncProject();
  const r = await api("/api/live/build", { mode: "clock", channel: $("#clkChannel").value, format: $("#clkFormat").value });
  st.project.data = r.data; st.dirty = true; renderList();
  toast("Texty hodin přidány – uložte a nahrajte do panelu");
});
$("#clkStart").onclick = guard(async () => {
  await api("/api/live/start", { ...liveOut(),  mode: "clock", channel: $("#clkChannel").value, format: $("#clkFormat").value }); liveStatus();
});
$("#ftSend").onclick = guard(async () => {
  const body = { text: $("#ftText").value, kind: $("#ftKind").value };
  if ($("#ftFont").value !== "") body.font = +$("#ftFont").value;
  if ($("#ftSpacing").value !== "") body.spacing = +$("#ftSpacing").value;
  if ($("#ftLine").value.trim()) body.line = $("#ftLine").value.trim();
  Object.assign(body, liveOut());
  const r = await api("/api/panel/text", body);
  $("#ftOut").textContent = "→ " + r.sent.join("  ");
  if (r.problems.length) toast(r.problems.join("; "), true);
});
$("#ftNormal").onclick = guard(async () => { await api("/api/panel/send", { telegrams: ["x9999"] }); $("#ftOut").textContent = "→ x9999"; });
$("#clkBuild").addEventListener("click", () => { if ($("#clkChannel").value === "text") toast("Kanál volný text nepotřebuje přidávat texty – rovnou Spustit"); });
$("#depStart").onclick = guard(async () => {
  const body = { mode: "departures", schedule_csv: $("#depCsv").value, format: $("#depFormat").value,
                 line_field: $("#depLineField").checked };
  if ($("#depFont").value !== "") body.font = +$("#depFont").value;
  if ($("#depSpacing").value !== "") body.spacing = +$("#depSpacing").value;
  await api("/api/live/start", body); liveStatus();
});
$("#plStart").onclick = guard(async () => {
  await api("/api/live/start", { ...liveOut(),  mode: "playlist", channel: "dest", keys: $("#plKeys").value.split(",").map((s) => s.trim()).filter(Boolean),
    interval: +$("#plInterval").value });
  liveStatus();
});
$("#liveStop").onclick = guard(async () => { await api("/api/live/stop", {}); liveStatus(); });

let loadedPresets = [];
export async function loadLivePresets() {
  try {
    const r = await api("/api/live/templates");
    loadedPresets = r.presets || [];
    const sel = $("#livePresetSelect");
    if (!sel) return;
    sel.innerHTML = `<option value="">— načíst uloženou předvolbu —</option>` +
      loadedPresets.map((p, i) => `<option value="${i}">${esc(p.name)} (${p.scope === "project" ? "projekt" : "šablona"})</option>`).join("");
  } catch { /* ignore */ }
}
$("#livePresetApply")?.addEventListener("click", () => {
  const idx = $("#livePresetSelect").value;
  if (idx === "") return;
  const p = loadedPresets[+idx];
  if (!p) return;
  $("#ftText").value = p.text || "";
  if (p.kind) $("#ftKind").value = p.kind;
  $("#ftFont").value = p.font ?? "";
  $("#ftSpacing").value = p.spacing ?? "";
  $("#ftLine").value = p.line || "";
  toast("Předvolba načtena: " + p.name);
});
$("#livePresetSave")?.addEventListener("click", guard(async () => {
  const name = $("#livePresetName").value.trim() || $("#ftText").value.trim().slice(0, 20) || "Předvolba";
  const body = {
    name,
    text: $("#ftText").value,
    kind: $("#ftKind").value,
    line: $("#ftLine").value.trim(),
    scope: $("#livePresetScope").value,
  };
  if ($("#ftFont").value !== "") body.font = +$("#ftFont").value;
  if ($("#ftSpacing").value !== "") body.spacing = +$("#ftSpacing").value;
  await api("/api/live/templates/save", body);
  toast("Předvolba uložena: " + name);
  await loadLivePresets();
}));
loadLivePresets();
