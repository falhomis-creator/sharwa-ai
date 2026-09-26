#!/usr/bin/env bash
set -euo pipefail
cd /home/sharwa/sharwa_ai

echo "=== 1) append P0.2 closure section to docs/P0_PROGRESS.md ==="
cat >> docs/P0_PROGRESS.md <<'ADDEOF'

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

ADDEOF
echo "P0_PROGRESS.md updated OK"

echo "=== 2) overwrite docs/PHASE_GATE.md (P0.2 -> DONE) ==="
cat > docs/PHASE_GATE.md <<'GATEEOF'
P0: IN PROGRESS — P0.0 done, P0.1 verified on real Docker at C1. P0.2 DONE (acceptance criteria a/b/c/d verified on real Docker + real redis-durable, see docs/P0_PROGRESS.md P0.2 §2.6; a real WhatsApp phone connection is explicitly out of P0.2's official acceptance scope, deferred as a separate operational smoke test). P0.3–P0.8 remaining.
P1: LOCKED
P2: LOCKED
P3: LOCKED
P4: LOCKED
P5: LOCKED

GATEEOF
echo "PHASE_GATE.md updated OK"

echo "=== 3) review before commit ==="
git status
git diff --stat
