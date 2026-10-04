// Dependency-free SVG bar charts: thin 4px-rounded data ends, hairline grid, a legend,
// a per-day hover tooltip (hit targets wider than the marks) and a table view
// (identity is never colour alone).
import { h, svg, num, clear } from "./dom.js";

const W = 640, H = 220, PL = 8, PR = 40, PT = 12, PB = 26;

function niceMax(v) {
  if (v <= 4) return 4;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 2.5, 5, 10]) if (v <= m * p) return m * p;
  return v;
}
function dayLabel(iso) { const [, m, d] = iso.split("-"); return `${+d}/${+m}`; }

export function barChart(host, rows, keys, { stack = true, title = "" } = {}) {
  clear(host);
  const total = (r) => (stack ? keys.reduce((a, k) => a + r[k.key], 0) : Math.max(...keys.map((k) => r[k.key])));
  const max = niceMax(Math.max(0, ...rows.map(total)));
  const iw = W - PL - PR, ih = H - PT - PB;
  const band = iw / Math.max(rows.length, 1);
  const bw = Math.max(3, Math.min(28, band * 0.6));
  const y = (v) => PT + ih - (v / max) * ih;

  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": title, preserveAspectRatio: "xMidYMid meet" });
  for (let i = 0; i <= 4; i++) {
    const v = (max / 4) * i, yy = y(v);
    root.append(svg("line", { class: i === 0 ? "ax" : "g", x1: PL, x2: W - PR, y1: yy, y2: yy }));
    const t = svg("text", { x: W - PR + 6, y: yy + 4 }); t.textContent = num(Math.round(v * 10) / 10); root.append(t);
  }
  const every = rows.length > 14 ? Math.ceil(rows.length / 10) : 1;
  const tip = h("div", { class: "tip", hidden: true });
  rows.forEach((r, i) => {
    // RTL: oldest on the right, newest on the left reads naturally in Arabic.
    const cx = W - PR - band * (i + 0.5);
    if (stack) {
      let acc = 0;
      keys.forEach((k, ki) => {
        const v = r[k.key]; if (!v) return;
        const y0 = y(acc), y1 = y(acc + v);
        const gap = acc > 0 ? 2 : 0;           // 2px surface gap between stacked fills
        root.append(svg("rect", { class: k.cls, x: cx - bw / 2, y: y1, width: bw,
          height: Math.max(1, y0 - y1 - gap), rx: ki === keys.length - 1 || acc + v === total(r) ? 4 : 0 }));
        acc += v;
      });
    } else {
      const gw = bw / keys.length;
      keys.forEach((k, ki) => {
        const v = r[k.key]; if (!v) return;
        root.append(svg("rect", { class: k.cls, x: cx - bw / 2 + ki * gw, y: y(v), width: Math.max(2, gw - 2),
          height: Math.max(1, y(0) - y(v)), rx: 3 }));
      });
    }
    if (i % every === 0 || i === rows.length - 1) {
      const t = svg("text", { x: cx, y: H - 8, "text-anchor": "middle" }); t.textContent = dayLabel(r.date); root.append(t);
    }
    const hit = svg("rect", { class: "hit", x: cx - band / 2, y: PT, width: band, height: ih });
    hit.addEventListener("mouseenter", () => {
      clear(tip).append(h("div", { class: "t" }, r.date),
        ...keys.map((k) => h("div", {}, `${k.label}: `, h("b", {}, num(r[k.key])))));
      tip.hidden = false;
      const hb = host.getBoundingClientRect(), rb = hit.getBoundingClientRect();
      tip.style.left = `${Math.max(0, Math.min(hb.width - 150, rb.left - hb.left - 40))}px`;
      tip.style.top = "4px";
    });
    hit.addEventListener("mouseleave", () => { tip.hidden = true; });
    root.append(hit);
  });

  const legend = h("div", { class: "legend" },
    keys.map((k) => h("span", {}, h("i", { class: k.cls }), k.label)));
  const table = h("details", { class: "tv" }, h("summary", {}, "عرض كجدول"),
    h("div", { class: "table-scroll" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "اليوم"), keys.map((k) => h("th", {}, k.label)))),
      h("tbody", {}, rows.map((r) => h("tr", {}, h("td", {}, r.date), keys.map((k) => h("td", {}, num(r[k.key])))))))));
  host.append(legend, h("div", { class: "chart" }, root, tip), table);
}

// Horizontal single-hue bars for a small categorical breakdown (cart statuses).
export function hBars(host, items, { title = "" } = {}) {
  clear(host);
  const max = Math.max(1, ...items.map((i) => i.value));
  const rowH = 30, w = 560, lw = 150;
  const root = svg("svg", { viewBox: `0 0 ${w} ${items.length * rowH + 6}`, role: "img", "aria-label": title });
  items.forEach((it, i) => {
    const yy = i * rowH + 4;
    const lab = svg("text", { x: w - 4, y: yy + 16, "text-anchor": "end" }); lab.textContent = it.label; root.append(lab);
    const full = w - lw - 60;
    root.append(svg("rect", { class: "seg1", x: w - lw - (it.value / max) * full, y: yy + 5, width: Math.max(2, (it.value / max) * full), height: 14, rx: 4 }));
    const v = svg("text", { x: w - lw - (it.value / max) * full - 6, y: yy + 17, "text-anchor": "end" }); v.textContent = num(it.value); root.append(v);
  });
  host.append(h("div", { class: "chart" }, root));
}
