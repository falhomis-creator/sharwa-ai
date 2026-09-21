# Sharwa AI — WhatsApp Gateway (Baileys)

بوابة واتساب مستقلة تماماً لخدمة **شروة** (`sharwa_ai`). لا علاقة كودية أو سرّية بأي
مشروع آخر (لا `sharwa_saas` ولا `sharwaconnect`).

> هذه هي **المرحلة 3-أ / الخطوة 1**: تأسيس البوابة (جلسات معزولة، طابور إرسال FIFO،
> نداءات موقَّعة إلى Django). لا يشمل هذا الجزء ربط QR حقيقي بمكتبة Baileys بعد، بل
> البنية الأساسية الكاملة الجاهزة لذلك.

---

## البنية

```
gateway/
  src/
    index.js              # Express: واجهة API الداخلية (Django -> البوابة)
    sessions.js           # إدارة جلسات Baileys + طابور الإرسال + فلترة الوارد
    webhook.js            # نداءات موقَّعة صادرة (البوابة -> Django)
    __tests__/
      webhook.test.js     # الاختبارات الثلاثة المطلوبة
  package.json
  Dockerfile
  .env.example
  .gitignore
  .dockerignore
README.md
```

## المكتبات المختارة (موثَّقة)

| المكتبة | الاختيار | السبب |
|---|---|---|
| Baileys | [`@whiskeysockets/baileys`](https://github.com/WhiskeySockets/Baileys) | المكتبة القياسية المعروفة للاتصال بـ WhatsApp Web (جلسة واحدة لكل تاجر) |
| خادم HTTP | `express` v4 | مطلوب صراحة في المواصفة (`index.js` = Express app) |
| أداة الاختبار | `node:test` (المدمجة في Node 22+) | لا حاجة لتبعية خارجية، تدعم mocks/asserts وتشغيلها `npm test` |
| إدارة البيئة | `--env-file-if-exists=.env` (مدمجة في Node) | لا حاجة لـ `dotenv` |

## المتغيّرات البيئية

انسخ `.env.example` إلى `.env` واملأ القيم:

| المتغير | الوصف |
|---|---|
| `PORT` | منفذ الاستماع (افتراضي `4001` — مختلف عمداً عن منفذ SharwaConnect `4000`) |
| `DJANGO_BASE_URL` | عنوان Django الداخلي (لا قيمة افتراضية عامة — إن غاب تسجّل البوابة خطأً واضحاً وتتوقف عن الإرسال بدل فشل صامت) |
| `SHARWA_AI_GATEWAY_API_KEY` | مفتاح حماية واجهة البوابة الداخلية (ترويسة `X-Gateway-Key`) |
| `SHARWA_AI_GATEWAY_WEBHOOK_SECRET` | سرّ توقيع النداءات الصادرة إلى Django (مستقل تماماً عن أي سرّ آخر) |
| `AUTH_SESSIONS_DIR` | (اختياري) مسار مجلد الجلسات، افتراضي `auth_sessions/` |

## التشغيل محلياً

```bash
cd gateway
npm install
cp .env.example .env   # ثم عبّئ القيم
npm start              # أو: node --env-file-if-exists=.env src/index.js
```

## الاختبارات

```bash
cd gateway
npm test   # node --test src/__tests__/webhook.test.js
```

الاختبارات الثلاثة المطلوبة:

1. **`sign()` حتمية التوقيع**: نفس المدخلات => نفس التوقيع دائماً، وتغيير حرف واحد
   في الجسم يغيّر التوقيع بالكامل (ويتطابق مع `HMAC-SHA256(timestamp.body)`).
2. **فلترة الرسائل الواردة**: رسالة `fromMe: true` أو مجموعة (`@g.us`) أو بث
   (`@broadcast`) أو بلا نص فعلي **لا** تستدعي `postInboundMessage` إطلاقاً (Mock).
3. **طابور الإرسال**: رسالتان متتاليتان لنفس الجلسة لا تُرسَلان في نفس اللحظة —
   يتحقق الاختبار من عدم وجود إرسالين متزامنين (`maxActive === 1`) ومن فجوة Jitter.

## واجهة API الداخلية (Django -> البوابة)

كل المسارات تحت `/sessions` محمية بترويسة `X-Gateway-Key` (مقارنة زمن ثابت
`crypto.timingSafeEqual`). `GET /healthz` غير محمي.

| المسار | الوصف |
|---|---|
| `GET /healthz` | فحص حيوية (لا حماية) |
| `POST /sessions` `{session_id}` | إنشاء جلسة جديدة |
| `GET /sessions/:id/qr` | آخر QR معروف (أو `null`) |
| `GET /sessions/:id/status` | الحالة الحالية |
| `POST /sessions/:id/send` `{to, text}` | إدخال في الطابور، يعيد `message_id` أو `503` إن امتلأ |
| `POST /sessions/:id/logout` | تسجيل خروج فعلي + مسح بيانات الجلسة من القرص |

## النداءات الصادرة (البوابة -> Django)

- `POST /api/webhooks/sharwa-ai/session-status` — `{session_id, status, phone_number, detail}`
- `POST /api/webhooks/sharwa-ai/inbound-message` — `{session_id, from, text, message_id}`

التوقيع: `HMAC-SHA256("{timestamp}." + rawBody)` بمفتاح
`SHARWA_AI_GATEWAY_WEBHOOK_SECRET`، عبر الترويستين `X-Timestamp` و`X-Signature`.
إعادة المحاولة بتأخير تصاعدي حتى **5 محاولات**، مع تسجيل واضح عند الاستسلام النهائي
(لا يُسقَط أي حدث بصمت).

## الالتزام بالدستور

1. **عزل التجار**: `session_id` واحد لكل تاجر، ولا تولّد البوابة `session_id` أبداً —
   يأتي دائماً من Django، فلا يمكن التنبؤ به خارجياً.
2. **صفر N+1**: لا قاعدة بيانات في هذا المشروع.
3. **غير متزامن أولاً**: كل الشبكة عبر `async/await` (لا حجب متزامن).
4. **أقفال صارمة**: طابور إرسال FIFO لكل جلسة مع Jitter 2000-3000ms — لا رسالتان
   لنفس الجلسة في نفس اللحظة.
5. **لا إعادة اختراع**: نفس أنماط بوابات WhatsApp Gateway المثبتة (Baileys القياسية).

### فلترة الوارد (منع الحلقة اللانهائية)

تُتجاهَل تماماً أي رسالة واردة: `fromMe: true`، أو من مجموعة `@g.us`، أو بث
`@broadcast`، أو بلا نص فعلي — **قبل** إرسالها لأي webhook.

### إعادة فتح الجلسات عند الإقلاع (`rehydrateSessions`)

- يُعاد فتح كل جلسة لها `creds.json` بحقل `registered: true`.
- يُحذف أي مجلد جلسة "يتيمة" لم تكتمل (QR ظهر ولم يُمسَح) بدل إحيائها.

## Docker

```bash
cd gateway
docker build -t sharwa-ai-gateway .
docker run --rm -p 127.0.0.1:4001:4001 \
  -v sharwa_ai_auth_sessions:/app/auth_sessions \
  --env-file .env \
  sharwa-ai-gateway
```

- صورة `node:24-alpine` خفيفة، تُشغِّل `node src/index.js`.
- `VOLUME /app/auth_sessions` لاستمرارية الجلسات عبر إعادة التشغيل.
- `EXPOSE` من متغير `PORT` (افتراضي `4001`).

## ملاحظات وقيود حالية (خارج نطاق هذه الخطوة)

- لا يوجد **إعادة اتصال تلقائية** بعد انقطاع (تُنقل الحالة `DISCONNECTED` لـ Django
  ليتخذ القرار) — خطوة لاحقة إن طُلبت.
- `to` في `send` يُمرَّر كما هو إلى Baileys (يفترض أن Django يرسل JID صالحاً مثل
  `201234567890@s.whatsapp.net`).
