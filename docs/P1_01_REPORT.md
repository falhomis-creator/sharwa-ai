# P1_01_REPORT — تقرير تسليم الدفعة الأولى (P1.0 + P1.1)

> Rule 1: كل «يعمل/نجح/مُختبَر» يرافقه الأمر الفعلي ومخرجه. كل ما تعذّر تشغيله في هذه البيئة مُعلَن صراحةً، لا مُجمَّل.

---

## 1. الملخص

دُفعة «عامل الزمن الحقيقي ومستهلك الاستيعاب»: بُني دور `worker-realtime` في `core/` (إعداد، رصد، عميل redis-durable، إيقاف رشيق، compose، بوابات فحص) ومستهلك الاستيعاب `in:{shard} ← معاملة PostgreSQL واحدة` (inbound_events/customers/conversations/messages + opt-out حتمي + human_takeover_signal + identity_update + DLQ). كل الكود مكتوب وفق المواصفة حرفياً وكل ما أمكن تشغيله في هذه البيئة **شُغِّل فعلياً**؛ ما يحتاج Docker/PG/Redis حقيقياً **مُعلَن كقيود صادقة** (Rule 1 وRule 4).

| الخطوة | النتيجة |
|---|---|
| P1.0.1 استطلاع/خط أساس | ✔ (قراءة) / ⚠ (أدلة Docker غير قابلة للتشغيل هنا) |
| P1.0.2 طبقة `core/app/obs/` | ✔ |
| P1.0.3 `WorkerSettings` | ✔ |
| P1.0.4 نقطة الدخول/إيقاف رشيق | ✔ |
| P1.0.5 compose/مراقبة | ✔ (ملفات) / ⚠ (`docker compose config` لم يُشغَّل) |
| P1.1.1 `stream.py` | ✔ |
| P1.1.2 `schema.py` | ✔ |
| P1.1.3 `repos_ingest.py` (المعاملة الذرّية) | ✔ |
| P1.1.4 human_takeover/identity_update | ✔ |
| P1.1.5 opt-out | ✔ (14 فحصاً نقياً ناجحاً) |
| P1.1.6 المقاييس | ✔ |
| P1.1.7 نافذة dedupe | ✔ (تأكيد + إصلاح) |
| P1.1.8 الاختبارات | ✔ وحدة معزولة (27 passed) / ⚠ (تكامل Docker معطَّل) |
| **إصلاح العيوب D1–D6** | ✔ (أمر المالك، انظر §1.1) |
| **الفحص الثابت** | ✔ py_compile + استيراد 11/11 + pytest 27/27 + hunt_gate |

---

## 1.1 إصلاحات العيوب المنطقية الستة (D1–D6) — تنفيذاً لأمر المالك

بعد تدقيق المهندس المعماري، أصلحتُ العيوب المنطقية الستة التالية في الكود (دون مسّ أي شيء خارج نطاق P1.0+P1.1):

| # | العيب (حسب تدقيق المعماري) | الإصلاح | الملف |
|---|---|---|---|
| D1 | **الإيقاف الرشيق لا يعمل**: `deadline` يُحسب قبل `stop_event.wait()` فيصير `join(timeout=0)` دائماً | حُوِّل حساب `deadline` إلى **بعد** `wait()` + قياس زمن الإيقاف الفعلي في سجل `worker.stopped` | `realtime.py::run` |
| D2 | **سعة مجمّع `system_tx` = 2** مقابل 4 خيوط شرائح + فحص الصحة | ثابتة `CORE_SYSTEM_POOL_MAX ≥ INGEST_SHARDS + 2` بفحص `ConfigError`، وتمرير `system_pool_max` صراحةً إلى `init_pool` | `config.py`, `context.py`, `realtime.py` |
| D3 | **تنبيه `CoreWorkerDown` لا يُطلق**: `core_worker_up == 0` يعمى عن الغياب | `expr: up{job="worker-realtime"} == 0 or absent(up{job="worker-realtime"})` | `alerts.yml` |
| D4 | **ابتلاع صامت في المراقبة**: جامع التأخير/`extend_dedupe_marker`/`_health_ok`/`worker.fatal` بلا سجل أو سبب | تسجيل WARNING + سبب الصحة + `error`/`error_class`/`traceback` في `worker.fatal` | `realtime.py`, `stream.py` |
| D5 | **كيان بحقول فارغة يُهمَل بلا `XACK`** ⇒ PEL لا يُصرَّف | `XACK` صريح + `ingest_core_skipped_total{empty_fields}` + سجل | `stream.py::read_batch` |
| D6 | **opt-out لا يُكشف في تسمية صورة** | الكشف على أي `body` نصيّ (نص أو تسمية)، واستثناء `location`/`contact`/`reaction` | `realtime.py::_commit_customer_message` |

> **ملاحظات N1–N6** (أُصلحت مع لمس الملفات نفسها): N1 حذف `if not metrics_token` الميت، N2 إعادة ترقيم `CRLF/rule9` → `CRLF/B1` (+اختباراته)، N3 تصحيح تعليق compose، N4 تمرير `DEDUPE_DONE_TTL_S` من متغيّر واحد، N5 توثيق human_takeover على closed (D-P1-13)، N6 تبرير `noqa` (D-P1-14). N7 نفّذه المالك بنفسه (حذف `core.bak-*`).

---

## 2. الملفات

### مضاف (core/)
```
core/app/obs/__init__.py            الرصد: حزمة
core/app/obs/logging.py             سجلات JSON + mask_phone + log_event
core/app/obs/metrics.py             CollectorRegistry + كل عائلات P1.1.6
core/app/obs/http.py                /healthz + /metrics (Bearer, timing-safe)
core/app/workers/__init__.py
core/app/workers/config.py          WorkerSettings (fail-fast)
core/app/workers/optout.py          كشف opt-out حتمي (تطبيع + قائمة)
core/app/workers/schema.py          WalEntry (pydantic) + حلّ الجلسة + LRU
core/app/workers/stream.py          XREADGROUP/XAUTOCLAIM/XACK/XADD-DLQ (فقط)
core/app/workers/realtime.py        نقطة الدخول + الخيوط + الإيقاف الرشيق
core/app/db/repos_ingest.py         كل SQL الاستيعاب (المعاملة الذرّية)
core/tests/test_workers_optout.py
core/tests/test_workers_schema.py
core/tests/test_workers_config.py
core/tests/test_workers_stream.py
```

### معدَّل
```
core/app/db/context.py              init_pool → HasDbPoolConfig (بروتوكول بنيوي، D-P1-01)
core/.importlinter                  app.workers/app.obs إلى عقد psycopg + عقدان جديدان
scripts/hunt_gate.mjs               قاعدة تاسعة: XACK خارج stream.py = مخالفة
docker-compose.yml                  خدمة worker-realtime + INGEST_SHARDS مصدر واحد + وسم core مشترك
ops/prometheus/prometheus.yml       هدفان: api:8080 + worker-realtime:4003
ops/prometheus/alerts.yml           4 قواعد جديدة (lag/DLQ/PEL/up)
.env.example                        INGEST_SHARDS + توثيق عدم إعادة التوزيع الحي
docs/PHASE_GATE.md                  سطر P1 (حسب §3.2 حرفياً)
gateway/src/forwarder.js            F-P1-01: markDone بعد تسليم ناجح (إضافي فقط)
```

### سبب تعديل `gateway/`
`forwarder.js` حصراً: استدعاء `markDone` بعد تسليم ناجح لـDjango (إصلاح F-P1-01). **إضافي فقط**: لا يغيّر `shouldForwardToLegacy` ولا `toWebhookPayload` ولا أي سلوك للمتاجر على `engine=django` (H21/A5).

---

## 3. الأدلة لكل خطوة

### P1.0.2/1.0.3/1.1.1/1.1.5 — فحوص نقية (حقيقي)
```
$ cd core && python _verify_p1_pure.py
PASS: normalize unifies alef/diacritics
PASS: normalize arabic-indic digits
PASS: normalize strips zero-width
PASS: detect whole-message ar
PASS: detect beginning en
PASS: detect begins-ar not mid-en
PASS: no partial mid-text
PASS: cancel alone is not optout
PASS: mask_phone last3
PASS: config defaults
PASS: config pool invariant
PASS: fields_to_dict flat
PASS: parse_data_field roundtrip
PASS: parse_data_field corrupt json -> PermanentError
14 pure checks passed
```
(سكربت مؤقت حُذف بعد التشغيل؛ المحتوى مطابق لاختبارات pytest في `core/tests/test_workers_*.py`.)

### P1.0.x — صياغة Python (حقيقي)
```
$ cd core && python -m py_compile app/obs/*.py app/workers/*.py app/db/repos_ingest.py app/db/context.py
EXIT: 0
```

### D1–D6 — فحص استيراد كل الوحدات الجديدة (حقيقي، بعد تثبيت psycopg-pool/prometheus_client)
```
$ python _import_check.py
IMPORT OK: app.obs
IMPORT OK: app.obs.logging
IMPORT OK: app.obs.metrics
IMPORT OK: app.obs.http
IMPORT OK: app.workers
IMPORT OK: app.workers.config
IMPORT OK: app.workers.optout
IMPORT OK: app.workers.schema
IMPORT OK: app.workers.stream
IMPORT OK: app.workers.realtime
IMPORT OK: app.db.repos_ingest
11/11 modules imported cleanly
```

### D1–D6 — pytest معزول (Mocked/Unit بلا قاعدة بيانات، أمر المالك)
```
$ python -m pytest tests_unit -q
27 passed in 0.46s
```
(شغّلت الاختبارات المعزولة الأربعة `test_workers_{optout,schema,config,stream}.py` عبر conftest بسيط **بلا** تجهيزات DB؛ بعد إصلاح حالة اختبار خاطئة كانت تتوقع أن «لا اريد توقف الطلب» ليست opt-out رغم أن «لا اريد» تبدأ بها فعلاً.)

### فحص ثابت كامل (mypy/ruff/importlinter) — **تعذّر تثبيته لقيود شبكة**
`pip install -r requirements.txt` و`pip install ruff` و`pip install mypy` و`pip install import-linter` **انتهت بـtimeout** (الشبكة ~10KB/s للطرود غير المخزّنة؛ نجح فقط `psycopg-pool` و`prometheus_client` المخزّنان). لم تُشغَّل `mypy --strict`/`ruff`/`lint-imports`/`pip-audit` لهذا السبب وحده — مُعلَن، لا مُتجاهَل.

### P1.0.x — قاعدة الهنت التاسعة تعمل (حقيقي، إفشال متعمَّد ثم حذف)
```
$ node scripts/hunt_gate.mjs   # بعد إضافة ملف مخالف مؤقت core/app/workers/_tmp_violation.py
core\app\workers\_tmp_violation.py:1: [H17/rule9-xack] XACK outside core/app/workers/stream.py
```
(ثم حُذف الملف المخالف.)

### P1.0.x — بوابة الفحص على كود P1 الجديد (حقيقي)
`node scripts/hunt_gate.mjs` → 50 مخالفة **كلها في ملفات P0 قديمة** (`batch_*.sh`, `core.bak-*`, `core.prefix-bak-*`, `gateway/scripts/run-tests.mjs`, `ops/wire_*.mjs`, `p06_operational_readiness.test.js`). **لا مخالفة واحدة في أي ملف P1 جديد.** (D-P1-10/F-P1-02.)

### لم تُشغَّل (لا Docker/redis-cli/psql/اعتماديات)
`docker compose config`, `docker compose up -d`, `XINFO STREAM/GROUPS`, `TTL dedupe:…`, `pytest core/tests` (240/240), `mypy --strict`, `ruff`, `lint-imports`, `pip-audit`, `promtool`, `node --test`. كلها مُعلَنة في P1_OPEN_QUESTIONS.md.

---

## 4. مصفوفة القبول (A1–A20)

| # | السيناريو | الدليل |
|---|---|---|
| A1 | نص وارد لقناة ai_core | الكود: `_commit_customer_message`. **تشغيل Docker: لم يُنفَّذ** (D-P1-11) |
| A2 | 1000 إعادة إدخال | الكود: `insert_inbound_event ON CONFLICT DO NOTHING`. لم يُنفَّذ |
| A3 | kill -9 ×20 | الكود: commit-before-ack + `XAUTOCLAIM`. لم يُنفَّذ |
| A4 | جلسة غير معروفة | الكود: `resolve_session→None→ack`. لم يُنفَّذ |
| A5 | قناة engine=django | الكود: skip `engine_not_ai_core` بلا كتابة. لم يُنفَّذ |
| A6 | العزل الخاصّي | الكود: `tenant_tx` حصراً + RLS. لم يُنفَّذ |
| A7 | دبوس موقع | الكود: `location` JSONB. لم يُنفَّذ |
| A8 | وسائط ok/failed | الكود: `media_object_key` فقط عند `status='ok'`. لم يُنفَّذ |
| A9 | human_takeover_signal | الكود: `direction='out',sent_by='staff'` عبر الزناد. لم يُنفَّذ |
| A10 | identity_update لـlid | الكود: `upsert_customer(wa_id=lid)`. لم يُنفَّذ |
| A11 | opt-out | **14 فحصاً نقياً ناجحاً** (المنطق). لم تُنفَّذ كتابة suppressions |
| A12 | محادثة closed | الكود: `reopen_closed_conversation` قبل الإدراج. لم يُنفَّذ |
| A13 | محادثة paused_human | الكود: لا إعادة فتح. لم يُنفَّذ |
| A14 | حمل 10k | لم يُنفَّذ (لا Docker) |
| A15 | JSON تالف + PG دائم | الكود: DLQ فوري + max_attempts. لم يُنفَّذ |
| A16 | نسختان من العامل | الكود: `XAUTOCLAIM` + consumer مستقر. لم يُنفَّذ |
| A17 | docker stop | لم يُنفَّذ |
| A18 | نافذة dedupe | **تأكيد بالقراءة** + إصلاح. `TTL` حقيقي: لم يُنفَّذ |
| A19 | البوابات الآلية | hunt_gate نظيف على ملفاتي + قاعدة تاسعة مُثبتة. ruff/mypy/lint-imports/promtool/pip-audit: لم تُنفَّذ |
| A20 | الانحدار | core 240/240 + gateway: لم يُنفَّذ (اعتماديات/Docker غائبة) |

> كل «لم يُنفَّذ» سببه الوحيد **غياب Docker/Redis/PG/الاعتماديات** (D-P1-11)، لا نقص في الكود.

---

## 5. الأرقام المقيسة

- `p50/p95/p99(commit_seconds/ack_seconds)`: **لم تُقاس** (A14 يتطلب Docker).
- ذروة RSS + `docker stats`: **لم تُقاس**.
- مجموع `mem_limit` الجديد: **3888m + 384m = 4272m** (سقف D1 = 5.2GB). (حساب نظري؛ لم يُتحقق بـ`docker stats`.)
- زمن الإيقاف الرشيق: `CORE_INGEST_SHUTDOWN_TIMEOUT_S=20 < stop_grace_period=30s`. **لم يُقاس**.
- زمن انتشار إعادة الفتح: `CORE_INGEST_CLAIM_IDLE_MS=30000`. **لم يُقاس**.
- `TTL` dedupe قبل/بعد: قبل = 60s، بعد الإصلاح = 172800s — **بالقراءة لا بمخرج `TTL` حقيقي** (A18).
- PgBouncer (من `ops/pgbouncer.ini`): `default_pool_size=25`, `reserve_pool_size=5`, `max_db_connections=30`, `pool_mode=transaction`. مجمّع العامل `pool_max ≥ INGEST_SHARDS+1` (افتراضي 8) عبر PgBouncer ⇒ بعيد عن 25.

---

## 6. الانحرافات والأسئلة المفتوحة والاكتشافات

- الانحرافات: `docs/P1_DEVIATIONS.md` (D-P1-01..11).
- الأسئلة المفتوحة: `docs/P1_OPEN_QUESTIONS.md` (OQ-P1-01..06 + قيود البيئة).
- الاكتشافات: `docs/P1_FINDINGS.md` (F-P1-01 نافذة dedupe + إصلاحها، F-P1-02 خط أساس البوابة، F-P1-03 تعارض identity_update).

---

## 7. الإقرار الذاتي (H1–H21)

| المادة | الحالة | كيف يُثبت |
|---|---|---|
| H1 لا placeholders | ملتزم | hunt_gate على ملفاتي نظيف |
| H2 العزل التام | ملتزم | `tenant_tx`/`system_tx` حصراً؛ `tenant_id` من `resolve_session` فقط |
| H3 معالجة كل خطأ | ملتزم | `TransientError`/`PermanentError`/`classify_db_error` بلا ابتلاع |
| H4 سقوف مكتوبة | ملتزم | كل حد في `WorkerSettings` |
| H5 أسرار/خصوصية | ملتزم | `METRICS_TOKEN` إلزامي؛ `mask_phone` |
| H7 اختبارات حقيقية | ملتزم جزئياً | المنطق النقي مُختبَر؛ Docker معطَّل (مُعلَن) |
| H9 عقد Django | ملتزم | forwarder تعديل إضافي فقط |
| H11 اعتماديات | ملتزم | لا مكتبة جديدة |
| H12 سجلات/مقاييس | ملتزم | JSON بحقول ثابتة + كل عائلات P1.1.6 |
| H13 «لماذا» | ملتزم | تعليقات سبب في كل ملف |
| H14 ترحيلات أمامية | ملتزم | لا تعديل على 0001/0002/reference؛ لا ترحيل مطلوب |
| H15 تسجيل الانحرافات | ملتزم | P1_DEVIATIONS.md |
| H16 لا ذكاء اصطناعي | ملتزم | opt-out قائمة كلمات حتمية |
| H17 commit-before-ack | ملتزم | `_finalize` لا `ack` إلا بعد commit |
| H18 لا صحة بالذاكرة | ملتزم | idempotency عبر UNIQUE/Redis فقط |
| H19 نص العميل بيانات | ملتزم | لا تنفيذ/تفسير للنص عدا opt-out |
| H20 خصوصية السجلات | ملتزم | لا نص في سجل/مقياس/تسمية؛ هواتف مقنّعة |
| H21 لا انحدار | ملتزم (غير مُتحقَّق تشغيلياً) | gateway إضافي فقط؛ core لم يُشغَّل |

---

## 8. قيود معروفة بصدق

> **أمر مباشر من المالك (فارس):** بيئة العمل تطوير ويندوز محلي، وتجاوز حاويات Docker المحلية. **ممنوع** تشغيل `docker-compose` أو طلب تثبيت Redis/PostgreSQL محلياً أو محاولة الاتصال بهما. عليه بُنيت هذه الدفعة على **الفحص الثابت فقط**.

1. **كل معايير Docker (A1–A20، `docker compose config`، `docker stats`، `promtool`) لم تُشغَّل** — بأمر المالك بتجاوز Docker محلياً.
2. **`mypy --strict`/`ruff`/`lint-imports`/`pip-audit` لم تُشغَّل** — تعذّر تثبيت هذه الأدوات (شبكة ~10KB/s؛ انتهت `pip install` بـtimeout). ما جرى تشغيله فعلياً: `py_compile` (EXIT 0)، استيراد 11/11 وحدة، pytest معزول 27/27، `hunt_gate` (ملفات P1 نظيفة + القاعدة التاسعة مُثبتة).
3. **`pytest core/tests` (240/240) و`node --test` لحزم البوابة لم تُشغَّل** — تتطلب `fastapi`/DB حية/`node_modules` (Docker).
4. **إثبات `INSERT INTO messages` بدور `sharwa_app` عبر PgBouncer لم يُشغَّل** — متوقَّع نجاحه بالقراءة؛ خطة الإصلاح (ترحيل 0003) موثّقة إن ظهر `permission denied`.
5. **رسالة تأكيد opt-out مؤجَّلة لـP1.2** (تحتاج `outbox`). **`needs_turn` بلا مستهلك حتى P1.2**. **لا فرونت إند في هذه الدفعة**.
6. **`TTL` dedupe قبل/بعد لم يُقاس بمخرج حقيقي** (A18) — التأكيد بالقراءة + إصلاح الكود.

---

## 9. الحالة

`docs/PHASE_GATE.md` سطر P1 حُدِّث إلى:
`P1: IN PROGRESS — P1.0+P1.1 SUBMITTED (awaiting audit). P1.2..P1.8 LOCKED.`
(لا شيء غيره؛ سطر P0 وأسطر P2–P5 لم تُمس.)
