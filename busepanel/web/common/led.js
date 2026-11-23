// LED panel renderer for frames in the server's preview format:
// {line: item|null, dest: item|null}, item = {cols, inv, width, start, lo, hi, run}
// interior LED panels are red (BS 120.0A: 625 nm, PID standards)
const RED = { on: "#ff3b2f", off: "#2a1514", glow: "#ff2a1a" };
const AMBER = { on: "#ffb02e", off: "#2a2118", glow: "#ff9d00" };

export function drawCols(canvas, cols, columns = 135, rows = 8, upper = null, color = RED) {
  // rows > 8: two-line panel, `upper` = columns of the upper line, `cols` = lower line
  const ctx = canvas.getContext("2d");
  const s = Math.max(2, Math.floor(canvas.width / columns)), r = s * 0.36;
  canvas.height = s * rows + 4;
  ctx.fillStyle = "#0b0907"; ctx.fillRect(0, 0, canvas.width, canvas.height);
  for (let x = 0; x < columns; x++) for (let y = 0; y < rows; y++) {
    const src = rows > 8 ? (y < 8 ? (upper || []) : cols) : cols, yy = rows > 8 ? y % 8 : y;
    const on = ((src[x] || 0) >> (7 - yy)) & 1;
    ctx.fillStyle = on ? color.on : color.off;
    ctx.shadowBlur = on ? s * 0.6 : 0; ctx.shadowColor = color.glow;
    ctx.beginPath(); ctx.arc(x * s + s / 2 + 2, y * s + s / 2 + 2, r, 0, 7); ctx.fill();
  }
  ctx.shadowBlur = 0;
}

// Exterior DOT-LED matrix (gBUSE0 emulator): bits[x] = column, bit y = row from the TOP.
// The dot size is the same for every panel width up to `ref` columns, so v1 … v4 keep their proportions.
export function drawMatrix(canvas, bits, columns, rows, ref = 140) {
  const ctx = canvas.getContext("2d");
  const s = Math.max(2, Math.floor(canvas.width / Math.max(columns, ref))), r = s * 0.38;
  canvas.height = s * rows + 4;
  ctx.fillStyle = "#0b0907"; ctx.fillRect(0, 0, canvas.width, canvas.height);
  for (let x = 0; x < columns; x++) for (let y = 0; y < rows; y++) {
    const on = ((bits[x] || 0) >> y) & 1;
    ctx.fillStyle = on ? "#ffb02e" : "#2a2118";
    ctx.shadowBlur = on ? s * 0.6 : 0; ctx.shadowColor = "#ff9d00";
    ctx.beginPath(); ctx.arc(x * s + s / 2 + 2, y * s + s / 2 + 2, r, 0, 7); ctx.fill();
  }
  ctx.shadowBlur = 0;
}

function blit(frame, item, start, lo, hi) {
  item.cols.forEach((v, i) => {
    const x = start + i;
    if (x >= lo && x <= hi) frame[x] = item.inv[i] ? (~v & 0xff) : v;
  });
}

export class LedView {
  constructor(canvas) { this.canvas = canvas; this.anim = null; this.key = null; }

  show(frame, g) {
    if (frame.matrix) {                    // exterior panel: the server sends the finished picture
      const key = JSON.stringify(frame.bits);
      if (key === this.key) return;
      this.key = key;
      if (this.anim) cancelAnimationFrame(this.anim), (this.anim = null);
      drawMatrix(this.canvas, frame.bits, g.columns, g.rows);
      return;
    }
    const key = JSON.stringify([frame.line && frame.line.bytes, frame.dest && frame.dest.bytes, frame.heading && frame.heading.bytes, frame.mode]);
    if (key === this.key) return;          // unchanged – keep the running animation
    this.key = key;
    if (this.anim) cancelAnimationFrame(this.anim), (this.anim = null);
    const compose = (off) => {
      const f = new Array(g.columns).fill(0);
      const d = frame.dest;
      if (d) {
        if (d.run) {
          const span = d.hi - d.lo + 1;
          blit(f, d, d.lo + span - (off % (d.width + span)), d.lo, d.hi);
        } else blit(f, d, d.start, d.lo, d.hi);
      }
      let upper = null;
      const two = (g.rows || 8) > 8;
      if (two) {
        // PID layout: line number + heading in the upper row, the text over the whole lower row
        upper = new Array(g.columns).fill(0);
        if (frame.heading) blit(upper, frame.heading, frame.heading.start, frame.heading.lo, frame.heading.hi);
      }
      if (frame.line) {
        const row = two ? upper : f;
        for (let x = g.line_left; x <= g.line_right; x++) row[x] = 0;
        blit(row, frame.line, frame.line.start, frame.line.lo, frame.line.hi);
      }
      drawCols(this.canvas, f, g.columns, g.rows || 8, upper);
    };
    if (frame.dest && frame.dest.run) {
      let off = 0, last = performance.now();
      const tick = (t) => {
        const step = g.scroll_ms || 20;
        if (t - last >= step) { off += Math.max(1, Math.floor((t - last) / step)); last = t; compose(off); }
        this.anim = requestAnimationFrame(tick);
      };
      this.anim = requestAnimationFrame(tick);
    } else compose(0);
  }
}
