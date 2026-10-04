# PROMPT P3.1 — جولة الإصلاح الثالثة (F-P3-16..19) ثم الإغلاق
المصدر الملزِم: `docs/P3_01_FINAL_AUDIT.md`. ما في `PROMPT_P3_01_sendpolicy_for_deepseek.md` و`PROMPT_P3_01_stage_DG_for_deepseek.md` ما زال سارياً.

## §0 — الوضع
A–G مُنفَّذة والتقرير صادق (`UNVERIFIED`). شغّلتُها أنا على قاعدة حقيقية: الدفتر نظيف عدا **عيبين في المنتج** يُسقطان رسائل عملاء بصمت، وS22/S23 مفقودتان، وأحد عشر اختبار db بفخاخ fixtures. P2 أُغلقت فعلاً. P3.1 تُغلَق بهذه الجولة فقط. **القاعدة نفسها:** لا بند «fixed» بلا ناتج قاعدة؛ وإلا `UNVERIFIED (no db)`. **لكن هذه المرّة أحتاج أن تشغّل المسبارين بنفسك على قاعدة** (انظر §1) — فإن لم تملك قاعدة فاكتب الإصلاحات واختباراتها وقِف بـ`UNVERIFIED` ويشغّل المالك.

## §1 — العيوب

### 1.1 F-P3-16 [حرج] — التأجيل يستهلك المحاولات
`claim_outbox` يزيد `attempts` عند كل مطالبة؛ التأجيل بسياسة ليس محاولة إرسال. **الإصلاح:** في `repos_policy.defer_outbox` أضِف `attempts = GREATEST(attempts - 1, 0)` إلى الـUPDATE (التأجيل يُرجع ما استهلكته المطالبة). **لا** تفعل ذلك في `fail_closed` (خطأ البوّابة يستهلك المحاولة ليُفشَل الصفّ المسموم). عمر الصفّ المؤجَّل محدود بالـTTL (مرافق 6س، تسويق 24س) فلا يدور للأبد.
**اختبار القبول** `core/tests/test_dispatch_burst_db.py` (`@pytest.mark.db`): عبر `dispatch.dispatch_cycle` الكامل (لا `gate()` منفردة)، عملاء مُوافقون ومتفاعلون قبل يومين، قالب `stock_available`، `number_health` سقف مرافق 100، جيتواي مزيَّف يقبل، ويُعاد الزمن بين الدورات بإرجاع `next_attempt_at` و`next_utility_at` للماضي (حقن، بلا `sleep`). حالات N = 5 و12 و30: **`failed` = 0 في كلٍّ منها، وكل الصفوف تنتهي `sent`** (ما دام N ≤ سقف المرافق) وتُرسَل بترتيب مطالبتها. أرفق الناتج. ثم أضِف حالة N = 130: 100 `sent` والباقي `pending` (مؤجَّل) لا `failed`.

### 1.2 F-P3-17 [حرج] — STOP لعميل واحد يُلغي طابور الجميع
`repos_policy.cancel_pending_proactive` يتجاهل `customer_id`. أضِف إلى الـWHERE: `AND conversation_id IN (SELECT id FROM conversations WHERE tenant_id = %s AND customer_id = %s)` (مرِّر المعامِلَين). **اختبار القبول:** عميلان A وB لكلٍّ إشعار pending؛ إلغاء بمعرّف A ⇒ A `dropped_policy` وB `pending`؛ ثم اختبار نهاية-لنهاية عبر مسار الاستيعاب الحقيقي (رسالة «إيقاف» من A) ⇒ نفس النتيجة، وأن `suppressions` كُتبت لـA وحده.

### 1.3 F-P3-18 [متوسط] — S22 وS23
نفِّذهما كما في §6 الأصلي، **كلٌّ بإفشال متعمَّد وناتجه حرفياً**: S22 (أ) `insert_outbox(…origin="automation"…)` لا يُنادى إلا من `app/workers/proactive.py`، (ب) `proactive.py` لا يعرّف معامل `message_class` في أي دالة عامة ولا يمرّر ما ليس من الكتالوج، (ج) S10-a المُعدَّل قائم، (د) `GatewayClient.send` لا يُنادى خارج `dispatch.py`. S23: في `dispatch.py` كل فرع `origin == "automation"` يمرّ بـ`policy_gate.gate` قبل أي `_send_*`، ولا مسار يصل إلى `client.send` لصفّ automation دونه. آخر أمر للبوّابة بصفر مخالفة، ورمز الخروج `rc=0`.

### 1.4 F-P3-19 [متوسط] — فخاخ الـfixtures (الأحد عشر)
- `testsupport.seed_number_health`: اضبط `next_marketing_at` و`next_utility_at` على `'1970-01-01'` (معاملان اختياريان) — لا `DEFAULT now()` مع زمن محقون في الماضي.
- `testsupport.seed_inbound_message`: أضِف معامل `age` (افتراضي يومان) وأدخِل `created_at = now() - age`؛ الاختبارات التي تريد «يتحدث الآن» تمرّر `age=0`.
- `test_policy_gate_db::test_gate_reserves_eligible_stock_notice`: تحقّق من `utility_sent_today`.
- `test_dispatch_db._settings`: ابنِه من القيم الافتراضية الحقيقية في `config` (كل حقول `send_policy_*` المطلوبة) لا من ستاب ناقص؛ وصفّ automation في اختبار «كل origin» يحمل `template: stock_available` + موافقة + تفاعلاً سابقاً قديماً + `number_health`.
- **نمط ثابت:** أي fixture يحقن زمناً يضبط كل أعمدة الزمن المرتبطة به. ولا تعتمد على تاريخ مُرمَّز بالأيام في اختبار db دون سبب.

### 1.5 تغطية H86 المفقودة (مطلوبة، `@pytest.mark.db`، حتميّة بحقن الزمن)
- **المكنسة:** سقف الإحماء حسب اليوم المحلي · `throttled` يضرب السقف بالمعامل · لا رجوع `throttled→healthy` قبل `THROTTLE_COOLDOWN_H` ثم رجوع · `paused` لا يخرج بالمكنسة · إعادة التصفير بعد الخمول.
- **CLI:** `policy status` لا يطبع رقماً ولا نصّ عميل (H48)، و`policy reinstate` يُخرج من `paused` ويُدوِّن السبب.
- **الفشل المُغلَق:** استثناء مُحقَن داخل `gate` ⇒ الصفّ `pending` مؤجَّل بـ`policy_reason='policy_error'`، لا إرسال، `policy_errors_total` يزيد.
- **سقوف التكرار لكل عميل** على قاعدة: تسويق 1/24س و2/7أيام (قالب تسويقي محقون في الاختبار فقط)، مرافق 3/24س.

## §2 — ملاحظات مُلزِمة (N-6..N-8)
- `count_class_handoffs`: احسب `status IN ('reserved','handed_off')`.
- مرِّر `now` المحقون إلى `repos_policy.has_prior_interaction/has_active_chat/count_class_handoffs` بدل `now()` القاعدة (حتميّة النوافذ، H84).
- التأجيل للساعات الهادئة: `defer_until = quiet_hours.next_allowed_at(...)`؛ وللمحادثة النشطة: آخر وارد + `ACTIVE_CHAT_COOLDOWN_S`. لا نبض كل 60 ثانية.

## §3 — شروط التسليم
- التقرير يفصل VERIFIED (ناتج قاعدة ملصوق) عن `UNVERIFIED (no db)`.
- `python scripts/static_gate.py` بآخر سطر حرفي و`rc=0`؛ `pytest tests -q` نقي بآخر سطر؛ `git status --porcelain` فارغ.
- التزام واحد مستقل `fix(p3.1): …`.
- **سطر الإغلاق:** `P3.1 FIX ROUND 3 COMPLETE — <VERIFIED|UNVERIFIED (no db)>. STOPPING. AWAITING AUDIT. NO P3.2 WORK STARTED.`
