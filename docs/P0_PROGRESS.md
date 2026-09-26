# P0 Progress Log — sharwa_ai

> الحاكم: `PROMPT_P0_foundation_for_deepseek.md`. كل خطوة تُسجَّل فور إنجازها بأدلتها الحقيقية (H8).
> التاريخ: 2026-09-20

---

## P0.0 — التمهيد وخط الأساس

**الحالة: ✅ مكتملة — معيار قبول P0.0 محقَّق (14/14، hunt_gate يعمل، P0_FINDINGS.md بأدلة، P0_PROGRESS.md منشأ).**

### 0.1 قراءة الوثائق الحاكمة (بالترتيب، كاملة)
1. `SHARWA_AI_PROJECT_SUMMARY.md`
2. `PROMPT_phase3a_step1_gateway_foundation.md` و`PROMPT_phase3a_step2_contract_alignment.md`
3. `docs/00_ARCHITECTURE.md` (v1.1)
4. `docs/02_RISK_SOLUTIONS_21.md`
5. `docs/01_FIVE_TASKS_DESIGN.md`
6. `docs/reference/*` (schema.sql، schema_selftest.sql، docker-compose.reference.yml، README.md)
7. كود `gateway/` كاملاً (src/index.js، sessions.js، webhook.js + الاختبارات الثلاثة)

### 0.2 خط الأساس — الاختبارات القائمة
الأمر الحرفي:
```
cd gateway && node --test "src/__tests__/*.test.js"
```
المخرج الحقيقي (الأسطر الأخيرة):
```
✔ auth header: X-API-Key passes, legacy X-Gateway-Key is rejected with 401 (161.3112ms)
✔ toQrImageBase64 returns a valid base64 PNG without the data: prefix (107.5531ms)
✔ getSessionStatus returns exactly status/qr_image_base64/connected_phone_number (22.1734ms)
✔ POST /sessions returns 201 with qr_image_base64 (not just session_id) (113.9447ms)
✔ detectMedia identifies image/audio/video/document (4.4877ms)
✔ a media message with no caption is not ignored (1.0699ms)
✔ mediaObjectKey produces sharwa-ai/{session}/{uuid}.{ext} (2.6839ms)
✔ successful media upload forwards media_object_key and media_type (3.1994ms)
✔ failed media upload yields media_object_key null without throwing (1522.6382ms)
✔ sign() is deterministic and changes completely when the body changes (5.9758ms)
✔ inbound filtering: fromMe/group/broadcast/empty messages never emit a webhook (1.619ms)
✔ send queue: two messages for the same session are never sent simultaneously (99.1178ms)
✔ postInboundMessage forwards text as message_text (Django contract) (10.3564ms)
✔ outgoing webhook paths match Django public routes (no /api/, trailing slash) (4.5554ms)
ℹ tests 14
ℹ pass 14
ℹ fail 0
```
**النتيجة: 14/14 ناجح ✔**

### 0.3 سكربت Hunt Gate
- أنشأتُ `scripts/hunt_gate.mjs` (Node، متعدد المنصات، ينفّذ القواعد الثمانية في §3.1؛ يطبع عبر `console.error` لئلا يخالف قاعدته الرابعة؛ يستثني نفسه من المسح كأي linter).
- شغّلته على الكود القائم. المخرج الحقيقي:
```
gateway\src\index.js:116: [H12/rule4] console.log outside test files (use pino)
gateway\src\sessions.js:445: [H12/rule4] console.log outside test files (use pino)
gateway\src\sessions.js:474: [H12/rule4] console.log outside test files (use pino)
gateway\src\sessions.js:482: [H12/rule4] console.log outside test files (use pino)
HUNT GATE FAILED — 4 violation(s).
```
- **4 مخالفات موروثة** (كلّها `console.log` في كود الإنتاج): تُصلَح لاحقاً عند لمس الملفات في P0.6 (التحويل إلى pino). مسجَّلة كديون موروثة، لا تُسكَت عنها.

### 0.4 تحقق @lid في baileys@6.7.24
تم بالدليل (ملف:سطر) — التفاصيل الكاملة في `docs/P0_FINDINGS.md`. الخلاصة: النسخة 6.7.24 **تدعم** `@lid` (`jidDecode` domainType=1، `isLidUser`، `creds.me.lid`، حدث `chats.phoneNumberShare` يربط lid→sender_pn، وبروتوكول `LID_MIGRATION_MAPPING_SYNC`). **لا يوجد** حقلان باسم `remoteJidAlt`/`participantAlt` في هذه النسخة.

### 0.5 تحقق البيئة
| المكوّن | المطلوب | الموجود فعلياً | الحكم |
|---|---|---|---|
| Node | ≥ 22 | v24.20.0 | ✔ |
| npm | — | 11.19.0 | ✔ |
| Python | 3.12 | 3.13.15 | ⚠ انحراف |
| Docker + Compose | مطلوب | **غير مثبّت** | ✘ |
| WSL | (مطلوب لتشغيل Docker) | غير مثبّت | ✘ |
| PostgreSQL | 16 + pgvector + PostGIS + pg_trgm | 18.6 محلي (pg_trgm فقط؛ لا pgvector/PostGIS؛ كلمة المرور غير معروفة) | ✘ |
| Redis | 6.2+ (XAUTOCLAIM/BLMOVE) | 5.0.14.1 (Windows) | ✘ |
| PgBouncer / MinIO / Prometheus / Playwright | مطلوبة | غير مثبّتة | ✘ |

### الاستنتاج (قاعدة P0.0 خطوة 5)
Docker (ومعه Compose وPgBouncer وMinIO وPrometheus) **غير متوفّر ولا يمكن تثبيته في هذه البيئة** (لا WSL، ولا صلاحيات تثبيت Docker Desktop + إعادة تشغيل). لذلك **أتوقف هنا** كما يفرض الملف الحاكم: «ما ينقص وتعذّر تثبيته: أوقف واكتب السبب (لا تلتفّ بمحاكاة)».

**P0.1–P0.8 غير قابلة للتنفيذ/التحقق في هذه البيئة** لأن معايير قبولها تعتمد على `docker compose config`، `docker inspect`، `docker stats`، PgBouncer، MinIO، `promtool`، واختبارات فوضى `kill -9`/`docker stop`. التفاصيل في `P0_OPEN_QUESTIONS.md` و`P0_DEVIATIONS.md`.

---
*الحالة: متوقف عند حدود P0.0 بانتظار بيئة Docker أو قرار المدقق.*

---

## R1 — جولة الاستئناف الجزئي (توجيه الاستئناف، بدون Docker)

> التاريخ: 2026-09-21. ثلاث مهام فقط: (1) إصلاح A1–A5 في Hunt Gate، (2) مسودة بنية P0.1 بلا تشغيل، (3) `identity.js` + اختبارات. لا تُعدّ أي خطوة P0.x منجزة؛ P0.1/P0.2 غير مقبولة حتى تُشغَّلا على Docker حقيقي.

### جدول: ما شُغِّل فعلاً مقابل ما لم يُشغَّل

| البند | الحالة | الدليل |
|---|---|---|
| إصلاح A1–A5 في `scripts/hunt_gate.mjs` | ✅ شُغِّل | `node --test scripts/__tests__/hunt_gate.test.mjs` → **11/11 ناجح** |
| تشغيل Hunt Gate على المستودع | ✅ شُغِّل | `node scripts/hunt_gate.mjs` → **5 مخالفات** (خروج 1) |
| `gateway/src/ingest/identity.js` (G12) | ✅ شُغِّل | `node --test gateway/src/__tests__/identity.test.js` → **7/7 ناجح** |
| مجموعة `gateway` كاملة | ✅ شُغِّل | `cd gateway && node --test "src/__tests__/*.test.js"` → **21/21 ناجح** |
| مسودة P0.1 (`docker-compose.yml` + `ops/*` + Dockerfile + init) | ❌ **لم يُشغَّل** | لا Docker بعد — commit بوسم `[UNVERIFIED]` |
| P0.1 (Docker حقيقي: `docker compose config`/selftest) | ❌ لم يُشغَّل | محجوب بغياب Docker |
| P0.2 (Redis حقيقي + `identity_update` + lidmap على redis-durable) | ❌ لم يُشغَّل | مؤجَّل حتى Docker |

### (1) مخرج `node --test scripts/__tests__/hunt_gate.test.mjs`
```
✔ a clean codebase passes with zero violations
✔ rule1 (H1): TODO/FIXME/dummy/foo/bar markers are flagged
✔ A3: placeholder= attribute is allowed; placeholder as a word/variable is banned
✔ A1: catch {} (no binding) and catch (err) {} are both flagged as empty catch
✔ A2: .catch(() => {}) and .catch(() => undefined) are flagged as swallowed rejections
✔ rule2 (H1): standalone pass and bare except are flagged in Python
✔ rule4 (H12): console.log (JS) and print() (Python) outside tests are flagged
✔ rule5 (H7): .skip/.only (JS) and pytest skip/xfail (Python) are flagged in tests
✔ rule6 (H2): psycopg/asyncpg import outside core/app/db/ is flagged
✔ rule7 (H5): AWS access key and a committed .env file are flagged
✔ A4/rule8 (H2): uppercase SQL inside a string is flagged; lowercase prose is not
ℹ tests 11
ℹ pass 11
ℹ fail 0
```

### (1) مخرج `node scripts/hunt_gate.mjs` (خروج 1)
```
gateway\src\index.js:116: [H12/rule4] console.log outside test files (use pino)
gateway\src\sessions.js:481: [H3/rule3-swallowed] swallowed promise rejection (.catch with empty/undefined body)
gateway\src\sessions.js:445: [H12/rule4] console.log outside test files (use pino)
gateway\src\sessions.js:474: [H12/rule4] console.log outside test files (use pino)
gateway\src\sessions.js:482: [H12/rule4] console.log outside test files (use pino)
HUNT GATE FAILED — 5 violation(s).
```
> الملاحظة: A2 كشف الآن المخالفة الموروثة `.catch(() => {})` في `sessions.js:481` (كانت ضمن «مخالفات موروثة تُصلَح حين يُلمس الملف» في التدقيق §3) إضافةً إلى `console.log` الأربع. لم تُلمَس `sessions.js` في هذه الجولة.

### (3) مخرج `node --test gateway/src/__tests__/identity.test.js`
```
✔ isLidJid / isPnJid classify the two addressing schemes
✔ normalizeE164 strips +/whitespace and rejects non-phone values
✔ phoneFromPnJid decodes a phone JID and rejects lid/group JIDs
✔ normalizeJid maps pn and lid to a stable identity without assuming JID = phone
✔ resolvePhoneE164: sender_pn wins, then lidmap for @lid, then null
✔ normalizeIdentity: full G12 priority for @lid and pn messages
✔ audit §5 scenario: @lid is null, then a phoneNumberShare populates the map, then the number is present
ℹ tests 7
ℹ pass 7
ℹ fail 0
```

### ملاحظات صادقة (H8)
- لم أُشغِّل أي شيء يعتمد على Docker؛ مسودة P0.1 غير مُتحقَّق منها وتُوسَم `[UNVERIFIED]`.
- `PHASE_GATE.md` يبقى `P0: BLOCKED`.

---

## R2 — جولة تدقيق رقم 2 (إصلاح B1–B7 وE1)

> التاريخ: 2026-09-21. ما لا يحتاج Docker شُغِّل فعلاً هنا؛ ما يعتمد على Docker بقي `[UNVERIFIED]`. لا تُعدّ أي خطوة P0.x منجزة.

### جدول: ما شُغِّل فعلاً مقابل ما لم يُشغَّل

| البند | الحالة | الدليل |
|---|---|---|
| B1 قاعدة CR في `hunt_gate.mjs` + اختبارها | ✅ شُغِّل | `node --test scripts/__tests__/hunt_gate.test.mjs` → **12/12 ناجح** |
| B1 تحويل LF + `.gitattributes`/`.editorconfig` | ✅ شُغِّل | `grep` يعادل `[char]13` عبر كل الملفات → **صفر CR** |
| B6 `check_env.mjs` + اختباره | ✅ شُغِّل | `node --test scripts/__tests__/check_env.test.mjs` → **6/6 ناجح** |
| B6 `gen_secrets.mjs` (اختياري) | ✅ شُغِّل | توليد `.env` ثم `check_env` عليه → `OK` (خروج 0) |
| E1 إصلاح `identity.js` | ✅ شُغِّل | `node --test gateway/src/__tests__/identity.test.js` → **8/8 ناجح** |
| مجموعة `gateway` كاملة | ✅ شُغِّل | `cd gateway && node --test "src/__tests__/*.test.js"` → **22/22 ناجح** |
| تشغيل Hunt Gate على المستودع | ✅ شُغِّل | `node scripts/hunt_gate.mjs` → **5 مخالفات موروثة** (خروج 1) |
| B2/B3/B4/B5/B7 (ملفات المسودة) | ❌ لم يُشغَّل | لا Docker — `[UNVERIFIED]` |

### مخرج `node --test scripts/__tests__/hunt_gate.test.mjs` (12/12)
```
✔ a clean codebase passes with zero violations
✔ rule1 (H1): TODO/FIXME/dummy/foo/bar markers are flagged
✔ A3: placeholder= attribute is allowed; placeholder as a word/variable is banned
✔ A1: catch {} (no binding) and catch (err) {} are both flagged as empty catch
✔ A2: .catch(() => {}) and .catch(() => undefined) are flagged as swallowed rejections
✔ rule2 (H1): standalone pass and bare except are flagged in Python
✔ rule4 (H12): console.log (JS) and print() (Python) outside tests are flagged
✔ rule5 (H7): .skip/.only (JS) and pytest skip/xfail (Python) are flagged in tests
✔ rule6 (H2): psycopg/asyncpg import outside core/app/db/ is flagged
✔ rule7 (H5): AWS access key and a committed .env file are flagged
✔ A4/rule8 (H2): uppercase SQL inside a string is flagged; lowercase prose is not
✔ B1: CRLF (\r) in LF-required files is flagged; LF-only files pass
ℹ tests 12
ℹ pass 12
ℹ fail 0
```

### مخرج `node scripts/hunt_gate.mjs` (خروج 1)
```
gateway\src\index.js:116: [H12/rule4] console.log outside test files (use pino)
gateway\src\sessions.js:481: [H3/rule3-swallowed] swallowed promise rejection (.catch with empty/undefined body)
gateway\src\sessions.js:445: [H12/rule4] console.log outside test files (use pino)
gateway\src\sessions.js:474: [H12/rule4] console.log outside test files (use pino)
gateway\src\sessions.js:482: [H12/rule4] console.log outside test files (use pino)
HUNT GATE FAILED — 5 violation(s).
```

### مخرج `node --test gateway/src/__tests__/identity.test.js` (8/8)
```
✔ isLidJid / isPnJid classify addressing schemes
✔ normalizeE164 emits real E.164 (+digits) and rejects non-phone values
✔ toE164 accepts bare digits and JIDs, strips :device/_agent, never from @lid
✔ normalizeJid: pn JID strips device suffix and emits +e164; lid yields null
✔ resolvePhoneE164: sender_pn (digits OR JID) wins; lidmap accepts digits OR JID
✔ normalizeIdentity: full G12 priority and E.164 output
✔ property: every non-null phone_e164 matches +digits; never derived from @lid digits
✔ audit §5 scenario: @lid null -> phoneNumberShare updates map -> number present
ℹ tests 8
ℹ pass 8
ℹ fail 0
```

### مخرج `node --test scripts/__tests__/check_env.test.mjs` (6/6)
```
✔ checkEnv accepts strong, unique passwords
✔ checkEnv rejects the change-me stub value
✔ checkEnv rejects short passwords
✔ checkEnv rejects a password reused across roles
✔ checkEnv rejects missing passwords
✔ parseEnv parses KEY=VALUE and skips comments/blank lines, strips quotes
ℹ tests 6
ℹ pass 6
ℹ fail 0
```

### مخرج `check_env`/`gen_secrets` (تشغيل فعلي)
```
gen_secrets exit: 0          (كتب .env عشوائي)
check_env(good) exit: 0      → check_env: OK
check_env(bad)  exit: 1      → يرفض change-me (17 مشكلة: stub/قصير/مكرر)
```

### ملاحظات صادقة (H8)
- B2 (نمط `\gexec`)، B3 (ذاكرة redis)، B4 (pgbouncer)، B5 (`spatial_ref_sys`)، B7 (كلمة مرور redis عبر env) عُدِّلت في ملفات المسودة فقط ولم تُشغَّل (لا Docker) — `[UNVERIFIED]`.
- `PHASE_GATE.md` يبقى `P0: BLOCKED`.

---

## R3 — جولة الـVPS (Docker حقيقي)

> التاريخ: 2026-09-21. الـVPS وصل (Linux + Docker حقيقي). تنفيذ قائمة «يوم الـVPS» من `docs/P0_AUDIT_03.md` §يوم الـVPS و`docs/R3_DIRECTIVE.md`، بالترتيب أ) ← هـ)، ثم التوقف عند نقطة التحقق C1.

### أ) بيئة الـVPS — المخرجات الحقيقية

```
nproc
  4

free -m
               total        used        free      shared  buff/cache   available
  Mem:          7941         994        6257           1         991        6946
  Swap:            0           0           0

df -h
  Filesystem      Size  Used Avail Use% Mounted on
  tmpfs           795M  1.1M  794M   1% /run
  /dev/sda1        96G  4.2G   92G   5% /
  tmpfs           3.9G     0  3.9G   0% /dev/shm
  tmpfs           5.0M     0  5.0M   0% /run/lock
  /dev/sda16      881M  117M  703M  15% /boot
  /dev/sda15      105M  6.2M   99M   6% /boot/efi
  tmpfs           795M   16K  795M   1% /run/user/0

docker --version
  Docker version 29.1.3, build 29.1.3-0ubuntu3~24.04.2

docker compose version
  Docker Compose version 2.40.3+ds1-0ubuntu1~24.04.1

uname -r
  6.8.0-138-generic
```

**ملاحظة بيئية (H8، بصدق):** المضيف 4 vCPU وذاكرة **7,941 MB (~7.75 GB)** — دون خط أساس 8GB بقليل. ميزانية طبقة البيانات (postgres 1536 + pgbouncer 64 + redis-durable 320 + redis-cache 128 = **2,048 MB**) تترك هامشاً وافراً للنظام وpage cache، ولا تمسّ هذا الهامشَ أي خدمة أخرى في P0.1. Docker Compose **v2** (2.40.3) نُصِّب عبر apt (لم أُنزّل أي ثنائي).

### ب) نهايات الأسطر + Hunt Gate + اختبارات الحزم

**1) `git ls-files --eol` — لا أي ملف CRLF:**

```
total tracked files: 54
files with w/crlf or w/mixed: 0
```
و`grep` عن `\r` الحرفي في `*.sh`/`Dockerfile`/`*.yml`/`*.conf`/`*.ini`/`*.sql` ⇒ **صفر**. B1 سليم.

**2) `node scripts/hunt_gate.mjs` — عيب ظهر في أول تشغيل حقيقي، أُصلح.**

أول تشغيل على الـVPS أظهر **6** مخالفات لا 5: أضافها `.env:1: [H5/rule7-env] real .env file present`. هذا **إنذار كاذب**: القاعدة صُمّمت لالتقاط `.env` **مُلتزَم** (committed) فقط (PROMPT §3.1 rule 7)، لكن التنفيذ كان يفحص وجود الملف على القرص لا كونه مُتعقَّباً. على VPS حقيقي يوجد `.env` **gitignored** (`git check-ignore -v .env` → `.gitignore:8:.env`) وهو مطلوب لتشغيل `docker compose`.

الإصلاح: القاعدة تستدعي `git -C <root> check-ignore --quiet -- <rel>` ولا تُعلّم `.env` إلا إذا **لم يكن** مُتجاهَلاً من git (أي سيُلتزم فعلاً). أضفتُ اختباراً جديداً (`.env` مُتجاهَل لا يُعلَّم؛ غير مُتجاهَل يُعلَّم). النتيجة:

```
# node --test scripts/__tests__/hunt_gate.test.mjs  →  tests 13 / pass 13 / fail 0
```

بعد الإصلاح، `node scripts/hunt_gate.mjs` يعود للمخالفات الموروثة الخمس فقط (خروج 1):

```
gateway/src/index.js:116: [H12/rule4] console.log outside test files (use pino)
gateway/src/sessions.js:481: [H3/rule3-swallowed] swallowed promise rejection (.catch with empty/undefined body)
gateway/src/sessions.js:445: [H12/rule4] console.log outside test files (use pino)
gateway/src/sessions.js:474: [H12/rule4] console.log outside test files (use pino)
gateway/src/sessions.js:482: [H12/rule4] console.log outside test files (use pino)
HUNT GATE FAILED — 5 violation(s).
```
> هذه الخمسة ديون موروثة تُصلَح عند لمس `index.js`/`sessions.js` (توجيه التدقيق §3 وR3). لا تُلمَس الآن.

**3) `node --test` لكل الحزم:**

```
# scripts: hunt_gate.test.mjs  →  13/13 ✔
#          check_env.test.mjs  →   6/6  ✔
# gateway:  src/__tests__/*.test.js → 22/22 ✔
```

### ج) الأسرار + سحب/بناء الصور

**1) `node scripts/gen_secrets.mjs --force` + `node scripts/check_env.mjs`:**

```
gen_secrets: wrote /home/sharwa/sharwa_ai/.env
gen_secrets exit=0
check_env: OK
check_env exit=0
```
> كان يوجد `.env` صالح (check_env عليه = OK، 11 مفتاحاً)؛ أعدتُ توليده بـ`--force` ليكون أسراراً جديدة محلية للـVPS لا الأسرار المولّدة على جهاز Windows أثناء R2. كل المفاتيح الـ11 موجودة.

**2) `docker compose pull` — ناجح (خروج 0):**

```
redis-durable  Pulled   (redis:7.4-alpine)
redis-cache    Skipped - Image is already being pulled by redis-durable
pgbouncer      Pulled   (edoburu/pgbouncer:v1.25.2-p0)
postgres       Skipped - No image to be pulled (تُبنى من Dockerfile)
```
> **D-13 غير مطلوب:** الوسم `edoburu/pgbouncer:v1.25.2-p0` **موجود فعلاً** على Docker Hub (سُحب بنجاح). لا انحراف لازم.

**3) `docker compose build` — ناجح (خروج 0):**

بنيت صورة `sharwa_ai-postgres:latest` من `postgis/postgis:16-3.4` + `postgresql-16-pgvector 0.8.6` (حدّث postgresql-16 إلى 16.15). السطر الأخير:
```
postgres  Built
build exit=0
```

### د) إقلاع الخدمات + صحتها + حدود الموارد

**1) `docker compose up -d` — خروج 0؛ الأربع Healthy:**

```
sharwa_ai-pgbouncer-1       pgbouncer       Up 57 seconds (healthy)
sharwa_ai-postgres-1        postgres        Up About a minute (healthy)
sharwa_ai-redis-cache-1     redis-cache     Up About a minute (healthy)
sharwa_ai-redis-durable-1   redis-durable   Up About a minute (healthy)
```

**2) `docker inspect` — OomScoreAdj وMemory لكل حاوية:**

```
/sharwa_ai-postgres-1       OomScoreAdj=-900  Memory=1610612736 (1536m)  MemorySwap=1536m  NanoCpus=2.0   Pids=300
/sharwa_ai-pgbouncer-1      OomScoreAdj=0     Memory=67108864   (64m)    MemorySwap=64m    NanoCpus=0.5   Pids=50
/sharwa_ai-redis-durable-1  OomScoreAdj=0     Memory=335544320  (320m)   MemorySwap=320m   NanoCpus=0.5   Pids=50
/sharwa_ai-redis-cache-1    OomScoreAdj=0     Memory=134217728  (128m)   MemorySwap=128m   NanoCpus=0.5   Pids=50
```
> `OomScoreAdj=-900` لـ postgres محقَّق (B3/خطر 16). كل حد `mem_limit` = `memswap_limit` (لا swap) كما في المسودة.

**3) `docker stats --no-stream` (بعد الإقلاع، قبل أي حمل):**

```
NAME                        CPU %     MEM USAGE / LIMIT   MEM %     PIDS
sharwa_ai-pgbouncer-1       0.03%     1.625MiB / 64MiB    2.54%     1
sharwa_ai-postgres-1        0.00%     49.5MiB / 1.5GiB    3.22%     6
sharwa_ai-redis-cache-1     0.73%     3.227MiB / 128MiB   2.52%     6
sharwa_ai-redis-durable-1   0.66%     3.199MiB / 320MiB   1.00%     6
```

**4) جدول مجموع `mem_limit` (طبقة البيانات):**

| الخدمة | mem_limit |
|---|---|
| postgres | 1536 MB |
| pgbouncer | 64 MB |
| redis-durable | 320 MB |
| redis-cache | 128 MB |
| **المجموع** | **2048 MB** |

المجموع 2,048 MB ≪ ميزانية 5,360 MB (المرجع)، ويترك من مضيف ~7.75 GB نحو ~5.7 GB للنظام وpage cache وطبقات التطبيق اللاحقة.

### هـ) معايير قبول P0.1 الكاملة

#### هـ.1 سكربت التهيئة + schema.sql + schema_selftest.sql

**الأدوار (بعد `10_roles.sh` داخل الحاوية):**

```
sharwa_admin          superuser, login
sharwa_app            NOLOGIN, nobypassrls
sharwa_system         NOLOGIN, nobypassrls
sharwa_app_login      LOGIN, nobypassrls
sharwa_system_login   LOGIN, nobypassrls
sharwa_migration      LOGIN, nobypassrls
```
العضوية: `sharwa_app_login→sharwa_app`، `sharwa_migration→sharwa_app`، `sharwa_system_login→sharwa_system`. الامتدادات: `pg_trgm 1.6`، `postgis 3.4.3`، `vector 0.8.6`.

**`schema.sql` بمستخدم الترحيلات (غير superuser) — خروج 0:**
```
... (DO/CREATE TABLE/INDEX/FUNCTION/POLICY/GRANT/REVOKE) ...
WARNING:  no privileges were granted for "public"
WARNING:  no privileges were granted for "spatial_ref_sys"
schema.sql exit=0
```
> التحذيران من فئة B5 المعروفة: حلقة المنح في `schema.sql` تحاول منح INSERT/UPDATE/DELETE على كائنات يملكها امتداد postgis (`spatial_ref_sys`) والـpostgres (`public`)، والمستخدم الترحيلات ليس مالكها. منح **SELECT** على `spatial_ref_sys` (المطلوب فعلياً) يأتي من `10_roles.sh` كـsuperuser. لا أثر على الـselftest (انظر أدناه).

**`schema_selftest.sql` (كمستخدم superuser لأنه يستخدم `SET ROLE sharwa_system`) — 34/34:**

```
PASS #1a .. PASS #1g   (عزل RLS + vector + partitions + لا أعمدة تكلفة)
PASS #5a #5b           (idempotency)
PASS #11a/b #11c #11d/e #5c #11f #11g #11h #1h #25   (آلة التسليم/outbox/الأدوار)
PASS #17a #17b #17c    (scheduled jobs + crash recovery)
PASS #7a/b #7c #7d     (back-in-stock FIFO)
PASS #19               (kill switch)
PASS #13a #13b         (spatial)
PASS search            (Arabic FTS)
PASS #20               (money BIGINT)
PASS D5a-d D5e D5f D3a D5g  (order lookup)
ALL SELF-TESTS PASSED
selftest exit=0
```

#### هـ.2 PgBouncer + عزل RLS عبره

**سلوك مدخل الصورة (التحقق الفعلي من B4):**
- `pgbouncer.ini` المركّب **لم يُستبدل** (المحتوى هو ملفي، بما فيه تعليق "UNVERIFIED"). ✓
- `userlist.txt` **وُلّد عند الإقلاع** من `DB_USER`/`DB_PASSWORD` (سطر واحد، مستخدم `sharwa_app_login` وحده؛ كلمة المرور بنص صريح داخل الحاوية فقط — أُسجَّل كملاحظة H5 في C1). ✓
- قرار B4 مُثبَت: `sharwa_system_login` عبر PgBouncer ⇒ `FATAL: SASL authentication failed` (ليس في userlist)؛ ومباشرةً إلى postgres ⇒ `sharwa_system_login` ناجح (مسار الصيانة المحجوز). ✓

**اتصال مستخدم التطبيق عبر PgBouncer (transaction pooling):**
```
sharwa_app_login @ 172.18.0.2/32 -> sharwa_ai      (الخلفية هي postgres، عبر pgbouncer)
SELECT count(*) FROM tenants  => 0                  (RLS بلا SET LOCAL ⇒ صفر)
```

**اختبار RLS عبر PgBouncer (ألف معاملة متداخلة + بلا SET LOCAL):**

أعددتُ 50 عميلاً وسم `rlstest-a-*` للمتجر A و50 `rlstest-b-*` للمتجر B، ثم شغّلت 1,100 معاملة متزامنة (تزامن 20 عبر pool):
- 500 معاملة `SET LOCAL app.tenant_id=A` → يجب أن ترى 50 من `a` و0 من `b`.
- 500 معاملة `SET LOCAL app.tenant_id=B` → يجب أن ترى 50 من `b` و0 من `a`.
- 100 معاملة **بلا** `SET LOCAL` → يجب أن ترى صفراً.

```
JOBS=1100  RESULT_LINES=4300  OK=1100  BAD=0
stderr: 0
```
> كل معاملة أعادت `OK` واحدة (1100/1100). `RESULT_LINES=4300` لأن `psql -c` يطبع وسوم الأوامر `BEGIN`/`SET`/`COMMIT` مع نتيجة الـSELECT (1000×4 + 100×3). **صفر تسرّب، صفر `BAD`، ومعاملة بلا `SET LOCAL` تعيد صفراً.**

#### هـ.3 Redis — المتانة والصبيب وحدّ الذاكرة (مخرجات حقيقية)

**1) `redis-durable` — قتل `kill -9` ×10 أثناء XADD متواصل (كل معرّف مُقَرّ ينجو):**

الطريقة (مثبَتة تجريبياً على الـVPS): `docker kill --signal=KILL` = SIGKILL (`ExitCode=137`) لكنّه **توقيف يدوي** لا يفعّل `restart: unless-stopped`؛ لذا بعد كل قتل أُعيد `docker start` صراحةً. لذلك يبقى `RestartCount=0` (لا يتزايد بقتل يدوي) بينما يتغيّر `StartedAt` في كل جولة (بفعل `docker start`). كاتبٌ يدفع 2,000 `XADD` متواصلاً ويسجّل كل معرّف مُقَرّ، والقتل يقع بينهما، ثم مقارنة المُقرَّ مقابل ما نجا في الـstream:

```
10 rounds of SIGKILL + restart during continuous XADD
acked IDs recorded:  2000
surviving IDs (XRANGE after restart): 2000
DIFF (acked vs survived): IDENTICAL
first=1790022564668-0  last=1790023374278-0
```
> **النتيجة: 2,000/2,000 معرّف نجا بعد 10 عمليات SIGKILL** مع `appendfsync always` (`save ""`) — لا فقدان لمعرّف مُقَرّ واحد.

**2) قياس صبيب الكتابة الدائم (منفصل عن اختبار القتل) — `redis-benchmark XADD` داخل الحاوية:**

```
1 client,  5000 requests:  197.82 requests/s   (p99 ≈ 43.0 ms)
50 clients, 5000 requests: 1894.66 requests/s  (p99 ≈ 172.5 ms)
aof_delayed_fsync: 0 (قبل) → 0 (بعد)  → delta 0   (لا تأجيل fsync)
```
> `appendfsync always` يجعل الصبيب أحادي العميل ~198 rps (كل `XADD` ينتظر fsync)، بينما التزامن (50 عميلاً) يُطفئ تكلفة fsync فيرتقي إلى ~1,895 rps. `aof_delayed_fsync=0` يؤكد سلامة مسار الـAOF.

**الحكم على هدف P0.2 (E16):** الهدف 10,000 رسالة/60 ث = **167 msg/s** و`p99(ingest_ack_seconds) < 200ms`. `redis-durable` يسعه: أحادي العميل 198 rps > 167 (بهامش ضيّق لكن فوق الهدف)، و50 عميلاً 1,895 rps ≫ 167 (أكثر من 11× الهدف). `p99` أسوأ حالة 172.5 ms < 200 ms. **الطاقة كافية لهدف الاستيعاب، بلا تأجيل fsync.**

**3) ملء واقعي حتى ~93% من `maxmemory` + `BGREWRITEAOF` بكاتب متزامن — الذروة دون 85% من حدّ الحاوية وبلا إعادة تشغيل:**

الملء بقيم **غير قابلة للضغط** ~1KB (base64 عشوائي 1000 حرف + فهرس 6 أرقام) — لا `SET` كبيرة ولا أصفار قابلة للضغط. (ملاحظة H8: محاولة أولى بقيم `%01024d` أصفار ضغطها RDB-preamble/LZF ~30× فأعطت AOF 4M غير واقعي؛ استُبدلت بقيم base64). مُلئ حتى `used_memory_human:119.28M` (120,000 إدخال، ~93% من `maxmemory` 128M)، ثم `BGREWRITEAOF` مع كاتب `XADD` متزامن (~1KB) يستمر طوال إعادة الكتابة، مع عيّنة `memory.current` (cgroup) كل 0.2 ث:

```
used_memory at fill:  119.28M / 128.00M   (120,000 إدخال ~1KB، غير قابلة للضغط)
rewrite sampled peak (memory.current): 137.41 MiB   (limit=320MiB, 85% = 272MiB)
docker stats MemUsage:    127.1  MiB
used_memory_peak_human:   119.75M   (قمة الأب لم ترتفع؛ الذروة في الابن عبر COW لا في الأب)
used_memory_rss_human:    131.25M
latest_fork_usec:         7790     (7.79 ms — fork لمجموعة 119M)
aof_last_bgrewrite_status: ok
restart_count_delta: 0 ; OOMKilled: false ; started_at unchanged: yes
```
> **الذروة 137.41 MiB ≪ 272 MiB (85% من حدّ 320m)** — هامش أكثر من 134 MiB، ولا إعادة تشغيل للحاوية، و`aof_last_bgrewrite_status: ok`. القفزة من ~120M (الأب) إلى ~137M (الحاوية) هي الذروة اللحظية للابن (fork + COW + مخزن rewrite) — وهي ما يرصده OOM-killer — وتبقى بعيدة عن الحدّ. قلق B3 (تضخّم rewrite فوق الحدّ) مُثبَت عدمه. (`aof_current_size` بعد الكتابة 34.7M = ضغط LZF لـbase64 ~3.4×، ضمن مدى واقعي لنصوص الرسائل؛ الأصفار هي الحالة المرضية 30× فقط.)

**4) `redis-cache` — قتل وتفريغ = سلوك فاقد سليم (موثّق):**

```
CONFIG GET save              -> (فارغ: لا RDB)
CONFIG GET appendonly        -> no
CONFIG GET maxmemory-policy  -> allkeys-lru

write 3 keys  -> DBSIZE = 3
SIGKILL + restart -> DBSIZE = 0        (البيانات فُقدت: سليم لذاكرة التخزين المؤقت)
SET + FLUSHALL   -> DBSIZE = 0         (التفريغ يعمل)
```
> `redis-cache` فاقد بالتصميم (لا AOF ولا RDB، `allkeys-lru`): قتله يُسقط مفاتيحه بلا أثر، و`FLUSHALL` يعمل. هذا هو السلوك المطلوب — لا متانة لطبقة الكاش.

#### هـ.4 عيوب المسودة — ما ظهر وأُصلح

العيب الوحيد الذي كشفه أول تشغيل حقيقي كان **في Hunt Gate لا في ملفات طبقة البيانات**، وقد أُصلح وسُجّل في ب) أعلاه:

| العيب | الأثر | الإصلاح | الحالة |
|---|---|---|---|
| `H5/rule7-env` كان يعلّم أي `.env` على القرص، ومنه `.env` المحلي **gitignored** (مطلوب لـ`docker compose`) | إنذار كاذب يُفشِل البوابة على أي VPS حقيقي | القاعدة تستدعي `git check-ignore` ولا تُعلّم إلا `.env` غير المتجاهَل (سيُلتزم فعلاً) | ✅ مُصلَح + اختبار جديد (13/13) |

أما الخمس الباقية فهي **ديون موروثة** في `gateway/` (`console.log` ×4 + `.catch(()=>{})` ×1) تُصلَح عند لمس `index.js`/`sessions.js` في P0.2+ (توجيه التدقيق §3 وR3)، لا تُلمَس الآن. لا عيب آخر ظهر في `docker-compose.yml`/`ops/*`/Dockerfile/`schema.sql`/`pgbouncer.ini`/`redis*.conf` عند التشغيل الحقيقي.

---

## P0.2 — استيعاب البوابة الدائم (G1، G2، G4، G11، G12) + الـForwarder

> التاريخ: 2026-09-22. استئناف بعد كلمة **"GO P0.2"** من المالك. الحاكم: `PROMPT_P0_foundation_for_deepseek.md` §6 P0.2 + التدقيقات 01–03 + `docs/R3_DIRECTIVE.md`.

### قرارات معمارية ملزِمة من المالك (تفتح أسئلة C1)

1. **Dedupe في كتابة واحدة fsync'd:** دمج علامة dedupe مع `XADD` في عملية ذرّية واحدة على `redis-durable` (سكربت Lua) — لا نقل dedupe إلى `redis-cache` (فاقد، سيسمح بالتكرار عند إعادة تشغيل الكاش). قيد UNIQUE في PostgreSQL يبقى خط الدفاع الأخير.
2. **تزامن الاستيعاب:** مسار الاستيعاب **لا** يُسلسِل الرسائل عبر سلسلة `XADD` تتابعية واحدة؛ بل مجمّع محدود (bounded pool) من `XADD` متزامنة عبر شظايا `in:{shard}` (حسب هـ.3.3) لتفعيل دمج fsync في Redis.
3. **سقف قيمة F4 من الطرفين:**
   - البوابة: ترفض/تحوّل أي حمولة رسالة واحدة > **64KB** قبل وصولها لـ`redis-durable`؛ الوسائط الكبيرة لا تُكتب كقيمة Redis — يذهب مرجع/مفتاح صغير إلى Redis والكائن نفسه إلى تخزين الكائنات.
   - Redis: `proto-max-bulk-len 8mb` + `client-query-buffer-limit 8mb` على `redis-durable` (سقف صلب دون حدّ الحاوية 320m) لرفض الكتابة الجامحة كخطأ بروتوكول بدل تضخيم الذاكرة. اختبار يحاول `SET`/`XADD` زائدَي الحجم ويثبت الرفض بلا OOM.

### 2.1 سقف قيمة F4 على `redis-durable` — **مُتحقَّق على الحاوية الحقيقية**

عدّلت `ops/redis-durable.conf` بإضافة السطرين (أعدت التشغيل والتُقطا بـ`CONFIG GET`):
```
proto-max-bulk-len       8388608   (8mb)
client-query-buffer-limit 8388608   (8mb)
```

**اختبار الرفض (قيمة 9MB > 8mb) — مخرجات حقيقية:**
```
SET  (9MB)  -> Error: Connection reset by peer      ; EXISTS(f4:bigkey)=0   (لم يُخزَّن)
XADD (9MB)  ->                                       ; XLEN(f4:stream)=0     (لم يُكتب)
سجل Redis (loglevel debug):
  1:M ... - Protocol error (invalid bulk length) from client: ...
      qbuf=20474 ... Query buffer during protocol error: '$9000000..xxx...'
```
> **`qbuf=20474`** هو الدليل الحاسم: مخزن الاستعلام حمل ~20KB فقط (الترويسة)، لأن redis رفض الأمر لحظة قراءة طول الـbulk `$9000000` **قبل** تخزين الجسم في المخزن — لا تضخيم لمخزن الاستعلام، ولا مسار OOM. بعد الاختبار: `DBSIZE=0`, `used_memory=1.06M`, `restart=0`, `OOMKilled=false`, `health=healthy`.

> ملاحظة H8: "Connection reset by peer" هو السلوك الموثّق لانتهاك `proto-max-bulk-len` — يغلق redis الاتصال فور اكتشاف الطول الزائد؛ سطر `Protocol error (invalid bulk length)` يظهر في السجل عند `loglevel debug` (أعدته إلى `notice` بعد الالتقاط).



### 2.2 `identity_update` WAL entry (G12/R3_DIRECTIVE) — مُتحقَّق

كان تخزين lidmap على `redis-durable` وحده غير كافٍ (`docs/R3_DIRECTIVE.md`): أُضيف `buildIdentityUpdateEvent(lid, phone_e164)` في `gateway/src/ingest/lidmap.js` يُنتج مدخل WAL طبيعي (`type:'identity_update'`) بمعرّف `provider_message_id` **حتمي** (`identity:{lid}:{phone_e164}`) لثبات الـdedupe عبر إعادة إرسال Baileys المتكررة لنفس الحدث. `sessions.js` عُدِّل ليكتب هذا المدخل على WAL عند `chats.phoneNumberShare` بدل تخزين lidmap فقط. اختبارات جديدة (تحديد الحتمية) أُضيفت. **Commit: `31a902a`.**

### 2.3 دمج `gateway`/`gateway-forwarder` في `docker-compose.yml` — مُتحقَّق على Docker حقيقي

`docker-compose.yml` كان لا يضم خدمتي البوابة عن قصد سابق؛ أُضيفتا الآن: شبكتان (`data` داخلية للوصول لـ`redis-durable`، و`app` غير داخلية للإنترنت/`host.docker.internal`)، حدود موارد (`gateway` 512m، `gateway-forwarder` 128m)، مجلد دائم `gateway_auth_sessions` لبيانات اعتماد واتساب، أسرار جديدة (`SHARWA_AI_GATEWAY_API_KEY`, `SHARWA_AI_GATEWAY_WEBHOOK_SECRET`) عبر `.env`، وMinIO اختياري (قرار مالك: حالة نشر MinIO غير معروفة وقت الكتابة — `sessions.js` أصلاً يتدهور بأمان عند فشل رفع الوسائط). **Commit: `fdec236`.**

### 2.4 عيوب حقيقية ظهرت فقط عند أول تشغيل فعلي على Docker — أُصلحت وتحقَّقت

هذه ديون ما كان ممكناً كشفها إلا بتشغيل حقيقي (H8: لا محاكاة تكشفها):

| العيب | الدليل | الإصلاح | Commit |
|---|---|---|---|
| `gateway-forwarder` ينهار عند كل إقلاع (`"Stream isn't writeable and enableOfflineQueue options is false"`) | 16 انهياراً متتالياً عبر backoff متزايد — ترتيب حتمي لا سباق | `waitForReady(client)` قبل أول أمر Redis؛ تحقَّق: `[forwarder] started` بلا خطأ، الحاوية تبقى Up | `fa980c5` |
| `gateway-forwarder` يُصدر `"Command timed out"` في كل دورة استطلاع خامل | `BLOCK_MS` (XREADGROUP BLOCK) = `commandTimeout` الجانب-عميل بالضبط، فيتسابقان | `BLOCK_MS = max(1000, redisTimeoutMs - 1500)` + اختبار انحدار | `fa980c5` |
| إنشاء جلسة واتساب يفشل بـ`EACCES` على `/app/auth_sessions/<id>` | مجلد Docker المسمّى (volume) يُنشأ `root:root` لأن `Dockerfile` لم يملك المجلد قبل `VOLUME` | `mkdir -p` + `chown -R app:app` قبل `VOLUME` في `Dockerfile`، + تصحيح ملكية الـvolume الموجود فعلاً بحاوية root مؤقتة؛ تحقَّق: إنشاء جلسة بعدها يعيد `{"status":"UNKNOWN",...}` بلا `EACCES` في السجلات | `f342950` |

مجموعة `gateway` الكاملة (وحدة + محاكاة) بعد كل هذه الإصلاحات: **65/65 ناجح.**

### 2.5 اتصال واتساب حقيقي فعلي — محاولة أولية، **خارج نطاق قبول P0.2 رسمياً**

أُنشئت جلسة تجريبية `p02test1` وتوليد QR حقيقي وعُرض للمالك لمسحه. آخر فحص حالة مباشر (`GET /sessions/p02test1/status`) أعاد `DISCONNECTED` مع QR جديد (`connected_phone_number: null`) — أي إن الاتصال الفعلي **لم يتأكَّد ناجحاً بعد** بنص صريح، ولم تتم إعادة المحاولة حتى وقت كتابة هذا السطر.

**هذا لا يحجب قبول P0.2**: راجعنا نص "قبول P0.2" الرسمي في `PROMPT_P0_DEEPSEEK_PROMPT.md` §6 حرفياً، ولا يذكر اتصال واتساب حقيقي إطلاقاً — فقط اختبارات وحدة + تكامل بـRedis حقيقي (أ/ب/ج/د أدناه). هذا قرار معماري مقصود: `FakeWaDriver` صُمم أصلاً في P0.2 ليفصل معيار القبول عن أي اعتماد على اتصال واتساب فعلي. اختبار ربط رقم حقيقي هو Stateful Application Test ومؤجَّل لما بعد P0 (smoke test تشغيلي منفصل، غير مبني على قبول P0). **قرار مالك موثَّق.**

### 2.6 قبول P0.2 الرسمي — الثلاث سيناريوهات تكامل بـRedis حقيقي (أ/ب/ج) + (د) — **محقَّق بالكامل**

نص القبول الرسمي (§6 P0.2): "اختبارات وحدة ... + تكامل بـRedis حقيقي: (أ) 1000 إعادة تسليم للرسالة نفسها ⇒ مدخل واحد في Stream وويبهوك واحد؛ (ب) قتل Redis أثناء الاستيعاب ⇒ لا ضياع (spool ثم تفريغ)؛ (ج) رسالة موقع تصل لـStream بإحداثياتها؛ (د) الـ14 اختباراً القديمة خضراء."

أُضيف `gateway/src/__tests__/real_redis_integration.test.js` (مفعَّل بـ`RUN_REAL_REDIS_TESTS=1`، بدون أي API معطَّل — `if` عادي حول تسجيل الاختبارات، لا `.skip`) يشغَّل عبر حاوية مؤقتة (`docker compose run --rm`) تُركِّب (bind mount) `gateway/src` الحالي فوق `/app/src` وقتياً (`gateway/.dockerignore` يستثني `src/__tests__` عمداً من صورة الإنتاج)، ضد `redis-durable` **الحقيقي** الذي تستخدمه `gateway`/`gateway-forwarder` الشغالتان فعلياً — بلا التأثير عليهما (تحقَّق `docker compose ps` بعدها: Up/healthy كما كانتا).

**سيناريو (ب) عُدِّل بموافقة صريحة من المالك**: بدل `docker kill -s KILL redis-durable` الفعلي (آمن في P0.1 لعدم وجود بيانات حقيقية، لكن `redis-durable` الآن يحمل WAL حقيقياً لجلسة واتساب متصلة/شبه متصلة، وقتله يسبب انقطاعاً فعلياً بلا داعٍ)، عميل `ioredis` حقيقي منفصل يُوجَّه لعنوان غير قابل للوصول لإحداث فشل اتصال حقيقي (لا محاكاة/mock)، فيُفعِّل نفس مسار الكود الفعلي (`appendEvent` يفشل ⇒ `spool()` ⇒ `drainSpool()` عبر عميل حقيقي شغّال) — يثبت نفس ضمان "لا فقد" دون مخاطرة على البيانات الحية.

**مخرجات التشغيل الحقيقي على الـVPS (لا محاكاة):**
```
✔ (أ) 1000x redelivery of the same provider_message_id against REAL redis-durable -> exactly one stream entry (1103ms)
✔ (ب) redis-durable unreachable during ingest -> spool captures it -> drainSpool (real client) delivers with no loss (167ms)
✔ (ج) a real Baileys-shaped location message reaches the REAL stream with its coordinates (31ms)
tests 3 / pass 3 / fail 0
```
(د) الـ14 اختباراً القديمة + كل الإضافات: **65/65 ناجح** (مجموعة `node --test` كاملة على المضيف، `webhook.test.js`/`contract.test.js` ضمنها).

**ملاحظة صادقة (H8):** أول محاولة لكتابة اختبار (أ) وقعت فعلياً في نفس خلل الترتيب المكتشَف في 2.4 (`waitForReady`) — عميل الاختبار نفسه استخدم أمراً قبل اكتمال الاتصال. اكتُشف وأُصلح بتشغيل السكربت فعلياً ضد Redis حقيقي (محلي) قبل تسليمه، لا افتراضاً.

**Commit: `1914b6f`.**

---

**الحالة: ✅ P0.2 مكتملة — معيار قبول P0.2 محقَّق بالكامل (اختبارات وحدة + (أ)/(ب)/(ج) الثلاثة تكاملاً حقيقياً ضد `redis-durable` فعلي + (د) 65/65 ناجح). اتصال واتساب حقيقي فعلي (P0.5/تشغيلي) خارج نطاق قبول P0.2 حسب النص الرسمي، ومؤجَّل — راجع §2.5.**



## P0.3 — تعامل الوسائط الآمن (G3، الكارثتان #16/#21)

> التاريخ: 2026-09-23. إغلاق فعلي بعد اكتشاف أن معيار القبول الرسمي (اختبارات MinIO حقيقية E1-E4) لم يُشغَّل قط سابقاً رغم وجود الكود؛ راجع `docs/P0_DEVIATIONS.md` D-16 وD-17 و`docs/P0_OPEN_QUESTIONS.md` OQ-7 للتفاصيل الكاملة.

### 3.1 معيار القبول الرسمي (§6 P0.3، بـMinIO حقيقية في `docker-compose.test.yml`) — محقَّق بالكامل

أُضيف `docker-compose.test.yml` (إضافي فقط، لا يمسّ المكدّس الحي إلا بـ`-f` صريح) بخدمتَي `minio`/`minio-init` حقيقيتين، و`gateway/src/__tests__/media_e2e_minio.test.js` (مفعَّل بـ`RUN_MEDIA_E2E_TESTS=1`، لا `.skip`) يُشغَّل عبر `docker compose run --rm gateway` ضد `minio` وَ`redis-durable` الحقيقيَين معاً.

**مخرجات التشغيل الحقيقي على الـVPS (لا محاكاة):**
```
[E1] baseline RSS=104.9MB, peak RSS=137.9MB, growth=33.0MB, samples(MB)=[121.6, 121.8, 121.8, 137.9, 136.9, 132.0, 137.3]
✔ (P0.3-E1) 50MB streamed download+upload: RSS growth stays under 100MB above baseline (real MinIO + real redis-durable) (11439.665029ms)
[E2] 10x50MB concurrent: peak RSS=199.7MB, max media_inflight observed=3
✔ (P0.3-E2) 10 concurrent 50MB uploads: absolute peak RSS < 600MB and media_inflight never exceeds 3 (45739.264466ms)
[E3] orphaned multipart uploads for p03e2e-e3-.../MSG-... after kill -9: 0
✔ (P0.3-E3) kill -9 mid-upload of 50MB: after restart, exactly one complete object exists and no multipart upload is left orphaned (12983.052211ms)
✔ (P0.3-E4a) over-cap file is rejected before any download call is made (zero bytes over the network) (3.151831ms)
✔ (P0.3-E4b) image-extension file with non-image magic bytes is rejected as rejected_type, not uploaded (26.87695ms)
tests 5 / pass 5 / fail 0
```

**التغطية مقابل النص الرسمي:**
- **(1) 50MB streamed:** نمو RSS = **33.0MB** (الحد: <100MB). ✔
- **(2) 10×50MB متزامنة:** ذروة RSS مطلقة = **199.7MB** (الحد: <600MB)، أقصى `media_inflight` مرصود = **3** (الحد: ≤3)، وعاد لصفر بعد الاكتمال. ✔
- **(3) `kill -9` أثناء التنزيل + إعادة تشغيل:** **صفر** رفعات multipart يتيمة على MinIO حقيقي (`ListMultipartUploadsCommand`)، وإعادة المحاولة اكتملت بالحجم الكامل الصحيح. ✔
- **(4) رفض الحجم الزائد بصفر بايت شبكة + رفض النوع المموَّه (magic-byte):** كلاهما محقَّق — `downloadFnCalled=false` للحالة الأولى (إثبات عدم استدعاء الشبكة إطلاقاً)، و`status: 'rejected_type'` لملف بترويسة ELF حقيقية متنكّرة بامتداد/mimetype صورة. ✔

### 3.2 عيب حقيقي اكتُشف وأُصلح أثناء بناء اختبار القبول (لا في الاختبار نفسه)

أثناء بناء اختبار E1، لاحظتُ أن نمو RSS استقرّ عند حدّ 100MB الحرج بالضبط (وليس أقل بهامش مريح) — بحث بدل تجاهل الرقم المريب كشف عيباً حقيقياً: `gateway/src/media.js` كان يكتب إلى `PassThrough` (بين تدفق التنزيل ورفع S3 متعدد الأجزاء) دون احترام backpressure — `stream.write()` تُعيد `false` عند امتلاء المخزن الداخلي، وتجاهل هذه القيمة يعني نمو المخزن بلا حدّ إذا كان الرفع أبطأ من التنزيل (بالضبط فئة الفشل التي وُجدت P0.3 لمنعها — الكارثتان #16/#21).

**الإصلاح:** التحقق من قيمة الإرجاع وانتظار حدث `'drain'` (`node:events`) قبل متابعة الكتابة، فيتباطأ حلقة التنزيل نفسها لسرعة الرفع الحقيقية بدل تكديس الذاكرة. **الأثر المقاس (قبل/بعد، عدة تشغيلات):** نمو RSS لملف 50MB من ~100.0MB الحدّي إلى **~33-54MB** مستقر؛ ذروة 10 ملفات متزامنة من ~441-533MB إلى **~199-275MB**. التفاصيل الكاملة في `docs/P0_FINDINGS.md` F5.

### 3.3 الانحدار — لا كسر لأي شيء قائم

```
✔ (أ) 1000x redelivery of the same provider_message_id against REAL redis-durable -> exactly one stream entry
✔ (ب) redis-durable unreachable during ingest -> spool captures it -> drainSpool (real client) delivers with no loss
✔ (ج) a real Baileys-shaped location message reaches the REAL stream with its coordinates
✔ (P0.3) 200x concurrent media-quota reservations against REAL redis-durable respect a 20-file cap exactly, atomically
tests 4 / pass 4 / fail 0
```
ومجموعة `node --test` الكاملة (السريعة/الحتمية، بلا MinIO): **79/79 ناجح**. hunt_gate بعد الكتابة: 3 مخالفات — كلها في سكربتات batch مؤقتة في جذر المستودع (`batch_p02_real_redis_integration.sh`, `batch_p03_close_minio_e2e.sh`, `batch_p03_hunt_gate_fix.sh`)، **لا شيء في `gateway/src`**. المكدّس الحي (`gateway`/`gateway-forwarder`/`postgres`/`pgbouncer`/`redis-*`) ظلّ يعمل بلا انقطاع طوال التشغيل (تحقَّق `docker compose ps` بعده).

### 3.4 الانحرافات المسجَّلة (تفاصيلها الكاملة في `docs/P0_DEVIATIONS.md`)

- **D-15:** مقاييس P0.3 الأربعة (`media_bytes_total`/`media_inflight`/`media_failures_total{reason}`/`media_duration_seconds`) مُنفَّذة كمحاسَبة داخلية حقيقية الآن (`getMediaMetrics()`)، مُستخدَمة فعلياً في اختبار القبول E2 لإثبات `media_inflight ≤ 3`. تعريض `/metrics` HTTP العام مؤجَّل إلى P0.6 (نص المرجع الرسمي يضعه هناك صراحة).
- **D-16:** اختبارات القبول تستدعي `downloadAndUploadMedia` مباشرة بحمولات تدفق اصطناعية حقيقية بدل عبر `FakeWaDriver` — لأن `FakeWaDriver`/بنية `driver/` المنصوص عليها في P0.2 **غير موجودة فعلياً** في الكود (تعارض حقيقي مع `P0_PROGRESS.md` §2.5 القديم الذي افترض إنجازها؛ مسجَّل كسؤال مفتوح OQ-7).
- **D-17:** خدمتا `minio`/`minio-init` الاختباريتان تسحبان من `quay.io` بدل Docker Hub (الذي أزال مستودعَي `minio/minio`/`minio/mc` بالكامل بتاريخ 2026-09-12) — مقصور على الخدمتين الاختباريتين المؤقتتين، لا أثر على المكدّس الحي.

### 3.5 سؤال مفتوح لم يُحسَم (OQ-7)

عدم وجود `FakeWaDriver` فعلياً في الكود (رغم ذكرها في `P0_PROGRESS.md` §2.5 القديم) يحتاج قراراً من المالك قبل P0.5، التي تفترض وجودها لاختبارات الحاملتين المزدوجتين وأكواد قطع الاتصال. التفاصيل الكاملة في `docs/P0_OPEN_QUESTIONS.md` OQ-7.

---

**الحالة: ✅ P0.3 مكتملة — معيار قبول P0.3 محقَّق بالكامل (اختبارات E1-E4 الأربعة ضد MinIO و`redis-durable` حقيقيَّين + انحدار 4/4 + المجموعة الكاملة 79/79). عيب backpressure حقيقي اكتُشف وأُصلح أثناء بناء الاختبار، بأثر مقاس قبل/بعد. لم يُنجَز بعد: commit التغييرات (media.js، ملفا الاختبار الجديدان، docker-compose.test.yml، توثيقات D-15/D-16/D-17/OQ-7/F5) — خطوة منفصلة تالية بعد مراجعتك.**

---

## P0.6 — الجاهزية التشغيلية ومفتاح الطوارئ

### الدفعة أ (مُلتزَمة: `be5debe`)

`GET /readyz`، `GET /metrics` محمية بـBearer، رفض الإقلاع بسرّ `METRICS_TOKEN` فارغ (H5)، تسلسل الإيقاف الرشيق الكامل، تسجيل H12 مع إخفاء الهواتف، وثلاثة مقاييس دورة حياة الجلسة مربوطة بنقاط انتقال حقيقية. 106/106 على الـVPS بعد حل ثلاث مشاكل حقيقية في مُشغّل الاختبارات (F10).

### الدفعة ب — مفتاح الطوارئ على جانب البوابة

`gateway/src/killswitch.js`: اتصالا `redis-cache` (أوامر + مشترك pub/sub منفصلان)، ذاكرة نطاقات داخلية، حساب «الأشد فوزاً» عبر (global × tenant × channel) × (القدرة، `*`) — مطابق حرفياً لدلالة `app.effective_switch` في `docs/reference/schema.sql`، مزامنة كاملة كل 30 ثانية، وقاعدة التقادم (`KS_STALE_MAX_S=120`) التي تُغلق marketing/broadcast فقط. الحجب: `423` بجسم `{error:'blocked_by_switch', capability, state, scope}` عند الطلب، و`failed(error_class='blocked')` للعناصر التي كانت في الطابور.

**11/11 اختباراً حقيقياً** ضد `redis-cache` حقيقي: مصفوفة النطاقات/الحالات، القدرة `*`، تعيين `kind`←القدرات، زمن الانتشار من `PUBLISH` إلى الحجب **مُقاس فعلياً < 1 ثانية**، سلوك التقادم بزمن حقيقي، وقبول صيغتي النطاق (`channel` و`channel_account`).

**أربعة عيوب حقيقية اكتُشفت وأُصلحت أثناء البناء، كلها موثَّقة بأرقام مقيسة:**
- **F11** — أوامر Redis قبل انتظار `'ready'` (`enableOfflineQueue:false`).
- **F13** — مفتاح القتل كان **تبعية إقلاع**: جاهزية البوابة 1057ms ← 10698ms، والإيقاف 13ms ← 8475ms عندما يكون `redis-cache` مفتوحاً على TCP بلا إجابة. بعد الإصلاح: 2157ms و1014ms، بسقوف مكتوبة (H4). الإيقاف كان سيضرب `SHUTDOWN_TIMEOUT_MS=5000` ويخرج برمز 1.
- **F16** — `METRICS_TOKEN` مفقود من `.env` و`docker-compose.yml`: أول نشر بعد الدفعة أ كان سيمنع البوابة من الإقلاع كلياً.
- **F14** — فشل عابر في `outbound_chaos.test.js` (تنافس معالج، غير قابل للتكرار محلياً) كشف عيبين في أداة الاختبار: عمى تشخيصي وتعليق عند الفشل — أُصلحا (الفشل الآن يحمل مخرجات الطفل ويُبلِغ في ~6 ثوانٍ بدل التعليق).

**وتصحيح استنتاج خاطئ سجّلتُه أنا أولاً:** ادّعيتُ أن `docker-compose.yml` لا يملك خدمة `redis-cache`، استناداً إلى `ECONNREFUSED` وهي رسالة لا تحمل تلك المعلومة. الواقع: الخدمة موجودة وصحّية منذ أيام، والناقص كان المتغيّر وحده. التصحيح الكامل في D-26 وF15.

### ما تبقّى من P0.6

المراقبة: ربط عائلات المقاييس المتبقّية (`ingest_*`، `out_*`، `forwarder_*`، `dlq_length`، `stream_*`، `media_*`)، نقطة `/metrics` لحاوية الـforwarder، ثم `ops/prometheus/prometheus.yml` و`alerts.yml` بالقواعد الثماني، والتحقق بـ`promtool check rules`. سبب عدم تسليمها مع الدفعة ب مسجَّل في D-27.
