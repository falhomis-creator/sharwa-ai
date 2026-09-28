# CONSOLE_API_CONTRACT.md — عقد واجهة لوحة الموظفين (H56 — مجمَّد)

**الحالة:** مجمَّد (frozen). أي تغيير لمسار أو حقل أو رمز خطأ بلا تحديث هذه
الوثيقة وإعادة توليد `docs/console_api.lock.json` هو **مخالفة بوابة S13** لا
مسألة أسلوب. يبني فريق الواجهة على هذه الوثيقة وحدها بلا قراءة الكود.

المرجع الآلي لهذه الوثيقة هو `docs/console_api.lock.json`، يولّده
`scripts/generate_console_lock.py` من الجدول أدناه (§1)، وتقارنه بوابة S13
بالمسارات الفعلية في `app/api/**`.

---

## 1. جدول المسارات

الصلاحيات من `app/security/permissions.py` (`PERMISSIONS`). الترويسة الإلزامية
`Authorization: Bearer <JWT>` على كل مسار. طرق GET بلا جسم طلب؛ طرق POST تقبل
`application/json`.

| Method | Path | Permission | Request body | Response body |
|--------|------|------------|--------------|---------------|
| GET | `/v1/conversations` | `conversation.read` | query: `status?`, `cursor?`, `limit?` | `{"items":[{...summary}],"next_cursor":str\|null}` |
| GET | `/v1/conversations/{conversation_id}` | `conversation.read` | — | `{conversation summary + local customer card}` |
| GET | `/v1/conversations/{conversation_id}/messages` | `conversation.read` | query: `before_seq?`, `limit?` | `{"items":[{message}]}` |
| POST | `/v1/conversations/{conversation_id}/messages` | `conversation.reply` | `{"text":str}` | `{"message_id":uuid}` |
| POST | `/v1/conversations/{conversation_id}/handoff` | `conversation.handoff` | `{"version":int?}` | `{conversation summary}` |
| POST | `/v1/conversations/{conversation_id}/resume` | `conversation.handoff` | `{"version":int?}` | `{conversation summary}` |
| POST | `/v1/conversations/{conversation_id}/close` | `conversation.handoff` | `{"version":int?}` | `{conversation summary}` |
| POST | `/v1/conversations/{conversation_id}/claim` | `conversation.assign` | `{"version":int?}` | `{conversation summary}` |
| POST | `/v1/conversations/{conversation_id}/assign` | `conversation.assign` | `{"staff_id":uuid,"version":int?}` | `{conversation summary}` |
| POST | `/v1/conversations/{conversation_id}/transfer` | `conversation.assign_others` | `{"staff_id":uuid,"version":int?}` | `{conversation summary}` |
| GET | `/v1/conversations/{conversation_id}/notes` | `note` | query: `limit?` | `{"items":[{note}]}` |
| POST | `/v1/conversations/{conversation_id}/notes` | `note` | `{"body":str}` | `{"note_id":uuid}` |
| GET | `/v1/inbox/events` | `conversation.read` | query: `since?`, `limit?` | `{"items":[{event}],"latest_seq":int}` |
| POST | `/v1/ws/ticket` | `conversation.read` | — | `{"ticket":str,"expires_in_s":int}` |

**حقل المحادثة الملخّص** (`_CONVERSATION_SUMMARY_FIELDS`):
`id` · `bot_status` · `assigned_staff_id` · `last_message_at` · `customer_id` ·
`channel_account_id` · `epoch` · `version`. **لا** `internal_notes` ولا هاتف ولا
نصّ رسالة في أي ملخّص أو قائمة (H57/H34).

**حقل الرسالة** (`list_messages`): `id` · `seq` · `direction` · `sent_by` ·
`type` · `body` · `provider_message_id` · `status` · `created_at`.

---

## 2. غلاف الخطأ

كل خطأ يعود بالغلاف الوحيد:

```json
{"error":{"code":"...","request_id":"...","retry_after_s":123}}
```

`retry_after_s` حاضرٌ فقط حيث يكون له معنى (مثال `RATE_LIMITED`). جدول الرموز
منقول حرفياً من `ERROR_CODES`/`_STATUS_BY_CODE` في `app/api/errors.py`:

| Code | HTTP |
|------|------|
| `UNAUTHENTICATED` | 401 |
| `TOKEN_EXPIRED` | 401 |
| `FORBIDDEN_ROLE` | 403 |
| `TENANT_SUSPENDED` | 403 |
| `NOT_FOUND` | 404 |
| `CHANNEL_ALREADY_EXISTS` | 409 |
| `GATEWAY_UNAVAILABLE` | 503 |
| `CHANNEL_CONFLICT` | 409 |
| `CHANNEL_LOGGED_OUT` | 409 |
| `CHANNEL_BANNED` | 403 |
| `RATE_LIMITED` | 429 |
| `SWITCH_LOCKED_BY_ADMIN` | 403 |
| `VALIDATION_FAILED` | 422 |
| `INTERNAL` | 500 |
| `STAFF_NOT_PROVISIONED` | 403 |
| `FORBIDDEN_PERMISSION` | 403 |
| `CONVERSATION_CLOSED` | 409 |
| `CONFLICT_VERSION` | 409 |
| `IDEMPOTENCY_KEY_REQUIRED` | 400 |
| `IDEMPOTENCY_KEY_REUSED` | 409 |
| `TICKET_ISSUE_FAILED` | 503 |
| `PLATFORM_SIGNATURE_INVALID` | 401 |
| `PLATFORM_TIMESTAMP_SKEW` | 401 |
| `PLATFORM_PAYLOAD_TOO_LARGE` | 413 |

---

## 3. مصفوفة الأدوار × الصلاحيات

من `app/security/permissions.py` (`PERMISSIONS`). أربعة أدوار وستّ صلاحيات.

| Permission | owner | manager | agent | viewer |
|------------|:-----:|:-------:|:-----:|:------:|
| `conversation.read` | ✔ | ✔ | ✔ | ✔ |
| `conversation.reply` | ✔ | ✔ | ✔ | — |
| `conversation.handoff` | ✔ | ✔ | ✔ | — |
| `conversation.assign` | ✔ | ✔ | ✔ | — |
| `conversation.assign_others` | ✔ | ✔ | — | — |
| `note` | ✔ | ✔ | ✔ | — |

`platform_admin` بلا صف `staff_members` للقراءة فقط (`conversation.read`)، ولا
يرسل رسالة نيابة عن متجر.

---

## 4. بروتوكول الـWebSocket

- **التذكرة:** `POST /v1/ws/ticket` (JWT عادي) يعيد `{ticket, expires_in_s}`؛
  التذكرة 32 بايت عشوائية آمنة، تخزَّن على `redis-cache` بـ`SETEX`
  `ws:ticket:{ticket}` لمدة **30 ثانية** (سقف مكتوب)، وتُستهلك بـ`GETDEL` ذرّي —
  التذكرة **أحادية الاستعمال**. الحمولة `{tenant_id, staff_id, role, permissions[]}`
  بلا JWT ولا سرّ (H31).
- **الاتصال:** `WS /v1/ws?ticket=<ticket>` — التذكرة هي المصادقة الوحيدة؛
  رمز JWT في المسار/الاستعلام مرفوض (H31).
- **الاشتراك:** يختاره الخادم (H32): اشتراك واحد لكل تينانت
  `tenant:{tenant_id}:inbox`. العميل لا يختار.
- **أنواع الإطارات الصادرة:** `hello` (حمولة `latest_seq`, `retention_days`) ·
  `message.new` · `message.status` · `conversation.updated` ·
  `conversation.closed` · `handoff.requested` · `resync_required` · `ping`.
  كل مفتاح حمولة خاضع لـ`INBOX_EVENT_ALLOWED_KEYS` — لا نصّ رسالة ولا هاتف ولا
  ملاحظة داخلية في أي إطار (H34/H57).
- **النبض:** الخادم يرسل `ping` كل **25 ثانية**؛ يجب أن يصل `pong` خلال
  **20 ثانية** وإلا يُغلق الاتصال (برمز 1000) ويُرتفع `ws_pong_timeouts_total`.
- **إعادة المزامنة:** عند إعادة الاتصال يرسل العميل `last_seq`؛ يُعاد كل ما فاته
  (حد الاحتفاظ 7 أيام، `WS_REPLAY_BUFFER_MAX=1000`، `WS_RESYNC_MAX=500`). إن حُذف
  ما يلزمه بالاحتفاظ ⇒ `resync_required`.
- **العميل لا يرسل إلا `pong`** (H32): أي إطار آخر ⇒ إغلاق برمز 1008. سقف
  الإطار الوارد 4KB (1009) ومعدل 20 إطاراً/ث (1008).
- **حدود الاتصال:** `WS_PER_STAFF_MAX=5` · `WS_PER_TENANT_MAX=200` ·
  `WS_GLOBAL_MAX=1000`.

---

## 5. الترقيم بالمؤشّر (keyset cursor)

قائمة المحادثات بلا `OFFSET`. المؤشّر `base64url(json({"t": ISO-8601, "id": uuid}))`
بلا حشو `=` — **معتم**، وعلى اللوحة ألّا تفسّره ولا تصنعه. مؤشّر تالف ⇒
`VALIDATION_FAILED`. `limit` يُقصّ إلى سقف مكتوب (`CONVERSATIONS_LIMIT_MAX=100`).

---

## 6. التعاقُب (Idempotency)

- `Idempotency-Key` **إلزامي** على الردّ `POST /v1/conversations/{id}/messages`
  (H30). غيابه ⇒ `IDEMPOTENCY_KEY_REQUIRED`.
- إعادة طلب بنفس المفتاح **والجسم** ⇒ يعاد الردّ الأول كما هو، بلا كتابة جديدة.
- نفس المفتاح بجسم مختلف ⇒ `IDEMPOTENCY_KEY_REUSED`.

---

## 7. CORS وحدود المعدّل

- **CORS:** قائمة نطاقات صريحة من الإعداد `CONSOLE_ALLOWED_ORIGINS` (مفصولة
  بفواصل). قائمة فارغة ⇒ **لا يُسجَّل وسيط CORS إطلاقاً** (لا انفتاح افتراضي).
  `allow_methods=["GET","POST","OPTIONS"]`؛
  `allow_headers=["Authorization","Idempotency-Key","X-Request-Id","Content-Type"]`؛
  `expose_headers=["X-Request-Id"]`؛ `allow_credentials=false` (الاستيثاق عبر
  الترويسة لا الكوكيز، و`allow_origins=["*"]` مع `allow_credentials=true`
  ممنوعة).
- **الـWebSocket لا يحميه CORS:** تُفحص ترويسة `Origin` على نفس القائمة،
  و`Origin` خارجها ⇒ إغلاق برمز 1008. غياب `Origin` (عميل غير متصفّح) مسموح.
- **حدود المعدّل (H59):** نافذة منزلقة على `redis-cache` بمفتاح
  `rl:{tenant_id}:{staff_id}:{group}`، ثلاث مجموعات: `read` (افتراض 120/د) ·
  `write` (60/د) · `ticket` (10/د — أضيقها). التجاوز ⇒ `RATE_LIMITED` مع
  `retry_after_s`. **الفشل مفتوح هنا:** إن سقط `redis-cache` يمرّ الطلب ويُرتفع
  `rate_limit_fail_open_total{group}` — حدّ المعدّل حماية من الاستنزاف لا ضمانة
  سلامة.

---

## 8. ما لا تفعله اللوحة

- لا تُرسل `tenant_id` (يأتي من مطالبة JWT وحدها — H2).
- لا تُمرّر رمزاً في مسار URL أو استعلام (H31).
- لا تتوقّع نصّ رسالة أو هاتفاً أو ملاحظة داخلية في إطار WS (H34/H57).
- لا تفسّر المؤشّر ولا تنشئه (§5).


