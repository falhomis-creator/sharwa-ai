# PROMPT P3.3 — جولة الإصلاح (F-P3-26 … F-P3-30) ثم الإغلاق — إلى GLM
المصدر الملزِم: `docs/P3_03_FINAL_AUDIT.md`. ما في `PROMPT_P3_03_for_glm.md` ما زال سارياً. **H96 عُدِّلت في الدستور بيد المعماري: STOP يغلب الاشتراك في الرسالة الواحدة فقط؛ وفي الدفعة والدور «آخر كلمة صريحة بترتيب الورود تحسم»، والسجلّ والردّ يتّفقان دائماً.**

## §0 — الوضع
A→D مبنيّة بنيةً صحيحة وF-P3-25 مُغلَق. شغّلتُها على قاعدة حقيقية: `run_db_suite` ⇒ `3 failed` ثم `4 failed` (غير مستقرّ)، ومسابير مستقلة عبر `RealtimeWorker._commit` كشفت تناقضين بين ما يُسجَّل وما يُقال للعميل. كلها في طبقة القرار والاختبارات لا في التصميم. **قاعدة التسليم نفسها:** لا بند «fixed» بلا ناتج قاعدة؛ وإلا `UNVERIFIED (no db)` وأشغّله أنا. وكل SQL جديد: تسلسل ⇒ `list(...)`، ولا `row_factory` في `execute`.

## §1 — الإصلاحات (التزام واحد مستقل `fix(p3.3): F-P3-26..30`)

### 1.1 F-P3-26 [متوسط] — تأكيد كاذب لاشتراك بتعليق صورة
`turn.py` يكشف الاشتراك من `repos_outbox.latest_inbound_texts` (بلا نوع) فتشمل تعليقات الوسائط، بينما الالتقاط عند الاستيعاب `entry.type == "text"` فقط. أضِف في `repos_outbox.py` دالة قراءة جديدة `latest_inbound_typed(conn, conversation_id, after_seq) -> list[tuple[int, str | None, str]]` تُرجع `(seq, body, type)` مرتّبةً بـ`seq` (اقرأ **اسم عمود النوع الحقيقي** في `messages` من 0001 — لا تفترضه). **لا تعدِّل** `latest_inbound_texts` (مستهلكون آخرون). كشف الاشتراك في الدور يعدّ **رسالة `type == 'text'` فقط**؛ وكشف STOP يبقى على أي جسم (تعليق وسائط يوقف كما اليوم).

### 1.2 F-P3-28 [متوسط] — «آخر كلمة صريحة تحسم»
استبدل البوليانين `optout_detected`/`optin_detected` في `decide()` بمدخل واحد `consent_word: Literal["optout", "optin"] | None`. دالة نقيّة جديدة `last_consent_word(messages: list[tuple[int, str | None, str]], settings) -> str | None`: تمرّ على الرسائل **بترتيب seq** وتحسب لكل رسالة: `stop = optout.detect(body…)` (أي نوع) ثم `optin = (type == 'text') and optout.detect_optin(body…) and not stop` (STOP يغلب داخل الرسالة الواحدة)؛ وتُرجع **الكلمة الأخيرة** `optout` أو `optin` أو `None`. `decide()`: `optout` ⇒ `OPTOUT_CONFIRM`، `optin` ⇒ `OPTIN_CONFIRM`، وإلا السلّم كما هو (kill-switch → handoff → …). حدِّث `turn.py` وكل المستدعين والاختبارات القائمة (`detect_optout`/`detect_optin` المنفردتان تبقيان أو تُحذفان إن لم يبقَ مستهلك — لا دالة يتيمة).
**قبول (مساباري، يجب أن تعطي حرفياً):** صورة بتعليق «اشتراك» ⇒ صفوف السجلّ 0 والقرار **ليس** `OPTIN_CONFIRM` · رسالتان `stop` ثم `subscribe` ⇒ التسويق مؤهَّل **والقرار `OPTIN_CONFIRM`** · `subscribe` ثم `stop` ⇒ غير مؤهَّل والقرار `OPTOUT_CONFIRM` · رسالة واحدة تطابق القائمتين ⇒ STOP.

### 1.3 F-P3-27 [منخفض] — ترتيب حتميّ داخل المعاملة
`repos_consent.write_consent`: أدخِل `created_at = clock_timestamp()` صراحةً (لا الاعتماد على `DEFAULT now()` = بدء المعاملة). **اختبار db:** صفّان لنفس (عميل، نطاق) في معاملة واحدة (`granted=false` ثم `granted=true`) ⇒ `read_latest_consent` يعكس **الأحدث كتابةً** في 50 تكراراً متتالياً (لا تقطّع). و`read_history` يرتّب بالترتيب نفسه.

### 1.4 F-P3-29 [منخفض] — STOP المتكرّر بلا ضجيج
`record_optout`: لا تُلحق صفّاً لنطاقٍ آخرُ صفّ له `granted=false` بمصدر `customer_message_optout`؛ تُرجع `"revoked"` إن كتبت ما يكفي صفاً واحداً على الأقل و`"noop"` إن لم تكتب شيئاً (الحجب `ON CONFLICT DO NOTHING` يبقى كما هو). `realtime.py`: عدّاد `consent_events_total{action="noop",source="customer_message_optout"}` عند `noop` وإلغاء الطابور يستمرّ في الحالتين. **قبول:** ثلاث رسائل `stop` متتالية ⇒ **3 صفوف** لا 9. وSTOP بعد اشتراك بعد STOP ⇒ 3 + 1 + 3 = 7.

### 1.5 F-P3-30 [فخاخ fixtures] — ثلاثة اختبارات ثابتة الفشل
1. `test_p3_consent_capture_db::test_stop_via_realtime_writes_history_suppressions_and_cancels_queue`: **لا تزرع محادثة بعد** أن التزم الـworker رسالة (أنشأتها)؛ اقرأ المحادثة القائمة للعميل (`SELECT id FROM conversations WHERE tenant_id=… AND customer_id=…`).
2. `test_p3_consent_db::test_optin_after_stop_lifts_only_marketing`: ازرع حجب `operator` للتسويق **قبل** `record_optout` (فيحفظه `ON CONFLICT DO NOTHING`)، ثم اختبر أن اشتراكاً لاحقاً يُرجع `"granted"` (لا `granted_and_lifted`) وأن حجب `marketing` بسبب `operator` **ما زال قائماً** (لا يرفعه كلام العميل) وكذلك `back_in_stock` و`review_request`؛ **وأضِف** اختباراً مستقلاً: STOP عميل وحده ثم اشتراك ⇒ `"granted_and_lifted"` ويبقى `back_in_stock` و`review_request` محجوبَين. ووثِّق في docstring `insert_suppressions`: «كاتب حجب `operator` مستقبلاً يجب أن يكون `DO UPDATE` للسبب، وإلا ابتلعه حجبُ STOP سابق فرفعه كلامُ العميل».
3. `test_p3_consent_e2e_db::test_matrix_explicit_optin_sends_then_stop_suppresses_then_reoptin_sends`: لا تتوقّع `status == "sending"` (هذا عمل الـdispatcher)؛ وكّد `decision.send is True` وحالة الدفتر `read_ledger_status == 'reserved'` (H85) وصفّ الـoutbox `pending`؛ وفي فروع STOP: `decision.send is False` و`dropped_policy/suppressed`.
(الرابع المتقطّع `test_consent_history_cli_no_phone_no_text` يزول بـ1.3.)

### 1.6 التوثيق
حدِّث `docs/CONSENT_POLICY.md`: استبدل «STOP يغلب الاشتراك دائماً… في الدفعة وفي الدور» بـ«في الرسالة الواحدة STOP يغلب؛ وفي الدفعة/الدور آخر كلمة صريحة بترتيب الورود تحسم والسجلّ والردّ متّفقان»، وسجّل أن STOP المتكرّر لا يُلحق صفوفاً.

## §2 — اختبارات إلزامية (db حيث يلزم، بلا sleep)
(أ) نقي: جدول `last_consent_word` (نصّ/صورة، stop→subscribe، subscribe→stop، رسالة تطابق القائمتين، دفعة بلا كلمة ⇒ None). (ب) db عبر `RealtimeWorker._commit` ثم قراءة `latest_inbound_typed` ثم `decide`: الحالات الأربع في 1.2 حرفياً. (ج) `record_optout` ×3 ⇒ 3 صفوف، و`noop` على الثانية والثالثة. (د) التقطّع (1.3) 50 تكراراً. (هـ) الاختبارات الثلاثة المُصلَحة (1.5). (و) `run_db_suite` أخضر مرّتين متساويتين.

## §3 — شروط التسليم
- البوّابة الساكنة **آخر أمر** بآخر سطر حرفي و`rc=0`؛ `pytest tests -q` نقي؛ `git status --porcelain` فارغ؛ التزام واحد.
- التقرير يفصل VERIFIED عن `UNVERIFIED (no db)`؛ لا ثالث.
- **ممنوع** لمس `app/policy/**` و`claim_*` و`gateway/` و`PHASE_GATE`/`CONSTITUTION` ولا تسجيل قالب تسويقي. لا P3.4.
- **سطر الإغلاق:** `P3.3 FIX ROUND COMPLETE — <VERIFIED|UNVERIFIED (no db)>. STOPPING. AWAITING AUDIT. NO P3.4 WORK STARTED.`
