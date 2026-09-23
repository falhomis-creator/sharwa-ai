# P0.2 Dependencies (H11)

> H11 — every new dependency is justified, version-pinned, and audited before it
> reaches the gateway. This is the running ledger for P0.2.

## `ioredis@^5.11.1` — NEW

- **Why**: the gateway's durable tier is Redis Streams. The ingest path needs
  `XADD`, `XGROUP CREATE`, `XACK`, `XAUTOCLAIM`, `XTRIM`, and an atomic
  dedupe+XADD fused into one fsync'd write via Lua `EVAL` (owner decision #1).
  `ioredis` exposes all of these with a Promise API, per-command retry caps, and
  an offline queue that can be disabled — the exact "fail fast and spool" shape
  the ingest contract requires (see `src/redis.js`).
- **Why not `redis` (node-redis)**: it is not installed in the lockfile, and its
  v4/v5 API split means choosing it would add a second surface to review. One
  client library, one place to reason about error classification (H3).
- **Pin**: `^5.11.1` (installed `5.11.1`). `engines.node >= 22` — compatible.
- **Audit**: `npm audit` → `found 0 vulnerabilities` (2026-09-22).

## Pre-existing (unchanged by P0.2)

| Package | Version | Role |
| --- | --- | --- |
| `@whiskeysockets/baileys` | `6.7.24` (pinned) | WhatsApp protocol |
| `express` | `^4.21.2` | HTTP control plane |
| `@aws-sdk/client-s3` | `^3.1136.0` | MinIO object store (media; P0.3) |
| `qrcode` | `^1.5.4` | session QR rendering |

No dependency was upgraded or removed in P0.2; the only change is the addition of
`ioredis` to `gateway/package.json` and `gateway/package-lock.json`.

### `@aws-sdk/lib-storage` (P0.3)

- **الاسم/الإصدار:** `@aws-sdk/lib-storage@^3.1136.0` (مطابق لإصدار `@aws-sdk/client-s3` الحالي في المشروع لتفادي تعارض إصدارات AWS SDK v3).
- **لماذا:** توفير رفع متعدد الأجزاء (multipart) حقيقي بالبث (`Upload` class) بدلاً من `PutObjectCommand` بحمولة واحدة كاملة في الذاكرة — مطلب P0.3 الرسمي: عدم تحميل الملف كاملاً في الذاكرة، ورفع بـ`partSize` 5MB و`queueSize` 2.
- **لماذا لا نكتبها يدوياً:** تطبيق multipart upload يدوياً (بدء/رفع أجزاء/إكمال/إجهاض عبر `S3Client` الخام) عرضة لأخطاء دقيقة في إدارة الحالة والتزامن؛ `@aws-sdk/lib-storage` هي الحزمة الرسمية من AWS نفسها لهذا الغرض تحديداً، ومتوافقة مع MinIO (S3-compatible API).
- **الترخيص:** Apache-2.0 (متوافق مع بقية AWS SDK v3 المستخدم أصلاً في المشروع).
- **التدقيق:** حزمة AWS رسمية، صيانة نشطة، لا تبعيات ثالثة مشبوهة خارج نطاق AWS SDK v3 نفسه.

### `file-type` (P0.3)

- **الاسم/الإصدار:** `file-type@22.1.1`.
- **لماذا:** التحقق من نوع المحتوى عبر البايتات السحرية (magic bytes) على أول ~4KB من الملف المُنزَّل، لمطابقته بنوع الوسائط المُعلَن من Baileys — مطلب P0.3 الرسمي: رفض أي عدم تطابق (`media.status='rejected_type'`) دون رفعه.
- **لماذا لا نكتبها يدوياً:** فحص توقيعات البايتات لعشرات صيغ الوسائط (JPEG/PNG/WEBP/MP4/OGG/PDF/...) يدوياً عرضة للأخطاء والفجوات (false negatives)؛ `file-type` مكتبة مرجعية معروفة لهذا الغرض تحديداً بالضبط، وتتحقق من التوقيعات الثنائية الحقيقية لا من الامتداد أو الـ`Content-Type` المُعلَن.
- **الترخيص:** MIT.
- **التدقيق:** `npm view file-type version license` أكَّد التوفر والترخيص وقت الكتابة؛ حزمة صغيرة الاعتماديات (pure-JS signature matching)، صيانة نشطة.

### `@aws-sdk/lib-storage` (P0.3)

- **الاسم/الإصدار:** `@aws-sdk/lib-storage@^3.1136.0` (مطابق لإصدار `@aws-sdk/client-s3` الحالي في المشروع لتفادي تعارض إصدارات AWS SDK v3).
- **لماذا:** توفير رفع متعدد الأجزاء (multipart) حقيقي بالبث (`Upload` class) بدلاً من `PutObjectCommand` بحمولة واحدة كاملة في الذاكرة — مطلب P0.3 الرسمي: عدم تحميل الملف كاملاً في الذاكرة، ورفع بـ`partSize` 5MB و`queueSize` 2.
- **لماذا لا نكتبها يدوياً:** تطبيق multipart upload يدوياً (بدء/رفع أجزاء/إكمال/إجهاض عبر `S3Client` الخام) عرضة لأخطاء دقيقة في إدارة الحالة والتزامن؛ `@aws-sdk/lib-storage` هي الحزمة الرسمية من AWS نفسها لهذا الغرض تحديداً، ومتوافقة مع MinIO (S3-compatible API).
- **الترخيص:** Apache-2.0 (متوافق مع بقية AWS SDK v3 المستخدم أصلاً في المشروع).
- **التدقيق:** حزمة AWS رسمية، صيانة نشطة، لا تبعيات ثالثة مشبوهة خارج نطاق AWS SDK v3 نفسه.

### `file-type` (P0.3)

- **الاسم/الإصدار:** `file-type@22.1.1`.
- **لماذا:** التحقق من نوع المحتوى عبر البايتات السحرية (magic bytes) على أول ~4KB من الملف المُنزَّل، لمطابقته بنوع الوسائط المُعلَن من Baileys — مطلب P0.3 الرسمي: رفض أي عدم تطابق (`media.status='rejected_type'`) دون رفعه.
- **لماذا لا نكتبها يدوياً:** فحص توقيعات البايتات لعشرات صيغ الوسائط (JPEG/PNG/WEBP/MP4/OGG/PDF/...) يدوياً عرضة للأخطاء والفجوات (false negatives)؛ `file-type` مكتبة مرجعية معروفة لهذا الغرض تحديداً بالضبط، وتتحقق من التوقيعات الثنائية الحقيقية لا من الامتداد أو الـ`Content-Type` المُعلَن.
- **الترخيص:** MIT.
- **التدقيق:** `npm view file-type version license` أكَّد التوفر والترخيص وقت الكتابة؛ حزمة صغيرة الاعتماديات (pure-JS signature matching)، صيانة نشطة.
