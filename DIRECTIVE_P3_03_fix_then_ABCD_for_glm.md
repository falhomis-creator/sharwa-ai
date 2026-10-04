# توجيه معماري ملزِم — F-P3-25 ثم P3.3 المراحل A→D (إلى GLM)

**الحكم على الخطوة صفر (05d81d7): مرفوضة كما سُلِّمت، والإصلاح سطر واحد.** شغّلتُها على قاعدة حقيقية فسقطت **18 اختباراً** (`18 failed, 344 passed`، مرّتين) كلها بسبب واحد: `REVIVABLE_CANCEL_REASONS` **tuple** يُمرَّر لـ`= ANY(%s)` فيُكيِّفه psycopg سجلّاً لا مصفوفة — `malformed array literal: "(too_late,template_not_registered,no_conversation)"`. أثره الحيّ: كل `cart.updated` على الويبهوك ⇒ 500. التفاصيل والأرقام في `docs/P3_03_STEP0_AUDIT.md`. بعد رقعتي في نسختي ⇒ `362 passed` مرّتين (`DB SUITE: STABLE`)، فتصميم F-P3-22/23 سليم.

## الأمر 1 — التزام مستقل `fix(p3.2-0): F-P3-25 tuple SQL parameter adapted as record`
1. في `repos_scheduler.schedule` مرِّر `list(REVIVABLE_CANCEL_REASONS)` لا الـtuple. **لا تغيّر شيئاً آخر** في هذا الإصلاح (الثابت يبقى tuple).
2. **S28** (بوّابة ساكنة، بإفشال متعمَّد وناتجه حرفياً ثم الإزالة ورمز `rc=0`): في `app/db/repos_*.py` أي نداء `.execute(...)` يكون أحد عناصر معاملاته اسماً/سمة تشير إلى **ثابت على مستوى الوحدة** قيمته tuple أو set أو frozenset (أو literal tuple داخل tuple المعاملات) ⇒ `[S28] sequence SQL parameter must be list(...) (psycopg adapts a tuple as a record)`. يجب أن تُمسَك النسخة المعطوبة الأصلية من `schedule` (اختبرها بإعادة السطر القديم مؤقتاً).
3. تُضاف قاعدة إلى `docs/CONSTITUTION.md`؟ **لا** — H86 تغطيها وقد عمّمتُها في التدقيق؛ لا تلمس الدستور.

## الأمر 2 — ثم فوراً P3.3 المراحل A→D كما في `PROMPT_P3_03_for_glm.md`
(الخطوة صفر §2 منجزة بهذا الإصلاح، فلا تُعِدها.) نفّذ **A ثم B ثم C ثم D بحذافيرها**، التزام مستقل لكل مرحلة، ولا مرحلة فوق سابقة لم تمرّ ببوّابتك ونقيّك. القواعد كما هي:
- لا بند «fixed/verified» بلا ناتج قاعدة (الأمر + UTC + السطر الأخير)، وإلا `UNVERIFIED (no db)` وأشغّله أنا. **لأن العيب أعلاه هو بالضبط ما تُخفيه غياب القاعدة:** كل SQL جديد تكتبه (ترحيل 0016، `repos_consent`، حذف الحجب، قراءة `(granted, source)`) راجِع معاملاته بنفسك: أي تسلسل ⇒ `list(...)`، وأي `RETURNS TABLE` ⇒ `#variable_conflict use_column`، وأي `UNION … ORDER BY` ⇒ استعلام فرعي، ولا `conn.execute(..., row_factory=)`.
- لا قالب تسويقي ولا `cart_reminder` في `PROACTIVE_TEMPLATES`؛ ممنوع لمس `app/policy/**` و`claim_*` و`gateway/` و`PHASE_GATE`/`CONSTITUTION`؛ لا تبنِ P3.4.
- **قرارات المالك الجارية:** `optin_confirm` بالنصّ المقترح حرفياً (OQ-P3-10)، لا أزرار (OQ-P3-11)، انضمام قائمة الانتظار يرفع حجب `back_in_stock` (OQ-P3-12).
- البوّابة الساكنة آخر أمر قبل التقرير بآخر سطر حرفي و`rc=0`؛ `pytest tests -q` نقي؛ `git status --porcelain` فارغ.

**سطر الإغلاق:** `P3.3 COMPLETE — CONSENT CAPTURED & ENFORCED, MARKETING STILL DARK. STOPPING. AWAITING AUDIT. NO P3.4 WORK STARTED.` (أو مع `— UNVERIFIED (no db)`). عند التسليم أشغّل: `python -m app.cli migrate` (يُطبّق 0016) ثم `scripts/run_db_suite.py` مرّتين.
