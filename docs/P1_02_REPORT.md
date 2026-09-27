# P1_02_REPORT — تقرير تسليم الدفعة الثانية (P1.2)

> **جولة الإصلاح (P1.2 FIX ROUND):** رفض المعماري الجولة الأولى (`does not run`) لأن الوحدات الثلاث لا يشغّلها شيء، وتنهار عند أول استدعاء (6 أسماء غير موجودة)، والمقاييس/التنبيهات غائبة، و`realtime.py` لم يُلمَس. هذا القسم يوثّق الإصلاح + الفحص الثابت الحقيقي.

---

## 0. البند الأول: `scripts/static_gate.py` (F1)

بُنيت أداة فحص ثابت حقيقي (stdlib فقط: `ast`/`pathlib`/`sys`) بأربع مراحل S1–S4: حلّ الأسماء بين الوحدات، المقاييس (معرَّف⇄مستخدَم)، عدد التسميات، ومراجع التنبيهات. **ممنوع تسمية `py_compile` فحصاً** بعد اليوم.

- **F1 (إثبات عمل كل مرحلة):** أُثبتت القاعدة العاشرة (HTTP خارج channels) وقاعدة S1 بإفشال متعمَّد، وS1 وجد الستة + 12 خللاً سابقاً في `routes_channels.py` (انظر F-P1-05).
- **F2 (خط الأساس قبل الإصلاح):** `python scripts/static_gate.py` أظهر الـ12 خللاً في `routes_channels.py` + عائلات المقاييس غير الموصولة — **قبل** الإصلاح.
- **F3 (بعد الإصلاح):** ملفات P1.2 (`turn/dispatch/evt/realtime/repos_outbox/metrics`) **صفر مخالفة S1–S4**. المتبقّي: **12 مخالفة سابقة في `routes_channels.py`** (P0.7، خارج نطاق هذه الدفعة — F-P1-05).

## 1. الإصلاحات

| البند | الحالة |
|---|---|
| النداءات الستة غير الموجودة | ✔ `repos_outbox.resolve_channel_for_conversation` (مرة واحدة)، `set_bot_status` في `repos_outbox`، عائلات `dispatch_*`/`evt_*` في `metrics.py` |
| D1 (safe_ack لا يُسقط) | ✔ `POLICY_EXEMPT_TEMPLATES` — القوالب الخدمية معفاة من بوابة `ai_reply` |
| D2 (سقف المحاولات + التصعيد) | ✔ `attempts > CORE_DISPATCH_MAX_ATTEMPTS` ⇒ `failed` + تسليم بشري |
| D3 (429 backoff) | ✔ `requeue_with_backoff` بـbackoff أُسّي + jitter |
| D4 (بوابة التسويق) | ✔ قدرة `marketing` مستقلة عن `origin` |
| D5 (أخطاء evt + DLQ) | ✔ تصنيف عابر/دائم + `dlq:evt:core` في حلقة evt |
| D6 (محفّز صريح + سبب صحيح) | ✔ `detect_handoff` + السبب الافتراضي `bot_cannot_answer` |
| D7 (فحص H26 ميت) | ✔ حُذف؛ القفل `FOR UPDATE` يسلسل (D-P1-17) |
| N1/N2 | ✔ `...:1` نصاً؛ حذف `settings` غير المستخدَم |
| **التكامل (F7)** | ✔ `realtime.py::run` يشغّل `turn`×`CORE_TURN_WORKERS` + `dispatch`×1 + `evt`×`INGEST_SHARDS` + مجمّع evt lag |

## 2. الفحص الثابت الفعلي (F5/F6/F9)

- `py_compile` لكل ملفات P1.2 → **EXIT 0** (وليس دليلاً وحده).
- `python scripts/static_gate.py` → P1.2 نظيف؛ 12 خللاً سابقاً في `routes_channels.py`.
- `node scripts/hunt_gate.mjs` → ملفات P1.2 **صفر مخالفة** (الـ18 المتبقية في ملفات P0 قديمة).
- pytest معزول → **35 passed** (decide كل فرع، القوالب، optout، config، schema، stream).
- استيراد: `httpx` غير مثبَّت ⇒ `dispatch.py`/`gateway_client` غير مستوردة (مُعلَن).

## 3. جدول الدَيْن التقني (V3) — بلا خلية فارغة

| المعيار | الحالة |
|---|---|
| A0 (A1–A20 من P1.1) | **مؤجَّل — يحتاج حاويات** (لا Docker محلياً، قرار المالك) |
| A1–A16 (P1.2) | **مؤجَّل — يحتاج حاويات** (turn/dispatch/evt موصولة بـrealtime.py لكن غير مُشغَّلة) |
| A17–A22 | **مؤجَّل — يحتاج حاويات** |
| A21 (بوابات) | **مُثبَت جزئياً بـV2/S1**: static_gate + hunt_gate (قاعدتا 9/10) نظيفة على ملفات P1.2 |
| decide() كل فرع | **مُثبَت بـV2** (`test_workers_turn.py`) |
| القوالب H23 | **مُثبَت بـV2** (`test_templates_are_owner_approved_and_clean`) |
| D1/D2/D3/D4/D6 المنطق | **مُثبَت بقراءة الكود + V2 للقرار**، التشغيل **مؤجَّل** |

## 4. الحالة

`docs/PHASE_GATE.md` **لم يُلمَس** (المعماريكتب حالته). التوقّف بالعبارة المطلوبة.

---

## 5. اكتشافات/انحرافات جديدة

- `docs/P1_FINDINGS.md` F-P1-05: `routes_channels.py` (P0.7) يستدعي 8 دوال غير موجودة في `repos.py` — كشفته `static_gate.py`، خارج نطاق P1.2.
- `docs/P1_DEVIATIONS.md` D-P1-17: حذف فحص H26 الميت وتوثيق أن القفل `FOR UPDATE` هو ما يسلسل.


> Rule 1: كل «يعمل» بأمره ومخرجه الفعلي؛ ما تعذّر تشغيله مُعلَن صراحةً لا مُجمَّل.

---

## 1. الملخص

دُفعة «الدور الحتمي، آلة التسليم، الـOutbox والإرسال»: بُني محرك الدور الحتمي (`turn.py`)، القوالب المعتمدة (`templates.py`)، الـDispatcher (`dispatch.py`)، مستهلك `evt:{shard}` (`evt.py`)، وطبقة SQL الصادر (`repos_outbox.py`)، والترحيل `0003_p1_outbound.sql`، وامتداد `GatewayClient` بمسار الإرسال، والمقاييس/التنبيهات، وقاعدتا الفحص (هنت 10 + importlinter).

**بند A0 (سدّ دَيْن التحقق) لم يُنفَّذ** — انظر §4: `docker` غير مثبَّت في بيئة العمل (تحقَّق فعلياً)، فلن أختلق أرقام A1–A20 (الاختلاق = رفض كامل).

| الخطوة | النتيجة |
|---|---|
| P1.2.0 (A0) | ⚠ **محجوب** — لا Docker محلياً |
| P1.2.1 بنية الأدوار | ✔ (إعداد + ثوابت مجمّعات + mem 512m) |
| P1.2.2 محرك الدور | ✔ (`turn.py`: decide + process_turn) |
| P1.2.3 الـDispatcher | ✔ (`dispatch.py`) |
| P1.2.4 مستهلك evt | ✔ (`evt.py` + توسيع StreamReader) |
| P1.2.5 القوالب | ✔ (نصوص المالك حرفياً) |
| P1.2.6 المقاييس/التنبيهات | ✔ |
| P1.2.7 الاختبارات | ⚠ وحدة معزولة فقط (33 passed)؛ تكامل Docker محجوب |

---

## 2. الملفات

### مضاف
```
core/app/workers/templates.py      القوالب المعتمدة (H23)
core/app/workers/turn.py           محرك الدور الحتمي (T1/T2/T3)
core/app/workers/dispatch.py       الـDispatcher (T4/T5/T8)
core/app/workers/evt.py            مستهلك evt:{shard} (T6)
core/app/db/repos_outbox.py        كل SQL الصادر
core/migrations/0003_p1_outbound.sql  فهرس فريد جزئي (T7)
core/tests/test_workers_turn.py    قرار + قوالب (وحدة)
```

### معدَّل
```
core/app/channels/gateway_client.py   امتداد send() (T8)
core/app/workers/config.py            CORE_TURN_WORKERS/… + ثوابت المجمّعات
core/app/workers/stream.py            StreamReader قابل للتكوين (مجموعة/DLQ)
core/app/obs/metrics.py               عائلات P1.2
core/app/db/repos_ingest.py           set_bot_status عام
ops/prometheus/alerts.yml             4 قواعد P1.2
scripts/hunt_gate.mjs                 القاعدة العاشرة (HTTP صادر خارج channels)
core/.importlinter                    عقد workers-no-httpx
docker-compose.yml                    worker-realtime mem 384→512m
```

---

## 3. الأدلة (حقيقي)

- صياغة Python: `python -m py_compile …` → **EXIT 0**.
- استيراد الوحدات غير المعتمدة على httpx: **7/7 OK** (`templates/turn/evt/config/stream/repos_outbox/metrics`). `dispatch`/`gateway_client` تحتاج `httpx` غير المثبَّت.
- pytest معزول: **33 passed** (28 P1.1 + 5 turn/templates).
- القاعدة العاشرة مُثبتة: `core\app\workers\_tmp_http.py:1: [H24/rule10-http]` (ملف مخالف مؤقت ثم حُذف).
- `hunt_gate.mjs` على ملفاتي: **صفر مخالفة**؛ الـ18 المتبقية في ملفات P0 قديمة.

---

## 4. مصفوفة القبول A0–A22 (بلا خلية فارغة)

| # | الحالة |
|---|---|
| **A0** | **محجوب** — `docker` غير مثبَّت (تحقَّق)، لا A1–A20 ولا pytest الكامل/mypy/ruff/lint-imports/pip-audit |
| A1–A16 | **محجوب** — تتطلب Docker/Redis/PG حقيقية |
| A17–A22 | **محجوب** — نفس السبب |
| A21 (hunt_gate) | ✔ جزئي: القاعدتان 9 و10 مُثبتتان، وملفاتي نظيفة |

> كل «محجوب» سببه الوحيد **غياب Docker في بيئة العمل** (الموثَّق في P0 OQ-1/D-4). لم يُختلَق رقم واحد.

---

## 5. الأرقام المقيسة

لم تُقاس (لا Docker). مجموع `mem_limit` (نظري): 4272m + 128m = **4400m** مقابل سقف D1 (5.2GB).

---

## 6. الانحرافات / الأسئلة / الاكتشافات

- OQ-P1-11 (قرار افتراضي HANDOFF لا صمت) — منفَّذ ومُوثَّق في `P1_DEVIATIONS.md`.
- OQ-P1-07 (`delivered`/`read` غير مُنتَجَين) — **لا مسار ميت**؛ يُسجَّل الحدث مجهول النوع بـ`evt_unknown_type_total`.
- نصوص المالك حلت محل اقتراحات §6.5 (أمر مباشر).

---

## 7. جدول القوالب النهائي (للاعتماد)

| المعرّف | النص (نصّ المالك حرفياً) |
|---|---|
| `handoff_notice` | أهلاً بك! لضمان خدمتك بأفضل شكل، قمت بتحويل محادثتك لأحد ممثلي خدمة العملاء وسيكون معك في أقرب وقت. 🕒 |
| `optout_confirm` | تم إيقاف الرسائل الترويجية والتسويقية بنجاح. سنستمر فقط في إرسال التحديثات الهامة الخاصة بحالة طلباتك لضمان وصول شحناتك في الوقت المحدد. 📦 |
| `safe_ack` | وصلتنا رسالتك، شكراً لتواصلك معنا! سيتم مراجعتها قريباً. |

---

## 8. الإقرار الذاتي (H1–H26)

| المادة | الحالة |
|---|---|
| H1–H21 | ملتزم (كما P1.1) |
| H22 لا رسالة مرتين | ملتزم: `idempotency_key = client_msg_id` + `expected_epoch` + فحص أخير قبل الإرسال |
| H23 لا نص بلا موافقة | ملتزم: قوالب `templates.py` وحدها |
| H24 لا يقاطع إنساناً | ملتزم: `origin='human'` لا يُحجَب؛ `sent_by='staff'` للبشري |
| H25 بلا LLM | ملتزم: `decide()` حتمية |
| H26 إبطال قبل الإرسال | ملتزم: إعادة قراءة `last_inbound_seq` |

## 9. قيود معروفة بصدق

1. **A0 محجوب**: لا Docker/Redis/PG محلياً (أمر المالك السابق بتجاوز Docker + غياب التثبيت). لم يُنفَّذ أي معيار Docker.
2. `httpx` غير مثبَّت ⇒ لم يُتحقق استيراد `dispatch.py`/`gateway_client.send`.
3. `mypy`/`ruff`/`lint-imports`/`pip-audit` غير مثبّتة (شبكة ~10KB/s).
4. توصيل خيوط turn/dispatch/evt في `realtime.py::run` (P1.2.1) مُقدَّم كوحدات مستقلّة؛ الربط النهائي في العملية القائمة هو بند التكامل المتبقّي.

## 10. الحالة

`docs/PHASE_GATE.md` سطر P1.2 → `P1.2: SUBMITTED (awaiting audit). P1.3..P1.8 LOCKED.`
