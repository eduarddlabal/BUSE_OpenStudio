// CSV/XLSX bulk import and legacy gBUSE section file (.lin/.cil/.dru/.cyk/.dop/.bbd/.txt) import/export
import { $, api, guard, st, toast } from "../common/core.js";
import { showConfig } from "./config.js";
import { showCycles } from "./cycles.js";
import { renderList } from "./records.js";
import { readB64, syncProject } from "./shared.js";

export async function uploadSectionImport(f, section, replace) {
  await syncProject();
  const b64 = await readB64(f);
  const r = await api("/api/gbuse/section/import", { name: f.name, content_b64: b64, section, replace });
  st.project.data = r.data;
  st.dirty = true;
  renderList();
  showConfig();
  showCycles();
  return r;
}

export async function downloadSectionExport(ext) {
  const r = await fetch("/api/gbuse/section/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ext, section: $("#secImpSection").value, sort_by: $("#secBbdSort").value }),
  });
  if (!r.ok) throw new Error(await r.text());
  const a = document.createElement("a");
  a.href = URL.createObjectURL(await r.blob());
  a.download = "databaze" + ext;
  a.click();
}

$("#btnImport").onclick = guard(async () => {
  const f = $("#impFile").files[0];
  if (!f) { toast("Vyberte soubor", true); return; }
  const b64 = await readB64(f);
  const defaults = {};
  if ($("#impFont").value !== "") defaults.font = +$("#impFont").value;
  if ($("#impSpacing").value !== "") defaults.spacing = +$("#impSpacing").value;
  await syncProject();
  const r = await api("/api/import", { name: f.name, content_b64: b64, section: $("#impSection").value, replace: $("#impReplace").checked, defaults });
  st.project.data = r.data; st.dirty = true; renderList();
  $("#impOut").textContent = "Importováno: " + JSON.stringify(r.counts) + (r.problems.length ? "\n\nProblémy:\n" + r.problems.join("\n") : "");
});

$("#btnTemplate").onclick = () => {
  const csv = "sekce;cislo;text;font;mezera;zarovnani;bezici;inverze\ncile;101;HLAVNÍ NÁDRAŽÍ;3;2;;;\ncile;102;Dlouhý text, který poběží;3;1;;ano;\nlinky;10;1;3;3;;;\n";
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv" })); a.download = "vzor_importu.csv"; a.click();
};

$("#btnSecImport").onclick = guard(async () => {
  const f = $("#secImpFile").files[0];
  if (!f) { toast("Vyberte soubor", true); return; }
  const r = await uploadSectionImport(f, $("#secImpSection").value, $("#secImpReplace").checked);
  $("#secImpOut").textContent = "Načteno: " + JSON.stringify(r.counts) +
    (r.problems?.length ? "\n\nPoznámky:\n" + r.problems.join("\n") : "");
  toast("Soubor sekce načten");
});

$("#btnSecExport").onclick = guard(async () => {
  await syncProject();
  await downloadSectionExport($("#secExpExt").value);
});
