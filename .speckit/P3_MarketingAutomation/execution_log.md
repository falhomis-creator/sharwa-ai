# سجل تنفيذ P3 (يُدمج في docs عند Task 14)

## T1 [VERIFIED بالقراءة · تشغيل db UNVERIFIED_BLIND_ENV]
جرد 54 اختباراً في 5 ملفات (routes_carts 14، cart_e2e 5، cart_reminder 6، consent_e2e 5، marketing 24). غير مغطى: F-P3-24 (حدث نهائي قبل أول updated)، عميل حقيقي من محادثة واتساب، cart.recovered من شروه فعلياً. التشغيل: بيئة المنفّذ بلا psycopg ولا وصول إلى 127.0.0.1:5433.

## T2 [VERIFIED]
`lint-imports` (import-linter 2.15): Contracts: 2 kept, 2 broken — routes_carts.py:30 → app.workers.carts؛ psycopg في cart_reminder.py:23، carts.py:18، proactive.py:13. `static_gate.py` ⇒ STATIC GATE PASSED — 0 violations.

## T3 [VERIFIED على مستوى المنطق بالمحاكاة · UNVERIFIED_BLIND_ENV على PostgreSQL]
المسبار: `probes/probe_f_p3_24_mock.py` (كود `app.workers.carts.apply_cart_event` الحقيقي؛ psycopg مستبدَل؛ دوال المستودع نموذج في الذاكرة منسوخ من SQL في repos_carts.py). المخرج:
```
1) cart.recovered on unseen cart -> already_final | carts: {} | jobs: {}
2) late cart.updated               -> applied | carts: {'C1': 'open'} | jobs: {'cart:C1:stage1': 'pending'}
F-P3-24 REPRODUCED (purchased cart reopened + reminder scheduled): True
```
السبب في الكود: `finalize_cart` = `UPDATE ... WHERE status='open'` على صفّ غير موجود ⇒ 0 صفوف، لا شيء يُحفظ؛ ثم `upsert_cart_open` يُدرج سلة `open` ويُجدوَل التذكير. والجدول `carts.customer_id NOT NULL` (0015) فلا يمكن حفظ قبر في `carts` نفسه.
احتمال الحدوث واقعي: ناشر شروه يرسل كل ظرف بمهمة Celery مستقلة (`transaction.on_commit` ⇒ `.delay`) مع `autoretry` تصاعدي حتى 300 ث وتشويش، فـ`cart.updated` الأول الفاشل مؤقتاً قد يصل بعد `cart.recovered`.

## T4 [محسوم من المالك 2026-10-06]
OQ-P3-19 = (أ) قبور للأحداث النهائية + فحص مزدوج. OQ-P3-20 = اختبار «تم الشراء» بدفع غير COD (بند للمالك عند توفر عميل حقيقي).

## T5 البوّابة G [لم تُفتح — UNVERIFIED]
لا PostgreSQL مرجعي متاح للمعماري: نسخ المستودع إلى بيئة السحابة رُفض مرتين من نظام الصلاحيات (gh ثم git clone) رغم تفويض المالك؛ وبيئة الجهاز بلا صلاحية root ولا وصول إلى apt/conda (فقط PyPI)، ولا يوجد على PyPI PostgreSQL مع PostGIS (المطلوب في 0001). **كل كود المنتج أدناه مكتوب بأمر المالك صراحة قبل فتح G، وغير مُثبَت على قاعدة حقيقية.**

## T7–T9 F-P3-32 psycopg [VERIFIED ساكناً]
`import psycopg` كان لتوقيع النوع فقط (`conn: psycopg.Connection`) في الملفات الثلاثة؛ استُبدل بـ`Any` (نمط workers/stock.py المعتمد). بلا أثر تشغيلي. `lint-imports` ⇒ `Contracts: 3 kept, 1 broken` (كان 2/2). البوّابة الساكنة 0 مخالفة.

## T6 F-P3-32 routes_carts→workers.carts [موقوف: قرار معماري]
نقل `apply_cart_event` خارج app.workers يغيّر قائمة S24 (`_S24_WRITER_CALLER_ALLOWLIST` في scripts/static_gate.py) لأن الإلغاء في المعاملة نفسها (H92) يمرّ منها. البديلان: (1) نقلها إلى وحدة نطاق `app/cart_events.py` وتحديث S24 بالاسم الجديد، أو (2) استثناء مُعلَّل في `.importlinter`. لا يُنفَّذ أيهما دون قرار.

## T10–T12 القبور F-P3-24 [مكتوب · منطق VERIFIED بالمحاكاة · DB UNVERIFIED]
- ترحيل `core/migrations/0019_p3_cart_tombstones.sql`: جدول `cart_tombstones` (PK tenant+cart، CHECK الحالة، RLS صريح، `GRANT SELECT, INSERT` فقط)، و`app.purge_old_carts` يحذف القبور بنفس نافذة الاحتفاظ ويُبقي قيمة الإرجاع.
- `repos_carts.tombstone_unseen_cart` (يُكتب فقط إن لم يوجد صف سلة؛ الأول يغلب) و`cart_tombstone_status`.
- `workers/carts.py`: الحدث النهائي بلا صف ⇒ قبر؛ و`cart.updated` لسلة مقبورة ⇒ `already_final` بلا فتح ولا جدولة (المفردات المغلقة لم تتغير).
- `workers/cart_reminder.py`: بعد قفل الصف، القبر ⇒ إغلاق السلة بحالته و`Cancel("cart_closed")` (يسدّ نافذة التزامن، H89).
- `testsupport`: `delete_tenant_full` يحذف القبور، ومساعدان للاختبار.
- اختبار db جديد `core/tests/test_cart_tombstone_db.py` (6 حالات مع parametrize) — **مكتوب ولم يُشغَّل**.
مسبار بعد الإصلاح `probes/probe_f_p3_24_after_fix_mock.py`:
```
A) recovered-before-updated: already_final -> already_final | cart: None | job: None
B) reminder after concurrent tombstone: Cancel cart_closed | cart: recovered
C) normal path: applied | then recovered: applied | tombstone: None | job: cancelled
FIX HOLDS: True (A=True B=True C=True)
```
فحص التحوّل: (1) إلغاء كتابة القبر ⇒ `FIX HOLDS: False (A=False ...)`؛ (2) تعطيل فحص القبر في معالج التذكير ⇒ المعالج يتجاوز الإغلاق ويدخل مسار الإرسال بدل `Cancel`. الأصل لم يُمسّ.
`tests/test_repo_params_ast.py` ⇒ `5 passed`. البوّابة الساكنة 0 مخالفة.


## Converge المعماري (2026-10-06) — تقرير المنفّذ ذي الوصول إلى WSL
- **F-P3-32 (الخيار 1) VERIFIED:** `core/app/cart_events.py` مطابق حرفياً لـ`workers/carts.py` السابق عدا docstring (تحقّق المعماري بـdiff)؛ `routes_carts` يستورد `app.cart_events`؛ `workers/carts.py` حُذف؛ قائمة S24 حُدِّثت بالاسم الجديد (إعادة تسمية لا توسيع). تحقّق المعماري: `lint-imports` ⇒ `Contracts: 4 kept, 0 broken`؛ `static_gate.py` ⇒ 0 مخالفة. صُحِّح docstring في `repos_scheduler.py` ليشير إلى `app/cart_events.py`.
- **F-P3-24 VERIFIED على قاعدة حقيقية (PostgreSQL 18.6، WSL):** `python -m app.cli migrate` ⇒ `applied: 0019_p3_cart_tombstones`؛ ملفات القبور والسلة والتذكير ⇒ `31 passed in 11.92s` (مخرج المنفّذ).
- **ما بقي من البوّابة G:** قياس الحزمة الكاملة مرّتين متساويتين، والبيئة PG18 لا PG16 المرجعية. يُسجَّل «مفتوحة جزئياً».
