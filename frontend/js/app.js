import { api, ApiError, claims, getToken, setToken } from "./api.js";
import { h, clear, num, pct, when } from "./dom.js";
import { barChart, hBars } from "./charts.js";

const $app = document.getElementById("app");

// ---- Arabic copy for machine codes (the API never sends free text) ----------------
const PREFLIGHT_AR = {
  template_not_registered: "قالب التذكير غير مسجَّل في النظام",
  footer_not_explicit: "تذييل الإيقاف لم يُضبط صراحةً (MARKETING_FOOTER_AR) في بيئة الـAPI",
  no_connected_channel: "لا توجد قناة واتساب متصلة",
  number_not_healthy: "حالة الرقم غير سليمة",
  warmup_not_started: "لم يبدأ إحماء الرقم",
  warmup_too_young: "عمر الإحماء أقل من الحدّ الأدنى",
  global_switch_off: "المفتاح العام للتسويق مُطفأ",
  no_eligible_subscribers: "لا يوجد مشتركون مؤهَّلون (موافقة صريحة بالرسالة)",
};
// Positive phrasing for the checklist (a tick means the requirement is MET).
const CHECK_AR = {
  template_not_registered: "قالب التذكير مسجَّل في النظام",
  footer_not_explicit: "تذييل الإيقاف مضبوط صراحةً في بيئة الـAPI",
  no_connected_channel: "قناة واتساب متصلة",
  number_not_healthy: "حالة الرقم سليمة",
  warmup_not_started: "إحماء الرقم بدأ",
  warmup_too_young: "عمر الإحماء بلغ الحدّ الأدنى",
  global_switch_off: "المفتاح العام للتسويق يعمل",
  no_eligible_subscribers: "يوجد مشتركون مؤهَّلون (موافقة صريحة بالرسالة)",
};
const ERR_AR = {
  UNAUTHENTICATED: "الرمز غير صالح أو منتهٍ. سجّل الدخول من جديد.",
  TOKEN_EXPIRED: "انتهت صلاحية الرمز. سجّل الدخول من جديد.",
  FORBIDDEN_ROLE: "هذا الإجراء مخصَّص لمدير المنصّة.",
  TENANT_SUSPENDED: "المستأجر موقوف.",
  NOT_FOUND: "غير موجود.",
  VALIDATION_FAILED: "مدخلات غير صالحة.",
  PRECONDITION_FAILED: "لم تتحقق شروط التفعيل.",
  RATE_LIMITED: "طلبات كثيرة، حاول بعد قليل.",
  NETWORK: "تعذّر الاتصال بالخادم.",
  INTERNAL: "خطأ داخلي. حاول لاحقاً.",
};
const ACTION_AR = { enable: "تفعيل", disable: "إيقاف", cap_change: "تغيير السقف",
  "marketing.enable": "تفعيل", "marketing.disable": "إيقاف", "marketing.set_cap": "تغيير السقف",
  "marketing.enable_refused": "محاولة تفعيل مرفوضة" };
const STATUS_AR = { active: "نشط", suspended: "موقوف" };
const HEALTH_AR = { healthy: "سليم", throttled: "مخفَّض", paused: "متوقف", banned: "محظور" };
const CH_AR = { connected: "متصل", disconnected: "منقطع", reconnecting: "يعيد الاتصال", qr_pending: "بانتظار QR",
  unknown: "غير معروف", logged_out: "خرج", conflict: "تعارض", banned: "محظور" };
const SWITCH_AR = { on: "يعمل", degraded: "مخفَّض", off: "مُطفأ" };
const errText = (e) => ERR_AR[e.code] || ERR_AR.INTERNAL;

// ---- shell -----------------------------------------------------------------------
const role = () => (claims() || {}).role;
const isAdmin = () => role() === "platform_admin";

function shell(active, ...content) {
  const c = claims() || {};
  const tabs = isAdmin()
    ? [["#/", "المستأجرون"], ["#/metrics", "المؤشرات"]]
    : [["#/", "حالة التسويق"], ["#/metrics", "المؤشرات"]];
  clear($app).append(h("div", { class: "wrap" },
    h("header", { class: "top" }, h("h1", {}, "لوحة شروة AI"),
      h("span", { class: "who" }, `${isAdmin() ? "مدير المنصّة" : "مدير المتجر"} · ${c.sub || ""}`),
      h("button", { onclick: () => { setToken(null); location.hash = "#/"; render(); } }, "خروج")),
    h("nav", { class: "tabs" }, tabs.map(([href, label]) =>
      h("a", { href, "aria-current": active === href ? "page" : null }, label))),
    ...content));
}

function dialog(title, body, { confirmLabel, danger = false, onSubmit }) {
  const err = h("div", { class: "err", role: "alert" });
  const ok = h("button", { class: danger ? "danger" : "primary", type: "submit" }, confirmLabel);
  const dlg = h("dialog", {}, h("form", { method: "dialog" },
    h("h3", {}, title), body, err,
    h("div", { class: "row" }, ok, h("button", { type: "button", onclick: () => dlg.close() }, "إلغاء"))));
  dlg.addEventListener("close", () => dlg.remove());
  dlg.querySelector("form").addEventListener("submit", async (ev) => {
    ev.preventDefault(); ok.disabled = true; err.textContent = "";
    try { await onSubmit(); dlg.close(); }
    catch (e) {
      if (e instanceof ApiError && e.code === "PRECONDITION_FAILED" && e.details?.failed) {
        err.textContent = "لم تتحقق الشروط: " + e.details.failed.map((c) => PREFLIGHT_AR[c] || c).join(" · ");
      } else if (e instanceof ApiError && e.code === "VALIDATION_FAILED" && e.details?.field === "confirm") {
        err.textContent = "عبارة التأكيد غير مطابقة.";
      } else err.textContent = e instanceof ApiError ? errText(e) : ERR_AR.INTERNAL;
      ok.disabled = false;
      if (e instanceof ApiError && e.status === 401) render();
    }
  });
  document.body.append(dlg); dlg.showModal();
  return dlg;
}

const field = (label, input) => [h("label", { class: "f" }, label), input];
const tile = (label, value, sub, small = false) => h("div", { class: "card tile" },
  h("div", { class: "label" }, label), h("div", { class: small ? "num sm" : "num" }, value), sub ? h("div", { class: "sub" }, sub) : null);
const ratio = (a, b) => h("span", { class: "ltr-iso" }, `${num(a)} / ${num(b)}`);
const badgeEnabled = (a) => a.enabled
  ? h("span", { class: "badge on" }, "● مُفعَّل")
  : h("span", { class: "badge off" }, "○ مُطفأ");

// ---- login -----------------------------------------------------------------------
function loginView(msg) {
  const ta = h("textarea", { placeholder: "eyJhbGciOi…", autocomplete: "off", spellcheck: "false", "aria-label": "رمز الدخول" });
  const err = h("div", { class: "err", role: "alert" }, msg || "");
  const go = async () => {
    const t = ta.value.trim().replace(/^Bearer\s+/i, "");
    if (!t) return;
    setToken(t);
    try {
      await api("GET", isAdmin() ? "/v1/admin/marketing/tenants" : "/v1/marketing/overview");
      location.hash = "#/"; render();
    } catch (e) { setToken(null); err.textContent = errText(e); }
  };
  clear($app).append(h("div", { class: "wrap" }, h("div", { class: "card login" },
    h("h1", {}, "لوحة شروة AI"),
    h("p", { class: "muted" }, "الصق رمز الدخول (JWT) الصادر من المنصّة. يُحفَظ في هذا التبويب فقط ويُمحى عند إغلاقه، ولا يُرسَل إلا إلى هذا الخادم."),
    ta, err, h("div", { class: "row" }, h("button", { class: "primary", onclick: go }, "دخول")))));
}

// ---- superadmin: tenants table ----------------------------------------------------
async function tenantsView() {
  shell("#/", h("div", { class: "skeleton" }, "جارٍ التحميل…"));
  const d = await api("GET", "/v1/admin/marketing/tenants");
  const sw = d.global_marketing_switch;
  const banner = h("div", { class: `banner ${sw === "off" ? "alert" : sw === "on" ? "ok" : "info"}` },
    h("b", {}, `المفتاح العام للتسويق: ${SWITCH_AR[sw] || sw}`),
    h("span", { class: "grow" }, sw === "off" ? "كل رسائل التسويق متوقفة لكل المستأجرين." : "يسري على كل المستأجرين (المستوى ١ للتراجع)."),
    h("button", { class: sw === "off" ? "primary" : "danger", onclick: () => globalSwitchDialog(sw) },
      sw === "off" ? "إعادة التشغيل" : "إيقاف عام فوري"));
  const t = d.totals;
  const rows = d.tenants.map((r) => h("tr", { class: "click", tabindex: "0",
    onclick: () => { location.hash = `#/tenant/${encodeURIComponent(r.ref)}`; },
    onkeydown: (e) => { if (e.key === "Enter") location.hash = `#/tenant/${encodeURIComponent(r.ref)}`; } },
    h("td", {}, h("b", {}, r.name), h("div", { class: "mono muted" }, r.ref)),
    h("td", {}, r.status === "active" ? STATUS_AR.active : h("span", { class: "badge bad" }, "موقوف")),
    h("td", {}, badgeEnabled(r.activation)),
    h("td", {}, ratio(r.activation.sent_24h, r.activation.canary_cap_per_day)),
    h("td", {}, num(r.open_carts)), h("td", {}, num(r.eligible_subscribers)),
    h("td", {}, ratio(r.channels.connected, r.channels.total))));
  shell("#/", banner,
    h("div", { class: "tiles" },
      tile("المستأجرون", num(t.tenants)), tile("مُفعَّل لهم التسويق", num(t.enabled)),
      tile("أُرسل آخر 24 ساعة", num(t.sent_24h)), tile("سلال مفتوحة", num(t.open_carts))),
    h("h2", {}, "المستأجرون"),
    h("div", { class: "card table-scroll" }, rows.length ? h("table", {},
      h("thead", {}, h("tr", {}, ["المتجر", "الحالة", "التسويق", "24س / السقف", "سلال مفتوحة", "مشتركون مؤهَّلون", "قنوات متصلة"].map((x) => h("th", {}, x)))),
      h("tbody", {}, rows)) : h("div", { class: "empty" }, "لا مستأجرين بعد.")),
    h("p", { class: "muted" }, "الأرقام عدّادات فقط؛ لا تظهر هواتف ولا نصوص عملاء في أي شاشة."));
}

function globalSwitchDialog(current) {
  const next = current === "off" ? "on" : "off";
  const reason = h("input", { type: "text", maxlength: "200", "aria-label": "السبب" });
  dialog(next === "off" ? "إيقاف التسويق عن كل المستأجرين" : "إعادة تشغيل التسويق عالمياً",
    [h("p", {}, next === "off" ? "تُسقَط كل رسائل التسويق فور وصولها لبوّابة الإرسال، لكل المتاجر." : "يعود التسويق للمتاجر المُفعَّلة فقط؛ لا يُفعَّل أحد."),
      ...field("السبب (إلزامي)", reason)],
    { confirmLabel: next === "off" ? "أوقف الآن" : "أعد التشغيل", danger: next === "off",
      onSubmit: async () => {
        if (!reason.value.trim()) throw new ApiError(422, "VALIDATION_FAILED");
        const sub = (claims() || {}).sub;
        await fetch("/v1/admin/kill-switches/global/marketing", { method: "PUT",
          headers: { Authorization: `Bearer ${getToken()}`, "Content-Type": "application/json" },
          body: JSON.stringify({ scope: "tenant", state: next, reason: `${reason.value.trim()} (${sub})` }) })
          .then((r) => { if (!r.ok) throw new ApiError(r.status, r.status === 403 ? "FORBIDDEN_ROLE" : "INTERNAL"); });
        render();
      } });
}

// ---- superadmin: one tenant ----------------------------------------------------------
async function tenantView(ref) {
  shell("#/", h("div", { class: "skeleton" }, "جارٍ التحميل…"));
  const d = await api("GET", `/v1/admin/marketing/tenants/${encodeURIComponent(ref)}`);
  const a = d.activation;
  const failed = new Set(d.preflight_failed);
  const checks = Object.keys(CHECK_AR).map((code) => h("li", { class: failed.has(code) ? "no" : "ok" },
    h("span", { class: "ic" }, failed.has(code) ? "✕" : "✓"), CHECK_AR[code]));
  const actions = h("div", { class: "row" },
    a.enabled ? h("button", { class: "danger", onclick: () => disableDialog(d) }, "إيقاف التسويق")
              : h("button", { class: "primary", onclick: () => enableDialog(d) }, "تفعيل التسويق…"),
    h("button", { onclick: () => capDialog(d) }, "تغيير سقف الكاناري"),
    h("a", { class: "btn", href: "#/" }, "رجوع"));
  shell("#/",
    h("div", { class: "row" }, h("h2", { class: "grow" }, d.name || ref), badgeEnabled(a)),
    h("div", { class: "mono muted" }, d.ref),
    h("div", { class: "tiles" },
      tile("أُرسل آخر 24 ساعة", ratio(a.sent_24h, a.canary_cap_per_day), `متبقٍّ ${num(a.remaining_24h)}`),
      tile("مشتركون مؤهَّلون", num(d.eligible_subscribers)),
      tile("سلال مفتوحة", num(d.cart_preview.open_carts), `${num(d.cart_preview.eligible)} لمشتركين مؤهَّلين`),
      tile("آخر تفعيل", a.enabled_at ? when(a.enabled_at) : "—", a.enabled_by || "", true)),
    h("div", { class: "card" }, actions),
    h("div", { class: "cols" },
      h("div", {}, h("h2", {}, "شروط التفعيل (الفحص التمهيدي)"), h("div", { class: "card" }, h("ul", { class: "chk" }, checks))),
      h("div", {}, h("h2", {}, "القنوات"), h("div", { class: "card table-scroll" }, d.channel_list.length ? h("table", {},
        h("thead", {}, h("tr", {}, ["الاتصال", "صحة الرقم", "يوم الإحماء"].map((x) => h("th", {}, x)))),
        h("tbody", {}, d.channel_list.map((c) => h("tr", {}, h("td", {}, CH_AR[c.status] || c.status),
          h("td", {}, HEALTH_AR[c.health_state] || c.health_state || "—"), h("td", {}, num(c.warmup_day)))))) : h("div", { class: "empty" }, "لا قنوات")))),
    h("h2", {}, "معاينة الرسالة (بيانات اصطناعية)"),
    h("div", { class: "card" }, d.sample_text ? h("div", { class: "sample" }, d.sample_text) : h("div", { class: "muted" }, "القالب غير مسجَّل."),
      h("p", { class: "muted" }, "النصّ والتذييل مقترحان حتى يعتمدهما المالك (OQ-P3-15).")),
    h("h2", {}, "سجلّ التفعيل (إلحاقي لا يُعدَّل)"),
    h("div", { class: "card table-scroll" }, d.history.length ? h("table", {},
      h("thead", {}, h("tr", {}, ["الإجراء", "بواسطة", "السبب", "الوقت"].map((x) => h("th", {}, x)))),
      h("tbody", {}, d.history.map((x) => h("tr", {}, h("td", {}, ACTION_AR[x.action] || x.action),
        h("td", { class: "mono" }, x.actor), h("td", {}, x.reason || "—"), h("td", {}, when(x.at)))))) : h("div", { class: "empty" }, "لا أحداث بعد — الحالة الافتراضية: مُطفأ.")),
    h("h2", {}, "سجلّ التدقيق"),
    h("div", { class: "card table-scroll" }, d.audit.length ? h("table", {},
      h("tbody", {}, d.audit.map((x) => h("tr", {}, h("td", {}, ACTION_AR[x.action] || x.action),
        h("td", { class: "mono" }, `${x.actor} (${x.role})`), h("td", {}, when(x.at)))))) : h("div", { class: "empty" }, "لا سجلات.")));
}

function enableDialog(d) {
  const cap = h("input", { type: "number", min: "1", max: "500", value: String(d.activation.canary_cap_per_day), class: "ltr" });
  const reason = h("input", { type: "text", maxlength: "200" });
  const confirm = h("input", { type: "text", class: "ltr", autocomplete: "off", spellcheck: "false" });
  const failed = d.preflight_failed;
  dialog(`تفعيل التسويق — ${d.name || d.ref}`,
    [h("p", {}, "سيبدأ النظام بإرسال تذكيرات السلال المتروكة لمشتركين وافقوا صراحةً، ضمن سقف الكاناري اليومي، خارج الساعات الهادئة."),
      failed.length ? h("div", { class: "banner alert" }, "الفحص التمهيدي يفشل الآن: " + failed.map((c) => PREFLIGHT_AR[c] || c).join(" · ")) : null,
      ...field("سقف الكاناري اليومي (1–500)", cap),
      ...field("السبب (إلزامي)", reason),
      ...field("للتأكيد اكتب حرفياً:", h("div", { class: "mono sample" }, d.confirm_phrase)),
      confirm],
    { confirmLabel: "فعّل التسويق", onSubmit: async () => {
      await api("POST", `/v1/admin/marketing/tenants/${encodeURIComponent(d.ref)}/enable`,
        { cap: Number(cap.value), reason: reason.value.trim(), confirm: confirm.value });
      render();
    } });
}
function disableDialog(d) {
  const reason = h("input", { type: "text", maxlength: "200" });
  dialog(`إيقاف التسويق — ${d.name || d.ref}`,
    [h("p", {}, "يُسقَط طابور التسويق المعلّق، وتُعاد الفتحات المحجوزة، وتُجمَّد مهام تذكير السلال. رسائل الخدمة والمرافِقة للطلبات لا تتأثر."),
      ...field("السبب (إلزامي)", reason)],
    { confirmLabel: "أوقف التسويق", danger: true, onSubmit: async () => {
      await api("POST", `/v1/admin/marketing/tenants/${encodeURIComponent(d.ref)}/disable`, { reason: reason.value.trim() });
      render();
    } });
}
function capDialog(d) {
  const cap = h("input", { type: "number", min: "1", max: "500", value: String(d.activation.canary_cap_per_day), class: "ltr" });
  const reason = h("input", { type: "text", maxlength: "200" });
  dialog("سقف الكاناري اليومي",
    [h("p", {}, "عدد رسائل التسويق الأقصى كل 24 ساعة متحرّكة. ما زاد يُؤجَّل ولا يُسقَط. تغيير السقف لا يُفعِّل أحداً."),
      ...field("السقف (1–500)", cap), ...field("السبب (إلزامي)", reason)],
    { confirmLabel: "احفظ", onSubmit: async () => {
      await api("POST", `/v1/admin/marketing/tenants/${encodeURIComponent(d.ref)}/set-cap`, { cap: Number(cap.value), reason: reason.value.trim() });
      render();
    } });
}

// ---- merchant: own status ---------------------------------------------------------------
async function overviewView() {
  shell("#/", h("div", { class: "skeleton" }, "جارٍ التحميل…"));
  const d = await api("GET", "/v1/marketing/overview");
  const a = d.activation;
  shell("#/",
    h("div", { class: "row" }, h("h2", { class: "grow" }, d.name || "متجري"), badgeEnabled(a)),
    h("div", { class: "tiles" },
      tile("أُرسل آخر 24 ساعة", ratio(a.sent_24h, a.canary_cap_per_day)),
      tile("مشتركون مؤهَّلون", num(d.eligible_subscribers)), tile("سلال مفتوحة", num(d.open_carts)),
      tile("قنوات متصلة", ratio(d.channels.connected, d.channels.total))),
    h("div", { class: "banner info" }, a.enabled ? "التسويق مُفعَّل لمتجرك ضمن سقف يومي." : "التسويق غير مُفعَّل لمتجرك. يُفعِّله مدير المنصّة بعد المراجعة."));
}

// ---- metrics (both roles) ----------------------------------------------------------------
async function metricsView(days = 7) {
  shell("#/metrics", h("div", { class: "skeleton" }, "جارٍ التحميل…"));
  const admin = isAdmin();
  const m = await api("GET", admin ? `/v1/admin/marketing/metrics?days=${days}` : `/v1/marketing/metrics?days=${days}`);
  const t = m.totals, c = m.carts;
  const sel = h("select", { "aria-label": "الفترة", onchange: () => { metricsView(Number(sel.value)).catch(handle); } },
    [7, 14, 30, 90].map((n) => h("option", { value: n, selected: n === days }, `آخر ${n} يوماً`)));
  const chart1 = h("div"), chart2 = h("div"), chart3 = h("div");
  shell("#/metrics",
    h("div", { class: "row" }, h("h2", { class: "grow" }, admin ? "مؤشرات المنصّة" : "مؤشرات متجري"), sel),
    h("div", { class: "tiles" },
      tile("رسائل تسويق مُرسَلة", num(t.sent_marketing), `${num(t.marketing_dropped_policy)} أُسقطت بالسياسة · ${num(t.marketing_pending)} معلّقة`),
      tile("سلال مُسترجَعة", num(c.recovered), `نسبة الاسترجاع ${pct(c.recovery_rate_pct)}`),
      tile("إيقافات (Opt-out)", num(t.optouts), `${num(t.optins)} اشتراك جديد`),
      tile("رسائل خدمة ومرافِقة", num(t.sent_utility + t.sent_service))),
    h("h2", {}, "الرسائل المُرسَلة يومياً"), h("div", { class: "card" }, chart1),
    h("div", { class: "cols" },
      h("div", {}, h("h2", {}, "الاشتراكات والإيقافات يومياً"), h("div", { class: "card" }, chart2)),
      h("div", {}, h("h2", {}, "حالات السلال"), h("div", { class: "card" }, chart3))),
    admin ? [h("h2", {}, "حسب المتجر"), h("div", { class: "card table-scroll" }, h("table", {},
      h("thead", {}, h("tr", {}, ["المتجر", "تسويق مُرسَل", "مُسترجَعة", "إيقافات"].map((x) => h("th", {}, x)))),
      h("tbody", {}, m.tenants.map((r) => h("tr", { class: "click", onclick: () => { location.hash = `#/tenant/${encodeURIComponent(r.ref)}`; } },
        h("td", {}, r.name), h("td", {}, num(r.totals.sent_marketing)), h("td", {}, num(r.carts.recovered)), h("td", {}, num(r.totals.optouts)))))))] : null);
  barChart(chart1, m.series, [
    { key: "marketing", label: "تسويق", cls: "seg1" }, { key: "utility", label: "مرافِقة", cls: "seg2" },
    { key: "service", label: "خدمة", cls: "seg3" }], { title: "الرسائل المُرسَلة يومياً" });
  barChart(chart2, m.series, [{ key: "optins", label: "اشتراك", cls: "seg3" }, { key: "optouts", label: "إيقاف", cls: "seg2" }],
    { stack: false, title: "الاشتراكات والإيقافات" });
  hBars(chart3, [["مفتوحة", c.open], ["مُسترجَعة", c.recovered], ["ذُكِّر بها", c.reminded], ["منتهية", c.expired], ["أُفرِغت", c.cleared]]
    .map(([label, value]) => ({ label, value })), { title: "حالات السلال" });
}

// ---- router ----------------------------------------------------------------------------------
function handle(e) {
  if (e instanceof ApiError && e.status === 401) { loginView(errText(e)); return; }
  clear($app).append(h("div", { class: "wrap" }, h("div", { class: "card" }, h("p", {}, e instanceof ApiError ? errText(e) : ERR_AR.INTERNAL),
    h("button", { onclick: () => { location.hash = "#/"; render(); } }, "رجوع"))));
}

async function render() {
  if (!getToken()) { loginView(); return; }
  const hash = location.hash || "#/";
  try {
    if (hash.startsWith("#/tenant/") && isAdmin()) await tenantView(decodeURIComponent(hash.slice(9)));
    else if (hash === "#/metrics") await metricsView();
    else if (isAdmin()) await tenantsView();
    else await overviewView();
  } catch (e) { handle(e); }
}
window.addEventListener("hashchange", render);
render();
