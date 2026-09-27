# PROMPT التنفيذي — sharwa_ai — P1 الدفعة الثانية حصراً: «الدور الحتمي، آلة التسليم، الـOutbox والإرسال» (P1.2)

> **المُوجَّه إليه:** DeepSeek (المهندس المنفّذ).
> **المُدقِّق:** Claude (المهندس المعماري) ثم المالك (فارس).
> **الإصدار المعتمد للمعمارية:** `docs/00_ARCHITECTURE.md` v1.1.
> **الحالة:** `P1.0+P1.1: APPROVED` بقرار المالك (2026-09-27). **P1.2 مفتوحة الآن.**
> **هذا الملف يحكم عملك حتى تسلّم `docs/P1_02_REPORT.md` وتتوقف.** لا تلمس شيئاً من P1.3 وما بعدها.

---

## 0. دورك وقواعد الاشتباك

كما في الدفعة الأولى، بلا تخفيف: **(1)** لا تدّعِ ما لم تشغّله — كل «يعمل» بأمره ومخرجه الفعلي. **(2)** لا تخمّن عقداً قائماً — اقرأ الكود؛ وكل عقد في القسم 5 أدناه استخرجتُه أنا من الكود الحي، وإن خالفه الكود فالكود يغلب ويُسجَّل التعارض. **(3)** لا تتجاوز النطاق (القسم 4) ولا بوابة الإيقاف (القسم 2). **(4)** لا سؤال يستوقفك: سجّله في `docs/P1_OPEN_QUESTIONS.md` وتابع بالأكثر أماناً.

**وأمر خاص بهذه الدفعة:** أداؤك في P1.1 كان جيداً في الكود وصادقاً في التقرير، والعيوب الستة كلها كانت في **مسارات الفشل** (الإيقاف، التزاحم، الانهيار، تعطّل المراقبة) — وهي بالضبط ما لا تكشفه قراءة الكود. هذه الدفعة أكثر خطورة من سابقتها لأنها **تُرسل رسائل حقيقية إلى عملاء حقيقيين**، فالتحقق التشغيلي فيها ليس بنداً إدارياً بل هو الفرق بين نظام وكارثة علاقات عامة.

---

## 1. الحالة الفعلية — ما صار قائماً بعد P1.0+P1.1

| البند | الحالة |
|---|---|
| `core/app/workers/realtime.py` | عملية `worker-realtime`: خيط لكل شريحة، إيقاف رشيق (مهلة تبدأ عند وصول الإشارة)، fail-fast على خيط ميت، `/healthz` و`/metrics` على 4003 |
| `core/app/workers/stream.py` | **النقطة الوحيدة** لعمليات streams في `core/` (تفرضها قاعدة الهنت التاسعة آلياً): `XREADGROUP`/`XAUTOCLAIM`/`XACK`/`XADD` على DLQ |
| `core/app/workers/schema.py` | `WalEntry` (pydantic، `extra='ignore'` + عدّاد حقول مجهولة) + `resolve_session()` بكاش LRU محدود TTL 30ث |
| `core/app/db/repos_ingest.py` | المعاملة الذرّية: `inbound_events` ← `customers` ← `conversations` ← إعادة فتح ← `messages` + `suppressions`، و`classify_db_error` |
| `core/app/workers/optout.py` | كشف opt-out حتمي (تطبيع عربي كامل + قائمة عبارات + مطابقة الرسالة كاملةً/بدايتها) على **أي** نص وارد بما فيه تسمية الوسائط |
| `core/app/obs/` | سجلات JSON بحقول H12 + تقنيع هاتف، سجل مقاييس خاص بالعامل، خادم `/healthz` و`/metrics` بـBearer |
| `core/app/db/context.py` | `init_pool(settings, system_dsn=…, system_pool_max=…)`؛ ثابتتان مفروضتان: `CORE_DB_POOL_MAX ≥ INGEST_SHARDS+1` و`CORE_SYSTEM_POOL_MAX ≥ INGEST_SHARDS+2` |
| `gateway/src/forwarder.js` | يمدّد علامة dedupe بعد تسليم ناجح (إصلاح F-P1-01)، ويتجاهل `identity_update`/`human_takeover_signal`/`engine='ai_core'` |
| `docker-compose.yml` | خدمة `worker-realtime` (384m/0.75cpu)، وسم صورة مشترك `sharwa-ai-core:p1.0` مع `api`، و`INGEST_SHARDS` من متغيّر واحد للخدمات الثلاث |
| `ops/prometheus/` | أهداف: gateway، gateway-forwarder، api، worker-realtime + 4 قواعد تنبيه للاستيعاب |
| **`needs_turn`** | **يُضبَط صحيحاً بالزناد ولا أحد يقرأه** — هذه الدفعة هي مستهلكه الأول |
| **`outbox`** | **فارغ تماماً؛ لا كاتب ولا قارئ** |
| **`evt:{shard}`** | **تنتجه البوابة ولا مستهلك له إطلاقاً** |
| دَيْن تحقُّق مُعلَن | A1–A20 وpytest الكامل وmypy/ruff/lint-imports/pip-audit **لم تُشغَّل** في P1.1 (قرار المالك بالاعتماد على الفحص الثابت). **نُقل إلى البند A0 أدناه ولا يُلغى.** |

---

## 2. بروتوكول الإيقاف

### 2.1 موضع هذه الدفعة

| المرحلة | المحتوى | الحالة |
|---|---|---|
| P1.0 + P1.1 | الاستيعاب إلى PostgreSQL | ✅ APPROVED |
| **P1.2** | **محرك الدور الحتمي (قوالب فقط) + آلة التسليم البشري + `outbox` + Dispatcher + بوابة سياسة الإرسال + مستهلك `evt:{shard}`** | **مفتوحة — نفّذها الآن** |
| P1.3 | Inbox API + WebSocket + `inbox_events` + RBAC + الملاحظات الداخلية + الاستئناف بعد الخمول (`scheduled_jobs`) | 🔒 |
| P1.4 | الكتالوج + أحداث المنصة + البحث الهجين | 🔒 |
| P1.5 | طبقة مزوّد LLM + الميزانيات + Router + الملخص والفتحات | 🔒 |
| P1.6 | Agent loop + الأدوات + FactSet + Output Verifier | 🔒 |
| P1.7 | تتبع الطلبات + رابط الدفع + `CommercePort` | 🔒 |
| P1.8 | `console/` + قبول P1 | 🔒 |

### 2.2 القواعد

- **الدفعة كاملة أو لا شيء:** كود + اختبارات حقيقية بمخرجات فعلية + مقاييس وتنبيهات موصولة + توثيق.
- كل خطوة من القسم 6 تنجح قبل التالية، وتُسجَّل في `docs/P1_PROGRESS.md` بمخرجها الحقيقي.
- **لا فرونت إند** ولا نص موجَّه للتاجر (دستور U1–U9 يُفرض في P1.8). **لكن** نصوص موجَّهة إلى **العميل النهائي** موجودة في هذه الدفعة (القوالب) — وهي محكومة بالقسم 6.5.
- عند الاكتمال، حدّث `docs/PHASE_GATE.md` بحيث يصبح سطر P1.2 حرفياً:
  `P1.2: SUBMITTED (awaiting audit). P1.3..P1.8 LOCKED.`
  ولا تلمس سطور P0 ولا P1.0+P1.1 ولا P2–P5. ثم اكتب في آخر رسالة لك حرفياً:
  `P1.2 COMPLETE — STOPPING. AWAITING AUDIT. NO P1.3 WORK STARTED.`
- إن رُفضت: أصلح العيوب المرقّمة **وحدها**، أعد تشغيل المتأثر **والكامل**، حدّث التقرير، وتوقف بالعبارة نفسها.

---

## 3. دستور الهنت — H1–H21 سارية + خمس مواد جديدة

كل مواد `PROMPT_P0 §3` (H1–H15) ومواد `PROMPT_P1_01 §4` (H16–H21) سارية حرفياً. ويُضاف:

**H22 — لا رسالة تُرسَل مرتين، ولا رسالة قديمة تُرسَل أبداً.** كل صف في `outbox` له `idempotency_key` فريد يُستخدم **هو نفسه** كـ`client_msg_id` عند البوابة (البوابة idempotent عليه). كل رد بوت يحمل `expected_epoch`، ويُسقَط إن تغيّر الـepoch أو خرجت المحادثة من `active` — **والفحص الأخير يجري قبل الإرسال مباشرةً لا قبل ثانية** (§4.3 الخطوة 10: «الأخيرة تغلب دائماً»).

**H23 — لا نص يُرسَل إلى عميل لم يوافق عليه المالك.** كل نص موجَّه للعميل في هذه الدفعة **قالب ثابت** في وحدة واحدة (`templates.py`)، بلا تركيب حرّ، بلا حقول من نص العميل، ومسرود كاملاً في التقرير لاعتماد المالك. **ممنوع** أي نص عميل مبنيّ في موضع آخر من الكود.

**H24 — البوت لا يقاطع إنساناً أبداً.** أي مسار قد يرسل نيابة عن البوت يتحقق من `bot_status='active'` في المعاملة نفسها التي يكتب فيها، **و** يتحقق منه الـDispatcher ثانيةً قبل الإرسال. رسالة موظف لا تُحجب أبداً بأي بوابة (سياسة/مفتاح طوارئ/حد معدل) — اتجاه الفشل هنا مقلوب عن التسويق بالضبط.

**H25 — بلا LLM، ما زال.** H6/H16 ساريتان: لا مكتبة ولا استدعاء نموذج ولا تضمين ولا مصنّف. كل قرار في محرك الدور دالة حتمية بمدخل ومخرج ثابتين، قابلة للاختبار بجدول حالات.

**H26 — الإبطال قبل الإرسال.** إن وصلت رسالة عميل جديدة أثناء بناء الدور (`max(seq)` تغيّر) يُلغى الدور ويُعاد، ولا يُرسَل ردّ بُني على حالة قديمة (§4.3، قاعدة الإبطال).

### 3.1 توسيع بوابات الفحص

- `scripts/hunt_gate.mjs`: أضف **القاعدة العاشرة**: ممنوع في `core/` أي استدعاء HTTP صادر إلى البوابة خارج `core/app/channels/` (نقطة واحدة تملك الخروج إلى الشبكة — نظير القاعدة التاسعة لـXACK). وأصلح ترقيم القاعدة التاسعة المزدوج (`'CRLF/rule9'` في السطر 188) الذي أشرتُ إليه في التدقيق كملاحظة N2.
- `core/.importlinter`: `app.workers` ممنوع أن يستورد `httpx` مباشرةً (الخروج عبر `app.channels` وحده).
- `ruff` + `mypy --strict` + `lint-imports` نظيفة على الجديد، بلا `noqa`/`type: ignore` غير مبرَّر في `P1_DEVIATIONS.md`.

---

## 4. النطاق

### 4.1 داخل النطاق (وحده)

| الرمز | البند |
|---|---|
| T1 | **مستهلك `needs_turn`**: قفل المحادثة (`FOR UPDATE SKIP LOCKED`)، البوابات، القرار الحتمي، كتابة `outbox` + `last_processed_seq` + `needs_turn=false` في معاملة واحدة، وقاعدة الإبطال (H26) |
| T2 | **محرك القرار الحتمي**: أربع نتائج فقط (تأكيد opt-out / تسليم بشري / إقرار آمن عند مفتاح الطوارئ / صمت) — بلا LLM |
| T3 | **آلة التسليم البشري** كاملةً عبر `app.set_bot_status` وحدها: المحفّزات الحتمية، `epoch++`، قالب الإشعار |
| T4 | **الـDispatcher**: `app.claim_outbox` → بوابة السياسة → فحص epoch/bot_status الأخير → `POST /sessions/:id/send` → تحديث الحالة |
| T5 | **بوابة سياسة الإرسال (الحد الأدنى)**: تصنيف الرسالة، `suppressions`، مفتاح الطوارئ (القدرة المناسبة)، سقف ردود البوت المتتالية، ساعات الهدوء **للتسويق فقط** (ولا تسويق في هذه الدفعة أصلاً) |
| T6 | **مستهلك `evt:{shard}`** بمجموعة `ai-core-evt`: `queued`/`sent`/`failed` → `outbox.status` + صف `messages` للرد الصادر + المقاييس |
| T7 | `core/migrations/0003_p1_outbound.sql`: فهرس فريد جزئي على `messages (conversation_id, provider_message_id) WHERE provider_message_id IS NOT NULL` (طبقة idempotency ثانية للوارد والصادر معاً) |
| T8 | `core/app/channels/`: امتداد `GatewayClient` القائم بمسار الإرسال (مهلة، قاطع دائرة، تصنيف الردود) — **نقطة الخروج الوحيدة** |
| T9 | المقاييس والتنبيهات الجديدة + رفع `mem_limit` بقياس مُثبَت |
| T10 | اختبارات حقيقية + سيناريوهات الفوضى (القسم 7) |

### 4.2 خارج النطاق (ممنوع بتاتاً)

أي LLM/Router/Verifier/تضمين، الكتالوج والبحث، تتبع الطلبات ورابط الدفع وأي نداء لـ`sharwa_saas`، `slots` والملخص المتدحرج، الحملات و`campaigns`/`campaign_recipients` والبث والتسويق الفعلي، `scheduled_jobs` والـsweeper والاستئناف بعد الخمول (P1.3)، WebSocket و`inbox_events` والـInbox API وRBAC، `console/` وأي فرونت إند، Celery وأي عامل آخر، AddressResolver/BackInStock/SizeAdvisor/GiftCurator، أي تعديل على عقد Django أو على `docs/reference/*`.

**تنبيه:** `inbox_events` و`app.next_inbox_seq()` موجودان في المخطط وسيغريانك. **لا تكتب فيهما** — لا مستهلك لهما حتى P1.3، وكتابة أحداث لا يقرؤها أحد دَيْن لا رصيد.

---

## 5. العقود الثابتة — استخرجتُها من الكود الحي، لا تخمّن فيها

### 5.1 عقد الإرسال إلى البوابة (`gateway/src/index.js:180-237`)

```
POST /sessions/:id/send
Headers: X-API-Key: <SHARWA_AI_GATEWAY_API_KEY>, Content-Type: application/json
Body:   { to: string, text: string, client_msg_id?: string, kind?: 'interactive'|'bulk'|'marketing' }
```

| الرد | المعنى | تصرّفك |
|---|---|---|
| `202 {message_id, client_msg_id}` | قُبل ووُضع في الطابور الدائم | `outbox.status='sent'`… **اقرأ التحذير أدناه** |
| `202 {…, duplicate:true, state}` | نفس `client_msg_id` سابقاً — **لم يُرسَل ثانيةً** | نجاح idempotent: نفس معالجة 202 بلا أي إرسال إضافي |
| `400` | تحقق فاشل (`to`/`text`/`kind`) | **عيب برمجي عندك**: `status='failed'` + سجل `error` + تنبيه. لا إعادة محاولة |
| `404 {error:'session not found'}` | الجلسة غير معروفة للبوابة | `status='failed'` + تسليم بشري (القناة غير صالحة) |
| `423 {error:'blocked_by_switch', capability, state, scope}` | مفتاح الطوارئ حجبها **عند البوابة** | `status='dropped_policy'` + عدّاد. **لا إعادة محاولة** |
| `429 {error:'send queue is full'}` | طابور الجلسة ممتلئ | أعِدها إلى `pending` بـ`next_attempt_at` مستقبلي (backoff + jitter) |
| `5xx` / مهلة / خطأ شبكة | عابر | backoff + إعادة محاولة حتى السقف ثم `status='failed'` + تنبيه |

**تحذير جوهري في التسمية:** رد 202 يعني **«قُبل في الطابور»** لا «وصل إلى واتساب». الإرسال الفعلي يحدث لاحقاً في عامل البوابة، ويُعلَن في `evt:{shard}`. لذلك:
- عند 202: `outbox.status='sent'` تعني «سُلِّم إلى البوابة»، **ولا تكتب صف `messages` هنا** (انظر 5.2 — الكتابة عند حدث `sent` الحقيقي، وهذا يمنع رسالة وهمية في الصندوق لرد لم يُرسَل).
- `client_msg_id` **يجب** أن يكون `outbox.idempotency_key` نفسه: هو ما يجعل إعادة محاولة الـDispatcher بعد انهيار **لا تُرسل نسخة ثانية** (البوابة idempotent على هذا المفتاح).

### 5.2 عقد أحداث الصادر `evt:{shard}` (`gateway/src/outbound/queue.js`)

نفس مخطط الشرائح تماماً: `evt:{shardFor(session_id)}`، حقل واحد `data` بـJSON، و`MAXLEN ~ EVT_STREAM_MAXLEN` (100000).

```json
{ "v":1, "type":"queued|sent|failed", "session_id":"…", "ts":1758900000000,
  "client_msg_id":"…", "wa_message_id":"…", "error_class":"expired|blocked|…" }
```

**ثلاث حقائق أثبتُّها بقراءة الكود، وهي معيار تصميمك:**

1. **الأنواع المُنتَجة فعلاً ثلاثة فقط: `queued`, `sent`, `failed`.** تعليق رأس `queue.js` يذكر `delivered`، و**لا وجود له في الكود**: لا مستمع لـ`messages.update` ولا لإشعارات الاستلام في `sessions.js` إطلاقاً. فحالتا `delivered`/`read` في `messages.status` **لن تُستخدَما في هذه الدفعة** — لا تكتب لهما مساراً ميتاً (H1)، وسجّل الفجوة في `P1_OPEN_QUESTIONS.md` لتُغلق في دفعة لاحقة بتغيير إضافي في البوابة.
2. **`evt:{shard}` بلا أي مستهلك اليوم** — مجموعتك `ai-core-evt` هي الأولى. ابدأ من `$` كما في `ai-core-ingest`، ولا تلمس أي مجموعة أخرى.
3. **حدث `sent` هو الدليل الوحيد على إرسال فعلي**، ويحمل `wa_message_id` — وهو ما يُخزَّن في `messages.provider_message_id` و`outbox.provider_message_id`.

### 5.3 عقد `outbox` و`app.claim_outbox` (`docs/reference/schema.sql`)

أعمدة `outbox`: `id, tenant_id, conversation_id, channel_account_id, idempotency_key (UNIQUE), origin ∈ bot|human|automation, message_class ∈ service|utility|marketing, expected_epoch, to_wa_id, payload jsonb, status ∈ pending|sending|sent|failed|dropped_stale|dropped_policy, attempts, next_attempt_at, locked_until, provider_message_id, created_at, sent_at` + قيد `CHECK (origin <> 'bot' OR expected_epoch IS NOT NULL)`.

`app.claim_outbox(p_limit, p_lease)` (SECURITY DEFINER، مصرَّح لـ`sharwa_system`) يفعل **قبل** أن يعطيك شيئاً:
1. يضع `status='dropped_stale'` لكل صف `pending` من `origin='bot'` صار `expected_epoch` مخالفاً لـ`conversations.epoch` أو صارت المحادثة غير `active` — **هذا هو حاجز كارثة 11 على مستوى القاعدة، لا تعد تنفيذه في بايثون**.
2. يختار `pending` المستحق **أو** `sending` منتهي الـlease (استعادة بعد انهيار dispatcher) بـ`FOR UPDATE SKIP LOCKED`، ويحوّله إلى `sending` مع `attempts+1` و`locked_until = now() + lease`.

إذن الـDispatcher **لا يكتب `sending` بنفسه ولا يختار الصفوف بنفسه**؛ يستدعي الدالة ويتعامل مع ما تعيده. `origin='human'` لا يُسقَط أبداً بهذا المنطق — وهذا مقصود (H24).

### 5.4 آلة الحالة والمنح

- `bot_status`/`epoch`/`version` **لا تُحدَّث مباشرةً أبداً** (`REVOKE UPDATE` على مستوى الأعمدة). الطريق الوحيد `app.set_bot_status(p_conv, p_expected_version, p_new_status, p_reason, p_staff)` — تعيد `epoch` الجديد أو `NULL` عند نسخة قديمة.
- الأعمدة المصرَّح لـ`sharwa_app` بتحديثها في `conversations`: `summary, slots, needs_turn, last_processed_seq, assigned_staff_id, last_message_at` — **`last_processed_seq` و`needs_turn` من نصيبك، وما عداهما ممنوع**.
- الزناد `app.trg_messages_seq()` يوقف البوت تلقائياً عند `direction='out' AND sent_by='staff'`. **رد البوت `sent_by='bot'` لا يوقف شيئاً** — تأكّد أنك لا تضع `'staff'` لرد بوت بالخطأ، فهو سيوقف البوت عن محادثته نفسها.
- `app.effective_switch(p_tenant, p_channel, p_capability)` مصرَّح لـ`sharwa_app` — استخدمها من داخل `tenant_tx` كمصدر السلطة لمفتاح الطوارئ. **لا تكرّر نسخة Redis** التي يملكها `api` (`app/killswitch/redis_sync.py`)، ولا تعتمد عليها: البوابة تفحص نسختها بنفسها كخط دفاع مستقل (الرد 423)، وأنت تفحص القاعدة.

### 5.5 العقد المجمّد مع Django

لا يُلمس بحرف. المتاجر على `engine=django` تمرّ عبر `legacy-forwarder` كما هي، ولا يكتب `core` في `outbox` ولا يرسل شيئاً لأي جلسة لا يعيد `app.resolve_session` لها `engine='ai_core'`. هذا شرط قبول مُقاس (A10).

---

## 6. خطوات التنفيذ

### P1.2.0 — سدّ دَيْن التحقق (البند A0) **قبل أي كود جديد**

هذا أول عمل في الدفعة، لا آخره:

1. جهّز البيئة وأثبتها: `pip install -r core/requirements.txt` + أدوات الفحص، ثم `docker compose config`، `docker compose up -d`، وكل الخدمات `healthy` بما فيها `worker-realtime` (أرفق `docker compose ps`).
2. شغّل **كامل** ما لم يُشغَّل في P1.1: `pytest core/tests` (القائمة + الجديدة، على PostgreSQL + PgBouncer + `redis-durable` حقيقية)، `mypy --strict core/app`، `ruff check core`، `lint-imports`، `pip-audit`، `node --test` لحزم البوابة، `node scripts/hunt_gate.mjs`، `promtool check config/rules`.
3. أثبِت بوابة §6.2 من البرومبت الأول التي لم تُنفَّذ: `INSERT INTO messages` بدور `sharwa_app` **عبر PgBouncer** ينجح، و`seq`/`needs_turn` أُسندا فعلاً. **إن ظهر `permission denied` فهذه فجوة منح حقيقية** ⇒ سجّلها وأصلحها في `0003_p1_outbound.sql` بأضيق نطاق ممكن.
4. أثبِت `A18` الحقيقي: `TTL dedupe:{session}:{msg_id}` بعد استيعاب رسالة ⇒ ≈ 172800، وحدّث `F-P1-01` في `P1_FINDINGS.md` بالرقم بدل «تعذّر تشغيله».
5. شغّل معايير P1.1 التي لا تحتاج كوداً جديداً وأرفق أرقامها: **A3** (20 × `kill -9`، ضياع 0)، **A6** (1000 زوج عزل)، **A9** (التسليم البشري بالقيم الأربع)، **A14** (10,000/60ث مع `p99` و`docker stats`)، **A16** (نسختان)، **A17** (الإيقاف الرشيق — يجب أن ينجح الآن بعد إصلاح D1).

**إن فشل أي شيء هنا فهو عيب في P1.1 يُصلَح فوراً** (المالك اعتمد الدفعة على أساس صحة الكود؛ فشل التشغيل ينقض الأساس لا يؤكده). سجّله في `P1_FINDINGS.md` وأصلحه قبل المتابعة، ولا تبنِ P1.2 على أساس لم يُشغَّل.

### P1.2.1 — بنية الأدوار داخل العملية الواحدة

المعمارية (§3) تضع «ingest consumer، conversation turns، outbound dispatcher» **كلها في `worker-realtime`** — فلا تنشئ خدمة compose جديدة. أضف ثلاث مجموعات خيوط إلى العملية القائمة:

| المجموعة | العدد | العمل |
|---|---|---|
| `turn` | `CORE_TURN_WORKERS` (افتراضي 2) | يستهلك `needs_turn` |
| `dispatch` | 1 | `app.claim_outbox` → إرسال |
| `evt` | خيط لكل شريحة (`INGEST_SHARDS`) | يستهلك `evt:{shard}` |

كلها تحت نفس العقد القائم: `stop_event` واحد، fail-fast على خيط ميت، `/healthz` يشمل حياة كل المجموعات، الإيقاف الرشيق بالمهلة التي تبدأ عند وصول الإشارة. **راجع ثابتات المجمّعات**: الحد الأدنى الجديد لمجمّع التينانت `INGEST_SHARDS + CORE_TURN_WORKERS + INGEST_SHARDS + 2`، وللنظام `+2` أخرى (الـDispatcher يستدعي `claim_outbox` بـ`system_tx`). افرضها بـ`ConfigError` وأرفق الحساب مقابل `default_pool_size` في `ops/pgbouncer.ini` و`max_connections` في `ops/postgres.conf` (اقرأهما، لا تفترضهما).

`mem_limit` للخدمة: ارفعه إلى **512m** (المجموع يصبح 4400m مقابل سقف D1 = 5.2GB) وأثبِت الذروة الفعلية بـ`docker stats` تحت A16.

### P1.2.2 — محرك الدور (T1/T2)

`core/app/workers/turn.py`. لكل دورة: اختر محادثات `needs_turn=true` (`tenant_id, last_message_at` — الفهرس الجزئي القائم `conversations_needs_turn_idx`)، ثم **لكل محادثة معاملة واحدة**:

1. **القفل:** `SELECT … FROM conversations WHERE id=$1 AND tenant_id=$2 FOR UPDATE SKIP LOCKED`. لا صف ⇒ دور آخر يعمل عليها: اخرج بلا فعل شيء (لا خطأ، لا انتظار).
2. **البوابات، بهذا الترتيب** — أي فشل يأخذ مساراً آمناً صريحاً لا صمتاً:
   - `bot_status = 'active'` وإلا اخرج و`needs_turn=false`.
   - `app.effective_switch(tenant, channel, 'ai_reply')`: `off` ⇒ قالب الإقرار الآمن + تسليم بشري؛ `degraded` ⇒ قوالب فقط (وهو كل ما لدينا) ⇒ تابع؛ `on` ⇒ تابع.
   - سقف ردود البوت المتتالية (`CORE_MAX_CONSECUTIVE_BOT_REPLIES`، افتراضي 8): احسبه بـSQL من `messages` (عدد الصادر `sent_by='bot'` بعد آخر وارد `direction='in'`) — **بلا حالة جديدة في الذاكرة ولا عمود جديد** (H18). التجاوز ⇒ تسليم بشري.
3. **تحميل الحالة:** آخر `N` رسالة (`CORE_TURN_HISTORY`, افتراضي 6) + `last_inbound_seq` + `last_processed_seq`. الرسائل غير المعالَجة كلها تُعالَج **في دور واحد** (العميل أرسل ثلاث رسائل ⇒ دور واحد).
4. **القرار الحتمي** (`decide()`، دالة نقية قابلة للاختبار بجدول، بلا IO): أربع نتائج **فقط**:
   - `OPTOUT_CONFIRM` — إن كانت آخر رسالة عميل قد أنتجت `suppressions` في هذا الدور أو الدور السابق (الدَيْن المُعلَن من P1.1، §P1.1.5).
   - `HANDOFF` — طلب صريح من العميل (قائمة عبارات حتمية: «موظف»، «شخص حقيقي»، «اريد اتكلم مع حد»… بنفس تطبيع `optout.py` وبنفس قاعدة المطابقة الكاملة/البدائية)، أو تجاوز سقف الردود، أو مفتاح الطوارئ `off`.
   - `SAFE_ACK` — إقرار آمن («استلمنا رسالتك») عند مفتاح الطوارئ `off` قبل التسليم.
   - `NO_REPLY` — لا شيء يُرسَل.
   **القرار الافتراضي في هذه الدفعة `HANDOFF` لا `NO_REPLY`:** بلا LLM لا يستطيع البوت الإجابة، و§2.5 تنصّ أن عجز البوت محفّزُ تسليم. الصمت أمام عميل حقيقي ليس مساراً آمناً؛ التحويل لموظف هو المسار الآمن. وثّق ذلك في `P1_DEVIATIONS.md` بوصفه سلوك هذه المرحلة الذي سيتقدّمه LLM في P1.5.
5. **الإبطال (H26):** أعد قراءة `last_inbound_seq`؛ إن تغيّر عمّا قرأتَه في الخطوة 3 ⇒ **ألغِ الدور** بلا كتابة `outbox`، واترك `needs_turn=true` ليُعاد فوراً. عدّاد `turn_invalidated_total`.
6. **الكتابة (معاملة واحدة):** انتقال الحالة عبر `app.set_bot_status` إن كان القرار `HANDOFF` (السبب: `customer_requested` / `bot_reply_cap` / `kill_switch_off`) ⇒ **ثم** صف `outbox` بـ:
   - `idempotency_key = f"{conversation_id}:{turn_seq}:{n}"` حيث `turn_seq` = `last_inbound_seq` وقت الدور (يجعل المفتاح حتمياً: إعادة الدور نفسه لا تُنتج مفتاحاً جديداً ⇒ `UNIQUE` يمنع رداً مزدوجاً)،
   - `origin='bot'`، `message_class='service'`، `expected_epoch` = الـepoch **بعد** أي انتقال في هذا الدور، `to_wa_id` = `customers.wa_id`، `payload = {"template": "<id>", "text": "<النص النهائي>"}`،
   - ثم `last_processed_seq = last_inbound_seq` و`needs_turn=false`.
   **ترتيب حرج:** إن حدث انتقال ورفع الـepoch، فـ`expected_epoch` هو الجديد، وإلا أسقط الـDispatcher ردَّك بنفسك. أثبِت هذا باختبار.
7. **إيقاظ الـDispatcher** (اختياري، `SETEX` إشارة على `redis-durable` أو مجرد دورته القصيرة) — لا ETA ولا countdown (ADR-05).

### P1.2.3 — الـDispatcher (T4/T5/T8)

`core/app/workers/dispatch.py`، خيط واحد، دورة قصيرة (`CORE_DISPATCH_INTERVAL_MS`، افتراضي 500، مع سقف مكتوب):

1. `system_tx()` → `app.claim_outbox(CORE_DISPATCH_BATCH, lease)` (افتراضي 20 صفاً، lease 60ث).
2. لكل صف، **`tenant_tx(tenant_id)` للفحوص الأخيرة قبل الإرسال** (H22، §4.3 خطوة 10 «الأخيرة تغلب دائماً»):
   - `origin='bot'`: أعد قراءة `conversations.epoch` و`bot_status` ⇒ عدم التطابق ⇒ `status='dropped_stale'` + عدّاد، **بلا إرسال**.
   - `app.effective_switch(tenant, channel, capability)` حيث `capability = 'ai_reply'` لـ`origin='bot'` و`'marketing'` لـ`message_class='marketing'` ⇒ `off` ⇒ `dropped_policy`.
   - `suppressions`: يُفحَص لكل `message_class <> 'service'` فقط. **`origin='human'` لا يُفحَص بأي بوابة ولا يُحجَب أبداً** (H24).
3. الإرسال عبر `app/channels/` وحدها: `POST /sessions/{session_id}/send` بـ`client_msg_id = idempotency_key`، `kind = 'marketing' if message_class=='marketing' else 'interactive'`، مهلة 3ث، قاطع دائرة بسيط (نفس نمط `GatewayClient` القائم — اقرأه واستعمله لا تكتب ثانياً).
   **`session_id` من أين؟** `channel_accounts.session_id` للقناة — تحتاج قراءته؛ `app.resolve_session` يعمل بالاتجاه المعاكس، فاقرأ العمود مباشرةً داخل `tenant_tx` (RLS تحمي).
4. معالجة الرد وفق جدول 5.1 حرفياً، وكل حالة بعدّاد. `sent` هنا تعني «سُلِّم إلى البوابة» فقط: `status='sent'`, `sent_at=now()`، **ولا صف `messages`**.
5. `attempts >= CORE_DISPATCH_MAX_ATTEMPTS` (افتراضي 8) ⇒ `status='failed'` + سجل صارخ + عدّاد + **تسليم بشري للمحادثة** (رد خدمة تعذّر إرساله يستحق إنساناً).

### P1.2.4 — مستهلك `evt:{shard}` (T6)

`core/app/workers/evt.py`، لكن **كل عمليات الـstream في `stream.py`** (القاعدة التاسعة سارية — وسّع `StreamReader` أو أضف صنفاً في نفس الملف). مجموعة `ai-core-evt` من `$`، `XAUTOCLAIM` ثم `XREADGROUP`، وDLQ منفصل `CORE_EVT_DLQ_STREAM` (افتراضي `dlq:evt:core`).

لكل حدث: `app.resolve_session(session_id)` ⇒ غير معروف أو `engine<>'ai_core'` ⇒ `XACK` + عدّاد (أحداث متاجر Django ليست شغلك). ثم `tenant_tx`:

- **`queued`** ⇒ عدّاد فقط.
- **`sent`** ⇒ في معاملة واحدة:
  `UPDATE outbox SET status='sent', provider_message_id=$wa WHERE idempotency_key=$cid AND status <> 'sent' RETURNING id, conversation_id, tenant_id, origin, payload` — **الصف المُعاد هو حرس الـidempotency**: لا صف ⇒ حدث مُعالَج سلفاً ⇒ `XACK` بلا كتابة. صف ⇒ أدخِل `messages` بـ`direction='out'`, `sent_by` من `origin` (`bot`→`'bot'`, `automation`→`'automation'`, `human`→`'staff'`), `type='text'`, `body = payload->>'text'`, `provider_message_id = wa_message_id`, `status='sent'`. الفهرس الفريد الجزئي (T7) طبقة حماية ثانية.
  **`client_msg_id` مجهول** (رد أرسله شيء غير `core`) ⇒ `XACK` + عدّاد `evt_unknown_client_msg_total`، بلا خطأ.
- **`failed`** ⇒ `status='failed'` + `error_class` في السجل والعدّاد؛ و`error_class='blocked'` ⇒ `dropped_policy` بدلاً منها (البوابة حجبت بمفتاح طوارئ، ليس فشلاً تقنياً).
- **`delivered`/`read`** ⇒ غير مُنتَجَين اليوم (§5.2). **لا تكتب لهما مساراً**؛ حدث بنوع مجهول ⇒ `XACK` + عدّاد `evt_unknown_type_total` + سجل واحد لكل نوع جديد (نفس نمط الحقول المجهولة).

### P1.2.5 — القوالب (H23)

`core/app/workers/templates.py`: قاموس واحد `id → نص عربي`، بلا تركيب حرّ وبلا أي إدخال من نص العميل. الافتراضيات المقترحة (يعتمدها المالك أو يعدّلها):

| المعرّف | النص المقترح |
|---|---|
| `handoff_notice` | «شكراً لرسالتك. سيتابع معك أحد أفراد فريقنا في أقرب وقت.» |
| `optout_confirm` | «تم إيقاف الرسائل الترويجية عن رقمك. ستظل تصلك تحديثات طلباتك فقط.» |
| `safe_ack` | «استلمنا رسالتك وسنعود إليك قريباً.» |

**ثلاثة قيود:** لا وعد بزمن محدَّد لا نضمنه (لذلك «في أقرب وقت» لا «خلال 5 دقائق»)؛ `optout_confirm` يذكر صراحةً أن تحديثات الطلبات تستمر (لأن `order_updates` غير محجوب فعلاً)؛ ولا ذكر لكلمة «بوت» أو «ذكاء اصطناعي» أو أي مصطلح تقني. **اسرد الجدول النهائي في التقرير لاعتماد المالك.**

### P1.2.6 — المقاييس والتنبيهات (H12)

عائلات جديدة، بلا تسمية عالية التعدد: `turn_processed_total{decision}`، `turn_invalidated_total`، `turn_skipped_total{reason}`، `turn_duration_seconds`، `outbox_written_total{message_class}`، `dispatch_attempts_total{result}` (`sent|dropped_stale|dropped_policy|retry|failed`)، `dispatch_duration_seconds`، `dispatch_queue_depth` (gauge: `pending` + `sending`)، `dispatch_oldest_pending_seconds` (gauge)، `evt_processed_total{type,outcome}`، `evt_unknown_client_msg_total`، `evt_unknown_type_total`، `handoff_transitions_total{reason}` (وسّع القائمة القائمة)، `evt_stream_lag{shard}`، `evt_pel_size{shard}`.

أربع قواعد تنبيه جديدة بعتبات مبرَّرة: (أ) `dispatch_oldest_pending_seconds > 120` لمدة 2د، (ب) أي زيادة في `dispatch_attempts_total{result="failed"}` خلال 5د، (ج) `evt_stream_lag > 1000` لمدة 2د، (د) نسبة `dropped_stale` إلى المُرسَل فوق 20% لمدة 10د (مؤشر على خلل في منطق الـepoch لا على تسليم بشري طبيعي). **واستفد من درس D3: أي تنبيه على غياب عملية يُبنى على `up`/`absent` لا على مؤشر داخلي.**

### P1.2.7 — الاختبارات (T10)

بنفس بيئة P1.1 الحقيقية (PostgreSQL + PgBouncer + `redis-durable`)، وبـ**بوابة وهمية** (`fake-gateway`) مشروعة: خدمة HTTP حقيقية صغيرة في `tests/` تحاكي عقد §5.1 حرفياً بكل رموزه (202/202-duplicate/400/404/423/429/500/مهلة) وتسجّل كل طلب. **هذا استثناء مشروع واحد** (شبكة واتساب الحقيقية غير قابلة للاختبار، تماماً كـ`FakeWaSocket` في P0)، ولا يجوز mock لأي شيء آخر — لا Redis ولا PostgreSQL ولا منطقك.

---

## 7. مصفوفة القبول — كل سطر بمخرج حقيقي

| # | السيناريو | العتبة/الدليل |
|---|---|---|
| **A0** | **دَيْن P1.1 مسدَّداً** | كل بنود P1.2.0: المجموعة الكاملة خضراء + mypy/ruff/lint-imports/pip-audit نظيفة + A3/A6/A9/A14/A16/A17/A18 بأرقامها |
| A1 | رسالة عميل عادية ⇒ دور واحد | قرار `HANDOFF`، صف `outbox` واحد، `bot_status='paused_human'`، `epoch+1`، `needs_turn=false`، `last_processed_seq` = `last_inbound_seq` |
| A2 | ثلاث رسائل متتالية من العميل | **دور واحد** لا ثلاثة؛ صف `outbox` واحد |
| A3 | الدور يُعاد تشغيله على نفس الحالة (محاكاة انهيار قبل الالتزام) | صف `outbox` واحد فقط (`UNIQUE idempotency_key`)؛ صفر رد مزدوج |
| A4 | رسالة جديدة تصل أثناء الدور | الدور يُلغى (`turn_invalidated_total` +1)، لا `outbox`، ثم دور جديد يعالج الاثنتين معاً |
| A5 | **موظف يرد من هاتفه أثناء وجود رد بوت في `pending`** | `app.claim_outbox` يضع `dropped_stale`؛ **صفر إرسال**؛ والرد البشري يمرّ بلا حجب |
| A6 | مفتاح الطوارئ `ai_reply=off` **بعد** كتابة `outbox` وقبل الإرسال | `dropped_policy`؛ صفر إرسال؛ الانتشار مُقاس |
| A7 | البوابة ترد 423 | `dropped_policy` بلا إعادة محاولة |
| A8 | البوابة ترد 429 ثم 202 | إعادة جدولة بـbackoff ثم نجاح؛ **إرسال واحد** |
| A9 | البوابة ترد 202 `duplicate:true` | يُعالَج كنجاح؛ صفر إرسال إضافي؛ `outbox` صف واحد |
| A10 | قناة `engine='django'` | صفر كتابة في `outbox`، صفر إرسال من `core`، واختبارات العقد الأربعة والـ14 الأصلية خضراء بلا تعديل |
| A11 | حدث `sent` | `outbox.status='sent'` + `provider_message_id`؛ صف `messages` واحد بـ`sent_by='bot'` و`provider_message_id = wa_message_id` |
| A12 | **نفس حدث `sent` 100 مرة** | صف `messages` واحد (حرس انتقال الحالة + الفهرس الفريد)؛ العدّاد يعكس التكرار |
| A13 | حدث `failed` بـ`error_class='expired'` ثم آخر بـ`'blocked'` | الأول `failed`، الثاني `dropped_policy` |
| A14 | حدث بنوع مجهول (`delivered`) | `XACK` + `evt_unknown_type_total` + سجل واحد؛ صفر كتابة؛ **صفر مسار ميت في الكود** |
| A15 | `kill -9` للعامل بين 202 من البوابة وتحديث `outbox`، **20 مرة** | صفر رسالة مفقودة؛ صفر رسالة مزدوجة (البوابة idempotent على `client_msg_id`)؛ الأرقام مطبوعة |
| A16 | حمل: 2000 محادثة تحتاج دوراً + 2000 صف `outbox` | صفر ازدواج؛ `p99(turn_duration_seconds)` و`p99(dispatch_duration_seconds)` مُعلنان؛ `dispatch_oldest_pending_seconds` يعود إلى ~0؛ RSS مسطّح؛ `docker stats` < 85% من 512m |
| A17 | سقف ردود البوت المتتالية | بعد 8 ردود بلا وارد جديد ⇒ تسليم بشري، لا رد تاسع |
| A18 | opt-out ⇒ تأكيد | `suppressions` (3 نطاقات) + صف `outbox` واحد بقالب `optout_confirm` + وصوله؛ و`order_updates` غير محجوب |
| A19 | العزل الخاصّي على المسار الصادر | 1000 زوج: لا `outbox`/`messages` لمتجر يُقرأ أو يُكتب من سياق متجر آخر؛ معاملة بلا `SET LOCAL` تعيد صفراً |
| A20 | الإيقاف الرشيق تحت حمل | ≤ 25ث، خروج 0، صفر صف عالق في `sending` بعد انتهاء الـlease، صفر إرسال مزدوج |
| A21 | البوابات الآلية | `hunt_gate.mjs` (بقاعدتيه 9 و10، مع إفشال متعمَّد يثبت القاعدة العاشرة) + ruff + mypy + lint-imports + promtool + pip-audit — نظيفة |
| A22 | الانحدار | كل اختبارات `core` و`gateway` خضراء بلا تعديل أي اختبار قائم |

---

## 8. المحظورات المطلقة

1. لمس `sharwa_saas` أو قاعدته أو عقد Django.
2. أي كود/جدول/stub لـP1.3+ (H10) — وتحديداً: لا WebSocket، لا `inbox_events`، لا `scheduled_jobs`، لا Inbox API، لا `console/`.
3. أي LLM أو تضمين أو مصنّف (H6/H16/H25).
4. تعديل `docs/reference/*` أو `0001`/`0002` أو أي `PROMPT_*`. التغيير في القاعدة = ترحيل أمامي جديد فقط.
5. تحديث `bot_status`/`epoch`/`version` بغير `app.set_bot_status`؛ أو اختيار/تحويل صفوف `outbox` بغير `app.claim_outbox`.
6. إرسال نص إلى عميل من غير `templates.py`؛ أو إدخال أي جزء من نص العميل في نص صادر.
7. حجب رسالة `origin='human'` بأي بوابة (H24).
8. أي حساب سعر أو عملة (قرار D3).
9. سرّ مكتوب، أو `.env` حقيقي، أو نص رسالة عميل في سجل/مقياس/تقرير (H5/H20).
10. `XDEL`/`XTRIM` أو لمس مجموعة `legacy-forwarder` أو مجموعة الاستيعاب.
11. `git push --force` أو حذف تاريخ.

---

## 9. تقرير التسليم — `docs/P1_02_REPORT.md`

بقالب P1.1 نفسه، بلا حذف بند: (1) ملخص + نتيجة كل خطوة P1.2.0…P1.2.7. (2) شجرة الملفات. (3) الأدلة: الأمر + آخر 15–30 سطراً من مخرجه الحقيقي. (4) **مصفوفة A0–A22 بلا خلية فارغة**. (5) الأرقام المقيسة: `p99` للدور والإرسال، ذروة RSS و`docker stats`، مجموع `mem_limit`، زمن الإيقاف، حساب المجمّعات مقابل `default_pool_size`/`max_connections`، نافذة الازدواج المقيسة في A15، زمن انتشار مفتاح الطوارئ في A6. (6) الانحرافات والأسئلة المفتوحة والاكتشافات بأثرها المقاس قبل/بعد. (7) **جدول القوالب النهائي لاعتماد المالك** (H23). (8) إقرار ذاتي H1–H26. (9) قيود معروفة بصدق. (10) سطر `PHASE_GATE.md` المنصوص في §2.2.

---

## 10. أسئلة مفتوحة أعرفها سلفاً

| الرمز | السؤال | خيارك الآمن |
|---|---|---|
| OQ-P1-07 | `delivered`/`read` غير مُنتَجَين من البوابة (لا مستمع لإشعارات الاستلام) | لا تكتب لهما مساراً؛ سجّل الفجوة لدفعة لاحقة تضيف المستمع في البوابة |
| OQ-P1-08 | نصوص القوالب تحتاج اعتماد المالك ولهجة يمنية مناسبة | استخدم المقترح في 6.5 وأدرِج الجدول في التقرير للاعتماد |
| OQ-P1-09 | الاستئناف التلقائي بعد خمول N ساعة (§4.4) يحتاج `scheduled_jobs` | مؤجَّل لـP1.3؛ في هذه الدفعة التسليم البشري نهائي حتى يستأنفه موظف |
| OQ-P1-10 | `dropped_stale` قد يبدو مرتفعاً في الاختبارات بسبب التسليم البشري الطبيعي | فرّق في المقاييس بين `dropped_stale` بسبب انتقال حالة و«بلا سبب ظاهر»، وارفع السؤال إن تجاوزت النسبة 20% بلا انتقال |
| OQ-P1-11 | القرار الافتراضي `HANDOFF` يعني أن كل رسالة عميل تُحوَّل لموظف في هذه المرحلة | صحيح ومقصود حتى P1.5؛ وثّقه ولا «تحسّنه» بردّ مُختلَق |

---

## 11. الختام

نجاح هذه الدفعة يُقاس بجملة واحدة: **رسالة عميل حقيقية تُنتج رداً واحداً بالضبط يصل إلى واتساب، ولا تُنتج شيئاً إن كان إنسان قد تدخّل أو مفتاح الطوارئ قد أُغلق — مهما انهارت العمليات في أي لحظة.** هذه أول دفعة يخرج فيها كلام النظام إلى عميل حقيقي، فكل ازدواج هو إحراج للتاجر، وكل رد بعد تسليم بشري هو مقاطعة لموظفه.

ابدأ من **P1.2.0** (سدّ دَيْن التحقق) — لا من الكود. وعند الاكتمال:

`P1.2 COMPLETE — STOPPING. AWAITING AUDIT. NO P1.3 WORK STARTED.`
