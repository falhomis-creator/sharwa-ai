# PROMPT P3.1 — دفعة المتابعة: جولة إصلاح (0′) ثم المراحل D–G
المصدر الملزِم: `docs/P3_01_BC_AUDIT.md` (التدقيق على قاعدة حقيقية) + `PROMPT_P3_01_sendpolicy_for_deepseek.md` §2–§9 (ما زال سارياً كما هو إلا ما يُعدِّله هذا النص).

## §0 — ما حدث وما المطلوب
B وC (طبقة القرار النقيّة ومخطّط 0013 وأرقام التقطير) **مقبولة**. لكن تدقيقي على قاعدة حقيقية وجد أن **الحجز لا يعمل** (F-P3-10) وأن **الـdispatcher ما زال ميّتاً** (F-P3-11/12/13 — F-P1-12 يعود مفتوحاً)، وأن البوّابة الساكنة تفشل على المستودع الحيّ (F-P3-14)، وأن اختباراً واحداً من إصلاحك لـF-P2-09 يخالف مخرج الـCLI (F-P3-15). كلها من الصنف نفسه: **كود لم يُشغَّل على قاعدة**.

قاعدة هذه الدفعة، وهي ملزِمة: **لا بند يُوسَم `fixed` إن لم يُلصَق معه ناتج أمر على قاعدة حقيقية (الأمر + الوقت UTC + السطر الأخير حرفياً). ما لم تستطع تشغيله يُوسَم `UNVERIFIED (no db)` في التقرير — ولا يُحسب إنجازاً.** صدقك في التسمية أهمّ عندي من اكتمال القائمة.

المسار حسب بيئتك:
- **إن كانت عندك قاعدة PG16 + pgvector + PostGIS + Redis (6390 بكلمة `test-redis-pw`)**: نفِّذ §1 ثم §2 كاملةً وسلِّم.
- **إن لم تكن**: نفِّذ §1 وحده (الإصلاحات حرفية ومُجرَّبة عندي)، التزم، **وقِف** بالسطر: `P3.1 FIX ROUND COMPLETE — UNVERIFIED (no db). STOPPING. OWNER: RUN scripts/run_db_suite.py.` ولا تبدأ D–G. المالك يشغّل الحزمة ويلصق الناتج ثم تُستأنف D–G.

## §1 — الجولة 0′ (إصلاحات حرفية)

### 1.1 F-P3-10 — `app.reserve_send_slot` (ترحيل جديد `0014_p3_fix_reserve.sql`)
لا تُعدِّل 0013 (مُطبَّق عند من شغّله). `CREATE OR REPLACE` للدالة نفسها بالتوقيع نفسه، مع السطر `#variable_conflict use_column` كأول شيء بعد `AS $$` (قبل `DECLARE`). **لا تغيّر أسماء أعمدة الإخراج** (بايثون سيقرأها بالاسم). أعد `REVOKE/GRANT` كما في 0013.
اختبار `core/tests/test_send_slot_db.py` (`@pytest.mark.db`) يعيد مساباتي بدور `sharwa_app` عبر `tenant_tx`: حجز أول ⇒ `reserved` · ثانٍ فوراً ⇒ `spacing` بـ`defer_until` مستقبلي · بلوغ السقف ⇒ `cap_reached` بـ`defer_until` = منتصف الليل المحلي التالي · المرافق مستقل عن التسويق · `paused` يمنع التسويق فقط · `release_send_slot` ينقص صنفه ولا يهبط تحت الصفر · انقلاب اليوم يُصفِّر · مستأجر آخر ⇒ `no_health_row` · دور `sharwa_system` ⇒ `permission denied` · **سباق 20 خيطاً (`threading` بـ`tenant_tx` لكلٍّ) بسقف 7 وفجوة 0 ⇒ `reserved` = 7 بالضبط** وسباق بفجوة 60 ⇒ 1 بالضبط. بلا `sleep`؛ الزمن بحقن `p_now`.

### 1.2 F-P3-11 / F-P3-12 / F-P3-13 — الـdispatcher (F-P1-12 يُغلَق فعلاً)
1. `repos_outbox.claim_outbox`: `conn.cursor(row_factory=dict_row).execute(sql, params).fetchall()` — ليس `conn.execute(..., row_factory=...)`.
2. ترحيل `0014` يُعيد تعريف `app.claim_outbox(integer, interval, integer)` بالتوقيع نفسه بعد لفّ الاتحاد: `picked AS ( SELECT * FROM ( SELECT * FROM due WHERE message_class <> 'marketing' UNION ALL SELECT * FROM marketing ) u ORDER BY (message_class = 'marketing'), next_attempt_at LIMIT p_limit )`. (أعِد `REVOKE/GRANT`.)
3. `testsupport.seed_suppression`: أضِف `reason` (`'test'`) إلى الـINSERT.
4. **شرط القبول (حرفي):** `test_dispatch_db.py` الخمسة تمرّ، وأضِف اختباراً يُرسِل بـ`dispatch_cycle` صفّاً حقيقياً من كل `origin` (`bot`/`human`/`automation`) على قاعدة حقيقية ويُظهر `status='sent'` لكلٍّ (عميل الجيتواي مزيَّف، القاعدة حقيقية).

### 1.3 F-P3-14 — البوّابة الساكنة
- استثنِ `scripts/static_gate.py` من S20 صراحةً (أو ابنِ التعبير النمطي بحيث لا يطابق سطر تعريفه)، ووحِّد المسارات بـ`as_posix()` في كل شرط `startswith("core/…"/"scripts/…")` عبر البوّابة كلها (ابحث عن كل `relative_to(ROOT)` + `startswith`).
- إثبات بإفشال متعمَّد: حقن `DELETE FROM tenants` في `scripts/x.py` مؤقتاً ⇒ يُمسَك؛ ثم الإزالة ⇒ صفر. واكتب الناتج الأخير من `python scripts/static_gate.py` حرفياً مع رمز الخروج `rc=0`.

### 1.4 F-P3-15 — `test_migrate`
عدِّل المقارنة لتشتق النص من `", ".join(names)` أو لتحلّل مخرج الـCLI إلى قائمة — لا تفترض فاصلاً. شغّل `test_migrate.py` على قاعدة.

### 1.5 ملاحظات صغيرة (مُلزِمة)
- `config.py`: ارفض `SEND_POLICY_QUIET_START == SEND_POLICY_QUIET_END` (ConfigError).
- `scripts/run_db_suite.py`: ضمّن في رسالة التحقق المسبق أن الحزمة تحتاج Redis على `127.0.0.1:6390` بكلمة `test-redis-pw`، واطبع ذلك عند الفشل.
- التزام git مستقل لـ0′: `fix(p3.1): …` ولا يختلط بـD–G.

## §2 — المراحل D–G (كما في البرومبت الأصلي §3، مع هذه التعديلات)
**D** (`proactive.py` + كتالوج القوالب + نقل إشعار المخزون إلى البوّابة + كتابة الموافقة عند الانضمام لقائمة الانتظار + STOP يُلغي الطابور) و**E** (`policy_gate.py` + `repos_policy.py` + دمج الـdispatcher) و**F** (المكنسة + CLI + المقاييس + التنبيهات + الإعداد) و**G** (اختبار التقطير الشامل): النص الملزِم في البرومبت الأصلي §3 D–G و§5 و§6. تعديلات هذه الدفعة:

1. **E لا تبدأ** قبل أن يمرّ شرط 1.2-4 على قاعدة حقيقية. لا سطر في `policy_gate.py` فوق dispatcher ميّت.
2. **كل استدعاء SQL جديد** (في `repos_policy.py` وغيره) يُختبَر على قاعدة بدور الاستدعاء الحقيقي — H86. ولا `conn.execute(..., row_factory=…)` أبداً: ابحث بـ`grep` وأرفق الناتج.
3. **F — المكنسة** (من التدقيق، N-2/N-3، ملزِم):
   - هي من تُنشئ صفّ `number_health` لكل قناة `ai_core`/`whatsapp_baileys` (`INSERT … ON CONFLICT DO NOTHING`) وإلا بقيت `reserve_send_slot` تُعيد `no_health_row` للأبد. تعمل بـ`policy_sweep_targets()` ثم `tenant_tx` لكل هدف.
   - **ارتفاع بطيء:** لا خروج من `throttled` إلى `healthy` قبل مرور `SEND_POLICY_THROTTLE_COOLDOWN_H` (افتراضي 24) منذ `state_changed_at`، ولا رفع `daily_cap` درجةً إلا بعد يوم محلي كامل، و`paused` لا يُرفع إلا بـ`policy reinstate` (H81). اختبار حتمي بحقن الزمن.
   - عند `throttled` يُضرَب السقف بـ`THROTTLE_CAP_FACTOR` والفجوة بـ`GAP_FACTOR` (القيم في `config.py`).
4. **G — الاختبار الشامل**: على قاعدة حقيقية بلا `sleep`، 60 عميلاً مُوافقاً بقالب تسويقي تجريبي (حقن الكتالوج في الاختبار فقط — لا قالب تسويقي في الإنتاج: «مُوصَّل لكن مُظلَم»): عدد المسلَّم ≤ سقف اليوم، والحدّ الأدنى للفجوة بين أي رسالتين تسويقيتين من الرقم ≥ `GAP_MIN`، والخدمة لا تتأخّر خلف البثّ (H82)، وإعادة تشغيل الدورة لا تستهلك فتحة ثانية للصف نفسه (H85).
5. **S22/S23** كما في §6 الأصلي، كلٌّ بإفشال متعمَّد.

## §3 — شروط التسليم
- التقرير يفصل صراحةً **VERIFIED (ناتج قاعدة ملصوق)** عن **UNVERIFIED (no db)**؛ لا ثالث.
- كل رقم بأمره ووقته. لا «متوقَّع». لا تقدير.
- `python scripts/static_gate.py` بآخر سطر حرفي ورمز الخروج.
- `git status --porcelain` فارغ. التزام لـ0′ وآخر لـD–G (أو لكل مرحلة).
- **سطر الإغلاق (إن اكتملت D–G وتحقّقت):** `P3.1 COMPLETE — PROACTIVE SENDS GATED, DRIP ENFORCED. STOPPING. AWAITING AUDIT. NO P3.2 WORK STARTED.`
- لا تلمس ما لا يخصّ هذه الدفعة (§8 الأصلي) ولا تبنِ `campaigns` أو السلال المتروكة أو التقاط الموافقة العام — هذه P3.2+.
