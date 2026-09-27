# PROMPT التنفيذي — sharwa_ai — المرحلة P1، الدفعة الأولى حصراً: «عامل الزمن الحقيقي ومستهلك الاستيعاب» (P1.0 + P1.1)

> **المُوجَّه إليه:** DeepSeek (المهندس المنفّذ).
> **المُدقِّق:** Claude (المهندس المعماري) ثم المالك (فارس).
> **الإصدار المعتمد للمعمارية:** `docs/00_ARCHITECTURE.md` v1.1 (2026-09-20).
> **قرار المالك (2026-09-26):** P0 مغلقة رسمياً. **P0.8 (E2E/chaos/report) مُسقطة بقرار إداري نهائي** — لا تطلبها ولا تنفّذها ولا تشترطها. بيئة العمل الآن **تطوير محلي (Local Dev)**.
> **هذا الملف يحكم عملك من الآن حتى تسلّم `docs/P1_01_REPORT.md` وتتوقف.** لا تلمس شيئاً من P1.2 وما بعدها.

---

## 0. دورك وقواعد الاشتباك

أنت مهندس تنفيذ صارم. لا تُصمّم معمارية جديدة ولا «تحسّن» القرارات المعتمدة؛ تنفّذها بدقة وتثبت بمخرجات حقيقية أنها تعمل. المعمارية قرّرت *ماذا* و*لماذا*؛ أنت مسؤول عن *كيف* بجودة إنتاجية.

**أربع قواعد فوق كل ما عداها:**

1. **لا تدّعِ ما لم تشغّله.** كل «يعمل/نجح/مُختبَر» يرافقه الأمر الفعلي ومخرجه الفعلي (آخر 15–30 سطراً) في التقرير. ادّعاء بلا مخرج = غير موجود عند المدقق. اختلاق رقم أو مخرج = رفض كامل للدفعة.
2. **لا تخمّن عقداً أو سلوكاً موجوداً.** اقرأ الكود أولاً. كل عقد في القسم 6 أدناه مُستخرج من الكود الحي فعلاً — إن وجدتَ الكود يخالف ما هنا فالكود هو الحقيقة، وتسجّل التعارض في `docs/P1_DEVIATIONS.md` وتتوقف عن التخمين.
3. **لا تتجاوز نطاق هذه الدفعة (القسم 5) ولا حدود الإيقاف (القسم 3).**
4. **لا سؤال يستوقفك.** ما تعذّر التحقق منه يُسجَّل في `docs/P1_OPEN_QUESTIONS.md` وتتابع بأكثر الخيارات أماناً دون مخالفة الوثائق الحاكمة.

---

## 1. الحالة الفعلية للمستودع — أثبتُّها بقراءة الكود الحي قبل كتابة هذا الملف

هذه ليست تخميناً ولا نقلاً عن تقارير؛ هي ما رأيتُه في `C:\sharwaai\sharwa-ai` بعد آخر `git pull`. اقرأها قبل أي سطر كود، وأبلِغ فوراً في `P1_DEVIATIONS.md` إن وجدتَ أي بند منها مخالفاً للواقع عندك:

| البند | الحالة الفعلية |
|---|---|
| `gateway/` (Node 22) | مكتمل عبر P0.0–P0.6: استيعاب دائم (`in:{shard}` بـ dedupe مدموج في Lua على `redis-durable`)، وسائط إلى MinIO، طابور صادر دائم، دورة حياة الجلسة (lease/fencing/reconnect)، مفتاح طوارئ، `/readyz` و`/metrics`، مراقبة. |
| `gateway/src/forwarder.js` | عملية منفصلة بمجموعة مستهلك `legacy-forwarder` تسلّم لـDjango. **تتجاهل بالتصميم**: `type='identity_update'`، `type='human_takeover_signal'`، وكل كيان بـ`engine==='ai_core'` (`shouldForwardToLegacy`). |
| `core/` (Python 3.12) | **موجود ومكتمل كـباك إند P0.7**: `app/config.py` (fail-fast)، `app/db/{context,repos,migrate,testsupport}.py` بمدخلَي `tenant_tx()`/`system_tx()` حصراً، `app/security/jwt.py` (RS256 + JWKS)، `app/api/{deps,errors,routes_health,routes_channels,routes_killswitches}.py`، `app/killswitch/redis_sync.py`، `app/channels/gateway_client.py`، `app/cli.py`، `migrations/0001_baseline.sql` (= المرجع) و`0002_p0_api.sql`، و`tests/` بـ 240/240 اختباراً حقيقياً، `mypy --strict` وruff وimport-linter نظيفة. |
| `core/` — ما **ليس** موجوداً | أي عامل (worker)، أي اتصال بـ`redis-durable`، أي مستهلك streams، أي كتابة في `inbound_events`/`customers`/`conversations`/`messages`، أي سجلات JSON أو مقاييس خارج مسار `api`. |
| `console/` (الفرونت إند) | **غير موجود**. لوحة التاجر مؤجَّلة بقرار المالك إلى دفعة لاحقة من P1 مع صندوق المحادثات. **ليست من نطاقك في هذه الدفعة.** |
| `docker-compose.yml` | الخدمات القائمة: `postgres`, `pgbouncer`, `redis-durable`, `redis-cache`, `gateway`, `gateway-forwarder`, `api`, `prometheus`. مجموع `mem_limit` الحالي **3888m** (سقف D1 المعتمد: 5.2GB على 8GB/4vCPU). |
| قاعدة البيانات | `schema.sql` مطبَّقة كاملةً (`schema_selftest.sql` = 34/34 على Docker حقيقي). **كل جداول P1 موجودة سلفاً**: `customers`, `conversations`, `messages`, `inbound_events`, `internal_notes`, `outbox`, `suppressions`, `consents`, `inbox_events`, `tenant_counters`, `llm_calls`, `verifier_blocks`, `catalog_*`. **لا تنشئ جدولاً موجوداً.** |
| متطلبات المنصة C1–C11 | **لم تُبرمَج في `sharwa_saas` بعد.** لذلك بدأنا P1 من الطرف الذي لا يحتاجها. |

---

## 2. الوثائق الحاكمة — اقرأها كاملةً قبل أي سطر كود (بهذا الترتيب)

| # | الملف | لماذا |
|---|---|---|
| 1 | `SHARWA_AI_PROJECT_SUMMARY.md` | العقد المجمّد مع Django والمبادئ الحاكمة |
| 2 | `PROMPT_P0_foundation_for_deepseek.md` | **دستور الهنت H1–H15 ودستور اللا-التباس U1–U9 وبروتوكول الإيقاف — كلها سارية عليك حرفياً**، ولا يُنقض شيء أُغلق فيها |
| 3 | `docs/00_ARCHITECTURE.md` §4.2 (خط الاستيعاب)، §4.3 (خط أنابيب الدور)، §4.4 (آلة التسليم البشري)، §4.7 (السياسة)، §4.8 (الجدولة)، §4.10 (مفتاح الطوارئ)، §5 (نموذج البيانات)، §6 (الموارد)، §10 (الخارطة) | القرارات التي تنفّذها |
| 4 | `docs/01_FIVE_TASKS_DESIGN.md` §1.1 (توحيد القنوات)، §1.3 (الملاحظات الداخلية)، §1.4 (الكتابة من الموظف)، §2.5 (محفّزات التسليم) | تصميم ما تبنيه هنا |
| 5 | `docs/02_RISK_SOLUTIONS_21.md` — كوارث **1، 4، 5، 11، 17، 18** | معيار قبول هذه الدفعة |
| 6 | `docs/reference/schema.sql` + `schema_selftest.sql` | **العقد الملزِم للبيانات** (لا يُعدَّل أبداً) |
| 7 | `core/` كاملاً (`app/**`, `tests/**`, `pyproject.toml`, `.importlinter`, `requirements.txt`, `Dockerfile`) | الأسلوب والاتفاقيات التي تبني عليها حرفياً |
| 8 | `gateway/src/ingest/{normalize,identity,dedupe,wal}.js`، `gateway/src/sessions.js`، `gateway/src/forwarder.js` | المنتج الفعلي للكيانات التي ستستهلكها |
| 9 | `docs/P0_FINDINGS.md`، `docs/P0_DEVIATIONS.md`، `docs/P0_OPEN_QUESTIONS.md` | ما اكتُشف وما بقي مفتوحاً |

**الأسبقية عند التعارض:** هذا الملف ← `00_ARCHITECTURE.md` ← `schema.sql` ← `PROMPT_P0` ← بقية الوثائق ← الكود القائم. **استثناء واحد:** في وصف سلوك قائم فعلاً (عقد WAL، عقد Django)، **الكود الحي يغلب كل ما سبق**، ويُسجَّل الفرق في `P1_DEVIATIONS.md` بلا حلّ صامت (H15).

---

## 3. بروتوكول الإيقاف والتدقيق — إلزامي

### 3.1 خارطة P1 (وضعتُها أنا بحسب المعمارية؛ لا تعدّلها ولا تستبق منها شيئاً)

| المرحلة | المحتوى | الحالة |
|---|---|---|
| **P1.0** | تهيئة دور `worker-realtime` في `core/`: الإعداد، السجلات JSON، المقاييس، عميل `redis-durable`، الإيقاف الرشيق، خدمة compose بحدود موارد، توسيع بوابات الفحص الآلي | **مفتوحة — نفّذها الآن** |
| **P1.1** | **مستهلك الاستيعاب**: `in:{shard}` ← معاملة PostgreSQL واحدة (`inbound_events`/`customers`/`conversations`/`messages`) + كشف opt-out حتمي + إشارة التسليم البشري + `identity_update` + `needs_turn` + DLQ + استرجاع PEL | **مفتوحة — نفّذها الآن** |
| P1.2 | آلة التسليم البشري الكاملة + `outbox` + الـDispatcher (تحقق `expected_epoch`) + إرسال عبر البوابة + أحداث الحالة | 🔒 مقفلة |
| P1.3 | Inbox API + WebSocket + `inbox_events` + RBAC + الملاحظات الداخلية | 🔒 مقفلة |
| P1.4 | نموذج قراءة الكتالوج + أحداث المنصة + البحث الهجين (FTS + trgm + pgvector/RRF) | 🔒 مقفلة |
| P1.5 | طبقة مزوّد LLM + الميزانيات + Router + الملخص المتدحرج والفتحات | 🔒 مقفلة |
| P1.6 | Agent loop + سجل الأدوات + FactSet + **Output Verifier** | 🔒 مقفلة |
| P1.7 | تتبع الطلبات + رابط الدفع + `CommercePort` (يتوقف على C1–C11) | 🔒 مقفلة |
| P1.8 | `console/` (لوحة التاجر + صندوق المحادثات) + قبول P1 النهائي | 🔒 مقفلة |

### 3.2 القواعد

- **الدفعة كاملة أو لا شيء:** P1.0 + P1.1 لا تُعدّان منتهيتين إلا بـ **كود + اختبارات حقيقية ناجحة بمخرجات فعلية + مقاييس وتنبيهات موصولة + توثيق**. لا «سأكمل الاختبارات لاحقاً».
- **لا فرونت إند في هذه الدفعة** (لا `console/`، ولا شاشة، ولا نص تاجر). دستور U1–U9 سيُفرض عليك في P1.8؛ لا تكتب الآن نصاً موجّهاً للتاجر إطلاقاً.
- **نقاط تحقق داخلية:** كل خطوة من القسم 7 تنجح اختباراتها قبل أن تبدأ التالية، وتُسجَّل نتيجتها في `docs/P1_PROGRESS.md` فور إنجازها بمخرجها الحقيقي.
- **التوقف الكامل:** عند اكتمال القسم 7 وتسليم `docs/P1_01_REPORT.md` بقالب القسم 10، اكتب في آخر رسالة لك حرفياً:
  `P1.0+P1.1 COMPLETE — STOPPING. AWAITING AUDIT. NO P1.2 WORK STARTED.`
  ثم **لا تفعل شيئاً**: لا تفتح ملفات P1.2+، لا تنشئ لها مجلدات، لا تضيف «تحضيراً مبكراً» في الكود.
- **بوّابة المرحلة:** حدّث `docs/PHASE_GATE.md` بحيث يصبح سطر P1 حرفياً:
  `P1: IN PROGRESS — P1.0+P1.1 SUBMITTED (awaiting audit). P1.2..P1.8 LOCKED.`
  ولا تلمس سطر P0 ولا أسطر P2–P5. المدقق وحده يفتح P1.2.
- **إن رُفضت الدفعة:** ستصلك قائمة عيوب مرقّمة. أصلح **هذه العيوب فقط**، أعد تشغيل الاختبارات المتأثرة **والمجموعة الكاملة**، حدّث التقرير، وتوقف مجدداً بالعبارة نفسها.

---

## 4. دستور الهنت — H1–H15 سارية حرفياً + ست مواد جديدة

كل مواد `PROMPT_P0 §3` (H1–H15) سارية على كل سطر تكتبه، بما فيها: **H1** لا placeholders ولا `pass` ولا `NotImplementedError`، **H2** العزل التام (`tenant_id` من هوية القناة حصراً، وكل وصول للقاعدة عبر `tenant_tx()`/`system_tx()` فقط)، **H3** معالجة كل خطأ بمهلة وتصنيف وسقف وbackoff بلا ابتلاع، **H4** كل شيء محدود بسقف مكتوب في الإعدادات، **H5** الأسرار والخصوصية (سرّ فارغ = رفض إقلاع؛ الهواتف مقنّعة في السجلات)، **H7** اختبارات حقيقية بلا mocks لما هو قيد الاختبار وبلا `skip`، **H9** العقد المجمّد مع Django، **H11** الاعتماديات مثبَّتة ومبرَّرة، **H12** المقاييس والسجلات المنظّمة، **H13** «لماذا» لا «ماذا» في التعليقات، **H14** الترحيلات أمامية فقط، **H15** تسجيل الانحرافات.

ويُضاف إليها لهذه الدفعة:

**H16 — لا ذكاء اصطناعي في هذه الدفعة.** H6 يبقى سارياً كما هو: **ممنوع** أي مكتبة أو استدعاء نموذج لغوي أو تضمين (embedding) أو مصنّف. كل ما تبنيه هنا **حتمي 100%** وقابل للاختبار بمدخل ومخرج ثابتين. كشف opt-out قائمة كلمات مُعدّة، لا مصنّف.

**H17 — الالتزام قبل الإقرار (Commit-before-ACK).** لا `XACK` قبل نجاح `COMMIT`، بلا استثناء. لا كتابتان في معاملتين حيث تكفي واحدة. كل كتابة الدور الواحد (الحدث + العميل + المحادثة + الرسالة + الآثار) في **معاملة PostgreSQL واحدة**. سقوط العملية في أي لحظة يترك الكيان في PEL ليُستعاد، ويمنع `UNIQUE` تكراره.

**H18 — لا صحة تعتمد على الذاكرة.** كل idempotency وكل ترتيب وكل عدّاد صحّي مصدره SQL أو Redis الدائم، لا متغيّر في العملية. تشغيل نسختين من العامل في وقت واحد يجب أن يبقى صحيحاً (لا تفترض نسخة واحدة أبداً، حتى لو نشرنا واحدة).

**H19 — نص العميل بيانات لا تعليمات.** كل نص/وسيط وارد من العميل النهائي يُخزَّن كما هو ولا يُفسَّر ولا يُنفَّذ ولا يُستخدَم في بناء استعلام أو مسار أو مفتاح Redis إلا بعد تطبيع صريح مكتوب. لا تكتب في هذه الدفعة أي منطق يتصرّف بناءً على معنى النص عدا كشف opt-out الحتمي المحدَّد في القسم 7.

**H20 — الخصوصية في السجلات.** **لا يُسجَّل نص رسالة العميل ولا أي جزء منه في أي سجل أو مقياس أو تسمية (label) إطلاقاً** — لا في التصحيح ولا في الخطأ. المسموح: `tenant_id`, `session_id`, `channel_account_id`, `conversation_id`, `provider_message_id`, `type`, `outcome`, `duration_ms`, وطول النص عدداً. الهواتف مقنّعة (آخر 3 خانات). لا تسمية مقياس ذات عدد قيم غير محدود (لا `session_id` ولا `tenant_id` كـlabel في Prometheus — كارثة 16).

**H21 — لا انحدار.** الاختبارات القائمة كلها تبقى خضراء **دون تعديل**: `core/tests` (240/240) وكل مجموعة `gateway`. أي تعديل في `gateway/` في هذه الدفعة **إضافي فقط** ولا يمسّ عقد Django ولا يغيّر سلوك المتاجر على `engine=django`.

### 4.1 توسيع بوابة الفحص الآلي (تكتبه في P1.0)

- `scripts/hunt_gate.mjs`: تأكّد أن قواعده الثماني تغطي فعلياً المسارات الجديدة (`core/app/workers/**`، `core/app/db/repos_ingest.py`، `core/app/obs/**`). القاعدة 6 (لا `psycopg` خارج `core/app/db/`) والقاعدة 8 (لا SQL في `core/` خارج `core/app/db/`) **يجب أن تُطبَّق على العامل كما تُطبَّق على `api`** — إن لم تكن كذلك، أصلح السكربت وأرفق مخرج فشل متعمَّد يثبت أن القاعدة تعمل (اكتب ملفاً مخالفاً مؤقتاً، أرفق فشل البوابة، ثم احذفه).
- أضف قاعدة تاسعة: **ممنوع `XACK` في `core/` في أي موضع غير `core/app/workers/stream.py`** (نقطة واحدة تملك الإقرار — H17 قابلة للفحص آلياً).
- `core/.importlinter`: أضف `app.workers` إلى `source_modules` في عقد `no-direct-psycopg-outside-db-layer`، وأضف عقداً جديداً: `app.workers` **ممنوع** أن يستورد `app.api` أو `app.main` (العامل لا يعتمد على طبقة HTTP إطلاقاً)، و`app.api` ممنوع أن يستورد `app.workers`.
- `ruff` + `mypy --strict` يمرّان على الكود الجديد بلا `# noqa` ولا `# type: ignore` غير مبرَّر في `P1_DEVIATIONS.md`.

---

## 5. النطاق

### 5.1 داخل النطاق (وحده)

| الرمز | البند | كوارث |
|---|---|---|
| W1 | حزمة `core/app/workers/` ودور تشغيل `worker-realtime` (نقطة دخول، إعداد، إيقاف رشيق) | 16، 17 |
| W2 | `core/app/obs/`: سجلات JSON بحقول H12 الثابتة + سجل مقاييس Prometheus للعامل + خادم HTTP صغير `/healthz` و`/metrics` (Bearer، مقارنة بزمن ثابت، رفض إقلاع بسرّ فارغ) | 16، 21 |
| W3 | عميل `redis-durable` في `core/` (مهلة صريحة، بلا طابور offline، انتظار `ready` قبل أول أمر) | 5، 17 |
| W4 | مستهلك مجموعة `ai-core-ingest` على `in:{0..N-1}` مع `XAUTOCLAIM` وDLQ محدود | 4، 5، 17 |
| W5 | المعاملة الذرّية الواحدة: `inbound_events` → `customers` → `conversations` → `messages` (+ إعادة فتح محادثة مغلقة) | 1، 5، 11 |
| W6 | كشف opt-out حتمي قبل أي شيء آخر + كتابة `suppressions` | 3 (أساس) |
| W7 | معالجة `human_takeover_signal` عبر `messages(direction='out', sent_by='staff')` فتتولّى الزناد إيقاف البوت ورفع `epoch` في المعاملة نفسها | 11 |
| W8 | معالجة `identity_update` (تحديث `customers.phone_e164` لهوية `lid`) | — |
| W9 | خدمة `worker-realtime` في `docker-compose.yml` بحدود موارد + هدف Prometheus + 4 قواعد تنبيه | 16، 21 |
| W10 | اختبارات pytest حقيقية (PostgreSQL + PgBouncer + `redis-durable` حقيقيان) + سيناريوهات فوضى القسم 8 | كل ما سبق |
| W11 | إصلاح عيب نافذة dedupe (القسم 7، الخطوة P1.1.7) إن أثبتتَ وجوده | 5 |

### 5.2 خارج النطاق (ممنوع بتاتاً في هذه الدفعة)

خط أنابيب الدور، أي LLM أو Router أو Verifier أو تضمين، `outbox` والـDispatcher وأي إرسال صادر من `core` (لا `POST /sessions/:id/send` من العامل إطلاقاً)، رسالة تأكيد opt-out للعميل (تحتاج outbox ⇒ P1.2)، WebSocket و`inbox_events` والـInbox API، الكتالوج والبحث، تتبع الطلبات ورابط الدفع وأي نداء لـ`sharwa_saas`، Celery وأي `worker-bulk`/`worker-batch`/`scheduler`، `console/` وأي فرونت إند، الملخص المتدحرج والفتحات (`slots`)، السياسة التسويقية الكاملة، لمس مشروع `sharwa_saas` أو قاعدة بياناته بأي شكل.

**ملاحظة صريحة:** كتابة `needs_turn = true` مسموحة ومطلوبة (الزناد يفعلها)، لكن **لا شيء يقرأها في هذه الدفعة**. هذا مقصود: تتركها كإشارة نظيفة لمرحلة P1.2، ولا تبني مستهلكاً لها الآن.

---

## 6. العقود الثابتة — استخرجتُها من الكود الحي، لا تخمّن فيها

### 6.1 عقد كيان الـWAL (`in:{shard}`) — المُنتج: `gateway/src/ingest/wal.js` + `normalize.js` + `sessions.js`

كل كيان في الـstream حقل واحد اسمه **`data`** قيمته JSON. الشكل (v=1):

```json
{
  "v": 1,
  "session_id": "<opaque، يولّده core وحده>",
  "provider_message_id": "<waId>",
  "type": "text|image|audio|video|document|location|contact|sticker|reaction|unsupported|identity_update|human_takeover_signal",
  "ts": 1758900000000,
  "identity": { "jid_raw": "…", "addressing": "pn|lid", "wa_id": "<أرقام بلا +>", "phone_e164": "+9677…|null" },
  "text": "…",
  "media":   { "kind": "image|audio|video|document|sticker", "mimetype": "…|null", "fileLength": 1234, "fileName": "…", "object_key": "…", "status": "ok|failed" },
  "location": { "lat": 15.3, "lng": 44.2, "name": "…", "address": "…", "is_live": false },
  "contact": { "name": "…", "numbers": ["9677…"] },
  "reaction": { "text": "👍", "target_message_id": "…" },
  "engine": "ai_core"
}
```

قواعد ملزِمة مستخرجة من الكود:

1. **`shardFor(session_id) = djb2(session_id) % INGEST_SHARDS`** (`wal.js`) — نفس الجلسة على نفس الشريحة دائماً. افتراضي الشرائح **4** (`INGEST_SHARDS`). **مصدر القيمة واحد لا اثنان** (القسم 7، P1.0.5).
2. **`engine` حقل اختياري.** موجود بقيمة `ai_core` فقط للقنوات التي أنشأها `core`. غيابه = المسار القديم (Django). **هو تلميح توجيه للـforwarder فقط**: عامِلك **لا يثق به في شيء يتعلق بالتينانت**، بل يحلّ الجلسة من قاعدة البيانات (البند 4 أدناه). قرار تخطّي الكيان يُبنى على **نتيجة `app.resolve_session`**، لا على `entry.engine`.
3. **`type='identity_update'`** و**`type='human_takeover_signal'`** ليسا رسائل عميل. الـforwarder يتجاهلهما بالتصميم لأنهما **موجودان لأجلك** (تعليقاته تقول ذلك حرفياً). `human_takeover_signal` يحمل `direction:'outbound_human'` و`identity` = محادثة العميل الذي أُرسل إليه (لا المرسل)، ونصه نص رسالة الموظف.
4. **`session_id` معتم.** الجسر الوحيد إلى المتجر هو `SELECT * FROM app.resolve_session($1)` (SECURITY DEFINER، مصرَّح به لـ`sharwa_system`) ويعيد `(channel_account_id, tenant_id, engine)`. جلسة غير معروفة ⇒ لا صفوف. **لا تستنتج `tenant_id` من أي حقل في الكيان ولا من أي نص.** (H2)
5. **الكيان ≤ 64KB** (`MAX_VALUE_BYTES`) والبوابة ترفض الأكبر قبل الكتابة، فلا داعي لحماية حجم إضافية — لكن **تكتب سقفاً لطول النص المخزَّن على أي حال** (H4).
6. **التقليم:** الـXADD يستخدم `MAXLEN ~ 100000` لكل stream. مجموعتك الجديدة **لا تحذف كياناً** (`XDEL` ممنوع) ولا تغيّر التقليم؛ وتراكم تأخيرك فوق 100k يعني **ضياعاً فعلياً**، لذلك مقياس التأخير وتنبيهه (القسم 7) شرط قبول لا رفاهية.
7. **مجموعتان مستقلتان على نفس الـstream** (`legacy-forwarder` و`ai-core-ingest`) سلوك صحيح في Redis Streams: كل مجموعة تستلم كل الكيانات وتُقرّ باستقلال. **لا تلمس مجموعة `legacy-forwarder` ولا PEL الخاص بها إطلاقاً.**

### 6.2 عقد قاعدة البيانات — `docs/reference/schema.sql` (لا يُعدَّل، H14)

- **`inbound_events`** — `UNIQUE (channel_account_id, provider_message_id)`: خط الدفاع الثاني والدائم ضد الازدواج (لا يعتمد على Redis).
- **`customers`** — `UNIQUE (tenant_id, wa_id)`. `wa_id` **قد لا يكون رقم هاتف** (`lid`)؛ `phone_e164` قابل لـNULL ولا يُختلق أبداً.
- **`conversations`** — `UNIQUE (tenant_id, channel_account_id, customer_id)`. الأعمدة الحاكمة: `bot_status ∈ (active, paused_human, closed)`, `epoch`, `version`, `needs_turn`, `message_seq`, `last_inbound_seq`, `last_processed_seq`, `summary`, `slots`.
- **المنح الحرجة (اقرأها في `schema.sql` بنفسك):** `REVOKE UPDATE ON conversations FROM sharwa_app` ثم `GRANT UPDATE (summary, slots, needs_turn, last_processed_seq, assigned_staff_id, last_message_at)`. أي محاولة تحديث `bot_status`/`epoch`/`version` مباشرة **ستُرفض على مستوى القاعدة**، وهذا مقصود: **الطريق الوحيد** هو `app.set_bot_status(p_conv, p_expected_version, p_new_status, p_reason, p_staff)` (SECURITY DEFINER، قفل تفاؤلي، يعيد `epoch` الجديد أو `NULL` إن كانت النسخة قديمة).
- **الزناد `app.trg_messages_seq()`** (BEFORE INSERT على `messages`) يفعل تلقائياً: إسناد `seq`، ورفع `message_seq`، وتحديث `last_inbound_seq` و`needs_turn` للوارد حين `bot_status='active'`، و`last_message_at`؛ **وإذا كان `direction='out'` و`sent_by='staff'` يضع `bot_status='paused_human'` ويرفع `epoch` و`version` في المعاملة نفسها** (كارثة 11). **لا تعد تنفيذ أي من ذلك في بايثون** — استخدم الزناد كما هو، واختبر أنه فعل ما يجب.
- **قبل أي شيء آخر:** أثبِت بمخرج حقيقي أن `INSERT INTO messages` **بدور التطبيق `sharwa_app` عبر PgBouncer** ينجح وأن `seq` و`needs_turn` أُسندا فعلاً (الزناد يحدّث أعمدة `message_seq`/`last_inbound_seq` غير المشمولة بمنح الأعمدة أعلاه؛ `schema_selftest.sql` يمرّ 34/34 بما يشمل إدخال رسائل بهذا الدور، فالسلوك المتوقع هو النجاح). **إن ظهر `permission denied` فهذه فجوة منح حقيقية**: سجّلها كـ`F-P1-xx` في `P1_FINDINGS.md`، وأصلحها بـ**ترحيل أمامي جديد** `core/migrations/0003_p1_ingest.sql` (مثلاً جعل الزناد `SECURITY DEFINER` بـ`search_path` مقيَّد، أو منح العمودين صراحةً — اختر الأضيق نطاقاً وبرّر الاختيار)، **ولا تعدّل `0001_baseline.sql` ولا `docs/reference/schema.sql` أبداً**، ولا تعطِ `sharwa_app` منحاً أوسع مما يلزم.
- **`suppressions`** — `PRIMARY KEY (tenant_id, customer_id, scope)`، أعمدتها `scope`, `reason`, `created_at`. لا عمود آخر.
- **RLS:** كل جدول فيه `tenant_id` عليه سياسة `tenant_isolation USING (tenant_id = app.current_tenant())`. معاملة بلا `SET LOCAL app.tenant_id` ترى **صفر صفوف** ولا تكتب. مدخلك الوحيد `tenant_tx()`.

### 6.3 العقد المجمّد مع Django (H9)

لا تلمسه بحرف. مساري الويبهوك وحقولهما وترويسة `X-API-Key` وشكل استجابة الحالة واختبارات العقد الأربعة تبقى كما هي. المتاجر على `engine=django` (أو بلا `engine`) يجب أن تستمر تماماً كاليوم عبر `legacy-forwarder`، وهذا شرط قبول مُقاس (القسم 8، A5).

**ملاحظة معمارية تحسم قلقاً محتملاً:** حتى لو وصل كيان جلسة `ai_core` إلى Django بالخطأ، فـ`session_id` الخاص بقنوات `core` غير مسجَّل في `WhatsAppSessionIndex` عند Django، فيرد `200 {"status":"unknown_session_ignored"}` بلا أي أثر. لا يبرّر ذلك تكراراً — لكنه يعني أن اتجاه الفشل هنا آمن، فلا تبنِ حماية معقّدة ضده.

---

## 7. خطوات التنفيذ — بهذا الترتيب

### P1.0.1 — استطلاع وخط أساس (لا كود إنتاجي)

1. اقرأ كل ملفات القسم 2. سجّل في `docs/P1_PROGRESS.md` جدولاً: الملف ← ما استخلصته منه في سطر ← أي تعارض مع القسم 6 أعلاه.
2. أثبِت خط الأساس بمخرجات حقيقية: `git log --oneline -5`، `git status --porcelain` (يجب أن يكون نظيفاً قبل أن تبدأ)، `git ls-files --eol` (لا `w/crlf`)، `node scripts/hunt_gate.mjs`، `python -m pytest core/tests -q` (240/240)، `node --test` لحزم البوابة، `ruff check core`، `mypy --strict core/app`، `lint-imports`.
3. أثبِت البنية الحقيقية: `docker compose config` (بلا أخطاء)، `docker compose up -d` والأربع+ خدمات `healthy`، ثم `docker compose exec redis-durable redis-cli -a *** XINFO STREAM in:0` و`XINFO GROUPS in:0` (سجّل المجموعات القائمة فعلاً وأطوال الـstreams).
4. أثبِت أرقام PgBouncer الفعلية: `max_client_conn` و`default_pool_size` و`pool_mode` من `ops/pgbouncer.ini` (اقرأه، لا تفترضه) + عدد الاتصالات الحالية. **ستحتاجها في P1.0.4 لتحديد حجم مجمّع العامل بلا تجاوز.**
5. أثبِت إدخال رسالة بدور `sharwa_app` عبر PgBouncer كما في §6.2 (الفقرة «قبل أي شيء آخر»)، وأرفق المخرج. هذه بوابة: لا تتقدّم قبل حلّها أو تسجيل الفجوة وإصلاحها بترحيل جديد.

### P1.0.2 — طبقة الرصد `core/app/obs/`

- `logging.py`: مُهيّئ سجلات JSON (stdlib `logging` + مُنسّق JSON خاص — لا مكتبة جديدة إلا بتبرير H11) بحقول ثابتة في كل سطر: `ts`, `level`, `event`, `component`, `tenant_id?`, `session_id?`, `conversation_id?`, `provider_message_id?`, `type?`, `outcome?`, `duration_ms?`. دالة تقنيع هاتف (آخر 3 خانات) تُستخدم في كل موضع يظهر فيه رقم. **ممنوع `print(`** (بوابة الهنت تفحصه) و**ممنوع نص الرسالة** (H20).
- `metrics.py`: `CollectorRegistry` خاص بالعامل + كل العائلات المذكورة في P1.1.6. **لا تسمية عالية التعدد** (H20).
- `http.py`: خادم HTTP صغير (stdlib `http.server` في خيط مستقل، أو Uvicorn إن برّرت) يخدم `GET /healthz` (مفتوح، يعيد 200 فقط حين حلقة الاستهلاك حيّة ومتصلة بـRedis وPostgres) و`GET /metrics` (Bearer `METRICS_TOKEN`، مقارنة `hmac.compare_digest`، رفض إقلاع بسرّ فارغ — نفس شكل `gateway/src/forwarder.js`، اقرأه واتبعه). المنفذ `CORE_WORKER_METRICS_PORT` افتراضي **4003** (4001 للبوابة، 4002 للـforwarder، 8080 لـ`api`).

### P1.0.3 — إعداد العامل بلا كسر `api`

- **حرج:** `Settings.load()` الحالية يستعملها `api`، وخدمة `api` في compose **لا تمرّر** أي متغيّر `REDIS_DURABLE_*`. لو أضفت حقلاً مطلوباً إلى `Settings` فسيتوقف `api` عن الإقلاع. لذلك أنشئ **إعداداً منفصلاً**: `WorkerSettings` (dataclass مجمَّد بنفس أسلوب `config.py`، بدواله `_required`/`_int` نفسها) في `core/app/config.py` أو `core/app/workers/config.py`، يُحمَّل **من نقطة دخول العامل وحدها**، ولا يُلمس `Settings` القائم ولا `/readyz` الخاص بـ`api`.
- المتغيّرات (كلها بسقوف افتراضية موثّقة — H4):

| المتغيّر | افتراضي | الغرض |
|---|---|---|
| `REDIS_DURABLE_HOST` / `_PORT` / `_PASSWORD` | — / 6379 / — | مطلوبان (كلمة السر بلا افتراضي: H5) |
| `REDIS_DURABLE_TIMEOUT_MS` | 5000 | مهلة أمر صريحة (H3) |
| `INGEST_SHARDS` | 4 | **يجب أن يساوي قيمة البوابة** — انظر P1.0.5 |
| `CORE_INGEST_GROUP` | `ai-core-ingest` | اسم مجموعة المستهلك |
| `CORE_INGEST_BATCH` | 50 | `COUNT` لكل قراءة |
| `CORE_INGEST_BLOCK_MS` | 3000 | **أقل من `REDIS_DURABLE_TIMEOUT_MS` بهامش** — عيب F-P0 حقيقي وقع في الـforwarder بسبب تساويهما؛ اقرأ تعليق `BLOCK_MS` في `forwarder.js` ولا تكرّره |
| `CORE_INGEST_CLAIM_IDLE_MS` | 30000 | حد الخمول لـ`XAUTOCLAIM` |
| `CORE_INGEST_MAX_ATTEMPTS` | 10 | سقف محاولات الكيان قبل DLQ |
| `CORE_INGEST_DLQ_STREAM` | `dlq:in:core` | **مستقل عن `dlq:in` الخاص بالـforwarder** |
| `CORE_INGEST_MAX_BODY_CHARS` | 65536 | سقف طول النص المخزَّن (تقليم مُحتسَب ومُسجَّل، لا صامت) |
| `CORE_INGEST_SHUTDOWN_TIMEOUT_S` | 20 | سقف الإيقاف الرشيق (< `stop_grace_period`) |
| `CORE_WORKER_METRICS_PORT` | 4003 | خادم الرصد |
| `CORE_OPTOUT_PHRASES_AR` / `_EN` | القائمة في P1.1.5 | قابلة للتعديل بلا نشر كود |

- عميل `redis-durable`: مهلة أمر صريحة، **بلا طابور offline**، و**انتظار حالة `ready` قبل أول أمر** (عيب F حقيقي أسقط الـforwarder في حلقة إعادة تشغيل — اقرأ `waitForReady` في `forwarder.js` واتبع منطقه).
- مجمّع PostgreSQL: العامل يستخدم `init_pool()` القائم كما هو. اشترط في الإعداد أن `CORE_DB_POOL_MAX ≥ INGEST_SHARDS + 1` وافشل بـ`ConfigError` عند الإقلاع إن لم يتحقق، وتحقق أن المجموع لا يتجاوز `default_pool_size` الذي قرأته في P1.0.1 (سجّل الحساب في التقرير).

### P1.0.4 — نقطة الدخول والإيقاف الرشيق

- `core/app/workers/__init__.py` و`core/app/workers/realtime.py` بنقطة دخول `python -m app.workers.realtime`.
- التشغيل: خيط واحد لكل شريحة (`INGEST_SHARDS` خيطاً)، **وكل شريحة تُعالَج تسلسلياً داخل خيطها** — لا معالجة متوازية لكيانين من نفس الشريحة أبداً (حفظ ترتيب رسائل الجلسة الواحدة، لأن `shardFor` يثبّت الجلسة على شريحة واحدة).
- `SIGTERM`/`SIGINT`: توقّف عن قراءة الجديد، أكمل ما في اليد، `XACK` لما التزم، أغلق المجمّعات والعميل، واخرج بـ`0` خلال `CORE_INGEST_SHUTDOWN_TIMEOUT_S`. سجّل زمن الإيقاف المقاس فعلياً.
- `unhandled exception` في أي خيط: تسجيل بسياق كامل + زيادة عدّاد + **إيقاف رشيق للعملية كلها** (fail fast، H3) ليعيد Docker تشغيلها — لا خيط ميت بصمت.
- `/healthz` يعيد 200 فقط إذا: كل الخيوط حيّة، وآخر دورة ناجحة لكل شريحة أحدث من عتبة مكتوبة، والاتصالان (Redis/PG) سليمان.

### P1.0.5 — compose والمراقبة

- خدمة جديدة `worker-realtime`:
  - نفس صورة `core` مرة واحدة لا مرتين: أضف وسماً مشتركاً (مثل `image: sharwa-ai-core:p1.0`) إلى `api` و`worker-realtime` بنفس نمط `gateway`/`gateway-forwarder` القائم، و`command: ["python", "-m", "app.workers.realtime"]`.
  - البيئة: `CORE_DATABASE_URL` (عبر **pgbouncer**)، `CORE_SYSTEM_DATABASE_URL` (عبر **postgres** مباشرة، كما في `api`)، `REDIS_DURABLE_*`، `METRICS_TOKEN`، و`INGEST_SHARDS`.
  - **مصدر واحد للشرائح:** مرّر `INGEST_SHARDS: ${INGEST_SHARDS:-4}` إلى **`gateway` و`gateway-forwarder` و`worker-realtime` من نفس المتغيّر**، وأضفه إلى `.env.example` بتعليق يقول إن تغييره يتطلّب إيقاف الخدمات الثلاث معاً (الشرائح ليست قابلة لإعادة التوزيع الحيّ). هذا يمنع انحرافاً يترك شريحة كاملة بلا مستهلك — أخطر عيب صامت محتمل في هذه الدفعة.
  - الحدود: `mem_limit`/`memswap_limit` **384m**، `cpus: 0.75`، `pids_limit: 100`، `stop_grace_period: 30s`، `logging: *default-logging`، `restart: unless-stopped`، `networks: [data]`، `healthcheck` على `/healthz`، `depends_on`: `postgres`+`pgbouncer`+`redis-durable` بحالة `service_healthy`.
  - **أعد حساب مجموع `mem_limit` وأرفقه** (3888m + 384m = 4272m مقابل سقف D1 = 5.2GB) مع `docker stats --no-stream` تحت حمل A14.
- `ops/prometheus/prometheus.yml`: أضف هدفين — `worker-realtime:4003` و`api:8080` (تعليق compose الحالي يقول صراحةً إن وصل `api` «متابعة صغيرة منفصلة»؛ أنجزها هنا). أرفق `promtool check config` و`promtool check rules` ومخرج `/api/v1/targets` يثبت `up=1` للهدفين فعلياً.
- `ops/prometheus/alerts.yml`: أربع قواعد جديدة بعتبات مكتوبة ومبرَّرة: (أ) تأخير مجموعتك على أي شريحة > 1000 لمدة 2د، (ب) أي زيادة في DLQ خلال 5د، (ج) حجم PEL > 500 لمدة 5د، (د) `up == 0` للعامل لمدة 1د.

### P1.1.1 — قراءة الـstream وحدود المجموعة

- `core/app/workers/stream.py`: **النقطة الوحيدة** التي تنفّذ `XREADGROUP`/`XAUTOCLAIM`/`XACK`/`XADD` على DLQ (H17 + قاعدة الهنت التاسعة).
- الإقلاع: `XGROUP CREATE in:{i} ai-core-ingest $ MKSTREAM` لكل شريحة، وتجاهل `BUSYGROUP` فقط. **قرار معتمد: ابدأ من `$` لا من `0`** — الكيانات القديمة التي وصلت قبل وجود العامل تخصّ متاجر Django وقد سُلّمت سلفاً، وقراءتها من `0` تعني إعادة معالجة تاريخ كامل. **سجّل هذا القرار في `P1_DEVIATIONS.md`** ببيان أثره (لا يضيع شيء من متاجر `ai_core` لأن أول قناة `ai_core` لا توجد بعد).
- كل دورة لكل شريحة: `XAUTOCLAIM` أولاً (استرجاع المتروك من مستهلك ميت) ثم `XREADGROUP … BLOCK` للجديد، ثم معالجة تسلسلية.
- اسم المستهلك مستقر ومميّز لكل عملية (`core-ingest-{hostname}-{pid}`).
- كيان `fields` فارغ (من `XAUTOCLAIM` لكيان محذوف) ⇒ `XACK` وتابع.
- JSON تالف أو غير مطابق للمخطط ⇒ **`XADD` إلى `CORE_INGEST_DLQ_STREAM` ثم `XACK`** + عدّاد + سجل بمستوى `error`، ولا حلقة سامة أبداً.
- خطأ عابر (Redis/PG/شبكة) ⇒ **لا `XACK`**، backoff مع jitter، والكيان يبقى في PEL. عدّاد محاولات لكل كيان في Redis (`core:fwd:attempts:{stream}:{id}` بانتهاء 7 أيام) وعند `CORE_INGEST_MAX_ATTEMPTS` ⇒ DLQ + `XACK` + سجل صارخ (نفس نمط الـforwarder — اقرأه واتبعه).

### P1.1.2 — التحقق من المخطط وحلّ الجلسة

- `WalEntry` بـPydantic: أنواع صريحة لكل حقل في §6.1، و**`extra='ignore'`** (لا `forbid`) مع **عدّاد `ingest_core_unknown_fields_total` وسجل واحد لكل حقل مجهول جديد**: العقد ينصّ على أن كل توسعة من البوابة «إضافية اختيارية»، ورفض حقل جديد يجعل أي إضافة مستقبلية في البوابة عطلاً فورياً في الاستيعاب. التسامح هنا ليس صمتاً — إنه تسامح **محسوب ومُقاس**.
- `type` غير معروف كلياً ⇒ خزّنه كـ`unsupported` (لا تُسقِطه) — نفس مبدأ البوابة: لا حدث يُهمَل بصمت.
- حلّ الجلسة: `system_tx()` + `app.resolve_session(session_id)`:
  - لا صفوف ⇒ `XACK` + `ingest_core_unknown_session_total` + سجل `info` (بلا رقم هاتف). لا خطأ ولا DLQ: البوابة أرسلت بحسن نية.
  - `engine <> 'ai_core'` ⇒ `XACK` + `ingest_core_skipped_total{reason="engine_not_ai_core"}`. **لا كتابة** (الـforwarder يملك هذه الرسالة).
  - يوجد ⇒ تابع بـ`tenant_id` و`channel_account_id` **من القاعدة وحدها** (H2).
- ذاكرة تخزين مؤقت للحلّ مسموحة بشرطين: سقف عدد مكتوب (LRU محدود، H4) وعمر قصير مكتوب (≤ 30ث)، **و`engine` لا يُخزَّن مؤقتاً أطول من ذلك** لأن ترحيل متجر من Engine v1 يجب أن يسري بسرعة.

### P1.1.3 — المعاملة الذرّية الواحدة (لبّ الدفعة)

كل SQL في `core/app/db/repos_ingest.py` (داخل `core/app/db/` ⇒ قاعدتا الهنت 6 و8 محفوظتان). المعاملة واحدة عبر `tenant_tx(tenant_id)`، وبهذا الترتيب بالضبط:

1. **`inbound_events`**: `INSERT … (tenant_id, channel_account_id, provider_message_id) ON CONFLICT (channel_account_id, provider_message_id) DO NOTHING RETURNING id`. لا صف ⇒ **مكرَّر**: أنهِ المعاملة بلا كتابة أخرى، `XACK`، `ingest_core_duplicates_total`. (هذا هو الضمان الدائم ضد الازدواج — لا يعتمد على Redis إطلاقاً.)
2. **`customers`**: `INSERT … (tenant_id, wa_id, phone_e164, display_name) ON CONFLICT (tenant_id, wa_id) DO UPDATE SET phone_e164 = COALESCE(EXCLUDED.phone_e164, customers.phone_e164), display_name = COALESCE(EXCLUDED.display_name, customers.display_name) RETURNING id`. **لا تمسح قيمة موجودة بـNULL أبداً** ولا تختلق هاتفاً من `wa_id`.
3. **`conversations`**: `INSERT … (tenant_id, channel_account_id, customer_id) ON CONFLICT (tenant_id, channel_account_id, customer_id) DO UPDATE SET last_message_at = now() RETURNING id, bot_status, epoch, version`. (`last_message_at` من الأعمدة المصرَّح بتحديثها؛ راجع §6.2.)
4. **إعادة فتح محادثة مغلقة**: إن كان `bot_status='closed'` ووصلت رسالة عميل جديدة ⇒ `app.set_bot_status(conv, version, 'active', 'reopened_by_customer', NULL)` **قبل** إدخال الرسالة (ليعمل شرط الزناد على `needs_turn`). النتيجة `NULL` (نسخة قديمة) ⇒ أعد قراءة الصف وأعد المحاولة مرة واحدة داخل نفس المعاملة، ثم اعتبرها خطأً عابراً. `paused_human` **تُترك كما هي** (الزناد لن يضع `needs_turn` — وهذا هو السلوك الصحيح: البوت لا يقاطع موظفاً).
5. **`messages`**: إدخال واحد. `direction='in'`, `sent_by='customer'`, `type` من الكيان، `body` = النص (مقلَّم عند `CORE_INGEST_MAX_BODY_CHARS` مع عدّاد)، `media_object_key` = `media.object_key` إن كان `status='ok'` وإلا `NULL`، `media_type` = `media.kind`، `location` = JSONB بالحقول الأربعة كما جاءت (`is_live` مسموح إضافةً)، `provider_message_id`, `status='received'`. **`seq` لا تُسنده أنت** — الزناد يفعل.
   - `type='contact'` و`type='reaction'`: خزّن الرسالة بنوعها، وضع الحمولة الخاصة في `body` كنص مقروء **أو** اتركها بلا حمولة ووثّق القرار في `P1_DEVIATIONS.md`. **لا تضف عموداً ولا جدولاً** لأجلهما في هذه الدفعة.
6. **الآثار الحتمية** (نفس المعاملة): كتابة `suppressions` عند opt-out (P1.1.5).
7. `COMMIT` ⇒ ثم فقط `XACK` ⇒ ثم المقاييس.

**قواعد لا تُخالف:** لا `SELECT`/`INSERT` خارج `repos_ingest.py`؛ لا `autocommit`؛ لا معاملة ثانية «للتنظيف»؛ لا استعلام بلا معاملات مُمَرَّرة (`%s` دائماً، لا تنسيق نصي — H19)؛ خطأ `UniqueViolation` على `messages(conversation_id, seq)` أو غيره ⇒ خطأ عابر يُعاد لا يُبتلع.

### P1.1.4 — `human_takeover_signal` و`identity_update`

**`human_takeover_signal`** (كارثة 11 — أهم بند في هذه الدفعة بعد المعاملة نفسها):

1. نفس خطوات 1–3 أعلاه (حدث + عميل + محادثة) — الإشارة تمرّ بـ`inbound_events` أيضاً لأنها تحمل `provider_message_id` فيصير ضمانها ضد الازدواج مجانياً وموحّداً.
2. أدخِل في `messages`: `direction='out'`, `sent_by='staff'`, `staff_id=NULL` (رد من هاتف التاجر نفسه، لا موظف معروف في النظام), `type='text'`, `body` = نص الموظف, `status='sent'`, `provider_message_id`.
3. **لا تنفّذ إيقاف البوت في بايثون.** الزناد `app.trg_messages_seq()` يفعل ذلك في المعاملة نفسها: `bot_status='paused_human'`, `epoch+1`, `version+1`, `handoff_reason='staff_replied'`. أثبِت بمخرج حقيقي أن القيم الأربع تغيّرت، وأن `needs_turn` **لم** يُضبط، وأن `last_inbound_seq` **لم** يتغيّر.
4. مقياس `handoff_transitions_total{reason="staff_replied"}`.
5. **ممنوع** أن تمرّ هذه الإشارة كرسالة عميل بأي حال (الحلقة اللانهائية تبقى مستحيلة بنية البيانات لا بالنيّة).

**`identity_update`**: حدّث `customers.phone_e164` للعميل صاحب `wa_id` (من `identity`) عبر نفس upsert الخطوة 2، بلا أي صف في `messages`، بلا لمس `conversations` عدا ما يلزم. لا صف ⇒ أنشئ العميل. عدّاد `ingest_core_identity_updates_total`.

### P1.1.5 — كشف opt-out الحتمي (قبل أي شيء غير حتمي، إلى الأبد)

- التطبيع: تقليم، توحيد حالة الأحرف، إزالة التشكيل، توحيد الألف (أ/إ/آ ⇒ ا) والياء (ى ⇒ ي) والتاء المربوطة (ة ⇒ ه)، تحويل الأرقام العربية-الهندية إلى لاتينية، إزالة محارف التحكم وBidi والمسافات الصفرية، وضغط المسافات. دالة واحدة مختبَرة بجدول حالات.
- المطابقة: **قائمة عبارات مُعدّة** (`CORE_OPTOUT_PHRASES_AR`/`_EN`) بمطابقة **الرسالة كاملةً بعد التطبيع** أو بدايتها، لا مطابقة جزئية وسط النص. الافتراضي العربي: `إيقاف`, `ايقاف`, `توقف`, `الغاء الاشتراك`, `إلغاء الاشتراك`, `لا اريد`, `لا أريد`, `لا ترسل`, `اوقف الرسائل`. الإنجليزي: `stop`, `unsubscribe`, `opt out`, `optout`.
  **قرار معتمد: `cancel`/`الغاء` وحدهما ليستا opt-out** (تعني «إلغاء طلب» في سياق تجاري). سجّل القرار في `P1_DEVIATIONS.md`.
- الأثر: `INSERT INTO suppressions (tenant_id, customer_id, scope, reason) VALUES (…, 'marketing', 'customer_message_optout') ON CONFLICT DO NOTHING` **في نفس المعاملة**، وكذلك `scope='back_in_stock'` و`'review_request'`، **دون** حجب `order_updates` ولا أي رسالة خدمة (اتجاه الفشل: تسويق fail-closed، خدمة للعميل fail-open — H4).
- **رسالة التأكيد للعميل مؤجَّلة إلى P1.2** (تحتاج `outbox` وهو خارج النطاق). وثّق ذلك صراحةً في `P1_OPEN_QUESTIONS.md` كدين معروف، ولا ترسل شيئاً من العامل.
- مقياس `optout_detected_total{lang}`. **لا تسجّل النص** (H20).

### P1.1.6 — المقاييس والسجلات (H12)

عائلات إلزامية على سجل العامل، بلا تسميات عالية التعدد:

`ingest_core_messages_total{type,outcome}` (`outcome ∈ committed|duplicate|skipped|unknown_session|dlq`)، `ingest_core_commit_seconds` (histogram: من قراءة الكيان إلى `COMMIT`)، `ingest_core_ack_seconds` (histogram: إلى `XACK`)، `ingest_core_duplicates_total`، `ingest_core_unknown_session_total`، `ingest_core_skipped_total{reason}`، `ingest_core_dlq_total{reason}`، `ingest_core_unknown_fields_total`، `ingest_core_body_truncated_total`، `ingest_core_identity_updates_total`، `handoff_transitions_total{reason}`، `optout_detected_total{lang}`، `ingest_core_stream_lag{shard}` (gauge من `XINFO GROUPS`)، `ingest_core_pel_size{shard}` (gauge)، `ingest_core_last_success_timestamp{shard}` (gauge)، `core_worker_up` (gauge).

جامع التأخير يستعلم `XINFO GROUPS` بفاصل مكتوب (≤ 15ث) ولا يحجب حلقة الاستهلاك.

### P1.1.7 — تحقيق في نافذة dedupe (عيب محتمل ورثناه من P0)

قراءتي للكود تقول: `fusedAppend` يضع علامة dedupe بعمر **`DEDUPE_PENDING_TTL_S` = 60 ثانية**، و`markDone` (التي تمدّدها إلى `DEDUPE_DONE_TTL_S` = 172800) **لا تُستدعى إلا في مسار `phoneNumberShare`** — الـforwarder لا يستدعيها بعد التسليم الناجح. إن صحّ ذلك، فنافذة dedupe الفعلية على Redis **60 ثانية** لا 48 ساعة، ووعد G2 غير محقَّق، ويبقى `UNIQUE inbound_events` هو الحامي الوحيد لإعادة تسليم متأخرة.

المطلوب منك بهذا الترتيب:

1. **أثبِت أو انفِ بمخرج حقيقي**: بعد استيعاب رسالة، `TTL dedupe:{session}:{msg_id}` على `redis-durable` — أرفق الرقم.
2. إن ثبت: سجّله كـ`F-P1-01` في `P1_FINDINGS.md` بالأثر المقاس، ثم أصلحه في **الموضعين**: عامِلك يمدّد العلامة بعد `COMMIT` بنفس دلالة `markDone` بالضبط (**مدّد فقط إن كانت العلامة موجودة**؛ `SET` جديد يُحيي علامة انتهت بحق فيحجب إعادة تسليم مشروعة)، والـforwarder يفعل الشيء نفسه بعد تسليم ناجح لـDjango (تغيير إضافي في ملف يملكه أصلاً، بلا مسّ العقد).
3. أرفق اختباراً يثبت `TTL` الجديد ≈ 172800 بعد الالتزام، واختبار انحدار يثبت أن إعادة تسليم بعد 60ث لم تُنتج صفاً ثانياً.
4. إن نفيتَ الفرضية: أرفق المخرج الذي ينفيها وامضِ. **الدليل يغلب قراءتي.**

### P1.1.8 — الاختبارات الحقيقية

- بيئة `core/tests`: PostgreSQL + PgBouncer + **`redis-durable` حقيقي بـ`appendfsync always`** (وسّع `docker-compose.test.yml` القائم أو أضف تجهيزة (fixture) بنفس نمط `conftest.py`/`testsupport.py` الحالي — **بلا `fakeredis` ولا SQLite ولا mock لما هو قيد الاختبار**، H7).
- تجهيزات: متجران (`TENANT_A`/`TENANT_B` القائمان) بقناتين حقيقيتين (`channel_accounts`) بـ`engine='ai_core'`، وقناة ثالثة بـ`engine='django'`.
- كل سيناريو في القسم 8 اختبار مستقل يطبع أرقامه ويفشل عند خرق العتبة. لا `skip` ولا `only` ولا تخفيف assertion (H7).
- اختبارات الفوضى (`kill -9`) بنفس نمط `gateway/src/__tests__/_*_child.mjs` القائم: عملية ابن حقيقية تُقتل، لا محاكاة.

---

## 8. مصفوفة القبول — كل سطر يحتاج مخرجاً حقيقياً في التقرير

| # | السيناريو | العتبة/الدليل |
|---|---|---|
| A1 | نص وارد لقناة `ai_core` | صف واحد في كل من `inbound_events`/`customers`/`conversations`/`messages`؛ `seq=1`؛ `needs_turn=true`؛ `last_inbound_seq=1`؛ PEL فارغ بعد الإقرار |
| A2 | 1000 إعادة إدخال لنفس `provider_message_id` | صف واحد في `messages`؛ `duplicates_total=999`؛ صفر أخطاء |
| A3 | `kill -9` للعامل بين القراءة والالتزام، **20 مرة** | **ضياع = 0**؛ لا صف مكرَّر؛ الكيانات تُستعاد بـ`XAUTOCLAIM`؛ الأرقام مطبوعة |
| A4 | جلسة غير معروفة | لا كتابة؛ `XACK`؛ `unknown_session_total` يزيد؛ لا خطأ |
| A5 | قناة `engine='django'` | **لا كتابة في `core`**؛ و`legacy-forwarder` سلّمها لـDjango كما اليوم (اختبارات العقد الأربعة خضراء، 14/14 الأصلية بلا تعديل) |
| A6 | العزل الخاصّي | متجران، **1000 زوج عشوائي** (رمز متجر أ ← مورد متجر ب) ⇒ صفر تسرّب؛ ومعاملة بلا `SET LOCAL` تعيد **صفراً** |
| A7 | دبوس موقع | `messages.type='location'` و`location` JSONB بـ`lat/lng` صحيحين (أساس كارثة 13 — الموقع كان يُسقَط بصمت قبل P0) |
| A8 | وسائط بـ`status='ok'` ثم أخرى بـ`status='failed'` | الأولى بـ`media_object_key` صحيح؛ الثانية صف سليم بـ`NULL` ولا فشل في الدور |
| A9 | `human_takeover_signal` | صف `direction='out', sent_by='staff'`؛ `bot_status='paused_human'`؛ `epoch` و`version` +1؛ `handoff_reason='staff_replied'`؛ `needs_turn` لم يُضبط؛ **لا صف رسالة عميل** |
| A10 | `identity_update` لهوية `lid` | `customers.phone_e164` تحدّث؛ لا صف في `messages` |
| A11 | opt-out («إيقاف» / «STOP» / «الغاء الاشتراك») | ثلاثة صفوف `suppressions` (marketing/back_in_stock/review_request)؛ `order_updates` غير محجوب؛ العدّاد يزيد؛ **لا نص في السجل** |
| A12 | محادثة `closed` تصلها رسالة | تعود `active` عبر `app.set_bot_status` بـ`reason='reopened_by_customer'`؛ `epoch` +1؛ `needs_turn=true` |
| A13 | محادثة `paused_human` تصلها رسالة | الرسالة تُخزَّن؛ `needs_turn` **يبقى false**؛ `bot_status` بلا تغيير |
| A14 | **حمل**: 10,000 كيان خلال 60ث | ضياع 0؛ `p99(ingest_core_commit_seconds)` مُعلَن ومقاس؛ التأخير يعود إلى 0؛ RSS مسطّح؛ `docker stats` ذروة < 85% من `mem_limit` |
| A15 | JSON تالف + خطأ PG دائم | الأول إلى `dlq:in:core` فوراً؛ الثاني بعد `CORE_INGEST_MAX_ATTEMPTS`؛ **لا حلقة سامة**؛ لا كيان مفقود |
| A16 | نسختان من العامل على نفس المجموعة | لا ازدواج ولا ضياع؛ الأرقام مطبوعة (H18) |
| A17 | `docker stop` تحت حمل | إيقاف ≤ 25ث، خروج برمز 0، ضياع 0 |
| A18 | نافذة dedupe (P1.1.7) | مخرج `TTL` قبل/بعد؛ واختبار إعادة التسليم بعد 60ث |
| A19 | البوابات الآلية | `hunt_gate.mjs` (بقاعدته التاسعة الجديدة، مع مخرج فشل متعمَّد يثبت عملها) + `ruff` + `mypy --strict` + `lint-imports` + `promtool check config/rules` + `pip-audit` — كلها نظيفة |
| A20 | الانحدار | `core/tests` **≥ 240/240** وكل مجموعات `gateway` خضراء **بلا تعديل أي اختبار قائم** |

---

## 9. المحظورات المطلقة

1. لمس مشروع `sharwa_saas` أو الاتصال بقاعدة بياناته أو تغيير أي عقد معه.
2. أي كود/ملف/مجلد/جدول/stub لـP1.2 وما بعدها (H10) — وتحديداً: لا `outbox`، لا dispatcher، لا إرسال، لا WebSocket، لا كتالوج، لا `console/`.
3. أي LLM أو تضمين أو مصنّف أو مكتبة ذكاء اصطناعي (H6/H16).
4. تعديل `docs/reference/*` أو `docs/00–02_*.md` أو `core/migrations/0001_baseline.sql` أو `0002_p0_api.sql` أو أي ملف `PROMPT_*`. أي حاجة لتغيير ⇒ ترحيل أمامي جديد + تسجيل في `P1_DEVIATIONS.md`.
5. إضعاف/حذف/تخطي أي اختبار قائم، أو تعديل عتبة في هذا الملف لتمرير اختبار.
6. أي حساب سعر أو عملة أو تحويل أو تقريب (المنصة تملك المال — قرار D3؛ ولا محل له في هذه الدفعة أصلاً).
7. سرّ مكتوب أو `.env` حقيقي مُلتزَم، أو نص رسالة عميل في سجل/مقياس/تقرير (H5/H20).
8. `git push --force`، حذف تاريخ، تغيير إعدادات git. الالتزام المحلي برسائل واضحة مسموح ومطلوب بعد نجاح كل خطوة.
9. جعل `api` يعتمد على `redis-durable` أو على أي إعداد جديد مطلوب (لا تكسر خدمة تعمل).
10. `XDEL` أو `XTRIM` أو أي لمس لمجموعة `legacy-forwarder` أو PEL الخاص بها.
11. طرح سؤال يستوقفك: سجّله في `P1_OPEN_QUESTIONS.md` وتابع بالخيار الأكثر أماناً.

---

## 10. تقرير التسليم — `docs/P1_01_REPORT.md` (بهذا القالب، بلا حذف بند)

1. **الملخص:** جملتان + نتيجة كل خطوة P1.0.1…P1.1.8 (✔/✘).
2. **الملفات:** شجرة المضاف/المعدَّل مع سطر لكل مجموعة، وسبب كل تعديل في `gateway/` إن وُجد.
3. **الأدلة لكل خطوة:** الأمر الحرفي + آخر 15–30 سطراً من مخرجه الحقيقي.
4. **مصفوفة القبول (القسم 8)** مملوءة A1…A20 بإشارة إلى رقم الدليل، بلا خلية فارغة.
5. **الأرقام المقيسة:** `p50/p95/p99` لـ`commit_seconds` و`ack_seconds` تحت A14؛ ذروة RSS و`docker stats`؛ مجموع `mem_limit` الجديد؛ زمن الإيقاف الرشيق؛ زمن انتشار إعادة الفتح؛ `TTL` dedupe قبل/بعد؛ أرقام PgBouncer وحساب حجم المجمّع.
6. **الانحرافات والأسئلة المفتوحة والاكتشافات:** ملخّص `P1_DEVIATIONS.md` و`P1_OPEN_QUESTIONS.md` و`P1_FINDINGS.md` — وكل عيب اكتشفتَه أثناء البناء بأثره المقاس قبل/بعد (هذا أنفع جزء في تقاريرك السابقة؛ لا تختصره).
7. **إقرار ذاتي:** جدول H1–H21: لكل مادة «ملتزم» + كيف يُثبت (سكربت/اختبار/سطر).
8. **قيود معروفة بصدق:** ما لم يُختبر أو ما تعذّر، بلا تجميل. أدرِج صراحةً: رسالة تأكيد opt-out مؤجَّلة لـP1.2، و`needs_turn` بلا مستهلك حتى P1.2، ولا فرونت إند في هذه الدفعة.
9. **الحالة:** حدّث `docs/PHASE_GATE.md` بسطر P1 المنصوص في §3.2 فقط، ولا شيء غيره.

**ملفات التوثيق المطلوبة:** `docs/P1_PROGRESS.md`, `docs/P1_FINDINGS.md`, `docs/P1_DEVIATIONS.md`, `docs/P1_OPEN_QUESTIONS.md`, `docs/P1_DEPENDENCIES.md` (كل مكتبة جديدة: الاسم، الإصدار المثبَّت، السبب، الترخيص، سبب عدم كفاية المكتبة القياسية + مخرج `pip-audit`).

---

## 11. أسئلة مفتوحة أعرفها سلفاً — سجّلها ولا تتوقف عندها

| الرمز | السؤال | خيارك الآمن الآن |
|---|---|---|
| OQ-P1-01 | لا توجد قناة `engine='ai_core'` حقيقية على واتساب بعد | اختبر بقنوات `channel_accounts` حقيقية في القاعدة وكيانات WAL حقيقية مُدخَلة بـ`XADD`؛ لا تنتظر هاتفاً حقيقياً |
| OQ-P1-02 | `type='contact'`/`'reaction'`: أين تُخزَّن حمولتهما الخاصة؟ | خزّن الرسالة بنوعها، ووثّق قرارك؛ **لا عمود ولا جدول جديد** |
| OQ-P1-03 | سياسة الاحتفاظ بـ`inbound_events` و`dlq:in:core` | لا تحذف شيئاً في هذه الدفعة؛ سجّل الحجم المتوقَّع شهرياً وارفع السؤال |
| OQ-P1-04 | تأكيد opt-out يحتاج outbox | مؤجَّل لـP1.2، مسجَّل كدين |
| OQ-P1-05 | قائمة عبارات opt-out قد تحتاج ضبطاً باللهجة اليمنية | القائمة في P1.1.5 كافتراضي قابل للتعديل بمتغيّر بيئة؛ ارفع السؤال للمالك |
| OQ-P1-06 | إعادة توزيع الشرائح عند تغيير `INGEST_SHARDS` | ليست قابلة لإعادة التوزيع الحيّ؛ وثّق ذلك في `.env.example` و`P1_OPEN_QUESTIONS.md` |

---

## 12. الختام

نجاح هذه الدفعة يُقاس بشيء واحد: **رسالة عميل حقيقية تعبر من الـstream إلى PostgreSQL مرة واحدة بالضبط، بلا ضياع ولا ازدواج ولا تسرّب بين متجرين، ورد الموظف من هاتفه يوقف البوت فوراً وذرّياً — وكل ذلك مُثبَت بمخرج حقيقي لا بادّعاء.** لا ذكاء اصطناعي، لا إرسال، لا واجهة. الأساس الذي سيقف عليه الوكيل كله.

ابدأ من P1.0.1. سجّل تقدّمك في `docs/P1_PROGRESS.md` خطوة بخطوة. وعند اكتمال P1.1.8 وتسليم التقرير، اكتب العبارة الحرفية وتوقّف:

`P1.0+P1.1 COMPLETE — STOPPING. AWAITING AUDIT. NO P1.2 WORK STARTED.`
