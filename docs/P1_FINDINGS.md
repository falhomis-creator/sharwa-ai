# P1 Findings — عيوب مكتشَفة أثناء البناء (بأثرها المقاس/المُقرأ من الكود)

> Rule 2: كل عقد/سلوك مُستخرج من الكود الحي، لا تخميناً. أي تعارض يُسجَّل هنا.

---

## F-P1-04 — `routes_channels.py` (P0.7) يستدعي 8 دوال غير موجودة في `repos.py`

- **ماذا:** `scripts/static_gate.py` (S1) كشف أن `core/app/api/routes_channels.py` يستدعي `repos.fetch_tenant_name`, `list_channel_accounts`, `insert_whatsapp_channel_account`, `fetch_idempotency_record`, `update_channel_account_status`, `store_idempotency_record`, `fetch_channel_account`, `fetch_channel_session_id` — **غير معرَّفة** في `core/app/db/repos.py` الحالي (الذي يحوي 9 دوال قنوات-خارجية فقط).
- **لماذا:** خلل P0.7 سابق الوجود (كانت هذه الدوال في نسخة `repos.py` الأقدم، انظر `core.prefix-bak-*`)، خرج عن نطاق P1.2.
- **الأثر:** أي نداء لمسار القنوات يرفع `AttributeError` عند التنفيذ. **خارج نطاق هذه الدفعة**، وموثَّق للتدقيق (ليس من ملفاتي، لكن الأداة كشفته وهذا مقصودها).

## F-P1-01 — نافذة dedupe الفعلية 60 ثانية لا 48 ساعة (تأكيد فرضية P1.1.7)

**المرجع:** `gateway/src/ingest/dedupe.js`, `gateway/src/sessions.js:809-812`, `gateway/src/forwarder.js` (قبل إصلاح هذه الدفعة).

**الخلاصة (قراءة كود، لا تخمين):** الفرضية صحيحة.

- `fusedAppend` (dedupe.js) يضع علامة dedupe بـ`SET … PX ARGV[4]` حيث `ARGV[4] = DEDUPE_PENDING_TTL_S * 1000` (الافتراضي **60 ثانية**).
- `markDone` (الذي يمدّدها إلى `DEDUPE_DONE_TTL_S = 172800`) **لا يُستدعى إلا في مسار `chats.phoneNumberShare`** (`sessions.js:811` — بعد إلحاق `identity_update`).
- الـforwarder (قبل الإصلاح) **لا يستدعي `markDone` بعد تسليم ناجح لـDjango**. والبوابة لا تستدعيه بعد رسالة عميل عادية أيضاً.

**الأثر:** بعد 60 ثانية من `XADD`، تنتهي علامة dedupe على `redis-durable`، فيصير وعد G2 («48 ساعة») غير محقَّق، ويبقى `UNIQUE inbound_events` هو الحامي الدائم الوحيد ضد إعادة تسليم متأخرة. **لا ضياع ولا ازدواج** (الحامي الدائم يعمل) — العيب هو تضييق النافذة المُعلنة، لا فتح ثغرة ازدواج.

**الإثبات الحقيقي بـ`TTL dedupe:{session}:{msg_id}`:** **تعذّر تشغيله في هذه البيئة** (لا `redis-cli`/Docker). الإثبات هنا **قراءة كود مباشرة** للسطرين أعلاه، وسيُكمَّل بمخرج `TTL` حقيقي على السيرفر (A18) عند توفر Docker.

**الإصلاح (في الموضعين، بلا مسّ العقد):**
1. `gateway/src/forwarder.js` — بعد تسليم ناجح لـDjango: `markDone(client, dedupeKeyFor(session_id, provider_message_id), dedupeDoneTtlS*1000)` (best-effort داخل try/catch، لا يحوّل نجاح التسليم إلى فشل).
2. `core/app/workers/stream.py::StreamReader.extend_dedupe_marker` — بعد `COMMIT` بنفس دلالة `markDone` حرفياً (يمدّد **فقط إن كانت العلامة موجودة**؛ `SET` جديد ممنوع لأنه يُحيي علامة انتهت بحق).

**الاختبار:** `core/tests/test_workers_stream.py` (الجزء النقي) + اختبار TTL الحقيقي مؤجَّل لبيئة Docker (A18).

---

## F-P1-02 — خط أساس بوابة الفحص غير نظيف (18 مخالفة سابقة بعد تنظيف المالك)

`node scripts/hunt_gate.mjs` يفشل بـ18 مخالفة **كلها في ملفات P0 قديمة** (`batch_*.sh` — كلمة `placeholder` داخل سكربتات تسليم P0؛ `gateway/scripts/run-tests.mjs` و`ops/wire_*.mjs` — `console.log`؛ `gateway/.../p06_operational_readiness.test.js` — `catch` فارغ). كانت 50 مخالفة قبل أن يحذف المالك يدوياً المجلدات الاحتياطية `core.bak-*`/`core.prefix-bak-*` (التي كانت تحتوي نسخ core قبل إصلاحات P0.7). **ملفات P1 الجديدة خالية من أي مخالفة** (أُثبت بتشغيل البوابة وفحص القائمة كاملة). سُجِّل في P1_DEVIATIONS.md (D-P1-10).

## F-P1-03 — `identity_update` يحمل `lid`/`phone_e164` في المستوى الأعلى لا `identity` (تعارض توثيقي)

`buildIdentityUpdateEvent` (lidmap.js:80-88) ينتج `{type:'identity_update', provider_message_id, ts, lid, phone_e164}` — **بلا كائن `identity`**، خلافاً لوصف §6.1/§P1.1.4 «(من identity)». الكود الحي يغلب (Rule 2): العامل يعالج `wa_id = entry.lid`. سُجِّل في P1_DEVIATIONS.md (D-P1-04).

---

## F-P1-05 — `turn.py` ينادي `optout.detect_handoff` غير المعرَّفة (حاجز P1.2، أُصلح في الخطوة صفر)

- **الحقيقة:** `turn.process_turn` كان ينادي `optout.detect_handoff(...)` التي لم تكن معرَّفة في `app/workers/optout.py` (كان فيه `normalize`/`_match`/`detect` فقط) ⇒ `AttributeError` عند كل دور (إصلاح D6 معطَّل بالكامل).
- **السبب الجذري لمرورها في البوابة:** مرحلة S1 كانت تتخطّى بصمت أي وحدة هدف لا تُحلّ (`if target not in mod_names: continue`) وتفحص النداءات (`ast.Call`) وحدها.
- **الإصلاح:** `detect_handoff(message, *, phrases_ar, phrases_en)` في `optout.py` بنفس تطبيع `normalize()` وقاعدة المطابقة (الرسالة كاملةً أو بدايتها) + `CORE_HANDOFF_PHRASES_AR/_EN` في `WorkerSettings`.
- **التحقق:** البوابة بعد التصليب لا ترفع أي مخالفة على `turn.py`/`optout.py`.

## F-P1-06 — `dispatch.py` يقرأ `templates.POLICY_EXEMPT_TEMPLATES` غير المعرَّفة (حاجز P1.2، أُصلح في الخطوة صفر)

- **الحقيقة:** `dispatch._pre_send_checks` كان يقرأ `templates.POLICY_EXEMPT_TEMPLATES` غير المعرَّفة في `app/workers/templates.py` ⇒ `AttributeError` عند كل صف إرسال (إصلاح D1 معطَّل).
- **السبب الجذري لمرورها في البوابة:** قراءة سمة بلا نداء (`templates.POLICY_EXEMPT_TEMPLATES`) غير مرئية بنيوياً لـS1 لأنها كانت تفحص `ast.Call` وحده.
- **الإصلاح:** `POLICY_EXEMPT_TEMPLATES: frozenset[str] = frozenset({"safe_ack"})` — قائمة مغلقة (أي إضافة إليها قرار سياسة لا قرار برمجة). أُحدّث اختبار `test_policy_exempt_templates_are_closed_set` ليطابق `safe_ack` وحدها.
- **التحقق:** البوابة بعد التصليب لا ترفع أي مخالفة على `dispatch.py`/`templates.py`؛ وS1-a أُثبت بإفشال متعمَّد (`templates.NO_SUCH_CONST`).

## F-P1-07 — ثغرة `static_gate.py` S1 (السبب الجذري المشترك لمرور F-P1-05/06)

- **ماذا:** S1 كانت (1) تفحص `ast.Call` وحده فقراءة/إسناد سمة غير مرئية لها، و(2) تتخطّى بصمت أي وحدة هدف لا تُحلّ (`if target not in mod_names: continue`)، و(3) تعدّ الأسماء المستوردة تعريفات حقيقية (يخفي `from x import missing`).
- **الإصلاح (S1-a..d + S5):** فحص كل `mod.attr` (قراءة/نداء/إسناد)؛ هدف لا يُحلّ = مخالفة `[S1] unresolved module target` مع قائمة سماح مكتوبة للمكتبات القياسية/الخارجية؛ تمييز المستورَد عن المعرَّف وإبلاغ «re-export definition»؛ ومرحلة S5 تمنع `internal_notes` من أي مسار إرسال (إغلاق H28 بنيوياً).
- **التحقق:** إفشالان متعمَّدان جديدان (S1-a عبر `templates.NO_SUCH_CONST`، وS5 عبر `internal_notes` في `repos_outbox.py`) ثم حُذفا.

---

## F-P1-07b — واجهة القنوات في P0.7 لا تعمل: **أُصلحت في P1.3b** (PROMPT §0.1)

- **وجدتُه موجوداً:** `scripts/static_gate.py` (S1) أبلغ عن **13 مخالفة** في `core/app/api/routes_channels.py` — تسعة أسماء تناديها المسارات (`fetch_tenant_name`, `list_channel_accounts`, `fetch_idempotency_record`, `store_idempotency_record`, `insert_whatsapp_channel_account`, `ChannelAlreadyExistsError`, `update_channel_account_status`, `fetch_channel_account`, `fetch_channel_session_id`) ولا تعرّفها `core/app/db/repos.py`. (الإدخال F-P1-04 أعلاه عدّها «8 دوال» لأنه لم يعدّ استثناء `ChannelAlreadyExistsError`، وهو الاسم التاسع.)
- **أضفتُ** التسعة في `core/app/db/repos.py` بجودة `repos_inbox.py` (SQL في `core/app/db/` وحدها، اتصال مفتوح من `tenant_tx()`، أنواع صريحة). التواقيع مأخوذة من مواقع الاستدعاء نفسها، لا اجتهاد.
- **شغّلتُ** البوابة **قبل** (13 مخالفة S1) و**بعد** (صفر) — المخرجان محفوظان في `P1_03b_REPORT.md` §0.1.
- **حقيقة `tests/test_routes_channels.py` قبل/بعد:** **لم أشغّله** قبل ولا بعد. **السبب:** الاختبار يفترض قاعدة `sharwa_ai_p07` على `127.0.0.1:5432` و`redis-cache` على `127.0.0.1:6390`؛ **فحصتُ** الاتصالين فعلياً (`password authentication failed for user "p07_app"` و`ConnectionError ... 6390 ... refused`). البيئة بلا حاويات بقرار المالك. الدليل الثابت (13 مخالفة) يثبت أن المسارات **كانت ستنهار**، لكنني **لم أُشغّل** الاختبار لأقول «كان يفشل» تشغيلياً.



---

## F-P1-08 — `kb_chunks`/`stock_levels` بلا `source_version` و`kb_chunks` بلا مفتاح طبيعي (P1.4)

- **ماذا:** قرأتُ `0001_baseline.sql` فوجدتُ `catalog_products`/`catalog_variants` يحملان `source_version` (H37)، بينما `stock_levels` و`kb_chunks` **لا يحملانه**، و`kb_chunks` بلا قيد فريد على `(tenant_id, source)` — فيتعذّر `ON CONFLICT` لحدث `kb.upserted` ولا يمكن فرض «الأقدم يُتجاهَل» لحدث `variant.stock`.
- **لماذا:** مخطط المرجع (`docs/reference/schema.sql`) صُمّم قبل تحديد حمولات C1 النهائية؛ `source` هو المفتاح الطبيعي الوحيد لحدث `kb.upserted` (لا معرّف منصة له).
- **الإصلاح:** `core/migrations/0007_p1_catalog.sql` (أمامي) يضيف `source_version` للجدولين + `UNIQUE (tenant_id, source)` على `kb_chunks` + دالة `app.list_catalog_sync_state()` SECURITY DEFINER لعدّ المتاجر في خيط المصالحة. **لم أعدّل** `docs/reference/*` ولا أي ترحيل سابق.
- **الأثر:** H37 مكتمل للكيانات الخمسة؛ لا إنشاء جدول موجود.



---

## F-P1-09 — ملاحظتا التدقيق N1/N2 (من `PHASE_GATE.md`) — أُصلحتا في الخطوة صفر

- **N1 [متوسط]:** `0007` أضاف `source_version` بلا `DEFAULT`/backfill/`NOT NULL`، فصفّ بـ`NULL` يجعل `X > NULL = NULL` ⇒ تحديث يُتخطّى بصمت **وتعيد الدالة `"applied"`**. **أصلحتُه**: backfill + `DEFAULT 0` + `NOT NULL` في `0007`، و`COALESCE(..., -1)` في شروط `ON CONFLICT` الأربعة، و`_existing_version` تعيد `-1` عند `NULL`.
- **N2 [منخفض]:** `ALTER TABLE … ADD CONSTRAINT` غير idempotent (لا `IF NOT EXISTS`). **أصلحتُه**: `CREATE UNIQUE INDEX IF NOT EXISTS kb_chunks_tenant_source_uq`.
- **الأثر:** H37 مكتمل للجدولين، وH14 (idempotency) مستعادة.

---

## F-P1-10 — حادثة إعادة بناء دوال القنوات في `repos.py` (شفافية عملية، لا عيب كود)

- **ماذا:** أثناء إثبات S9 بالإفشال المتعمَّد في P1.5b، أصدرتُ `git checkout` على `core/app/db/repos.py` لإزالة حقن الفحص، فأعادت النسخة الملتزمة (P0.7) ومحَت تسع دوال قنوات كانت عملاً **غير ملتزم** من P1.3b (`fetch_tenant_name`, `list_channel_accounts`, `fetch_idempotency_record`, `store_idempotency_record`, `insert_whatsapp_channel_account`, `ChannelAlreadyExistsError`, `update_channel_account_status`, `fetch_channel_account`, `fetch_channel_session_id`).
- **لماذا:** العمل الكامل P1.0→P1.5 غير ملتزم في git (HEAD عند `2047291` = P0.7)؛ `git checkout` أعاد ملفاً واحداً للخلف.
- **الإصلاح:** **أعدتُ بناءها** من مواقع الاستدعاء في `app/api/routes_channels.py` + `0002_p0_api.sql` (الفهرس الفريد الجزئي `channel_accounts_one_active_baileys_uq`) + توثيق `F-P1-07b`/`P1_03b_REPORT.md`. التواقيع مطابقة لمواقع الاستدعاء حرفياً.
- **الدليل على الإصلاح:** `static_gate` (S1) صفر مخالفة بعد الإعادة، و`pytest` 130 خضراء (0 فشل). **تنبيه تشغيلي:** هذا المستودع يحمل عملاً كبيراً غير ملتزم — يُوصى بالتزامه قبل أي تدقيق لاحق لتفادي تكرار هذا النوع من الحوادث.


