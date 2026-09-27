# P1 Progress — الدفعة الأولى (P1.0 + P1.1)

> سجل تقدّم خطوة بخطوة. كل خطوة بنتيجتها ومخرجها الفعلي (Rule 1: لا ادّعاء بلا مخرج).

---

## P1.0.1 — استطلاع وخط أساس

### 1. قراءة الوثائق الحاكمة (القسم 2)

| الملف | ما استخلصته | تعارض مع القسم 6؟ |
|---|---|---|
| `SHARWA_AI_PROJECT_SUMMARY.md` | العقد المجمّد مع Django؛ المنصة تملك المال | لا |
| `PROMPT_P0_foundation_for_deepseek.md` | دستور H1–H15 + U1–U9 + بروتوكول الإيقاف سارية | لا |
| `docs/00_ARCHITECTURE.md` | §4.2 الاستيعاب، §4.4 التسليم البشري، §6 الموارد (D1 5.2GB) | لا |
| `docs/01_FIVE_TASKS_DESIGN.md` | تصميم المهام (سياق فقط) | لا |
| `docs/02_RISK_SOLUTIONS_21.md` | كوارث 1/4/5/11/17/18 | لا |
| `docs/reference/schema.sql` + `schema_selftest.sql` | العقد الملزم للبيانات (لا يُعدَّل) | لا |
| `core/` كاملاً | أسلوب config/db/repos/import-linter الحالي | لا |
| `gateway/src/ingest/{normalize,identity,dedupe,wal}.js` + `sessions.js` + `forwarder.js` | شكل كيان WAL الفعلي + `shouldForwardToLegacy` + `BLOCK_MS`/`waitForReady` | **نعم — انظر F-P1-01 (نافذة dedupe)** |
| `docs/P0_FINDINGS/DEVIATIONS/OPEN_QUESTIONS.md` | ما اكتُشف سابقاً | لا |

### 2. خط الأساس (الأمر + المخرج)

- `git log --oneline -5` → `2047291 feat(p0): add core module and finalize P0.7` (آخر التزام).
- `git status --porcelain` → **غير نظيف** قبل البدء: `M docs/PHASE_GATE.md` + `?? PROMPT_P1_01_ingest_for_deepseek.md` + `?? "output clauds/"`. سُجِّل في P1_DEVIATIONS.md (D-P1-09).
- `python -m py_compile …` (كود P1 الجديد) → `EXIT: 0` (صياغة سليمة).
- `node scripts/hunt_gate.mjs` → **50 مخالفة كلها في ملفات P0 قديمة** (`batch_*.sh`، `core.bak-*`، `core.prefix-bak-*`، `gateway/scripts/run-tests.mjs`، `ops/wire_*.mjs`، `gateway/.../p06_operational_readiness.test.js`). **لا توجد أي مخالفة في ملفات P1 الجديدة.** سُجِّل في P1_DEVIATIONS.md (D-P1-10).
- `python -m pytest core/tests -q` → **تعذّر**: `fastapi`/`psycopg-pool`/`prometheus_client` غير مثبّتة في بيئة العمل المحلية (Windows، بلا Docker). سُجِّل في P1_OPEN_QUESTIONS.md.

### 3–5. البنية الحقيقية وأرقام PgBouncer وإثبات إدخال رسالة بدور `sharwa_app`

**تعذّر تنفيذها بالكامل**: لا Docker ولا `psql` ولا `redis-cli` في هذه البيئة. ما أمكن إثباته بالقراءة (لا بالتشغيل):
- `ops/pgbouncer.ini`: `pool_mode=transaction`, `default_pool_size=25`, `reserve_pool_size=5`, `max_client_conn=400`, `max_db_connections=30`.
- `schema.sql`: `GRANT SELECT, INSERT, UPDATE, DELETE ON … TO sharwa_app` لكل الجداول (بما فيها `messages`)؛ الزناد `app.trg_messages_seq()` مُحوَّل `SECURITY DEFINER` (سطر 807) فإدراج رسالة بدور `sharwa_app` عبر PgBouncer **متوقَّع نجاحه** (الزناد يحدّث `message_seq`/`last_inbound_seq` بسياق المالك). البوابة (P1.0.1 الخطوة 5) **لم تُشغَّل فعلياً** — سُجِّل كبند تحقق مؤجَّل في P1_OPEN_QUESTIONS.md مع خطة الإصلاح (ترحيل 0003) إن ظهر `permission denied`.

---

## P1.0.2 — طبقة الرصد `core/app/obs/` ✔

أنشئت: `logging.py` (JSON formatter + `mask_phone` + `log_event`)، `metrics.py` (كل عائلات P1.1.6 على `CollectorRegistry` خاص)، `http.py` (`/healthz` مفتوح + `/metrics` Bearer بـ`hmac.compare_digest`).

الدليل (حقيقي):
```
$ python -c "from app.obs.logging import mask_phone; print(mask_phone('+96771234567'))"
+***567
```

## P1.0.3 — إعداد العامل `WorkerSettings` ✔

`core/app/workers/config.py` بكل المتغيّرات (§7 الجدول) + الفحصين `CORE_DB_POOL_MAX ≥ INGEST_SHARDS+1` و`CORE_INGEST_BLOCK_MS < REDIS_DURABLE_TIMEOUT_MS`. إعادة استخدام `init_pool()` عبر بروتوكول `HasDbPoolConfig` (سُجِّل D-P1-01).

## P1.0.4 — نقطة الدخول والإيقاف الرشيق ✔

`core/app/workers/realtime.py` (`python -m app.workers.realtime`): خيط لكل شريحة، معالجة تسلسلية داخل الخيط، SIGTERM/SIGINT إيقاف رشيق، فشل سريع عند استثناء غير متوقَّع، `/healthz` حي.

## P1.0.5 — compose والمراقبة ✔

`docker-compose.yml` (خدمة `worker-realtime` بحدود 384m + `INGEST_SHARDS` مصدر واحد للثلاث خدمات + وسم `sharwa-ai-core:p1.0` مشترك)، `ops/prometheus/prometheus.yml` (هدفان جديدان)، `ops/prometheus/alerts.yml` (4 قواعد)، `.env.example` (`INGEST_SHARDS`). `docker compose config` **لم يُشغَّل** (لا Docker).

## P1.1.1 — `stream.py` ✔ · P1.1.2 — `schema.py` ✔ · P1.1.3 — `repos_ingest.py` ✔ · P1.1.4 — human_takeover/identity_update ✔ · P1.1.5 — `optout.py` ✔ · P1.1.6 — المقاييس ✔ · P1.1.7 — F-P1-01 + إصلاح ✔ · P1.1.8 — اختبارات (نُفِّذ الجزء النقي) ⚠

تفاصيل النتائج والأدلة في `P1_01_REPORT.md` و`P1_FINDINGS.md`.
