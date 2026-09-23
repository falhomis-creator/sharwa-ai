# P0 Findings — أدلة من الشيفرة الحقيقية (بلا تخمين)

> مخرجات P0.0 خطوة 3 و4. كل نتيجة مرفقة بمسار الملف ورقم السطر.

---

## F1 — دعم `@lid` في baileys@6.7.24 (السؤال المفتوح 4 في المعمارية)

المرجع: `gateway/node_modules/@whiskeysockets/baileys/lib/` (الإصدار 6.7.24 مثبَّت — مؤكَّد من `package.json`).

**الخلاصة:** النسخة تدعم معرّفات `@lid` بشكل فعلي، وتملك مساراً لربط LID → رقم الهاتف، لكنها **لا** تحمل حقلين باسم `remoteJidAlt`/`participantAlt`.

الأدلة:

| الملف:السطر | ما يثبته |
|---|---|
| `WABinary/jid-utils.js:10-25` | `jidDecode` يحلّل `@lid`: يعيد `server='lid'` و`domainType: server === 'lid' ? 1 : 0` (سطر 22). |
| `WABinary/jid-utils.js:33` | `isLidUser = (jid) => jid?.endsWith('@lid')`. |
| `WABinary/jid-utils.js:44-51` | `jidNormalizedUser` يطبّع `c.us`→`s.whatsapp.net` ويحافظ على `lid`. |
| `Socket/messages-recv.js:85,626,638` | فك/تشفير الرسائل يستخدم `authState.creds.me.lid` — أي أن بيانات اعتماد الحساب تخزّن LID الخاص بالرقم نفسه. |
| `Socket/messages-recv.js:502-503` | `const isLid = attrs.from.includes('lid'); const isNodeFromMe = areJidsSameUser(attrs.participant || attrs.from, isLid ? me.lid : me.id)` — تمييز رسائل `@lid` الواردة. |
| `Socket/messages-recv.js:643-644` | `ev.emit('chats.phoneNumberShare', { lid: node.attrs.from, jid: node.attrs.sender_pn })` — **المسار الفعلي** لربط LID برقم الهاتف (`sender_pn`). |
| `Utils/decode-wa-message.js:91` | `senderPn: stanza?.attrs?.sender_pn`. |
| `Utils/process-message.js:121,137` | `LID_MIGRATION_MAPPING_SYNC` (رسالة بروتوكول لترحيل LID). |
| `Socket/chats.js:506` | `from: isLid ? me.lid : me.id`. |

**الأثر على G12:** البوابة يجب ألّا تفترض أن JID = رقم هاتف. عند ورود `remoteJid` ينتهي بـ`@lid`، يكون `phone_e164=null` ما لم يُحصل الرقم من `sender_pn`/حدث `chats.phoneNumberShare`، مع حفظ `jid_raw` و`addressing: 'pn'|'lid'`.

---

## F2 — تعامل الكود القائم مع الوسائط بلا نص (سيُحاكى للأنواع الجديدة في P0.2)

المرجع: `gateway/src/sessions.js`.

- `extractText` (سطور 53-65): يعيد `''` لرسالة وسائط بلا caption.
- `shouldIgnoreInbound` (سطور 77-84): **لا يتجاهل** رسالة وسائط ولو كان نصّها فارغاً — `detectMedia(msg)` يرجع كائناً فيُرجع `false`.
- `processInboundMessage` (سطور 242-287) مع وسائط: ينزّل (`downloadMediaToBuffer`) ثم يرفع لـMinIO (`uploadWithRetry`، 3 محاولات)، ويرسل الويبهوك بـ:
  - `message_text` = caption إن وُجد وإلّا `''`.
  - `media_object_key` = مفتاح الكائن أو `null` عند فشل الرفع.
  - `media_type` = نوع الوسائط.
  - عند فشل الرفع بعد المحاولات: `outText = 'تعذّر معالجة المرفق المرسل.'` و`media_object_key=null` — **لا يُسقَط الحدث**.

**الأثر على P0.2:** الأنواع التي لا يعرفها Django القديم (`location`, `contact`, `sticker`, `reaction`, `unsupported`) ستُعامَل بنفس المنطق: تمرير `message_text` (قد يكون فارغاً) مع حقول إضافية اختيارية، ولا يُسقَط أي حدث بصمت.

---

## F3 — مفردات `mapConnectionStatus` الحالية (تُبقى حرفياً في P0.5)

المرجع: `gateway/src/sessions.js:191-204`.

| الشرط | القيمة المعادة |
|---|---|
| `update.qr` موجود | `QR_PENDING` |
| `update.connection === 'open'` | `CONNECTED` |
| `update.connection === 'close'` و`statusCode === DisconnectReason.forbidden` (403) | `BANNED` |
| `update.connection === 'close'` غير ذلك | `DISCONNECTED` |
| غير ذلك | `UNKNOWN` |

هذه المفردات هي التي يقرؤها عقد Django الحالي عبر `getSessionStatus` (`{status, qr_image_base64, connected_phone_number}`)، فلن تُغيَّر في P0.5 — الحالات الجديدة تُعرَض عبر مسار إضافي `GET /sessions/:id/health`.

---

## F4 — قيد تصميمي لـ P0.2: حدّ أقصى لحجم القيمة المكتوبة إلى `redis-durable`

**كُشف تجريبياً على الـVPS (R3/هـ.3).** محاولة `SET` واحدة بقيمة ~120MB أسقطت حاوية `redis-durable` (حدّها 320m) بقتل OOM — خرجت العملية بـ`Killed` (ExitCode 137، `OOMKilled`).

**الآلية:** قيمة واحدة ضخمة تُضخّم ذاكرة الحاوية مؤقتاً في ثلاث نقاط متزامنة قبل أن يعالج redis الأمر:
1. مخزن الاستعلام للعميل (`query buffer`) يحمل القيمة الواردة (~120MB).
2. `redis-cli` (المشغَّل داخل الحاوية) يحمل القيمة في مخزن إدخاله قبل إرسالها (~120MB).
3. القيمة المخزَّنة فعلياً في مجموعة البيانات (~120MB).

المجموع اللحظي (~360MB) يتجاوز حدّ الحاوية 320m فيقتلها OOM-killer — رغم أن `maxmemory` (128mb) سيرفض **تخزينها** لاحقاً بـ`noeviction`. أي أن `maxmemory` وحدها لا تحمي من قمة الذاكرة المؤقتة أثناء **استقبال** قيمة عملاقة.

**قيد P0.2 المطلوب:** البوابة يجب أن تفرض حدّاً أقصى لحجم الرسالة/القيمة قبل الكتابة إلى `redis-durable`، ويُرافقه حدّ على مستوى Redis حتى يرفض redis الأمر العملاق **عند الاستقبال** بدل تخزينه في المخزن المؤقت ثم قتله:

| المفتاح | المعنى | الافتراضي | الإجراء في P0.2 |
|---|---|---|---|
| `proto-max-bulk-len` | أقصى طول لسلسلة bulk يقبلها redis | 512mb | خفضه إلى حدّ منطقي لرسائل الـWhatsApp (مثلاً 1mb) بحيث يُرفَض أي أمر أكبر بـ`ERR Protocol error` لا بتخزينه |
| `client-query-buffer-limit` | حدّ صارم لمخزن استعلام العميل | 1gb | خفضه إلى بضعة ميغابايت كي يُقطع أي عميل يتجاوزه بدل تضخيم الذاكرة |

**الأثر:** لا يسمح بأي مسار يُمرِّر حمولة غير محدودة إلى `redis-durable`؛ الوسائط الكبيرة تذهب إلى MinIO (كائن) ويدخل Redis مفتاحها فقط (صغير). القيم الدقيقة تُقرَّر عند كتابة كود الاستيعاب في P0.2، مع اختبار قاسٍ يُحاول `SET` بقيمة أكبر من الحدّ ويثبت رفضه بلا قتل OOM.


## F5 — عيب ذاكرة حقيقي مُكتشَف أثناء بناء اختبارات قبول P0.3 (backpressure)

**السياق:** أثناء كتابة اختبارات القبول الرسمية لـP0.3 (منحنى RSS لملف 50MB) ضد خادم S3 حقيقي محلياً، جاءت أول محاولة بنمو RSS **100.0MB بالضبط** — على حافة العتبة المسموحة تماماً، وهو نمط مريب يستحق تحقيقاً لا تجاهلاً.

**السبب الجذري (مؤكَّد بالكود لا بالتخمين):** `downloadAndUploadMedia` في `gateway/src/media.js` كانت تكتب كل جزء وارد إلى `PassThrough` عبر `passthrough.write(chunk)` **دون فحص قيمة الإرجاع**. حين يكون رفع multipart إلى S3/MinIO أبطأ من تدفق التنزيل (شبكة محمَّلة، أو `partSize`/`queueSize` في `@aws-sdk/lib-storage` يحدّان من سرعة الاستهلاك)، يعيد `.write()` القيمة `false` (ضغط عكسي/backpressure)، وتجاهل هذه القيمة يجعل `PassThrough` يراكم كل الأجزاء غير المستهلَكة في مخزن داخلي بلا حد أعلى — أي **إعادة إنتاج نفس عطل "الملف كاملاً في الذاكرة" الذي صُمم P0.3 أصلاً لإلغائه**، بصمت وبدون أي رسالة خطأ.

**الإصلاح:** احترام الضغط العكسي فعلياً: `const canWriteMore = passthrough.write(chunk); if (!canWriteMore) { await once(passthrough, 'drain'); }` — هذا يُبطئ حلقة التنزيل نفسها لتتوافق مع سرعة الرفع الفعلية، وهو جوهر معنى "التدفق" (streaming) أصلاً.

**الأثر المقيس (محلياً، خادم S3 حقيقي وليس محاكاة):** نمو RSS لملف 50MB انخفض من 100.0MB (على الحافة، وربما كان سيتجاوزها فعلياً تحت شبكة الإنتاج الأبطأ) إلى ~32–54MB باستقرار عبر عدة تشغيلات متكررة؛ ذروة 10 ملفات متزامنة انخفضت من ~441–533MB إلى ~260–275MB. هذا اكتشاف حقيقي أثبته التشغيل الفعلي لا القراءة النظرية للكود (H8).
