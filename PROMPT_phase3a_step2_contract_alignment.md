# شروة AI (`C:\sharwa_ai\gateway`) — الخطوة 2: مطابقة العقد الفعلي مع Django

## الدستور (كما في الخطوة 1 — يُطبَّق حرفياً)

1. عزل تام بين التجار — `session_id` من Django دوماً، لا يُولَّد ولا يُخمَّن هنا.
2. لا قاعدة بيانات في هذا المشروع.
3. غير متزامن أولاً — `async/await` لكل عملية شبكية.
4. طابور إرسال FIFO لكل جلسة (لا تغيير عليه في هذه الخطوة).
5. **اقرأ الكود الفعلي الحالي أولاً** (`index.js`, `sessions.js`, `webhook.js`, واختباراتهما) قبل أي تعديل. التقرير النهائي يتضمن نص الكود الفعلي بعد التعديل ومخرجات تشغيل حقيقية كاملة (`npm test`).

## لماذا هذه الخطوة عاجلة — اكتشاف حرج

قارنّا الكود الفعلي لهذه البوابة مباشرة مع الكود الفعلي في `sharwa_saas` (`products/services/sharwa_ai_client.py` و`tenants/webhooks_ai.py` — كلاهما جاهز، مختبَر، ويعمل بالفعل من جانب Django منذ عدة مراحل). **العقد بين الطرفين مكسور في أربع نقاط محددة، بمعنى أن التكامل الحقيقي بينهما لن يعمل إطلاقاً حالياً رغم نجاح كل اختبارات Django** (لأنها اعتمدت على Mock لهذه الطبقة، لا استدعاء HTTP حقيقي). هذه الخطوة تصحح الأربع نقاط بدقة جراحية — **بلا أي تغيير آخر خارج ما هو مذكور هنا**.

**Django هو الطرف الثابت في كل نقطة أدناه (مُتحقَّق منه ومُختبَر فعلياً عبر 9 مراحل) — هذه البوابة هي التي يجب أن تتكيّف معه، لا العكس.**

## نطاق هذه الخطوة (`gateway/` فقط)

### ١) اسم ترويسة المصادقة (Django ← البوابة)

**المشكلة:** `src/index.js` السطر 30 يقرأ `req.get('X-Gateway-Key')`، لكن `sharwa_ai_client.py` (Django) يرسل فعلياً `headers = {'X-API-Key': api_key, ...}`. **كل استدعاء من Django (`POST /sessions`, `GET /sessions/:id/status`, `POST /sessions/:id/send`) يُرفَض بـ401 دائماً حالياً.**

**التصحيح:** غيّر السطر 30 في `index.js` من:
```js
const provided = req.get('X-Gateway-Key');
```
إلى:
```js
const provided = req.get('X-API-Key');
```
(اسم الدالة `requireGatewayKey` واسم متغيّر البيئة `SHARWA_AI_GATEWAY_API_KEY` يبقيان كما هما — لا علاقة لهما بالمشكلة، المشكلة فقط في اسم الترويسة HTTP المقروءة). حدِّث أي ذكر لـ`X-Gateway-Key` في `README.md` أيضاً ليعكس `X-API-Key`.

### ٢) مسار الـwebhooks الصادرة (البوابة → Django)

**المشكلة:** `src/webhook.js` السطرين 113 و136 يرسلان لـ`/api/webhooks/sharwa-ai/session-status` و`/api/webhooks/sharwa-ai/inbound-message`، لكن `config/urls_public.py` في Django يسجّل المسارين فعلياً على `/webhooks/sharwa-ai/session-status/` و`/webhooks/sharwa-ai/inbound-message/` (**بلا** `/api/`، **مع** شرطة مائلة ختامية). **لا يوجد مسار مطابق على Django لما ترسله البوابة حالياً — كل حدث يُرجع 404 فعلياً، ولا رسالة عميل واحدة أو تحديث حالة جلسة واحد كان سيصل لـDjango في الإنتاج.**

**التصحيح في `webhook.js`:**
```js
// كان:
return post('/api/webhooks/sharwa-ai/session-status', { ... });
// يصبح:
return post('/webhooks/sharwa-ai/session-status/', { ... });

// كان:
return post('/api/webhooks/sharwa-ai/inbound-message', { ... });
// يصبح:
return post('/webhooks/sharwa-ai/inbound-message/', { ... });
```
حدِّث أيضاً التعليق التوضيحي أعلى `post()` (السطر 60) الذي يذكر المسار القديم كمثال، وحدِّث `README.md` (قسم "النداءات الصادرة").

### ٣) شكل بيانات QR والحالة (البوابة → Django، عبر `GET/POST` المباشرة لا الـwebhooks)

**المشكلة (مُركَّبة من ثلاث فجوات مجتمعة):**
- Django (`create_session` في `sharwa_ai_client.py`) يتوقع رد `POST /sessions` يحوي `qr_image_base64` مباشرة ضمن نفس الرد — البوابة حالياً تُرجع `{session_id}` فقط (`index.js` السطر 52) بلا أي حالة أو QR.
- Django (`get_session_status`) يتوقع رد `GET /sessions/<id>/status` بالشكل `{status, qr_image_base64, connected_phone_number}` — البوابة حالياً تُرجع `{session_id, status, phone_number}` (`getSessionStatus` في `sessions.js`، السطور 343-351): **لا يوجد `qr_image_base64` إطلاقاً، واسم الحقل `phone_number` لا `connected_phone_number`.**
- رمز QR الخام من Baileys (`update.qr`، نص وليس صورة) **لا يُحوَّل أبداً إلى صورة PNG بترميز base64** — لا توجد حزمة `qrcode` في `package.json` أصلاً. حتى لو صُحِّحت أسماء الحقول، لا توجد صورة فعلية لإرسالها. مسار `/sessions/:id/qr` المنفصل حالياً غير مستخدَم من جانب Django إطلاقاً (`sharwa_ai_client.py` لا يستدعيه) — عملياً كود ميت.

**التصحيح:**

أ) أضف `qrcode` كتبعية جديدة في `package.json` (`npm install qrcode`).

ب) في `sessions.js`، أضف دالة مساعدة مُصدَّرة (قابلة للاختبار بمعزل):
```js
import QRCode from 'qrcode';

export async function toQrImageBase64(rawQr) {
  if (!rawQr) return null;
  // PNG بترميز base64 بلا بادئة "data:image/png;base64," — الواجهة (متصفح
  // التاجر عبر sharwa_ai_connect.html) تضيف البادئة بنفسها إن غابت.
  const dataUrl = await QRCode.toDataURL(rawQr, { type: 'image/png' });
  return dataUrl.replace(/^data:image\/png;base64,/, '');
}
```

ج) عدّل `getSessionStatus(sessionId)` لتصبح **غير متزامنة** (لأنها تحتاج تحويل QR) وتُرجع الشكل الذي يتوقعه Django بالضبط:
```js
export async function getSessionStatus(sessionId) {
  const session = sessions.get(sessionId);
  if (!session) return null;
  return {
    status: session.status,
    qr_image_base64: await toQrImageBase64(session.lastQr),
    connected_phone_number: session.phoneNumber,
  };
}
```
(لاحظ حذف `session_id` من الجسم — Django لا يقرأه من هذا الرد أصلاً، فلا داعٍ لإرساله؛ إبقاؤه لا يضر لكن الأسماء المطلوبة فعلياً هي الثلاثة أعلاه فقط. إن فضّلت إبقاءه للتوثيق الذاتي فلا مانع، المهم وجود الثلاثة الإلزامية بأسمائها الصحيحة حرفياً).

د) عدِّل `index.js`:
- `app.get('/sessions/:id/status', ...)` يصبح `async` ويُنادي `await getSessionStatus(...)`.
- `app.post('/sessions', ...)` بعد نجاح `createSession(session_id)`، أعد **حالة الجلسة الكاملة** لا `{session_id}` فقط:
```js
app.post('/sessions', async (req, res) => {
  const { session_id } = req.body ?? {};
  if (!session_id || typeof session_id !== 'string') {
    return res.status(400).json({ error: 'session_id (string) is required' });
  }
  try {
    await createSession(session_id);
    const status = await getSessionStatus(session_id);
    return res.status(201).json(status);
  } catch (err) {
    console.error(`[index] failed to create session ${session_id}: ${err.message}`);
    return res.status(500).json({ error: 'failed to create session' });
  }
});
```

هـ) مسار `/sessions/:id/qr` المنفصل: اتركه موجوداً (لا ضرر، قد يفيد لاحقاً للتصحيح اليدوي) لكن عدِّله ليستخدم نفس `toQrImageBase64` لو أردت اتساقاً، أو وثِّق في `README.md` أنه **غير مستخدَم من جانب Django حالياً** (مصدر الحقيقة الوحيد لعقد Django هو `/status`). لا تحذفه ولا تُضِف له استخداماً جديداً — خارج نطاق هذه الخطوة.

و) `postSessionStatus` في `webhook.js` لا تزال ترسل `phone_number` (لا `connected_phone_number`) في جسم الـwebhook الصادر — **هذا مقصود ولا يحتاج تعديلاً**: تحقّقنا أن `tenants/webhooks_ai.py::sharwa_ai_session_status_webhook` من جانب Django لا يقرأ `phone_number` من جسم هذا الـwebhook إطلاقاً أصلاً (رقم الهاتف المتصل يصل لـDjango حصراً عبر `GET /status` أعلاه، لا عبر الـwebhook). لا تُغيّر هذا الحقل، فقط وثِّق هذه الملاحظة في تعليق أعلى `postSessionStatus` منعاً لالتباس مستقبلي.

## تحديث الاختبارات القائمة (لا تتجاهلها — بعضها يؤكد الخطأ حالياً)

`src/__tests__/webhook.test.js` السطر 185 يؤكد فعلياً المسار **الخاطئ**:
```js
assert.equal(calls[0].url, `${base}/api/webhooks/sharwa-ai/inbound-message`);
```
صحِّحه إلى:
```js
assert.equal(calls[0].url, `${base}/webhooks/sharwa-ai/inbound-message/`);
```

## الاختبارات الجديدة المطلوبة

أضفها في `src/__tests__/webhook.test.js` و/أو ملف جديد `src/__tests__/contract.test.js`:

1. **ترويسة المصادقة**: طلب لأي مسار تحت `/sessions` بترويسة `X-API-Key` صحيحة يمر (لا 401)؛ نفس الطلب بترويسة `X-Gateway-Key` (الاسم القديم الخاطئ) بدل `X-API-Key` يُرفَض بـ401 — تأكيد صريح أن التصحيح فعلي لا افتراضي.
2. **مسارا الـwebhook الصادرين**: `postSessionStatus`/`postInboundMessage` (بـMock لـ`fetch` كما في الاختبار القائم) تستدعيان بالضبط `/webhooks/sharwa-ai/session-status/` و`/webhooks/sharwa-ai/inbound-message/` (لا `/api/` ومع الشرطة الختامية).
3. **`toQrImageBase64`**: مُدخَل نصي غير فارغ يُنتج نصاً base64 صالحاً بلا بادئة `data:`؛ مُدخَل `null`/فارغ يُنتج `null`.
4. **`getSessionStatus` شكل الرد**: لجلسة بها `lastQr` مضبوط، الرد يحوي المفاتيح الثلاثة بالضبط (`status`, `qr_image_base64`, `connected_phone_number`) بأسمائها الحرفية الصحيحة.
5. **`POST /sessions` عبر Express (integration خفيف)**: باستخدام Mock لـ`createSession` (لا اتصال Baileys حقيقي في الاختبار)، تأكيد أن رد 201 يحوي `qr_image_base64` (وليس فقط `session_id`).

## معايير القبول

- الأربع نقاط أعلاه مُصحَّحة بالضبط كما وردت.
- `npm test` يُظهر **كل** الاختبارات القديمة (webhook.test.js الأربعة + media.test.js الخمسة، بعد تحديث السطر 185 كما ذُكر) **و**الاختبارات الجديدة الخمسة تمر معاً — مخرجات حقيقية كاملة في التقرير، لا وصف.
- `npm install` يضيف `qrcode` بنجاح دون كسر أي تبعية قائمة.
- لا تعديل على أي شيء خارج `gateway/` (لا لمس لـ`sharwa_saas` إطلاقاً — العقد الذي يجب المطابقة معه ثابت وموثَّق أعلاه حرفياً، لا حاجة لفتح ذلك المشروع).
- لا تغيير على منطق الفلترة الواردة، طابور الإرسال، أو رفع الوسائط لـMinIO — هذه الخطوة تصحيح عقد اتصال فقط.

## ممنوع في هذه الخطوة

- لا تغيير على `SHARWA_AI_GATEWAY_WEBHOOK_SECRET` أو آلية التوقيع HMAC نفسها (صحيحة ومطابقة فعلاً لجانب Django — تحقّقنا من ذلك).
- لا حذف مسار `/sessions/:id/qr` (اتركه كما هو، معدَّلاً فقط إن اخترت اتساقاً اختيارياً).
- لا إضافة أي منطق تاجر/قاعدة بيانات لهذا المشروع.
- لا لمس أي ملف في `C:\sharwa_saas`.

عند الانتهاء، لخّص بالضبط ماذا عدَّلت في كل ملف، والناتج الحقيقي الكامل لـ`npm test`، وانتظر التأكيد الصريح قبل أي خطوة تالية (مثل ربط QR حقيقي فعلي عبر التكامل الحي بين المشروعين).
