// Thin fetch wrapper. The token lives in sessionStorage only (dies with the tab) and is
// sent as a Bearer header - never in a URL, never in localStorage. The server decides
// every permission; the role decoded below only chooses which screens to SHOW.
const KEY = "sharwa_console_token";
let memoryToken = null; // fallback if storage is blocked

export function getToken() {
  try { return sessionStorage.getItem(KEY) || memoryToken; } catch { return memoryToken; }
}
export function setToken(t) {
  memoryToken = t;
  try { t ? sessionStorage.setItem(KEY, t) : sessionStorage.removeItem(KEY); } catch { /* storage blocked */ }
}
export function claims() {
  const t = getToken();
  if (!t) return null;
  try {
    const part = t.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const json = new TextDecoder().decode(Uint8Array.from(atob(part), (c) => c.charCodeAt(0)));
    return JSON.parse(json);
  } catch { return null; }
}

export class ApiError extends Error {
  constructor(status, code, details) { super(code); this.status = status; this.code = code; this.details = details; }
}

export async function api(method, path, body) {
  const headers = { Authorization: `Bearer ${getToken() || ""}` };
  const init = { method, headers };
  if (body !== undefined) { headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(path, init); } catch { throw new ApiError(0, "NETWORK"); }
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    const e = (data && data.error) || {};
    if (res.status === 401) setToken(null);
    throw new ApiError(res.status, e.code || "INTERNAL", e.details);
  }
  return data;
}
