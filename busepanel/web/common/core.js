// Shared helpers for all modules
export const $ = (s) => document.querySelector(s);
export const $$ = (s) => [...document.querySelectorAll(s)];
export const api = async (path, body, method) => {
  const opt = body === undefined ? { method: method || "GET" }
    : { method: method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) || data || r.statusText);
  return data;
};
export function toast(msg, err) {
  const d = document.createElement("div");
  d.textContent = msg;
  if (err) d.className = "err";
  $("#toast").append(d);
  setTimeout(() => d.remove(), err ? 7000 : 3500);
}
export const guard = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); console.error(e); } };
export const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export function log(el, msg) { const o = $(el); o.textContent += msg + "\n"; o.scrollTop = o.scrollHeight; }

// shared state (current gBUSE project – its fonts are also used by Live)
export const st = { project: null, geometry: null, section: "destinations", key: null, previewLine: "", dirty: false,
                    fonts: null, font: null, glyph: null, anim: null };
