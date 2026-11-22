// LCD panel (PID style) – polls the simulation state and draws it.
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// The only pictogram on the LCD: bell for a stop on request (white, no background)
const BELL = '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 2.2c.8 0 1.4.6 1.4 1.4v.5a6.2 6.2 0 0 1 4.8 6.1v5.3l2.3 2.6v1H3.5v-1l2.3-2.6v-5.3a6.2 6.2 0 0 1 4.8-6.1v-.5c0-.8.6-1.4 1.4-1.4z"/><path fill="currentColor" d="M9.6 20.2h4.8a2.4 2.4 0 0 1-4.8 0z"/></svg>';
const icons = (s) => (s.request ? `<span class="ic">${BELL}</span>` : "");

function stopHtml(s, minLabel) {
  if (!s) return "";
  const m = minLabel ?? (s.minutes == null ? "" : `${s.minutes} min`);
  return `<span class="min">${esc(m)}</span><span class="dot"></span><span class="name"><span class="txt">${esc(s.name)}</span>${icons(s)}</span>`;
}

function render(v) {
  $("#line").textContent = v.line || "";
  $("#zoneVal").textContent = v.zone || "";
  $("#zone").hidden = !v.zone;
  $("#clock").textContent = v.time || "";
  const label = v.phase === "driving" ? null : v.phase === "ended" ? "konečná / terminus" : "zastávka / this stop";
  $("#first").innerHTML = stopHtml(v.first, label);

  // as many following stops as fit between the first row and the bottom bar
  const list = $("#list");
  const u = $("#screen").clientWidth / 160;
  const max = Math.max(0, Math.floor(list.clientHeight / (u * 10.5) + 0.05));
  const rows = (v.rows || []).slice(0, max);
  const msg = v.message || (v.phase === "ended" ? "Konečná zastávka, prosíme vystupte.|Final stop, please leave the vehicle." : "");
  $("#msg").hidden = !msg;
  if (msg) {
    const [cz, en] = msg.split("|");
    $("#msg").innerHTML = `<div>${esc(cz)}</div>` + (en ? `<div class="en">${esc(en)}</div>` : "");
    list.innerHTML = "";
  } else {
    list.innerHTML = rows.map((s, i) => `<div class="stop" style="top:calc(${i} * var(--u) * 10.5)">${stopHtml(s)}</div>`).join("");
  }
  $("#terminal").innerHTML = stopHtml(v.terminal);
  $("#screen").classList.toggle("ended", !v.terminal);     // first row is the terminal
  $("#screen").classList.toggle("atstop", v.phase === "at_stop" || v.phase === "idle");
}

async function poll() {
  try {
    const r = await fetch("api/lcd", { cache: "no-store" });
    if (r.ok) render(await r.json());
  } catch (e) { /* server restarting – try again */ }
  setTimeout(poll, 500);
}
poll();
