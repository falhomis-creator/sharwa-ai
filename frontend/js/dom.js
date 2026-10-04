// Safe DOM helpers: every piece of API data goes through textContent, never innerHTML
// (the dashboard renders tenant names and operator-typed reasons - they are DATA).
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = String(v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
export function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  for (const c of children.flat()) if (c) el.append(c);
  return el;
}
const nf = new Intl.NumberFormat("ar-u-nu-latn");
export const num = (n) => (n === null || n === undefined ? "—" : nf.format(n));
export const pct = (n) => (n === null || n === undefined ? "—" : `${nf.format(n)}%`);
export function when(iso) {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("ar-u-nu-latn", { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
}
export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
