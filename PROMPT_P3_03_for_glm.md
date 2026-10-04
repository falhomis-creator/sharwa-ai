# PROMPT P3.3 — نظام الموافقة التسويقية (Opt-in / Opt-out) — إلى GLM
المصدر الملزِم: `docs/P3_02_FINAL_AUDIT.md` · `docs/CONSTITUTION.md` (H76–H93 سارية، وH94–H99 أدناه أُضيفت إليه بيد المعماري قبل التسليم) · `docs/PHASE_GATE.md`. ما في `PROMPT_P3_01_*` و`PROMPT_P3_02_*` ما زال سارياً حيث لا يتعارض.

## §0 — الوضع وقرارات المالك
P3.2 مُغلَقة (مشروطة بالخطوة صفر أدناه). قرارات المالك المعتمدة (2026-10-04): **أرقام الجدولة §4 من P3.2 معتمدة كما هي** (24 ساعة تأخير، 12 ساعة أقصى تأخّر، …) · **نصّ تذكير السلة = عنوان أول منتج مقصوصاً + العدد فقط**. نطاق P3.3 بقرار المالك: التقاط الموافقة (Opt-in) وإلغاؤها (Opt-out) وحفظ تاريخها وربطها بالبوّابة.

**ما وجدتُه في الكود الحيّ (اقرأه قبل أن تكتب سطراً — لا تعيد بناء ما هو مبنيّ):**
- `consents` (0001) جدول تاريخي بـ`scope ∈ {marketing, back_in_stock, review_request, order_updates}` و`granted` و`source` و`evidence`. `policy_gate` يقرأها فعلاً: `repos_policy.read_latest_consent` (آخر صفّ يفوز، لا صفّ ⇒ لا موافقة H78).
- **الكاتب الوحيد للموافقة اليوم** هو تنسيق قائمة الانتظار (`stock.py` ⇒ `repos_policy.write_consent`, scope `back_in_stock`). **لا شيء يكتب موافقة `marketing` في أي مكان.**
- STOP موجود: `realtime.py` ⇒ `optout.detect` ⇒ `repos_ingest.insert_suppressions` للنطاقات الثلاثة `marketing, back_in_stock, review_request` (وليس `order_updates`) ⇒ `cancel_pending_proactive`؛ و`turn.py` ⇒ `OPTOUT_CONFIRM` بقالب `optout_confirm`. **لكن STOP لا يكتب صفّ `consents granted=false`** (التاريخ ناقص)، و`suppressions` لا حذف منها أبداً ⇒ **لا طريق للعودة بعد STOP**: `decision.py` يُسقط `suppressed` قبل `no_consent` للأبد.
- **عيب منتج قائم (من فحصي):** عميل يوقف الرسائل ثم يسجّل نفسه صراحةً في قائمة الانتظار ويتلقى «سنُشعرك فور توفره» — فيُكتب `consents back_in_stock granted` لكن صفّ `suppressions` يبقى ⇒ إشعار التوفّر يُسقَط `suppressed` بصمت. وعدٌ كاذب للعميل.
- مخطّط الإدخال (`schema.py`) لا يعرّف نوع رسالة «زر/قائمة» ⇒ **الأزرار التفاعلية خارج نطاق P3.3** (تتطلب عمل جيتواي ممنوعاً لمسه هنا) — OQ-P3-11. الموافقة بالنصّ فقط.

**قاعدة التسليم نفسها:** لا بند «fixed/verified» بلا ناتج قاعدة حقيقية (الأمر + وقت UTC + السطر الأخير حرفياً)؛ وإلا `UNVERIFIED (no db)` وأشغّله أنا. ولا تسجّل أي قالب تسويقي ولا `cart_reminder` في الكتالوج: **التسويق ما زال مُظلَماً حتى P3.4**.

## §1 — المواد الدستورية H94–H99 (مُضافة إلى `docs/CONSTITUTION.md` — لا تُعدِّلها)
- **H94 — تاريخ الموافقة لا يُمسّ.** `consents` إلحاقية فقط: الحالة الراهنة = آخر صفّ (latest-wins) ويفرضها **سحب `UPDATE/DELETE` من `sharwa_app` في القاعدة** لا بالأدب. لا موافقة تُعدَّل ولا تُمحى؛ التراجع صفّ جديد.
- **H95 — كاتب واحد وقائمة مصادر مغلقة.** كل `INSERT` على `consents` وكل `INSERT/DELETE` على `suppressions` يمرّ من `app/db/repos_consent.py` وحده. `source` من قائمة مغلقة (قيد `CHECK` في القاعدة): `customer_message_optin · customer_message_optout · waitlist_join · checkout_optin · import · operator`. **موافقة `marketing` لا يعدّها `policy_gate` إلا من `customer_message_optin`** — `checkout_optin` و`import` مسموحان في القيد ومحجوبان عن التسويق حتى قرار قانوني من المالك (OQ-P3-14).
- **H96 — الاشتراك صريح وحتميّ.** مطابقة **المساواة الكاملة** للرسالة المطبَّعة مع قائمة مغلقة (لا بداية رسالة، لا احتواء، لا LLM، لا تصنيف)، ونوع الرسالة `text` فقط (تعليق صورة لا يشترك). لا «نعم» ولا شراء ولا صمت ولا انضمام لقائمة انتظار يُعدّ موافقة تسويقية. **STOP يغلب الاشتراك** في الدفعة نفسها وفي الدور نفسه.
- **H97 — STOP يغلب الموافقة حتى اشتراك لاحق صريح لنفس النطاق.** رفع الحجب يقتصر على صفّ `suppressions` بسبب `customer_message_optout`، في المعاملة نفسها التي تُكتب فيها صفّ الموافقة، ولنطاقٍ واحد هو المُشترَك فيه (اشتراك `marketing` لا يرفع `back_in_stock`). حجب بأي سبب آخر (`operator`…) لا يُرفع بكلمة عميل أبداً.
- **H98 — الدليل معرّف لا نصّ.** `evidence` = معرّف الرسالة/القيد (UUID)، **لا نصّ العميل ولا هاتفه** (H20/H48)، في السجلّ والـCLI والمقاييس.
- **H99 — كتابة الموافقة ذرّية ومتماثلة.** تُكتب في معاملة الالتزام بالرسالة الواردة نفسها؛ إعادة تسليم الرسالة نفسها لا تُنتج صفّ موافقة ثانياً؛ إعادة الاشتراك وهو مشترك بالفعل لا يضيف ضجيجاً (لا صفّ إن لم تتغيّر الحالة).

## §2 — الخطوة صفر الإلزامية (قبل أي سطر من §3)
1. **F-P3-22** — في `repos_scheduler.schedule`: `ON CONFLICT (tenant_id, dedupe_key) DO UPDATE SET status='pending', run_at=EXCLUDED.run_at, attempts=0, cancel_reason=NULL, finished_at=NULL, last_error=NULL, max_lateness_s=EXCLUDED.max_lateness_s WHERE scheduled_jobs.status='cancelled' AND scheduled_jobs.cancel_reason = ANY(%s)` مع `RETURNING id`؛ الثابت `REVIVABLE_CANCEL_REASONS = ("too_late","template_not_registered","no_conversation")` يُعرَّف **مرّة واحدة** في `repos_scheduler.py`. الأسباب النهائية (`cart_recovered`, `cart_cleared`, `cart_closed`) تبقى نهائية. القيمة المُرجَعة True عند الإنشاء أو الإحياء، False عند وجود مهمة `pending` (فيستمرّ `reschedule`). **اختبارات db:** لكل سبب قابل للتعافي ⇒ نشاط جديد ⇒ المهمة `pending` بـ`run_at` الجديد و`attempts=0`؛ لكل سبب نهائي ⇒ تبقى `cancelled`؛ مهمة `failed`/`done` لا تُحيا؛ وصفّ واحد بعد استدعاءين متتاليين.
2. **F-P3-23** — أضِف `AND status = 'processing'` إلى `complete` و`fail_attempt` و`defer` و`finish_failed`؛ تُرجع كلٌّ منها `bool` (`rowcount > 0`)، والمحرك يعدّ `scheduler_outcomes_total{outcome="superseded"}` عند False ولا يرمي. **اختبارات db:** لكل من الأربع على مهمة `cancelled` ⇒ تبقى `cancelled`؛ والمسار العادي لم يتغيّر (انسخ مسابير المعماري: `cancel` ثم `fail_attempt` ⇒ `cancelled`).
3. **F-P3-24** — وثِّق في `docs/PLATFORM_CART_CONTRACT.md` (قسم جديد): «حدث نهائي يصل قبل أول `cart.updated` لا يُحجب؛ أرسِل `updated` قبل `recovered/cleared`». لا كود.
4. `python scripts/run_db_suite.py` مرّتين متساويتين (≥ 347 ناجحاً + الجديد) والبوّابة rc=0 — **التزام مستقل** `fix(p3.2-0): F-P3-22 revive recoverable cancels + F-P3-23 status-guarded result writers`. بلا ذلك لا تبدأ §3.

## §3 — المراحل A → D (التزام مستقل لكل مرحلة؛ لا مرحلة فوق سابقة لم تمرّ)

### A — القاعدة والكاتب الوحيد والبوّابة
- ترحيل **`0016_p3_consent.sql`** (idempotent H14؛ لا تعدّل 0001–0015): `REVOKE UPDATE, DELETE ON consents FROM sharwa_app` (و`GRANT SELECT, INSERT`)، وقيد `CHECK` على `consents.source` بالقائمة المغلقة (بكتلة `DO` + `pg_constraint`، وإن وُجد صفّ قديم بمصدر خارجها فاكتب ذلك في التقرير ولا تحذفه)، وفهرس `consents (tenant_id, customer_id, scope, created_at DESC, id DESC)` إن لم يغطِّه الموجود. اختبار بدور `sharwa_app` الحقيقي: `UPDATE`/`DELETE` ⇒ `InsufficientPrivilege`، و`INSERT/SELECT` يعملان، و`sharwa_system` لا يقرأ الجدول.
- **`app/db/repos_consent.py`** — الكاتب الوحيد (H95): `record_optin(conn,*,tenant_id,customer_id,message_id)` (يُلحق `marketing granted` بمصدر `customer_message_optin` إن لم تكن الحالة الراهنة `granted` فعلاً، ثم يحذف صفّ `suppressions` لـ`marketing` **فقط إن كان سببه `customer_message_optout`**، ويُرجع ما فعله بمفردات مغلقة `granted|noop|granted_and_lifted`)؛ `record_optout(conn,*,tenant_id,customer_id,message_id)` (يُلحق `granted=false` للنطاقات الثلاثة بمصدر `customer_message_optout` + يُدخل `suppressions` لها كما اليوم)؛ `record_waitlist_join(conn,…,entry_id)` (يُلحق `back_in_stock granted` بمصدر `waitlist_join` **ويرفع حجب `back_in_stock` بسبب `customer_message_optout` وحده** — OQ-P3-12: هذا الافتراضي المُعتمَد، لأن الانضمام فعل صريح وإلا كان الوعد في `stock_joined` كاذباً)؛ و`read_history(conn,…)` للـCLI. انقل إليه `write_consent`/`insert_suppressions` (واحذفهما من مكانيهما أو اجعلهما غير مُصدَّرتين) وحدِّث المستدعين.
- **`policy_gate`/`repos_policy`:** `read_latest_consent` يقرأ `(granted, source)` ويُرجع True لنطاق `marketing` **فقط إن كان آخر صفّ `granted` بمصدر `customer_message_optin`**؛ سائر النطاقات كما هي. `decision.py` والطبقة النقيّة `app/policy/**` **لا تُمسّ** (الأولوية `suppressed` ثم `no_consent` قائمة).
- **S27** (بوّابة ساكنة، بإفشال متعمَّد وناتجه حرفياً): (أ) `INSERT INTO consents` وأي `INSERT/DELETE` على `suppressions` خارج `app/db/repos_consent.py` (يُستثنى `testsupport.py` بإعلان صريح كما في S24-a) ⇒ مخالفة؛ (ب) نداء دوال `repos_consent` الكاتبة خارج `app/workers/realtime.py` و`stock.py` والاختبارات ⇒ مخالفة؛ (ج) قائمة `CONSENT_SOURCES` في الكود مطابقة حرفياً للقيد في 0016.

### B — الالتقاط: الاشتراك والإيقاف
- **الإعداد:** `DEFAULT_OPTIN_AR = ("اشتراك","اشترك","اشتراك في الرسائل","فعل الرسائل","فعّل الرسائل")` و`DEFAULT_OPTIN_EN = ("subscribe","start","opt in","optin")` في `config.py` مع `CORE_OPTIN_PHRASES_AR/EN` (قابلة للضبط بلا نشر، كما STOP). **ممنوع** إضافة «نعم/موافق/ok/yes» (H96).
- **`optout.py`:** دالة `detect_optin(message,*,phrases_ar,phrases_en) -> bool` بمطابقة **المساواة الكاملة** بعد `normalize` (لا `startswith`). **STOP يغلب:** رسالة تطابق القائمتين ⇒ STOP.
- **`realtime.py`:** بعد `insert_message` (الذي يعطي `message_id`): `entry.type == "text"` فقط للاشتراك؛ STOP كما اليوم لأي نص (تعليق وسائط يوقف). ترتيب الفحص: STOP أولاً ثم الاشتراك. ينادي `repos_consent.record_optout/record_optin` **في معاملة الالتزام نفسها** (H99)، وقبل ذلك يبقى `cancel_pending_proactive` للـSTOP كما هو. إعادة تسليم الرسالة نفسها تُتجاوَز قبل هذا المسار (تأكّد أن المسار المتكرّر لا يصل إليه وأثبته باختبار).
- **`turn.py`/`templates.py`:** قالب جديد `optin_confirm` بنصّ **مقترح بانتظار اعتماد المالك** (OQ-P3-10) — اكتبه حرفياً هكذا: «تم تفعيل الرسائل الترويجية بنجاح. يمكنك إيقافها في أي وقت بإرسال كلمة «إيقاف». 🎁» — و`Decision.OPTIN_CONFIRM` بترتيب: `optout` ثم `optin` ثم kill-switch…؛ `handoff=False`. يمرّ بالمدقّق `verify` كأي قالب معتمد، ولا يُضاف إلى `POLICY_EXEMPT_TEMPLATES` (هو ردّ على رسالة واردة لا إرسال استباقي). دفعة رسائل فيها STOP واشتراك ⇒ `OPTOUT_CONFIRM` وحده. الاشتراك المتكرّر وهو مشترك ⇒ يُؤكَّد بالقالب نفسه (لا صمت) لكن بلا صفّ جديد.
- **لا تغيّر** سلوك `order_updates` (الخدمة لا تُحجب أبداً).

### C — إعادة الانضمام، CLI، المراقبة
- `stock.py`: استبدل `repos_policy.write_consent` بـ`repos_consent.record_waitlist_join` (المعاملة نفسها). اختبار: STOP ⇒ انضمام لقائمة الانتظار ⇒ رُفع حجب `back_in_stock` وبقي `marketing`/`review_request` محجوبَين ⇒ إشعار التوفّر عبر `policy_gate` يُرسَل لا `suppressed`.
- CLI: `python -m app.cli consent history --platform-ref <ref> --customer-id <uuid>` يطبع `scope · granted · source · created_at · evidence` فقط (**لا هاتف ولا نصّ** H98/H48) مرتّباً زمنياً؛ ولا أمر يكتب موافقة (لا `grant` من الـCLI: التاجر لا يمنح موافقة نيابةً عن العميل).
- المقاييس: `consent_events_total{action,source}` بمفردات مغلقة (`action ∈ granted|revoked|noop|lifted`)، بلا تسمية بمعرّف عميل/مستأجر. قاعدة تنبيه واحدة على `consent_events_total{action="revoked"}` إن قفز بمعدّل غير معتاد (عتبة بالإعداد، لا رقم مرمَّز).

### D — النهاية للنهاية والبوّابات والتوثيق
- اختبار E2E (db، حقن الزمن، بلا sleep، قالب تسويقي **مُحقَن في الاختبار فقط**): بلا موافقة ⇒ `dropped_policy/no_consent` · «اشتراك» ⇒ `sent` · «إيقاف» ⇒ `dropped_policy/suppressed` وطابور السلة يُلغى · «إيقاف» ثم «اشتراك» ⇒ `sent` من جديد · موافقة بمصدر `import` أو `checkout_optin` ⇒ `no_consent` (H95) · موافقة `back_in_stock` من الانضمام لا تسمح بتسويق (فصل النطاقات) · عميل بالرقم نفسه في مستأجر آخر لا يتأثر (RLS).
- `docs/CONSENT_POLICY.md` (جديد، قصير): الحالة = latest-wins، المصادر، القوائم، سلوك STOP/اشتراك، وما لا يفعله النظام (لا أزرار، لا موافقة ضمنية، لا استيراد).
- أبقِ **الاختبار المظلم الرسمي** (P3.2 §5.11) أخضر: لا قالب تسويقي ولا `cart_reminder` في `PROACTIVE_TEMPLATES`.

## §4 — اختبارات إلزامية (كلها موسومة `@pytest.mark.db` ما عدا 1 و12)
1. (نقي) جدول `detect_optin`: المساواة الكاملة فقط — «اشتراك» ✓ · «اشتراك الباقة كم سعرها» ✗ · «نعم» ✗ · «ok» ✗ · «Subscribe» ✓ · تشكيل/همزة مطبَّعة ✓ · «إيقاف اشتراك» ⇒ STOP لا اشتراك.
2. الالتقاط في المعاملة: اشتراك ⇒ صفّ `granted` بمصدر `customer_message_optin` و`evidence`=UUID الرسالة ولا نصّ في أي عمود · إعادة التسليم ⇒ صفّ واحد · اشتراك وهو مشترك ⇒ لا صفّ ثانٍ (`noop`).
3. STOP: ثلاثة صفوف `granted=false` + ثلاثة `suppressions` + إلغاء الطابور (الموجود) + `order_updates` لم يُحجب.
4. STOP ثم اشتراك: صفّ `granted` + حذف حجب `marketing` وحده؛ `back_in_stock` و`review_request` يبقيان؛ حجب بسبب `operator` (بذر) لا يُرفع.
5. اشتراك صورة بتعليق «اشتراك» لا يشترك؛ STOP بتعليق صورة يوقف (كما اليوم).
6. دفعة فيها STOP واشتراك ⇒ STOP وحده، وقرار الدور `OPTOUT_CONFIRM`.
7. H94 بدور `sharwa_app` الحقيقي (UPDATE/DELETE مرفوضان)؛ وقيد `source` يرفض قيمة خارج القائمة.
8. البوّابة: جدول المصدر × الحالة (§D) على `policy_gate.gate()` الحقيقي.
9. إعادة الانضمام بعد STOP (§C) وعودة إشعار التوفّر.
10. CLI: لا هاتف ولا نصّ في المخرج (افحص بحثاً نصّياً عن رقم الاختبار ونصّ الرسالة).
11. ترحيل 0016 يُطبَّق ويُعاد مرّتين بلا خطأ.
12. (نقي/AST) `repos_consent.py` لا يعرّف معامل `text`/`body`/`phone` في أي دالة (H98)؛ ونمط §5.13 من P3.2 (لا معامل غير مستعمل في `repos_*`) يغطّي الملف الجديد.
13. التزامن: 20 خيطاً، كلٌّ برسالة «اشتراك» مختلفة لعميل واحد ⇒ الحالة الراهنة `granted` وعدد الصفوف ≥1 ولا خطأ؛ وللعميل نفسه STOP/اشتراك متداخلان ⇒ لا استثناء والحالة النهائية = آخر ما التزم.
14. اختبارات الخطوة صفر (§2) + المظلم.

## §5 — بوّابات وشروط التسليم
- **S27** بإفشال متعمَّد ناتجه حرفياً لكل بند (أ/ب/ج)، وS10/S22/S23/S24/S25/S26 ما زالت خضراء.
- `python scripts/static_gate.py` **آخر أمر** قبل التقرير بآخر سطر حرفي و`rc=0`؛ `pytest tests -q` نقي بآخر سطر؛ `git status --porcelain` فارغ.
- التقرير يفصل **VERIFIED** (ناتج قاعدة ملصوق: الأمر + UTC + السطر الأخير) عن **UNVERIFIED (no db)**؛ لا ثالث. ما لم تشغّله بنفسك على قاعدة فهو `UNVERIFIED` وأشغّله أنا (`python -m app.cli migrate` **يُطبّق 0016** ثم `scripts/run_db_suite.py` مرّتين متساويتين).
- **سطر الإغلاق:** `P3.3 COMPLETE — CONSENT CAPTURED & ENFORCED, MARKETING STILL DARK. STOPPING. AWAITING AUDIT. NO P3.4 WORK STARTED.` (أو مع `— UNVERIFIED (no db)`).

## §6 — ممنوع لمسه
`app/policy/**` (طبقة القرار النقيّة) · `claim_*` · `gateway/` (لا أزرار، لا أنواع رسائل جديدة) · `docs/PHASE_GATE.md` و`docs/CONSTITUTION.md` · `PROACTIVE_TEMPLATES` (لا قالب تسويقي ولا `cart_reminder`) · نصوص القوالب المعتمدة سلفاً · لا حملات، لا واجهة تاجر، لا استيراد موافقات، لا التقاط عند الـcheckout (Django). **لا تبنِ P3.4.**

## §7 — أسئلة مفتوحة (لا تقرّرها وحدك)
- **OQ-P3-10:** نصّ `optin_confirm` أعلاه مقترح — يبقى حرفياً حتى يقرّر المالك غيره (قوالب «Owner-approved verbatim»).
- **OQ-P3-11:** الأزرار/القوائم التفاعلية للاشتراك — تحتاج نوع رسالة جديداً في الجيتواي والمخطّط؛ مؤجَّلة لمرحلة لاحقة بقرار المالك.
- **OQ-P3-12:** انضمام قائمة الانتظار يرفع حجب `back_in_stock` (افتراضي المعماري، مُنفَّذ أعلاه) — يلغيه المالك إن أراد خلافه.
- **OQ-P3-13:** قوائم عبارات الاشتراك الافتراضية (§B) تُراجَع من المالك؛ قابلة للتغيير بالبيئة.
- **OQ-P3-14:** متى يصير `checkout_optin`/`import` مصدراً صالحاً للتسويق — قرار قانوني للمالك قبل P3.5؛ اليوم محجوبان.
