// Shared constants, state helpers, and pure utilities for the gBUSE modules
import { $, api, st } from "../common/core.js";

export const SECTION_CMD_1 = {
  lines: ["l", 3], destinations: ["z", 3], destinations4: ["zC", 4],
  stops: ["x", 4], messages: ["xA", 4], supplementary: ["DOP", 1], format_strings: ["STR", 1],
};
export const SECTION_CMD_0 = {
  lines: ["l", 3], destinations: ["z", 3], destinations4: ["zC", 4],
  stops: ["zD", 4], format_strings: ["STR", 1],
};
export const ALL_CMDS_1 = ["l", "z", "x", "xA", "xB", "zC", "zD", "v", "zI", "zM", "zN", "aA", "u", "e", "xC", "a", "aV", "lA"];

export const cyclesState = { id: "0", fmt: "ident", playing: false };
export const simState = { mode: "static", cycleId: "0", stepIdx: 0, playing: false, paused0: false };
export const metaCache = { projects: [], templates: {} };

export function isG0() { return st.project?.format === "gbuse0"; }
export function secMeta() { return isG0() ? SECTION_CMD_0 : SECTION_CMD_1; }
export function isSpecialSec(sec = st.section) { return sec === "supplementary" || sec === "format_strings"; }

export function slugify(s) {
  return (s || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
    .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "projekt";
}

export function setDirty(v) {
  st.dirty = !!v;
  if ($("#dirtyBadge")) $("#dirtyBadge").hidden = !st.dirty;
}

export function currentValue() {
  if (!st.project || st.key === null) return null;
  return (st.project.data[st.section] || {})[st.key];
}

export function plain(v) {
  if (v && typeof v === "object") return v.text ?? "";
  const s = String(v ?? "");
  if (isG0() || !s.includes("{")) {
    // Strip MNEMO control tokens to show clean text in the list
    const m = [...s.matchAll(/'([^']*)'/g)].map((x) => x[1]).join(" | ");
    if (m) return m;
  }
  return s.replace(/\{\{/g, "{").replace(/\{[^{}]*\}/g, "");
}

export function readB64(f) {
  return new Promise((res) => { const r = new FileReader(); r.onload = () => res(r.result.split(",")[1]); r.readAsDataURL(f); });
}

export async function syncProject() {
  if (st.project) await api("/api/project/update", { data: st.project.data });
}

export function touch() {
  setDirty(true);
  clearTimeout(touch.t);
  touch.t = setTimeout(() => syncProject().catch(() => {}), 350);
}
