// LED canvas rendering & gBUSE0/gBUSE1/bundle live preview
import { $, api, esc, st } from "../common/core.js";
import { currentValue, isG0, isSpecialSec, simState } from "./shared.js";

const led = $("#led");

function drawToCanvas(canvas, cols, columns = 135, rows = 8, isDot = false) {
  const c = canvas.getContext("2d");
  const s = Math.max(3, Math.floor(canvas.width / Math.max(1, columns)));
  const r = s * (isDot ? 0.38 : 0.36);
  canvas.height = s * rows + 4;
  c.fillStyle = "#0b0907";
  c.fillRect(0, 0, canvas.width, canvas.height);
  for (let x = 0; x < columns; x++) {
    const colVal = cols[x] || 0;
    for (let y = 0; y < rows; y++) {
      const on = isDot ? ((colVal >>> y) & 1) : ((colVal >> (7 - y)) & 1);
      c.fillStyle = on ? (isDot ? "#ffd12e" : "#ffb02e") : "#2a2118";
      if (on) {
        c.shadowColor = isDot ? "#ffb800" : "#ff9d00";
        c.shadowBlur = s * 0.55;
      } else {
        c.shadowBlur = 0;
      }
      c.beginPath();
      c.arc(x * s + s / 2 + 2, y * s + s / 2 + 2, r, 0, 7);
      c.fill();
    }
  }
  c.shadowBlur = 0;
}

export function drawFrame(cols, columns = 135, rows = 8, isDot = false) {
  drawToCanvas(led, cols, columns, rows, isDot);
}

function blit(frame, item, start, lo, hi) {
  item.cols.forEach((v, i) => {
    const x = start + i;
    if (x >= lo && x <= hi && x >= 0 && x < frame.length) frame[x] = item.inv[i] ? (~v & 0xff) : v;
  });
}

function stopAnim() {
  if (st.anim) cancelAnimationFrame(st.anim);
  if (st.animTimer) clearTimeout(st.animTimer);
  st.anim = null;
  st.animTimer = null;
}

async function previewAllPanels() {
  const wrap = $("#ledAllWrap");
  if (!wrap || !st.project) return;
  const lineKey = st.section === "lines" ? st.key : ($("#ed0PreviewLine")?.value || $("#edPreviewLine")?.value || "");
  const destKey = (st.section === "destinations" || st.section === "destinations4") ? st.key : "";
  const stopKey = st.section === "stops" ? st.key : ($("#ed0PreviewStop")?.value || "");
  let res;
  try {
    res = await api("/api/bundle/preview-all", {
      section: st.section,
      line_key: lineKey || "",
      dest_key: destKey || "",
      stop_key: stopKey || "",
      value: currentValue(),
    });
  } catch (e) { $("#ledInfo").textContent = e.message; return; }
  wrap.innerHTML = "";
  const ovList = [];
  for (const p of res.panels || []) {
    const item = document.createElement("div");
    item.className = "ledall-item";
    const ov = Object.entries(p.overflow || {}).filter(([, v]) => v).map(([k]) => k);
    if (ov.length) ovList.push(`${p.name} (${ov.join(",")})`);
    const hdr = document.createElement("div");
    hdr.className = "ledall-title";
    hdr.innerHTML = `<span><b>${esc(p.name)}</b> (${esc(p.format.toUpperCase())} ${p.columns}×${p.rows})</span>` +
      `<span>${ov.length ? "⚠ přetečení" : "OK"}</span>`;
    const cv = document.createElement("canvas");
    cv.width = 1350;
    if (p.format === "gbuse0") {
      const fr = (p.frames && p.frames[0]) || { bits: [] };
      drawToCanvas(cv, fr.bits || [], p.columns, p.rows, true);
    } else {
      const f = new Array(p.columns).fill(0);
      if (p.line) blit(f, p.line, p.line.start, p.line.lo, p.line.hi);
      const fr = (p.frames && p.frames[0]);
      if (fr) blit(f, fr, fr.start, fr.lo, fr.hi);
      drawToCanvas(cv, f, p.columns, 8, false);
    }
    item.append(hdr, cv);
    wrap.append(item);
  }
  $("#ledInfo").textContent = `Společný náhled (${(res.panels || []).length} panely)` +
    (ovList.length ? ` — ⚠ přetečení: ${ovList.join(", ")}` : "");
}

export async function preview() {
  if (!st.project) return;
  stopAnim();
  if (st.viewAll && st.project.bundle) {
    $("#led").hidden = true;
    $("#ledAllWrap").hidden = false;
    return previewAllPanels();
  }
  $("#led").hidden = false;
  $("#ledAllWrap").hidden = true;
  const g = st.geometry;

  if (isG0()) {
    const val = currentValue();
    const lineKey = $("#ed0PreviewLine")?.value || "";
    const stopKey = $("#ed0PreviewStop")?.value || "";
    const lineVal = st.section === "lines" ? null : (lineKey ? (st.project.data.lines || {})[lineKey] : null);
    const stopVal = st.section === "stops" ? null : (stopKey ? (st.project.data.stops || {})[stopKey] : null);
    let res;
    try {
      res = await api("/api/preview", {
        section: st.section,
        value: val,
        line: lineVal ?? null,
        stop: stopVal ?? null,
      });
    } catch (e) { $("#ledInfo").textContent = e.message; return; }

    const frames = res.frames || [];
    const colsN = res.columns || g.columns || 140;
    const rowsN = res.rows || g.rows || 19;
    let fi = 0;
    const renderIdx = () => {
      if (!frames.length) { drawFrame([], colsN, rowsN, true); return; }
      const fr = frames[fi % frames.length];
      drawFrame(fr.bits, colsN, rowsN, true);
      if (frames.length > 1 && !simState.paused0 && fr.seconds) {
        st.animTimer = setTimeout(() => { fi = (fi + 1) % frames.length; renderIdx(); }, Math.max(100, fr.seconds * 1000));
      }
    };
    renderIdx();

    const ov = Object.entries(res.overflow || {}).filter(([, v]) => v).map(([k]) => k);
    $("#ledInfo").textContent = `gBUSE0 ${colsN}×${rowsN}` +
      (frames.length > 1 ? ` · ${frames.length} stránek` : "") +
      (res.block ? " · celý panel (b)" : "") +
      (res.suppress_stops ? " · bez zastávek (w)" : "") +
      (ov.length ? ` — ⚠ přetečení (${ov.join(", ")})` : "");
    $("#ed0Bytes").textContent = res.bytes || "";
    if ($("#ed0Mode").value !== "mnemo" && res.mnemo !== undefined && document.activeElement !== $("#ed0Mnemo")) {
      $("#ed0Mnemo").value = res.mnemo;
    }
    const probs = [...(res.problems || [])];
    if (ov.length) probs.push("Text v poli (" + ov.join(", ") + ") se nevejde na šířku okna.");
    $("#edProblems").innerHTML = probs.map((p) => `<div class="${p.includes("nevejde") ? "warning" : ""}">${esc(p)}</div>`).join("");
    return;
  }

  // ---- gBUSE1 preview (static, effects t1/t2/t3, blink, or typ 2 cycle simulation)
  if (simState.mode === "cycle") {
    const steps = (st.project.data.cycles || {})[simState.cycleId] || [];
    const idx = steps.length ? Math.min(simState.stepIdx, steps.length - 1) : 0;
    $("#simStepLabel").textContent = `${steps.length ? idx + 1 : 0}/${steps.length}`;
    if (!steps.length) { drawFrame([], g.columns, 8, false); $("#ledInfo").textContent = "Vybraný cyklus je prázdný"; return; }
    let res;
    try {
      res = await api("/api/preview", {
        cycle_id: +simState.cycleId,
        step_idx: idx,
        line: $("#edPreviewLine")?.value || "",
        free_text: $("#simFree").value,
        time_str: $("#simTime").value,
        zone_str: $("#simZone").value,
      });
    } catch (e) { $("#ledInfo").textContent = e.message; return; }
    const cf = res.cycle_frame;
    drawFrame(cf.cols, g.columns, 8, false);
    $("#ledInfo").textContent = `Cyklus ${simState.cycleId}, krok ${idx + 1}/${steps.length} (${cf.duration} s)`;
    if (simState.playing) {
      st.animTimer = setTimeout(() => {
        simState.stepIdx = (idx + 1) % steps.length;
        preview();
      }, Math.max(200, (cf.duration || 2) * 1000));
    }
    return;
  }

  const value = currentValue();
  const lineVal = st.section === "lines" ? null : (st.previewLine !== "" ? (st.project.data.lines || {})[st.previewLine] : null);
  let res;
  try { res = await api("/api/preview", { section: st.section, value, line: lineVal ?? null }); }
  catch (e) { $("#ledInfo").textContent = e.message; return; }

  const base = () => new Array(g.columns).fill(0);
  const withLine = (f) => {
    if (st.section !== "lines" && !isSpecialSec()) {
      for (let x = g.line_left; x <= g.line_right; x++) f[x] = 0;
      if (res.line) {
        const w = g.line_right - g.line_left + 1;
        blit(f, res.line, g.line_left + Math.max(0, Math.floor((w - res.line.width) / 2)), g.line_left, g.line_right);
      }
    }
    return f;
  };

  const d = res.dest;
  if ($("#edMnemoText") && document.activeElement !== $("#edMnemoText") && res.mnemo !== undefined && !$("#edMnemoChk").checked) {
    $("#edMnemoText").value = res.mnemo;
  }

  let info = "";
  const frames = res.frames || [];
  if (d && (d.run || d.effect === 1)) {
    // the server gives the text window (lo..hi); older answers without it -> the destination field
    if (!Number.isFinite(d.lo) || !Number.isFinite(d.hi)) { d.lo = g.dest_left; d.hi = g.dest_right; }
    const span = d.hi - d.lo + 1, period = d.width + span;
    let off = 0, last = performance.now();
    const tick = (t) => {
      const step = g.scroll_ms || 20;
      if (t - last >= step) {
        off = (off + Math.max(1, Math.floor((t - last) / step))) % period;
        last = t;
        const f = base();
        blit(f, d, d.lo + span - off, d.lo, d.hi);
        drawFrame(withLine(f), g.columns, 8, false);
      }
      st.anim = requestAnimationFrame(tick);
    };
    st.anim = requestAnimationFrame(tick);
    info = `běžící text (t1), šířka ${d.width} sl.`;
  } else if (d && (d.effect === 2 || d.effect === 3)) {
    const target = d.start, span = d.hi - d.lo + 1;
    let curX = d.effect === 2 ? d.hi + 1 : d.lo - d.width;
    let last = performance.now();
    const tick = (t) => {
      if (t - last >= 18) {
        last = t;
        if (d.effect === 2) curX = curX > target ? curX - 2 : d.hi + 1;
        else curX = curX < target ? curX + 2 : d.lo - d.width;
        const f = base();
        blit(f, d, curX, d.lo, d.hi);
        drawFrame(withLine(f), g.columns, 8, false);
      }
      st.anim = requestAnimationFrame(tick);
    };
    st.anim = requestAnimationFrame(tick);
    info = `efekt t${d.effect}, šířka ${d.width} sl. (${span} okno)`;
  } else if (d && d.blink) {
    let on = true;
    const toggle = () => {
      const f = base();
      if (on) blit(f, d, d.start, d.lo, d.hi);
      drawFrame(withLine(f), g.columns, 8, false);
      on = !on;
      st.animTimer = setTimeout(toggle, 500);
    };
    toggle();
    info = `blikání (b), šířka ${d.width} sl.`;
  } else if (frames.length > 1) {
    let fi = 0;
    const cyclePage = () => {
      const fr = frames[fi % frames.length];
      const f = base();
      blit(f, fr, fr.start, fr.lo, fr.hi);
      drawFrame(withLine(f), g.columns, 8, false);
      st.animTimer = setTimeout(() => { fi = (fi + 1) % frames.length; cyclePage(); }, Math.max(250, (fr.seconds || 1) * 1000));
    };
    cyclePage();
    info = `${frames.length} stránek`;
  } else {
    const f = base();
    if (d) blit(f, d, d.start, d.lo, d.hi);
    drawFrame(withLine(f), g.columns, 8, false);
    if (d) info = `šířka ${d.width} sl., začátek ${d.start}, viditelné ${d.lo}–${d.hi}` + (d.overflow ? " — ⚠ nevejde se, panel text ořízne" : "");
  }
  $("#ledInfo").textContent = info;
  $("#edBytes").textContent = d ? d.bytes : "";
  const probs = res.problems || [];
  $("#edProblems").innerHTML = probs.map((p) => `<div>${esc(p)}</div>`).join("") +
    (d && d.overflow ? `<div class="warning">Text se nevejde – zkraťte ho, zmenšete mezeru/font nebo zapněte běžící text.</div>` : "");
}

// ---- simulation bar
$("#simMode").onchange = () => { simState.mode = $("#simMode").value; simState.playing = false; preview(); };
$("#simCykId").onchange = () => { simState.cycleId = $("#simCykId").value; simState.stepIdx = 0; preview(); };
$("#simStepPrev").onclick = () => {
  const n = ((st.project?.data?.cycles || {})[simState.cycleId] || []).length || 1;
  simState.stepIdx = (simState.stepIdx - 1 + n) % n;
  simState.mode = "cycle"; $("#simMode").value = "cycle"; preview();
};
$("#simStepNext").onclick = () => {
  const n = ((st.project?.data?.cycles || {})[simState.cycleId] || []).length || 1;
  simState.stepIdx = (simState.stepIdx + 1) % n;
  simState.mode = "cycle"; $("#simMode").value = "cycle"; preview();
};
$("#simStepPlay").onclick = () => {
  simState.mode = "cycle"; $("#simMode").value = "cycle";
  simState.playing = !simState.playing;
  $("#simStepPlay").textContent = simState.playing ? "⏸ Pauza" : "▶ Přehrát";
  preview();
};
["#simFree", "#simTime", "#simZone"].forEach((s) => $(s).oninput = () => { if (simState.mode === "cycle") preview(); });
$("#sim0AnimToggle").onclick = () => {
  simState.paused0 = !simState.paused0;
  $("#sim0AnimToggle").textContent = simState.paused0 ? "▶ Spustit střídání" : "⏸ Pozastavit střídání";
  preview();
};
