# P0 — تدقيق رقم 3 (على جولة R2) — 2026-09-21

**الحكم: R2 مقبولة.** كل إصلاحات B1–B7 وE1 تحقّقتُ منها بالتشغيل. `PHASE_GATE.md` يبقى `P0: BLOCKED` (بيئة). لا عمل آخر مسموح لديب سيك قبل وصول Docker/الـVPS.

## ما شغّلتُه بنفسي

| الفحص | النتيجة |
|---|---|
| اختبارات `hunt_gate` + `check_env` (ملفات صريحة) | **18/18** (12 + 6) ✔ |
| اختبارات `identity` | 8/8 ✔ |
| `identity` على الأشكال الحقيقية | `sender_pn` كـJID ⇒ `+201111112222`؛ `:12` و`@c.us` ⇒ `wa_id` أرقام و`phone_e164` بـ`+`؛ lidMap كـJID ⇒ رقم؛ أرقام `@lid` **لا تتسرّب** إلى الهاتف؛ مدخلات فاسدة ⇒ `null` ✔ |
| `check_env` | على `.env.example` ⇒ فشل (17 مشكلة)؛ على `.env` من `gen_secrets` ⇒ OK ✔ |
| `10_roles.sh` (نسخة R2) على PostgreSQL 16 حقيقي | ينجح، **وإعادة تشغيله آمنة** (exit 0) ✔ |
| `schema.sql` بمستخدم ترحيلات غير superuser ثم `schema_selftest.sql` | **ALL SELF-TESTS PASSED** ✔ |
| B5: دور التطبيق يقرأ `spatial_ref_sys` وينفّذ `ST_Transform` | ينجح ✔؛ والدور ليس superuser ✔ |
| `docker compose config` بقيم مولَّدة | ينجح؛ أمر Redis يُعرَض `--requirepass "$$REDIS_PASSWORD"`؛ حدود الذاكرة صحيحة (redis-durable = 320m) ✔ |
| `redis-durable.conf` (النسخة الجديدة) | يُحمَّل، والمصادقة مفروضة (`NOAUTH`) ✔ |
| `hunt_gate` على المستودع | المخالفات الموروثة الخمس فقط ✔ (ظهرت سادسة `gateway/Dockerfile` CRLF في نسختي لأنها لقطة قديمة قبل التحويل لـLF؛ تقريرك يقول `i/lf w/lf` وأقبله، وسيتأكد المدقق على الـVPS بـ`git ls-files --eol`) |

## ملاحظات للمستقبل (لا إصلاح الآن)

1. **P0.7:** عند بناء `core` بـ`psycopg`/PgBouncer قد تحتاج `ignore_startup_parameters = extra_float_digits` في `pgbouncer.ini` (تُجرَّب على الـVPS؛ لا تضفها قبل ظهور خطأ فعلي).
2. **PgBouncer/الصورة:** سلوك مدخل `edoburu/pgbouncer` مع `pgbouncer.ini` مُركَّب قراءةً فقط (هل يولّد `userlist.txt`؟) **غير مُتحقَّق** — أول فحص على الـVPS: `docker compose logs pgbouncer` ثم اتصال `psql` عبر PgBouncer بمستخدم التطبيق.
3. وسم الصورة `edoburu/pgbouncer:v1.25.2-p0` وسم غير مؤكَّد الوجود (لم أستطع الوصول لـDocker Hub). إن فشل `docker compose pull` فاستبدله بوسم موجود وسجّله في `P0_DEVIATIONS.md` (D-13).

## يوم الـVPS — قائمة التنفيذ بالترتيب (من `P0_RUNBOOK.md` + إضافات)

1. `sysctl vm.overcommit_memory=1`، `ufw` (SSH فقط)، Docker + Compose.
2. نقل المستودع بـgit عبر SSH (مفاتيح فقط)، ثم `git ls-files --eol` (لا `w/crlf` في أي ملف) ثم `node scripts/hunt_gate.mjs` و`node --test` للحزم كلها.
3. `node scripts/gen_secrets.mjs && node scripts/check_env.mjs`، ثم `docker compose pull && docker compose build`.
4. `docker compose up -d`، إثبات `healthy` للأربع، `docker inspect` (`OomScoreAdj`, `Memory`)، `docker stats --no-stream`.
5. اختبار حقيقي لسكربت التهيئة داخل الحاوية + `schema_selftest.sql` (34) + PgBouncer/RLS (ألف معاملة متداخلة) + اختبارات Redis (قتل ×10، وBGREWRITEAOF بذروة < 85%).
6. عندها فقط يُستأنف P0.2… حسب `PROMPT_P0`، والتقرير النهائي عند P0.8 بالقالب.

**توجيه لديب سيك الآن:** لا شيء جديد. التوقف قائم حتى يصل الـVPS.
