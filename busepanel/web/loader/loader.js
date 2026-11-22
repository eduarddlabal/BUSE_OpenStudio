// Loader module – connection, upload/backup, IBIS console (replacement of BSLoader)
import { $, $$, api, toast, guard, esc, log, st } from "../common/core.js";
import { syncProject, loadFiles } from "../gbuse/gbuse.js";

export async function loadPorts() {
  const r = await api("/api/panel/ports");
  $("#portSelect").innerHTML = r.ports.map((p) => `<option value="${esc(p.port)}">${esc(p.port)} – ${esc(p.description)}</option>`).join("")
    || `<option>${esc(r.port)}</option>`;
  $("#portSelect").value = r.port; $("#panelAddr").value = r.address;
  $("#panelPort").textContent = `${r.port} / adresa ${r.address}`;
}
$("#btnPortSave").onclick = guard(async () => {
  await api("/api/panel/config", { port: $("#portSelect").value, address: +$("#panelAddr").value });
  loadPorts(); toast("Nastaveno");
});
async function runJob(jid, out) {
  $(out).textContent = "";
  let shown = 0;
  for (;;) {
    const j = await api("/api/jobs/" + jid);
    j.log.slice(shown).forEach((m) => log(out, m)); shown = j.log.length;
    $("#progBar").style.width = j.total ? (100 * j.done / j.total) + "%" : "0";
    if (j.stage) $("#progBar").title = j.stage;
    if (j.state !== "running") { $("#progBar").style.width = j.state === "done" ? "100%" : "0"; return j; }
    await new Promise((r) => setTimeout(r, 400));
  }
}
$("#btnScan").onclick = guard(async () => {
  const j = await runJob((await api("/api/panel/scan", {})).job, "#jobOut");
  log("#jobOut", j.result?.length ? "Nalezeno: " + j.result.map((r) => `adresa ${r.address} (${r.version})`).join(", ") : "Nic nenalezeno");
});
$("#btnVersion").onclick = guard(async () => {
  const r = await api("/api/panel/send", { telegrams: ["aV" + "0123456789:;<=>?"[+$("#panelAddr").value]], query: true });
  log("#consoleOut", "aV ← " + (r.reply ?? "bez odpovědi"));
});
$("#btnUpload").onclick = guard(async () => {
  await syncProject();
  const j = await runJob((await api("/api/panel/upload", {})).job, "#jobOut");
  if (j.state === "done") {
    toast(j.result.verify_ok ? "Nahráno a zkontrolováno ✔" : "Nahráno, ale kontrola našla rozdíly", !j.result.verify_ok);
    toast("Vypněte a zapněte napájení panelu – čísla linek se z nových dat načtou až po restartu.", true);
  }
});
$("#btnBackup").onclick = guard(async () => {
  const j = await runJob((await api("/api/panel/backup", { eprom: $("#bkEprom").checked })).job, "#jobOut");
  if (j.state === "done") { toast("Záloha: " + j.result.folder); loadFiles(); }
});
async function sendTel(text, query) {
  const tel = text.trim().split(/\s+/).filter(Boolean);
  if (!tel.length) return;
  if (query) {
    const r = await api("/api/panel/send", { telegrams: [tel[0]], query: true });
    log("#consoleOut", `${tel[0]} ← ${r.reply ?? "(bez odpovědi)"}`);
  } else {
    await api("/api/panel/send", { telegrams: tel });
    log("#consoleOut", "→ " + tel.join("  "));
  }
}
$("#btnSend").onclick = guard(() => sendTel($("#telegram").value));
$("#btnQuery").onclick = guard(() => sendTel($("#telegram").value, true));
$("#telegram").onkeydown = (e) => { if (e.key === "Enter") guard(() => sendTel($("#telegram").value))(); };
$$("[data-tel]").forEach((b) => b.onclick = guard(() => sendTel(b.dataset.tel, !!b.dataset.q)));

$("#btnDevices").onclick = guard(async () => {
  const r = await api("/api/devices");
  $("#devList").textContent = r.devices.length ? r.devices.map((d) => d.id + (d.hello && d.hello.fw ? ` (fw ${d.hello.fw})` : "")).join(", ") : "žádné připojené zařízení";
});
