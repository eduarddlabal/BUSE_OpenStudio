// BUSE Studio – module switching and start-up
import { $, $$, api, guard, st } from "./common/core.js";
import { loadFiles, setProject, drawFrame, loadFonts, showConfig } from "./gbuse/gbuse.js";
import { loadPorts } from "./loader/loader.js";
import "./live/live.js";
import { activateSim } from "./sim/sim.js";

const TABS = { gbuse: ["texts", "config", "fonts", "import"], loader: ["panel"], live: ["live"], sim: ["sim"] };
const ui = { module: "gbuse", tab: { gbuse: "texts", loader: "panel", live: "live", sim: "sim" } };

function sanitize() {
  // never end up in a module/tab combination that does not exist (would show an empty page)
  if (!TABS[ui.module]) ui.module = "gbuse";
  const tab = { gbuse: "texts", loader: "panel", live: "live", sim: "sim", ...(ui.tab || {}) };
  for (const m of Object.keys(TABS)) if (!TABS[m].includes(tab[m])) tab[m] = TABS[m][0];
  ui.tab = tab;
}

function show() {
  sanitize();
  $$(".modules button").forEach((b) => b.classList.toggle("active", b.dataset.module === ui.module));
  $$("nav.tabs").forEach((n) => n.hidden = n.dataset.for !== ui.module);
  $$("nav.tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === ui.tab[ui.module]));
  $$(".tab").forEach((t) => t.hidden = !(t.dataset.module === ui.module && t.id === "tab-" + ui.tab[ui.module]));
  $$("section.ledwrap").forEach((s) => s.hidden = s.dataset.module !== ui.module);
  const tab = ui.tab[ui.module];
  if (tab === "fonts") guard(loadFonts)();
  if (tab === "config") showConfig();
  if (ui.module === "loader") guard(loadPorts)();
  guard(() => activateSim(ui.module === "sim"))();
  try { localStorage.setItem("buse.ui", JSON.stringify(ui)); } catch { /* storage blocked */ }
}

$$(".modules button").forEach((b) => b.onclick = () => { ui.module = b.dataset.module; show(); });
$$("nav.tabs button").forEach((b) => b.onclick = () => {
  const mod = b.closest("nav.tabs").dataset.for;      // a sub-tab always belongs to its own module
  ui.module = mod; ui.tab[mod] = b.dataset.tab; show();
});

window.addEventListener("beforeunload", (e) => { if (st.dirty) { e.preventDefault(); e.returnValue = ""; } });

guard(async () => {
  try { Object.assign(ui, JSON.parse(localStorage.getItem("buse.ui") || "{}")); } catch { /* ignore */ }
  const h = location.hash.slice(1);
  if (ui.tab[h]) ui.module = h;
  await loadFiles();
  await loadPorts();
  try { setProject(await api("/api/project")); } catch { drawFrame([]); }
  show();
})();
