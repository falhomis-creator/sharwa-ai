// P4 Task 6b: receive the platform's single-sign-on token by postMessage.
// Sharwa opens this console with window.open and keeps a handle on it.
// 1) We tell window.opener we are ready. READY carries NO data, so it may go to "*":
//    the opener is a merchant's own store subdomain, unknown in advance (no Referer).
// 2) We accept ONE token, only when ALL hold: the sender is window.opener, its origin
//    matches CONSOLE_SSO_PLATFORM_ORIGINS (exact, or one label under a configured
//    parent domain), and the payload is a JWT-shaped string. It then goes to
//    sessionStorage through setToken, exactly like the paste login.
export const READY = "sharwa-console-ready";
export const TOKEN = "sharwa-console-token";
export const MAX_TOKEN_LEN = 8192;
const PATTERN_RES = [
  /^https:\/\/(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)+(:[0-9]{1,5})?$/,
  /^http:\/\/(localhost|127\.0\.0\.1|\*\.localhost)(:[0-9]{1,5})?$/,
];
const LABEL = /^[a-z0-9-]{1,63}$/;
const JWT_SHAPE = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;

export function isValidPattern(p) {
  return typeof p === "string" && PATTERN_RES.some((re) => re.test(p));
}

export function originAllowed(origin, patterns) {
  if (typeof origin !== "string" || !Array.isArray(patterns)) return false;
  for (const p of patterns) {
    if (!isValidPattern(p)) continue;
    if (!p.includes("*")) { if (origin === p) return true; continue; }
    const [scheme, parent] = p.split("://*.");
    const prefix = `${scheme}://`;
    const suffix = `.${parent}`;
    if (!origin.startsWith(prefix) || !origin.endsWith(suffix)) continue;
    if (LABEL.test(origin.slice(prefix.length, origin.length - suffix.length))) return true;
  }
  return false;
}

export function acceptTokenMessage(ev, { patterns, opener }) {
  if (!ev || !opener || ev.source !== opener) return null;
  if (!originAllowed(ev.origin, patterns)) return null;
  const d = ev.data;
  if (!d || typeof d !== "object" || d.type !== TOKEN) return null;
  const t = d.token;
  if (typeof t !== "string" || t.length > MAX_TOKEN_LEN || !JWT_SHAPE.test(t)) return null;
  return t;
}

export function startSsoHandoff({ win, patterns, onToken }) {
  const usable = Array.isArray(patterns) ? patterns.filter(isValidPattern) : [];
  const opener = win && win.opener;
  if (!usable.length || !opener) return false;
  const listener = (ev) => {
    const t = acceptTokenMessage(ev, { patterns: usable, opener });
    if (!t) return;
    win.removeEventListener("message", listener);
    onToken(t);
  };
  win.addEventListener("message", listener);
  opener.postMessage({ type: READY }, "*");
  return true;
}
