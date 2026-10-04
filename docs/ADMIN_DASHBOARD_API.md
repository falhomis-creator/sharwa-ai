# ADMIN_DASHBOARD_API — واجهات لوحة التسويق (P3.5 · H104)

الترويسة الإلزامية `Authorization: Bearer <JWT RS256>`. الطرق المُغيِّرة **POST** (قائمة CORS لا تتضمن PUT/DELETE). الأخطاء بالغلاف الآلي الوحيد `{"error":{"code","request_id","details?"}}`. كل الاستجابات عدّادات وحالات فقط (H20/H48).

## مدير المنصّة (`platform_admin` حصراً — غيره ⇒ 403 `FORBIDDEN_ROLE`)

| Method | Path | الجسم | الاستجابة |
|---|---|---|---|
| GET | `/v1/admin/marketing/tenants` | — | `{tenants:[{ref,name,status,timezone,activation,channels,open_carts,eligible_subscribers}],totals,global_marketing_switch}` |
| GET | `/v1/admin/marketing/tenants/{ref}` | — | الحالة + `channel_list` + `preflight_failed` + `cart_preview` + `confirm_phrase` + `sample_text` + `history` + `audit` |
| GET | `/v1/admin/marketing/metrics?days=1..90` | — | `{series,totals,carts,tenants:[…]}` مجمَّعة عبر المستأجرين |
| POST | `/v1/admin/marketing/tenants/{ref}/enable` | `{cap:int 1..500, reason, confirm}` | `{ref,enabled:true,canary_cap_per_day}` |
| POST | `/v1/admin/marketing/tenants/{ref}/disable` | `{reason}` | `{ref,enabled:false,outbox_dropped,slots_released,jobs_cancelled}` |
| POST | `/v1/admin/marketing/tenants/{ref}/set-cap` | `{cap:int 1..500, reason}` | `{ref,canary_cap_per_day}` (لا يُفعِّل أحداً) |

المفتاح العام: المسار القائم `PUT /v1/admin/kill-switches/global/marketing` (يستدعيه المتصفح من نفس الأصل).

أخطاء `enable`: `VALIDATION_FAILED` + `details.field="confirm"` (العبارة ≠ `ENABLE-MARKETING <ref>`) · `PRECONDITION_FAILED` (412) + `details.failed=[…]` من المفردات المغلقة `template_not_registered · footer_not_explicit · no_connected_channel · number_not_healthy · warmup_not_started · warmup_too_young · global_switch_off · no_eligible_subscribers` · `TENANT_SUSPENDED` · `NOT_FOUND`.

## التاجر (`merchant_admin` أو `platform_admin`) — قراءة فقط، مستأجر التوكن نفسه

| Method | Path | الاستجابة |
|---|---|---|
| GET | `/v1/marketing/overview` | `{name,activation,channels,open_carts,eligible_subscribers,channel_list,global_marketing_switch}` |
| GET | `/v1/marketing/metrics?days=1..90` | `{days,series,totals,carts}` |

## نموذج الأمان (H104)
- **مستأجر الهدف** في مسارات المدير يأتي من المسار `{ref}` (الاستثناء الوحيد والمقصود من H2)، مقيَّداً بـ`platform_admin`، ويُحلّ في الخادم عبر `app.resolve_tenant`. لا يمنح تجاوزاً: كل قراءة/كتابة تجري داخل `tenant_tx(target)` **تحت RLS** بدور `sharwa_app`. قائمة المستأجرين الوحيدة العابرة تمرّ بدالة `SECURITY DEFINER` واحدة (0018: `app.admin_list_tenants`، خمسة أعمدة معرِّفات فقط، تنفيذها لـ`sharwa_system` حصراً).
- **التفعيل** بنفس بوّابات الـCLI: عبارة حرفية + فحوص تمهيدية + سبب. الفاعل المسجَّل = `dashboard:<sub من JWT>` لا حقل جسم. كل تغيير يكتب `audit_log` و`marketing_activation_log` في نفس المعاملة؛ والمحاولة المرفوضة تُدقَّق ولا تُطبَّق.
- بيئة الـAPI يجب أن تحمل `MARKETING_FOOTER_AR` (و`MARKETING_MIN_WARMUP_DAYS` إن غُيِّر) كبيئة العمّال، وإلا يُرفَض التفعيل بـ`footer_not_explicit`.
- الواجهة تُقدَّم من `CONSOLE_STATIC_DIR` فقط إن ضُبط (افتراضياً لا شيء)، بترويسات: CSP صارمة · `X-Frame-Options: DENY` · `no-store` · `nosniff`.
