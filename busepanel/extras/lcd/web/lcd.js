// LCD panel (PID style) – polls the simulation state and draws it.
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// small white-on-black pictograms (24x24 viewBox)
const SVG = {
  metro: '<path d="M3 3h18v8.5L12 21 3 11.5z" fill="#fff"/><path d="M8 7h8v4h3l-7 7-7-7h3z" fill="#000"/>',
  vlak: '<rect x="6" y="3" width="12" height="13" rx="3" fill="#fff"/><rect x="8" y="6" width="8" height="4" fill="#000"/><circle cx="9" cy="13" r="1.2" fill="#000"/><circle cx="15" cy="13" r="1.2" fill="#000"/><path d="M8 17l-2 4h2.2l1.2-2h5.2l1.2 2H18l-2-4z" fill="#fff"/>',
  bus: '<rect x="4" y="3" width="16" height="15" rx="2" fill="#fff"/><rect x="6" y="5" width="12" height="6" fill="#000"/><circle cx="8" cy="14.5" r="1.3" fill="#000"/><circle cx="16" cy="14.5" r="1.3" fill="#000"/><rect x="6" y="18" width="3" height="3" fill="#fff"/><rect x="15" y="18" width="3" height="3" fill="#fff"/>',
  tramvaj: '<path d="M9 2h6M12 2v3" stroke="#fff" stroke-width="1.6"/><rect x="6" y="5" width="12" height="13" rx="2" fill="#fff"/><rect x="8" y="7" width="8" height="5" fill="#000"/><path d="M8 18l-2 4h2.2l1.2-2h5.2l1.2 2H18l-2-4z" fill="#fff"/>',
  trolejbus: '<path d="M7 1l4 4M17 1l-4 4" stroke="#fff" stroke-width="1.6"/><rect x="4" y="5" width="16" height="13" rx="2" fill="#fff"/><rect x="6" y="7" width="12" height="5" fill="#000"/><rect x="6" y="18" width="3" height="3" fill="#fff"/><rect x="15" y="18" width="3" height="3" fill="#fff"/>',
  letadlo: '<path d="M12 2c1 0 1.5 1 1.5 2v5l7 4v2l-7-2v4l2 1.5V20l-3.5-1-3.5 1v-1.5l2-1.5v-4l-7 2v-2l7-4V4c0-1 .5-2 1.5-2z" fill="#fff"/>',
  vozik: '<circle cx="11" cy="4" r="2" fill="#fff"/><path d="M10 7v7h6l2 6" stroke="#fff" stroke-width="2.2" fill="none"/><path d="M8 10a6 6 0 1 0 8 7" stroke="#fff" stroke-width="2" fill="none"/>',
  prestup: '<path d="M4 8h13l-3-3M20 16H7l3 3" stroke="#fff" stroke-width="2.4" fill="none"/>',
  pozor: '<path d="M12 3l10 18H2z" fill="#fff"/><path d="M12 9v6M12 17v2" stroke="#000" stroke-width="2.2"/>',
  znameni: '<path d="M12 3a6 6 0 0 1 6 6v6l2 2H4l2-2V9a6 6 0 0 1 6-6z" fill="currentColor"/><circle cx="12" cy="20.5" r="2" fill="currentColor"/>',
  sipka: '<path d="M3 9h10V4l8 8-8 8v-5H3z" fill="#fff"/>',
};
const svg = (k) => `<svg viewBox="0 0 24 24">${SVG[k]}</svg>`;

// "metro-C" -> metro logo + C, "vlak-S9" -> train + S9, "S" -> S badge, other known names -> pictogram
function icon(name) {
  const [base, sub] = String(name).split("-");
  const b = base.toLowerCase();
  let h = "";
  if (SVG[b]) h += `<span class="ic blk">${svg(b)}</span>`;
  if (b === "metro" && sub && /^[abc]$/i.test(sub)) h += `<span class="ic m${sub.toUpperCase()}">${esc(sub.toUpperCase())}</span>`;
  else if (sub) h += `<span class="ic txt"><span class="l">${esc(sub)}</span></span>`;
  if (!SVG[b] && !sub) h += /^[A-Z]\d*$/.test(base) ? `<span class="ic s"><span class="l">${esc(base)}</span></span>` : "";
  return h ? `<span class="ig">${h}</span>` : "";
}
const icons = (s) => (s.icons || []).map(icon).join("") + (s.request ? `<span class="ic plain">${svg("znameni")}</span>` : "");

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
