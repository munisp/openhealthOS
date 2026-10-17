/* Code-drawn SVG instruments — no chart library, no network, no build.
 * Every instrument renders from values alone, so it works inside the
 * precached offline shell forever. */

const NS = "http://www.w3.org/2000/svg";

function svg(tag, attrs = {}) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
}

/* Circular gauge (stroke-dasharray dial) with an optional safe band —
 * used for battery SOC, water level, cold-chain temperature. */
export function dial({ value, max = 100, band = null, unit = "%", size = 96 }) {
  const r = 40, cx = size / 2, cy = size / 2;
  const circ = 2 * Math.PI * r;
  const frac = Math.max(0, Math.min(1, value / max));
  const root = svg("svg", { viewBox: `0 0 ${size} ${size}` });

  root.append(svg("circle", {
    cx, cy, r, fill: "none", stroke: "var(--panel-2)", "stroke-width": 7,
  }));

  if (band) { // safe band arc, e.g. cold chain 2–8°C
    const a0 = (band[0] / max) * 2 * Math.PI - Math.PI / 2;
    const a1 = (band[1] / max) * 2 * Math.PI - Math.PI / 2;
    const p = (a) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
    const [x0, y0] = p(a0), [x1, y1] = p(a1);
    root.append(svg("path", {
      d: `M ${x0} ${y0} A ${r} ${r} 0 0 1 ${x1} ${y1}`,
      fill: "none", stroke: "var(--ok)", "stroke-width": 7, opacity: .35,
      "stroke-linecap": "round",
    }));
  }

  const cls = band
    ? (value >= band[0] && value <= band[1] ? "var(--ok)" : "var(--crit)")
    : frac > 0.5 ? "var(--ok)" : frac > 0.25 ? "var(--warn)" : "var(--crit)";
  const arc = svg("circle", {
    cx, cy, r, fill: "none", stroke: cls, "stroke-width": 7,
    "stroke-linecap": "round",
    "stroke-dasharray": `${circ}`,
    "stroke-dashoffset": `${circ * (1 - frac * 0.999)}`,
    transform: `rotate(-90 ${cx} ${cy})`,
  });
  arc.style.transition = "stroke-dashoffset .8s cubic-bezier(.22,1,.36,1)";
  root.append(arc);

  const txt = svg("text", {
    x: cx, y: cy + 5, "text-anchor": "middle",
    fill: "var(--text)", "font-size": 16, "font-weight": 600,
    "font-family": "var(--font-data)",
  });
  txt.textContent = `${Math.round(value * 10) / 10}${unit}`;
  root.append(txt);
  return root;
}

/* Sparkline — 24-bucket trend, hairline stroke, area fill at 8% accent. */
export function sparkline(values, { w = 220, h = 36, color = "var(--accent)" } = {}) {
  const root = svg("svg", { viewBox: `0 0 ${w} ${h}`, class: "spark", preserveAspectRatio: "none" });
  if (!values || values.length < 2) return root;
  const max = Math.max(...values, 1);
  const step = w / (values.length - 1);
  const pts = values.map((v, i) => [i * step, h - 3 - (v / max) * (h - 6)]);
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"} ${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const area = svg("path", { d: `${line} L ${w} ${h} L 0 ${h} Z`, fill: color, opacity: .08 });
  const path = svg("path", { d: line, fill: "none", stroke: color, "stroke-width": 1.5 });
  root.append(area, path);
  const [lx, ly] = pts[pts.length - 1];
  root.append(svg("circle", { cx: lx, cy: ly, r: 2.5, fill: color }));
  return root;
}

/* Stylized state map — an LGA-cell lattice with pulsing facility dots.
 * Deliberately schematic (a situation board, not GIS): position encodes
 * rough geography, colour encodes live status. */
export function facilityMap(facilities, statusOf) {
  const W = 100, H = 100;
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}` });

  // faint lattice — the "state plane" grid
  for (let x = 10; x < W; x += 10)
    root.append(svg("line", { x1: x, y1: 0, x2: x, y2: H, stroke: "var(--line)", "stroke-width": .25 }));
  for (let y = 10; y < H; y += 10)
    root.append(svg("line", { x1: 0, y1: y, x2: W, y2: y, stroke: "var(--line)", "stroke-width": .25 }));

  const colors = { ok: "var(--ok)", warn: "var(--warn)", crit: "var(--crit)", dark: "var(--faint)" };
  for (const f of facilities) {
    const st = statusOf(f);
    const g = svg("g", { class: "facility-dot", "data-facility": f.id });
    const halo = svg("circle", {
      class: "halo", cx: f.x, cy: f.y, r: 2.2,
      fill: "none", stroke: colors[st], "stroke-width": .6,
    });
    const dot = svg("circle", {
      cx: f.x, cy: f.y, r: f.kind === "hospital" ? 2.4 : 1.8,
      fill: colors[st],
    });
    const label = svg("text", {
      x: f.x + 3.2, y: f.y + 1, "font-size": 3.4,
      fill: "var(--muted)", "font-family": "var(--font-data)",
    });
    label.textContent = f.id;
    g.append(halo, dot, label);
    const title = svg("title", {});
    title.textContent = `${f.name} (${f.lga}) — ${st}`;
    g.append(title);
    root.append(g);
  }
  return root;
}

/* Horizontal bar (tracer-drug stock, claims mix). */
export function hbar(frac) {
  const wrap = document.createElement("div");
  wrap.className = "hbar";
  const fill = document.createElement("div");
  fill.className = "hbar-fill" + (frac < 0.25 ? " low" : frac < 0.6 ? " mid" : "");
  fill.style.width = `${Math.round(Math.max(0, Math.min(1, frac)) * 100)}%`;
  wrap.append(fill);
  return wrap;
}
