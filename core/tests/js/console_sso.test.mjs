// P4 Task 6b: pure tests for frontend/js/sso.js (no browser, no npm).
// Run from the repo root: node --test --test-reporter=spec core/tests/js/console_sso.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { READY, TOKEN, MAX_TOKEN_LEN, isValidPattern, originAllowed, acceptTokenMessage, startSsoHandoff }
  from "../../../frontend/js/sso.js";

const JWT = "aaa.bbb.ccc";
const PATTERNS = ["https://*.sharwa.example", "https://sharwa.example"];
const fakeOpener = () => ({ posted: [], postMessage(msg, target) { this.posted.push([msg, target]); } });
const fakeWin = (opener) => ({
  opener, listeners: new Set(),
  addEventListener(t, f) { if (t === "message") this.listeners.add(f); },
  removeEventListener(t, f) { if (t === "message") this.listeners.delete(f); },
  emit(ev) { for (const f of [...this.listeners]) f(ev); },
});

test("pattern validation", () => {
  for (const ok of ["https://sharwa.example", "https://*.sharwa.example", "https://sharwa.example:8443",
                    "http://localhost:8000", "http://*.localhost:8000", "http://127.0.0.1"]) {
    assert.equal(isValidPattern(ok), true, ok);
  }
  for (const bad of ["*", "https://*", "https://*.com", "http://sharwa.example", "https://sharwa.example/",
                     "https://sharwa.example/x", "https://Sharwa.example", "https://a*.sharwa.example", "", null, 42]) {
    assert.equal(isValidPattern(bad), false, String(bad));
  }
});

test("exact origin matches only itself", () => {
  const p = ["https://sharwa.example"];
  assert.equal(originAllowed("https://sharwa.example", p), true);
  for (const o of ["https://sharwa.example:444", "http://sharwa.example", "https://x.sharwa.example", "null", undefined]) {
    assert.equal(originAllowed(o, p), false, String(o));
  }
});

test("wildcard allows exactly one label under the parent", () => {
  const p = ["https://*.sharwa.example"];
  assert.equal(originAllowed("https://store-1.sharwa.example", p), true);
  for (const o of ["https://a.b.sharwa.example", "https://evilsharwa.example", "https://sharwa.example",
                   "https://.sharwa.example", "http://store.sharwa.example", "https://store.sharwa.example:8443",
                   "https://store.sharwa.example.evil.test"]) {
    assert.equal(originAllowed(o, p), false, o);
  }
});

test("accepts a JWT-shaped token from the opener at an allowed origin", () => {
  const opener = fakeOpener();
  const ev = { source: opener, origin: "https://store.sharwa.example", data: { type: TOKEN, token: JWT } };
  assert.equal(acceptTokenMessage(ev, { patterns: PATTERNS, opener }), JWT);
});

test("rejects a disallowed origin", () => {
  const opener = fakeOpener();
  const ev = { source: opener, origin: "https://evil.test", data: { type: TOKEN, token: JWT } };
  assert.equal(acceptTokenMessage(ev, { patterns: PATTERNS, opener }), null);
});

test("rejects a sender that is not the opener", () => {
  const opener = fakeOpener();
  const ev = { source: {}, origin: "https://store.sharwa.example", data: { type: TOKEN, token: JWT } };
  assert.equal(acceptTokenMessage(ev, { patterns: PATTERNS, opener }), null);
  assert.equal(acceptTokenMessage({ ...ev, source: opener }, { patterns: PATTERNS, opener: null }), null);
});

test("rejects malformed payloads", () => {
  const opener = fakeOpener();
  for (const data of [null, "x", { type: READY, token: JWT }, { type: TOKEN }, { type: TOKEN, token: 42 },
                      { type: TOKEN, token: "not a jwt" }, { type: TOKEN, token: "a.b" },
                      { type: TOKEN, token: "a.b." + "c".repeat(MAX_TOKEN_LEN) }]) {
    const ev = { source: opener, origin: "https://store.sharwa.example", data };
    assert.equal(acceptTokenMessage(ev, { patterns: PATTERNS, opener }), null, JSON.stringify(data));
  }
});

test("hand-off does nothing without origins or opener", () => {
  const opener = fakeOpener();
  const never = () => assert.fail("onToken must not run");
  assert.equal(startSsoHandoff({ win: fakeWin(opener), patterns: [], onToken: never }), false);
  assert.equal(startSsoHandoff({ win: fakeWin(opener), patterns: ["*"], onToken: never }), false);
  assert.equal(startSsoHandoff({ win: fakeWin(null), patterns: PATTERNS, onToken: never }), false);
  assert.deepEqual(opener.posted, []);
});

test("hand-off sends a data-free READY and accepts exactly one token", () => {
  const opener = fakeOpener();
  const win = fakeWin(opener);
  const got = [];
  assert.equal(startSsoHandoff({ win, patterns: PATTERNS, onToken: (t) => got.push(t) }), true);
  assert.deepEqual(opener.posted, [[{ type: READY }, "*"]]);
  win.emit({ source: {}, origin: "https://store.sharwa.example", data: { type: TOKEN, token: "x.y.z" } });
  assert.equal(win.listeners.size, 1);
  win.emit({ source: opener, origin: "https://store.sharwa.example", data: { type: TOKEN, token: JWT } });
  win.emit({ source: opener, origin: "https://store.sharwa.example", data: { type: TOKEN, token: "d.e.f" } });
  assert.deepEqual(got, [JWT]);
  assert.equal(win.listeners.size, 0);
});
