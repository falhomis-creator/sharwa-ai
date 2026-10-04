# P3_04_REPORT — رفع الإظلام تحت ضبط (المعماري تولّى التنفيذ، 2026-10-04)

> GLM نفدت رموزه فنفّذ المعماري P3.4 بنفسه على PG16+Redis حقيقيين. هذا التقرير تنفيذ لا تدقيق مستقل: يستحقّ تدقيقاً مستقلاً قبل أول كاناري.

## 1. ما سُلِّم (VERIFIED على قاعدة حقيقية)
- **الخطوة صفر / F-P3-31**: اختبار إعادة الاشتراك أُكمل (تعتيق الرسائل الواردة + بوّابة بعد +5 دقائق). **F-P3-33** (جديد): إصلاح GLM ناقص — `active_chat` و`spacing` كانا يمنعان الإرسال؛ أُضيف `age_inbound_messages`.
- **A**: ترحيل 0017 (`marketing_activation` + سجلّ إلحاقي)، `repos_marketing` الكاتب الوحيد، S29 في البوّابة الساكنة (مثبتة بإخفاقات متعمَّدة).
- **B**: بابان في السياسة: `marketing_not_enabled` (إسقاط) و`canary_cap_reached` (تأجيل 15 دقيقة). الغياب = مُطفأ (fail-closed). اختبارات طفرات تثبت حساسية الترتيب.
- **C**: قالب `cart_reminder` (نصّ وتذييل **مقترحان**، OQ-P3-15) + `items_phrase` بمطابقة العدد العربية؛ مُطفأ ⇒ `Cancel("marketing_disabled")` (قابل للإحياء). E2E بالقالب الإنتاجي: رسالة واحدة بالنصّ الحرفي والتذييل عبر أربع دورات؛ والإطفاء بعد الإدراج لا يُرسل شيئاً.
- **D**: `python -m app.cli marketing status|preview|enable|disable|set-cap`؛ التفعيل بعبارة `ENABLE-MARKETING <ref>` + فحوص تمهيدية؛ الإيقاف يُسقط الطابور ويُعيد الفتحات.
- **E**: `marketing_sent_total` و`marketing_optout_after_send_total`، تنبيه `MarketingOptoutRatioHigh` (ينبّه ولا يُطفئ)، `docs/MARKETING_RUNBOOK.md` بثلاثة مستويات تراجع.

## 2. النتائج
- `run_db_suite`: `[run 1] 430 passed (295 s)` · `[run 2] 430 passed (289 s)` ⇒ **DB SUITE: STABLE**.
- نقي: 366 ناجحاً + إخفاقان في `test_import_boundaries` (انظر F-P3-32).
- `scripts/static_gate.py`: `STATIC GATE PASSED — 0 violations`.

## 3. ما لم يُفعل عمداً
لا مستأجر مُفعَّل، لا إرسال حيّ، لا نشر. «جاهزية الإرسال الحيّ» = **القدرة مُسلَّمة ومُظلَمة افتراضياً**؛ التفعيل فعل المالك.

## 4. بنود جديدة
- **F-P3-32** (قائم قبل P3.4، مفتوح): `lint-imports` يُبلغ 4 انتهاكات سابقة: `app.api.routes_carts → app.workers.carts`، و`psycopg` مستورد مباشرةً في `workers/cart_reminder.py` و`carts.py` و`proactive.py`. P3.4 لم يضف أياً منها (marketing.py وcli.py بلا psycopg). كان `lint-imports` غير مثبّت في بيئة العمل فلم يُكشف سابقاً.
- **F-P3-33** (مُغلَق هنا): انظر أعلاه.

## 5. مفتوح للمالك
OQ-P3-15 (النصّ والتذييل) · OQ-P3-16 (مستأجر الكاناري/السقف/الساعات) · OQ-P3-17 (CLI فقط) · OQ-P3-18 (مراجعة قانونية/سياسة واتساب — مسؤوليته) · OQ-P3-14 (checkout_optin/import محجوبان).
