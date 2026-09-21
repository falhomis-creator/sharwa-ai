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


