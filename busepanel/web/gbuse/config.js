// Panel & multi-panel bundle configuration editor
import { $, $$, api, esc, guard, st, toast } from "../common/core.js";
import { updateModeChrome } from "./projects.js";
import { ALL_CMDS_1, isG0, metaCache, syncProject, touch } from "./shared.js";

function parseAfText(txt) {
  return txt.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
    .map((l) => l.split(/[,;\s]+/).map((n) => +n || 0).slice(0, 3))
    .map((row) => [row[0] ?? 0, row[1] ?? 1, row[2] ?? 0]);
}

export function showConfig() {
  if (!st.project) return;
  const g0 = isG0();
  const d = st.project.data, c = d.config || (d.config = {}), p = d.panel || (d.panel = {});
  const df = c.defaults || (c.defaults = {}), meta = d.meta || {};
  const b = st.project.bundle;
  $("#cfgMetaName").value = b ? b.name : (meta.name ?? st.project.name ?? "");
  $("#cfgMetaDesc").value = b ? b.description : (meta.description ?? "");
  $("#cfgBundleWrap").hidden = !b;
  if (b) {
    const sysOpts = [`<option value="default">Výchozí systém</option>`].concat(
      (metaCache.templates.systemy || []).map((s) => `<option value="template:${esc(s)}">Šablona: ${esc(s)}</option>`)
    );
    $("#cfgBundleSys").innerHTML = sysOpts.join("");
    if (b.simulation?.system) $("#cfgBundleSys").value = b.simulation.system;
    const lineOpts = [`<option value="">— žádná —</option>`].concat(
      (metaCache.templates.trasy || []).map((s) => `<option value="template:${esc(s)}">Šablona: ${esc(s)}</option>`)
    );
    $("#cfgBundleLine").innerHTML = lineOpts.join("");
    if (b.simulation?.default_line) $("#cfgBundleLine").value = b.simulation.default_line;
  }
  $("#cfgFormat").value = g0 ? "gBUSE0" : "gBUSE1";
  $("#cfgType").value = p.type || (g0 ? "BS210" : "BS120");
  $("#cfgAddr").value = p.ibis_address ?? 10;
  $("#cfgColumns").value = c.columns ?? (g0 ? 140 : 135);
  $("#cfgRowsWrap").hidden = !g0;
  $("#cfgRows").value = c.rows ?? 19;
  $("#cfgVersion").value = c.version ?? "";

  const fwList = g0 ? ["1.6", "2.15", "3.08", "3.18"] : ["1.xx", "2.06", "2.12"];
  $("#cfgFw").innerHTML = fwList.map((f) => `<option value="${f}">${f}</option>`).join("");
  $("#cfgFw").value = fwList.includes(String(c.fw)) ? String(c.fw) : fwList[1];

  $("#cfg1Wrap").hidden = g0;
  $("#cfg0Wrap").hidden = !g0;

  if (!g0) {
    $("#cfgFont").value = df.font ?? 3;
    $("#cfgSpacing").value = df.spacing ?? 1;
    $("#cfgInterval").value = df.interval ?? 1;
    $("#cfgCycle").value = c.cycle_delay ?? 10;
    $("#cfgKind").value = String(c.panel_kind ?? 1);
    $("#cfgTariff").value = c.tariff_digits ?? 1;
  } else {
    $("#cfg0Num").value = p.panel_number ?? 0;
    $("#cfg0Bright").value = p.brightness ?? 0;
    $("#cfg0Sep").value = c.fnt_sep || ":";
    $("#cfg0Preset").value = "";
    const props = new Set(c.properties || []);
    const propMeta = st.project.gbuse0_meta?.properties || [];
    $("#cfg0Props").innerHTML = propMeta.map(([, name, label]) =>
      `<label class="chk"><input type="checkbox" value="${esc(name)}" ${props.has(name) ? "checked" : ""}> ${esc(label)}</label>`
    ).join("");
    $("#cfg0Props").querySelectorAll("input").forEach((el) => el.onchange = readConfig);

    const fields = c.fields || (c.fields = {});
    const fNames = [["line", "Pole linky (LIN)"], ["dest", "Pole cíle (CIL)"], ["stop", "Pole nácestné (DRU)"]];
    $("#cfg0Fields").innerHTML = fNames.map(([fk, title]) => {
      const f = fields[fk] || { r0: 0, r1: 0, c0: 0, c1: 0, font: 3, spacing: 1, shift: 0, interval: 1.0 };
      return `<div class="card0" data-fk="${fk}">
        <b>${title}</b>
        <div class="grid2">
          <label>r0 <input data-p="r0" type="number" value="${f.r0 ?? 0}"></label>
          <label>r1 <input data-p="r1" type="number" value="${f.r1 ?? 0}"></label>
          <label>c0 <input data-p="c0" type="number" value="${f.c0 ?? 0}"></label>
          <label>c1 <input data-p="c1" type="number" value="${f.c1 ?? 0}"></label>
          <label>font <input data-p="font" type="number" value="${f.font ?? 3}"></label>
          <label>mez. <input data-p="spacing" type="number" value="${f.spacing ?? 1}"></label>
          <label>posun <input data-p="shift" type="number" value="${f.shift ?? 0}"></label>
          <label>s <input data-p="interval" type="number" step="0.1" value="${f.interval ?? 1.0}"></label>
        </div>
      </div>`;
    }).join("");
    $("#cfg0Fields").querySelectorAll("input").forEach((el) => el.onchange = readConfig);
    $("#cfg0Afc").value = (c.afc || []).map((row) => row.join(", ")).join("\n");
    $("#cfg0Afd").value = (c.afd || []).map((row) => row.join(", ")).join("\n");
  }

  const allCmds = g0 ? (st.project.gbuse0_meta?.commands || []) : ALL_CMDS_1;
  const cmds = new Set(c.commands || []);
  $("#cfgCommands").innerHTML = allCmds.map((x) =>
    `<label class="chk"><input type="checkbox" value="${esc(x)}" ${cmds.has(x) ? "checked" : ""}> ${esc(x)}</label>`
  ).join("");
  $("#cfgCommands").querySelectorAll("input").forEach((i) => i.onchange = readConfig);
  $("#cfgKod").value = d.kod_table || "";
}

function readConfig() {
  if (!st.project) return;
  const g0 = isG0(), d = st.project.data, c = d.config, p = d.panel;
  const mn = $("#cfgMetaName").value.trim(), md = $("#cfgMetaDesc").value.trim();
  if (st.project.bundle) {
    st.project.bundle.name = mn;
    st.project.bundle.description = md;
    if (mn) st.project.name = mn;
    api("/api/bundle/update-meta", {
      name: mn,
      description: md,
      simulation: { ...(st.project.bundle.simulation || {}), system: $("#cfgBundleSys").value, default_line: $("#cfgBundleLine").value },
    }).catch(() => {});
  } else {
    d.meta = { name: mn, description: md };
    if (mn) st.project.name = mn;
  }
  p.type = $("#cfgType").value;
  p.ibis_address = +$("#cfgAddr").value;
  c.columns = +$("#cfgColumns").value;
  c.version = $("#cfgVersion").value;
  c.fw = $("#cfgFw").value;
  if (!g0) {
    c.magic = c.fw.startsWith("1") ? 0xAA : 0xAB;
    c.defaults = { font: +$("#cfgFont").value, spacing: +$("#cfgSpacing").value, interval: +$("#cfgInterval").value };
    c.cycle_delay = +$("#cfgCycle").value;
    c.panel_kind = +$("#cfgKind").value;
    c.tariff_digits = +$("#cfgTariff").value;
  } else {
    c.rows = +$("#cfgRows").value;
    c.magic = c.fw.startsWith("1") ? 0x55 : 0x57;
    c.fw_3x = c.fw.startsWith("3");
    p.panel_number = +$("#cfg0Num").value;
    p.brightness = +$("#cfg0Bright").value;
    c.fnt_sep = $("#cfg0Sep").value;
    c.properties = $$("#cfg0Props input:checked").map((el) => el.value);
    const fields = {};
    $$("#cfg0Fields [data-fk]").forEach((box) => {
      const f = {};
      box.querySelectorAll("input").forEach((inp) => { f[inp.dataset.p] = inp.dataset.p === "interval" ? +inp.value : Math.round(+inp.value); });
      if (f.r1 > 0 && f.c1 > 0) fields[box.dataset.fk] = f;
    });
    c.fields = fields;
    c.afc = parseAfText($("#cfg0Afc").value);
    c.afd = parseAfText($("#cfg0Afd").value);
  }
  c.commands = $$("#cfgCommands input:checked").map((i) => i.value);
  const kod = $("#cfgKod").value.trim();
  if (kod) d.kod_table = kod; else delete d.kod_table;
  st.geometry = { ...st.geometry, columns: c.columns, rows: g0 ? c.rows : 8 };
  updateModeChrome();
  touch();
}

["#cfgMetaName", "#cfgMetaDesc", "#cfgBundleSys", "#cfgBundleLine", "#cfgType", "#cfgFw", "#cfgAddr", "#cfgColumns", "#cfgRows", "#cfgVersion",
  "#cfgFont", "#cfgSpacing", "#cfgInterval", "#cfgCycle", "#cfgKind", "#cfgTariff",
  "#cfg0Num", "#cfg0Bright", "#cfg0Sep", "#cfg0Afc", "#cfg0Afd", "#cfgKod"].forEach((s) => $(s).onchange = readConfig);

$("#btnBundleApplySim").onclick = guard(async () => {
  await syncProject();
  const r = await api("/api/bundle/apply-sim", {});
  toast("Panely projektu načteny do simulace: " + (r.vehicle || "OK"));
});

$("#cfg0Preset").onchange = () => {
  const pr = $("#cfg0Preset").value;
  if (!pr || !isG0()) return;
  const presets = {
    front: [140, 19, ":", { line: { r0: 1, r1: 19, c0: 1, c1: 28, font: 8, spacing: 1, shift: 0, interval: 1 }, dest: { r0: 1, r1: 19, c0: 29, c1: 140, font: 7, spacing: 1, shift: 0, interval: 1 } }],
    side: [112, 19, " ", { line: { r0: 1, r1: 19, c0: 1, c1: 28, font: 8, spacing: 1, shift: 0, interval: 1 }, dest: { r0: 1, r1: 19, c0: 29, c1: 112, font: 7, spacing: 1, shift: 0, interval: 1 } }],
    side_via: [112, 19, " ", { line: { r0: 1, r1: 19, c0: 1, c1: 28, font: 8, spacing: 1, shift: 0, interval: 1 }, dest: { r0: 9, r1: 19, c0: 29, c1: 112, font: 3, spacing: 1, shift: 0, interval: 1 }, stop: { r0: 1, r1: 8, c0: 29, c1: 112, font: 11, spacing: 1, shift: 0, interval: 3 } }],
    rear: [28, 19, ".", { line: { r0: 1, r1: 19, c0: 1, c1: 28, font: 8, spacing: 1, shift: 0, interval: 1 } }],
  };
  const [cols, rows, sep, fields] = presets[pr];
  Object.assign(st.project.data.config, { columns: cols, rows, fnt_sep: sep, fields });
  touch(); showConfig(); updateModeChrome();
};
