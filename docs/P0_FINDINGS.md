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
