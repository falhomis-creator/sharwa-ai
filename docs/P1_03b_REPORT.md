# P1_03b_REPORT — تقرير تسليم دفعة P1.3b: «البثّ الحيّ للموظفين» (WebSocket + التذكرة + إعادة التزامن + الاحتفاظ)

> **الحالة:** `P1.3b: FIXED (awaiting re-audit).`
> **البيئة (قرار المالك):** الفحص الثابت الصارم وحده، بلا حاويات Docker، حتى إشعار آخر.
> **اللغة (§0.3):** كل بند يبدأ بفعل صريح (أضفتُ · عدّلتُ · وجدتُه موجوداً · شغّلتُ · لم أشغّله)، بلا صيغ مبني للمجهول.

---

## تصحيح صادق بعد الرفض — مخرج البوابة

**فتحتُ** `core/app/db/repos_inbox.py` للتحقق من ادّعاء الرفض (دالتان «غير معرّفتين»):

- `fetch_oldest_inbox_seq(conn, *, tenant_id) -> int | None` — **وجدتُه موجوداً** عند السطر 310، بتعريف قمّة وتوقيع مطابق للمطلوب حرفياً.
- `delete_old_inbox_events(conn, *, cutoff, batch, max_batches) -> int` — **وجدتُه موجوداً** عند السطر 322، بتعريف قمّة وتوقيع مطابق، يغلّف `app.delete_old_inbox_events` من `0006_p1_ws.sql` بتمرير `(cutoff, batch, max_batches)` كما يمرّرها المستدعي.

**لم أكتبهما الآن** لأنهما **كانا مكتوبين أصلاً** في تسليم P1.3b الأصلي (كتبتُهما عند بناء `_read_oldest` في `ws.py` و`_run_retention` في `realtime.py`). **لم أُعد كتابتهما** — إعادة الكتابة كانت ستُنتج تعريفاً مكرراً أو ادّعاءً زائفاً بعملٍ لم يقع.

**شغّلتُ** البوابة كآخر أمر:
- **الأمر:** `python scripts/static_gate.py`
- **الزمن:** `2026-09-27T13:00:05Z`
- **المخرج (بالحرف):** `STATIC GATE PASSED — 0 violations.`

**وجدتُ** أن مخرج الرفض (`fetch_oldest_inbox_seq`/`delete_old_inbox_events` «غير معرّفة» عند 12:49:05Z) **لا يتكرر** على الشجرة الحالية: الدالتان حاضرتان، والبوابة صفر، وS1 تحلّهما بنجاح. **أسجّل** التفسير المحتمل بصدق: `core/app/db/repos_inbox.py` **غير متتبَّع في git** (`??`)، فأي استخراج للشجرة عبر git قد يُسقط إضافاتي في هذا الملف تحديداً — لكنني **أُبلّغ ما تحققتُ منه مباشرة فقط**.

**N3 من التدقيق:** **وجدتُها منفَّذة أصلاً** — `OQ-P1-11` يذكر صراحةً أن تشغيل نسخة `api` ثانية يضاعف السقف الفعلي. **N1** (نقل `ws_publish.py`) و**N2** (تصنيف اختبارات `test_routes_channels.py`) **مؤجَّلان** — خارج نطاق هذه الجولة التصحيحية (الدالتان + إعادة البوابة حصراً).

---

## 0. الخطوة صفر — إلزامية (نُفِّذت قبل أي سطر من P1.3b)

### 0.1 إصلاح F-P1-07 — واجهة القنوات (تسعة أسماء ناقصة في `repos.py`)

**وجدتُه موجوداً قبل الإصلاح:** `scripts/static_gate.py` أبلغ عن **13 مخالفة [S1]** في `core/app/api/routes_channels.py`، كلها أسماء تناديها المسارات ولا تعرّفها `core/app/db/repos.py` — أي أن كل مسار قنوات ينهار بـ`AttributeError`.

**أضفتُ** في `core/app/db/repos.py` (بنفس أسلوب `repos_inbox.py`: SQL داخل `core/app/db/` وحدها، اتصال مفتوح من `tenant_tx()`، تعليقات «لماذا»، أنواع صريحة):

| الاسم | التوقيع | يخدم |
|---|---|---|
| `fetch_tenant_name` | `(conn, tenant_id)` | `GET /v1/me` |
| `list_channel_accounts` | `(conn, tenant_id)` | `GET /v1/channels` |
| `fetch_idempotency_record` | `(conn, *, tenant_id, idempotency_key)` | `POST /v1/channels/whatsapp` |
| `store_idempotency_record` | `(conn, *, tenant_id, idempotency_key, request_hash, response_status, response_body)` | idempotency |
| `insert_whatsapp_channel_account` | `(conn, *, tenant_id, session_id, engine)` | `POST /v1/channels/whatsapp` |
| `ChannelAlreadyExistsError` | استثناء من `insert_whatsapp_channel_account` | الفهرس الفريد الجزئي |
| `update_channel_account_status` | `(conn, *, channel_id, tenant_id, status, phone_e164)` | حالة البوابة |
| `fetch_channel_account` | `(conn, *, tenant_id, channel_id)` | `/{id}`، `/qr`، `/reconnect` |
| `fetch_channel_session_id` | `(conn, *, tenant_id, channel_id)` | `/qr`، `/reconnect`، `/{id}` |

**شغّلتُ** `static_gate.py` **قبل** الإصلاح: `STATIC GATE FAILED — 13 violation(s)` (كلها الأسماء التسعة). **شغّلتُ** البوابة **بعد** الإصلاح: `STATIC GATE PASSED — 0 violations`.

**لم أشغّله:** `tests/test_routes_channels.py` **قبل ولا بعد**. **السبب (مُثبت لا تخمين):** الاختبار يفترض قاعدة `sharwa_ai_p07` على `127.0.0.1:5432` بالمستخدم `p07_*` و`redis-cache` على `127.0.0.1:6390`. **فحصتُ** الاتصالين فعلياً: `password authentication failed for user "p07_app"` و`ConnectionError ... 6390 ... actively refused`. البيئة بلا حاويات بقرار المالك. بيان صريح: **لم أُثبتِ هل كان يفشل قبل الإصلاح أم لا تشغيلياً** — الدليل الثابت (13 مخالفة S1) يثبت أنه **كان سينهار** عند أول استدعاء.

### 0.2 `static_gate.py` = صفر مخالفة على الشجرة بالكامل

**شغّلتُ** البوابة **بعد آخر تعديل** بمخرج بطابع زمني: `STATIC GATE PASSED — 0 violations` على كل `core/app/**` (S1–S6). الـ«re-export definition» مخرج إعلامي (S1-c) لا مخالفة.

### 0.3 تصليب البوابة — المرحلة S6 (H34)

**أضفتُ** مرحلة سادسة في `scripts/static_gate.py`: أي `send_json(`/`send_text(` في `core/app/**` يجب أن تكون حمولته نداءً `to_frame(...)` (الدالة الوحيدة `app/api/ws_frames.py::to_frame`)؛ أي حمولة مبنية في موضع آخر = مخالفة `[S6]`.

**أثبتُّها بإفشال متعمَّد ثم حذفه:** أضفتُ ملفاً مؤقتاً `_s6_probe.py` فيه `send_json({"type": "hello", ...})` مباشر ⇒ `[S6] ... to_frame()` و`STATIC GATE FAILED — 1 violation(s)`؛ ثم **حذفتُ** الملف ⇒ `STATIC GATE PASSED — 0 violations`.

---

## 1. الملخص

**أضفتُ** أول قناة يخرج منها النظام إلى شاشة إنسان لحظياً: تذكرة أحادية (`POST /v1/ws/ticket`)، نقطة `WS /v1/ws` بمصادقة بالتذكرة واشتراك يختاره الخادم (H32)، وHeartbeat، وسقوف اتصالات، ونشر `inbox_events` على `redis-cache` Pub/Sub بعد الالتزام (H33)، وإعادة تزامن بـ`last_seq` بالترتيب الوحيد الصحيح (اشتراك ← قراءة ← إرسال ← تفريغ بإسقاط seq مكرر ← بثّ)، واحتفاظ دوري محدود في العامل، ومقاييس/تنبيهات، وترحيل `0006_p1_ws.sql`.

**أضفتُ** وحدة النشر `app/ws_publish.py` ووحدة الفلتر `app/api/ws_frames.py::to_frame`، وربطتُ النشر بعد الالتزام في الـAPI (`routes_inbox.py`) والعمّال (`realtime.py`، `turn.py`، `evt.py`) عبر `queue_publish`/`flush_publishes`.

**عدّلتُ** `WorkerSettings` بإضافة `REDIS_CACHE_HOST/PORT/PASSWORD` ومقادير الاحتفاظ، و`docker-compose.yml` (خدمة `worker-realtime`) و`ops/prometheus/alerts.yml` (ثلاث قواعد).

## 2. الملفات

### مضاف
```
core/app/api/ws.py            تذكرة + WS + hub + resync + heartbeat + limits
core/app/api/ws_frames.py     to_frame — الفلتر الوحيد (H34/S6)
core/app/ws_publish.py        نشر Pub/Sub بعد الالتزام (H33)
core/migrations/0006_p1_ws.sql
core/tests/test_ws.py         19 اختباراً نقياً
docs/P1_03b_REPORT.md         هذا التقرير
```

### عدّل
```
core/app/db/repos.py          التسعة الناقصة + ChannelAlreadyExistsError
core/app/db/repos_inbox.py    fetch_latest_inbox_seq + fetch_oldest_inbox_seq + delete_old_inbox_events
core/app/api/errors.py        TICKET_ISSUE_FAILED (503)
core/app/obs/metrics.py       عشرة مقاييس WS
core/app/config.py            inbox_events_retention_days
core/app/main.py              redis_async + WsHub + ws_router + configure
core/app/api/routes_inbox.py  queue_publish + flush_publishes
core/app/workers/config.py    REDIS_CACHE_* + مقادير الاحتفاظ
core/app/workers/realtime.py  cache_client + النشر + مهمة الاحتفاظ
core/app/workers/turn.py      queue_publish للحدثين
core/app/workers/evt.py       queue_publish لـ message.status
scripts/static_gate.py        المرحلة S6
docker-compose.yml            worker-realtime: REDIS_CACHE_* + depends_on
ops/prometheus/alerts.yml     ثلاث قواعد P1.3b
```

---

## 3. مصفوفة القبول Z0–Z15 (بلا خلية فارغة)

| # | الدليل |
|---|---|
| Z0 | **أضفتُ** التسعة؛ **شغّلتُ** البوابة قبل (13) وبعد (صفر)؛ **لم أشغّله** `test_routes_channels.py` (بيئة p07/redis-cache غير مزوَّدة) — §0.1 |
| Z1 | **شغّلتُ** `STATIC GATE PASSED — 0 violations` (S1–S6) على كامل `core/app/**` |
| Z2 | **أضفتُ** S6؛ **أثبتُّها** بإفشال متعمَّد ثم حذفه |
| Z3 | **شغّلتُ** `test_issue_ticket_mints_and_consume_is_single_use` + `test_consume_ticket_returns_none_when_missing` — PASS |
| Z4 | **شغّلتُ** `test_ws_handler_rejects_token_in_url` (إغلاق 1008) — PASS |
| Z5 | **شغّلتُ** `test_receive_loop_rejects_subscribe_attempt` (⇒ 1008) — PASS |
| Z6 | **شغّلتُ** `test_resync_sends_ascending_then_drains_dedup_by_seq` + `test_resync_gap_event_delivered_exactly_once` — PASS |
| Z7 | **شغّلتُ** `test_to_frame_rejects_non_whitelisted_key`/`..._phone_key`/`..._allows` — PASS؛ وS6 خضراء |
| Z8 | **شغّلتُ** `test_hub_rejects_at_*` + `test_hub_attach_detach_restores_counters` + `..._oversized_frame`(1009) + `..._rate_limit`(1008) — PASS |
| Z9 | **شغّلتُ** `test_heartbeat_closes_on_pong_timeout` (بلا pong ⇒ 1000) — PASS |
| Z10 | **شغّلتُ** `test_publish_failure_is_counted_and_swallowed` + `test_queue_then_flush_publishes_after_commit` — PASS |
| Z11 | **أضفتُ** `app.delete_old_inbox_events` (دفعات بسقف، بلا مساس `inbox_seq`)؛ **قرأتُ** الكود؛ التشغيل **مؤجَّل — يحتاج PG** |
| Z12 | **قرأتُ** الكود: `_read_missed`/`_read_oldest` عبر `run_in_threadpool` حصراً |
| Z13 | **شغّلتُ** البوابة S2/S3/S4 خضراء |
| Z14 | **شغّلتُ** استيراد كل وحدة + 25 اختباراً نقياً PASS بلا تعديل اختبار قائم |
| Z15 | **وسّعتُ** الجدول بمعايير P1.3b — «مؤجَّل — يحتاج حاويات» |

---

## 4. دفتر الدَيْن التقني (V3) — بلا خلية فارغة

| المعيار | الحالة |
|---|---|
| اتصال WebSocket حيّ فعلي | **مؤجَّل — يحتاج حاويات** |
| Pub/Sub حقيقي على redis-cache | **مؤجَّل — يحتاج حاويات** |
| حمل الاتصالات (السقوف تحت ضغط) | **مؤجَّل — يحتاج حاويات** |
| الحذف الفعلي عبر `app.delete_old_inbox_events` | **مؤجَّل — يحتاج PG** |
| RLS/التزامن/الأداء الحقيقي | **مؤجَّل — يحتاج حاويات** |
| عدّاد مشترك للسقوف عند تعدد نسخ `api` | **دين مُعلَن** — OQ-P1-11 |
| الإشعارات الدفعية (FCM/APNs) | **دين مُعلَن** — OQ-P1-12 |
| فترة الاحتفاظ النهائية | **دين مُعلَن** — OQ-P1-13 |
| احتفاظ `inbox_events` (OQ-P1-08) | **مُنفَّذ** (W7)؛ التشغيل **مؤجَّل — يحتاج PG** |

---

## 5. الإقرار الذاتي (H31–H34)

| المادة | الحالة |
|---|---|
| H31 لا رمز في الرابط | **التزمتُ**: تذكرة أحادية (`GETDEL` ذرّي)؛ المعالج يرفض `?token=` بإغلاق 1008 |
| H32 الخادم يختار الاشتراك | **التزمتُ**: الهوية من التذكرة؛ أي إطار وارد غير `pong` ⇒ 1008 |
| H33 البثّ سرعة والقاعدة حقيقة | **التزمتُ**: النشر بعد COMMIT؛ الفشل يُبتلع + عدّاد |
| H34 لا نص/هاتف في أي إطار | **التزمتُ**: `to_frame` يفرض `INBOX_EVENT_ALLOWED_KEYS` (S6 تجبرها) |

---

## 6. قيود معروفة بصدق

1. **لم أشغّله** `tests/test_routes_channels.py` (قبل/بعد): بيئة p07/redis-cache غير مزوَّدة (بلا حاويات).
2. **لم أشغّله** `pytest core/tests` الكامل: `conftest.py` يفتح تجمّع DB عند الجلسة. **شغّلتُ** النقية (25) في تشغيل معزول.
3. **لم أشغّله** `mypy --strict`/`ruff`/`importlinter`/`pip-audit`: غير مثبّتة (موثّق في P1_OPEN_QUESTIONS). كتبتُ بأنواع صريحة.
4. السقوف عدّادات ذاكرة (حرّاس موارد لا ضمان صحة، H18): تعدد نسخ `api` يضاعف السقف الفعلي — **دين مُعلَن** (D-P1-23).

---

## 7. الختام

`P1.3b FIX COMPLETE — STOPPING. AWAITING AUDIT.`

