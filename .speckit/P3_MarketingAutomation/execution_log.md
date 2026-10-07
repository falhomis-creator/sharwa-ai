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

## T5ب — قياس الحزمة الكاملة (بلا كود)

**البيئة الفعلية:** PostgreSQL 18.6 (Ubuntu 18.6-0ubuntu0.26.04.1) · PostGIS 3.6 · pgvector 0.8.1 · Python 3.13.15 (تشغيل من Windows ضد قاعدة WSL عبر localhost-forwarding إلى 5433) · Redis 6390 (`PONG`) · الترحيلات حتى 0019 (`schema_migrations`=19) · قاعدة نظيفة قبل كل جولة (tenants=0). ملاحظة: التشغيل من Windows يفسّر أخطاء `test_migrate.py` أدناه (تتطلب `sudo -u postgres psql`).

**الجولة 1 (مهلة 900s، لم تتجمّد):** `12 failed, 450 passed, 383 deselected, 9 errors in 402.31s (0:06:42)`
**الجولة 2 (مهلة 900s، لم تتجمّد):** `11 failed, 451 passed, 383 deselected, 9 errors in 426.53s (0:07:06)`

**المقارنة بين الجولتين (غير متساويتين):** الفارق اختبار واحد — `test_stock_allocation.py::test_sweep_once_with_none_commerce_allocates_nothing`: فشل في الجولة 1 (`assert 15 == 0`)، نجح في الجولة 2. أي تلوّث/ترتيب بين الاختبارات (نمط F-P2-06)، لا حتمية. مقابل خط الأساس **460** (P3_05_REPORT، PostgreSQL 16): تراجع ظاهر (450–451 ناجحاً)، والإخفاقات/الأخطاء خارج نطاق F-P3-24.

**عزل F-P2-11 (`test_burst_production_batch20_all_sent` منفرداً ×3، قاعدة نظيفة):**
```
run 1: 1 passed in 11.59s
run 2: 1 passed in 8.53s
run 3: 1 passed in 8.32s
```
**F-P2-11 لم يتكرّر (3/3 ناجح).**

**خلوّ الأثر (`test_cart_tombstone_db.py` وحده على قاعدة نظيفة):** `6 passed in 2.73s`، وبعده: tenants=0 · carts=0 · cart_tombstones=0 · scheduled_jobs=0 · outbox=0.

**تصنيف الإخفاقات (بلا إصلاح، وبلا تعديل اختبار/ترحيل):**
- **داخل نطاق F-P3-24 (سلة/تذكير/قبور):** صفر إخفاق/خطأ.
- **داخل المخزون:** 1 إخفاق غير حتمي — `test_sweep_once_with_none_commerce_allocates_nothing`؛ أول خطأ: `assert 15 == 0` (tests\test_stock_allocation.py:435).
- **خارج النطاق (تسويق/موافقة/سياسة/تقطير/سقوف، P3.4):** 11 إخفاقاً حتمياً؛ أول خطأ: `assert 0 == 7` (tests\test_drip_db.py:106، `test_drip_respects_cap_and_reprocess_is_idempotent`).
- **خارج النطاق (test_migrate.py، بيئي UNVERIFIED):** 9 أخطاء؛ أول خطأ: `subprocess.CalledProcessError: Command '['sudo', '-u', 'postgres', 'psql', '-c', 'DROP DATABASE IF EXISTS sharwa_ai_p07_migtest']' returned non-zero exit status 2.` (لا `sudo`/superuser على Windows).

**الخلاصة:** البوّابة G تبقى **جزئية**: F-P3-24 سليم على قاعدة حقيقية، لكن الحزمة الكاملة غير مستقرة (11≠12) ولا تُكافئ خط الأساس 460، والإخفاقات الـ11+9 خارج نطاق القبور/السلة/التذكير. F-P2-11 لم يتكرّر (3/3). لا تفعيل ولا إرسال حيّ.

## قرارات المالك بعد T5ب (2026-10-06)
- أخطاء `test_migrate.py` التسعة: `UNVERIFIED_ENV_LIMIT` (تتطلب `sudo -u postgres` غير المتاح من Windows).
- التذبذب: **F-P3-34** (Test State Pollution). السبب في الكود: `test_stock_allocation.py` ~السطر 435 يعدّ `SELECT count(*) FROM outbox` لكل المستأجرين. أُدخل في اختبار P2.3 نفسه (Task 8ب).
- الإخفاقات الـ11: أولوية قصوى؛ Task 13 مؤجَّلة حتى Task 21 خضراء. فرضية المعماري: قنبلة زمنية (`BASE` ثابت 2026-10-03 مقابل `now()` في SQL ضمن `repos_policy.py`). تُختبر في Task 16 قبل أي إصلاح.

## T15 — جرد الإخفاقات الـ11 (منفردة، قاعدة نظيفة، بلا كود)

كل الاختبارات الـ11 تعود `send=False` في **أول** صف (تسويق ومرافق معاً). الأخطاء الحرفية الأولى:

1. `test_drip_db.py::test_drip_respects_cap_and_reprocess_is_idempotent` ⇒ `assert 0 == 7` (سطر 106)
2. `test_frequency_cap_db.py::test_marketing_24h_cap_one_per_customer` ⇒ `assert False is True` (سطر 94)
3. `test_frequency_cap_db.py::test_utility_24h_cap_three_per_customer` ⇒ `assert [False, False, False, False] == [True, True, True, False]` (سطر 132)
4. `test_p3_consent_cli_db.py::test_rejoin_after_stop_restores_the_availability_notice` ⇒ `assert False is True` (سطر 104)
5. `test_p3_consent_e2e_db.py::test_matrix_explicit_optin_sends_then_stop_suppresses_then_reoptin_sends` ⇒ `assert False is True` (سطر 168)
6. `test_p3_consent_e2e_db.py::test_matrix_rls_same_wa_id_other_tenant_unaffected` ⇒ `assert False is True` (سطر 261)
7. `test_p3_marketing_db.py::test_enabled_with_explicit_optin_reserves_and_stays_pending` ⇒ `assert False is True` (سطر 122)
8. `test_p3_marketing_db.py::test_utility_is_unaffected_by_marketing_state` ⇒ `assert False is True` (سطر 161)
9. `test_p3_marketing_db.py::test_canary_cap_defers_then_releases_after_the_window` ⇒ `assert [False, False, False] == [True, True, False]` (سطر 172)
10. `test_p3_marketing_db.py::test_disable_drops_marketing_queue_gives_slots_back_and_leaves_utility` ⇒ `assert False is True` (سطر 247)
11. `test_policy_gate_db.py::test_gate_reserves_eligible_stock_notice` ⇒ `assert False is True` (سطر 91)

**سبب الرفض (مُستخرَج بسكربت تشخيصي خارج المستودع، دون تعديل الاختبارات):** `active_chat` (DEFER). مخرج السكربت على سيناريو الـdrip:
```
GateDecision: GateDecision(send=False, session_id=None, message_class='marketing')
outbox status/policy_reason: ('pending', 'active_chat', ...)
```

**جدول التجميع:**

| المجموعة | الاختبارات | السبب | سطر الكود |
|---|---|---|---|
| 1 (قنبلة زمنية) | الـ11 كلها | `active_chat` (DEFER) | `app/db/repos_policy.py:179` (`has_active_chat`: `m.created_at > now - make_interval(secs => cooldown_s)`). الجذر: `app/db/testsupport.py:569` (`seed_inbound_message` يزرع بـ`now() - age` الحقيقي) |

## T16 — اختبار الفرضيتين (في worktree مؤقت على fee2296)

**(أ) الانحدار؟** شغّلت الـ11 من الـworktree على `fee2296` (قبل القبور وcart_events) على قاعدة نظيفة: **`11 failed in 9.37s`** — نفس الإخفاقات الحرفية. أي **ليست انحداراً** من تغييرات الفرع.

**(ب) الساعة (القنبلة الزمنية):** غيّرت `BASE` في `test_drip_db.py` (في الـworktree فقط) من `2026-10-03` إلى `2026-10-06` (اليوم) وشغّلت `test_drip_respects_cap_and_reprocess_is_idempotent` ⇒ **`1 passed in 5.90s`**.

**الحسم:** الفرضية **مثبتة** — قنبلة زمنية: `seed_inbound_message` يزرع الرسالة بـ`now() - 2 يوم` (حقيقي)، و`has_active_chat` يقارن بـ`now` المحقون (`BASE=2026-10-03` ثابت). متى تجاوز `now()-2d` حدّ `BASE-30min` (≈ 2026-10-05) عاد `active_chat=True` خطأً. **لا إصلاح هنا — بانتظار قرار Task 17.**

## T17 — قرار المعماري (2026-10-06): F-P3-35 قنبلة زمنية في أدوات الاختبار، لا في المنتج
**التحقق:** `repos_policy.has_active_chat` يقارن `m.created_at > now - cooldown` بالـ`now` **المحقون** (لا `now()` في SQL) — المنتج متّسق. أما `testsupport.seed_inbound_message` فيزرع `created_at = now() - age` بساعة القاعدة **الحقيقية**، بينما الاختبارات تحقن `BASE = 2026-10-03`. بعد مرور ~يومين صارت الرسالة «في المستقبل» بالنسبة لـBASE، فاعتُبرت محادثة نشطة ⇒ `active_chat` DEFER لكل الـ11.
**القرار:** الإصلاح في أدوات الاختبار. **مرفوض** إضافة حدّ أعلى `m.created_at <= now` في المنتج: في الإنتاج قد تسبق ساعة القاعدة ساعة التطبيق بثوانٍ، فيُستبعد وارد حديث فعلاً ويُرسَل تسويق أثناء محادثة نشطة (إضعاف H80).
**الحل المعتمد:** `seed_inbound_message` يقبل وسيطاً صريحاً `created_at: datetime | None` (السلوك الافتراضي لا يتغير)، وتمرّر الاختبارات المتأثرة `created_at = BASE - <العمر نفسه>` فتصير حتمية ومستقلة عن تاريخ اليوم. أي استدعاء آخر يجمع `now` ثابتاً مع زرع بالساعة الحقيقية يُعالَج بالطريقة نفسها في Task 19.
**ملاحظة مفتوحة (O-P3-1، لا تُصلَح الآن):** أسطر في `repos_policy.py` (~381، ~398، ~414) تستعمل `now()` لنوافذ 24 ساعة بخلاف بقية البوّابة؛ متّسقة في الإنتاج لكنها قنابل محتملة للاختبارات. تُراجَع في Task 19.

## T18 — F-P3-35 الإصلاح في أدوات الاختبار (توقّف إجباري)

**التعديل (ملفا كود فقط، بلا كود منتج):**
- `core/app/db/testsupport.py` — `seed_inbound_message` يقبل `created_at: datetime | None = None`؛ إن مُرِّر يُدرج كما هو (عبر `%s`)، وإلا يبقى السلوك الحالي `now() - age` حرفياً.
- `core/tests/test_drip_db.py` — الاستدعاء يمرّر `created_at=BASE - timedelta(days=2)`.

**الإثبات:**
- (أ) `test_drip_db.py` كاملاً على قاعدة نظيفة: `1 passed in 5.84s`.
- (ب) فحص التحوّل: حذف `created_at` من الاستدعاء مؤقتاً ⇒ `assert 0 == 7` (1 failed in 5.69s)، ثم أُعيد وثُبت الاستعادة بـ`git diff`.
- (ج) السلوك الافتراضي لم يتغيّر: `test_cart_e2e_db.py` + `test_dispatch_db.py` ⇒ `11 passed in 5.22s`.
- (د) `python scripts/static_gate.py` ⇒ `STATIC GATE PASSED — 0 violations.` (EXIT=0)؛ `lint-imports` ⇒ **UNVERIFIED** (import-linter غير مثبَّت في بيئة التنفيذ).

**git diff --stat (كود T18):** ملفان فقط — `core/app/db/testsupport.py` و`core/tests/test_drip_db.py`.

## T19 — F-P3-35b (age_inbound_messages) + بقية الملفات الخمسة

**السبب الثاني (F-P3-35b):** `age_inbound_messages` كان يضبط `created_at = clock_timestamp() - age` بساعة القاعدة الحقيقية، بينما الاختبار يحقن `now = PNOW` ثابتاً ⇒ الرسالة المتقادمة تبقى «قريبة» من `now` المحقون ⇒ `active_chat`. الإصلاح في أدوات الاختبار (لا المنتج): أُضيف `as_of: datetime | None = None` — إن مُرِّر يُضبط `created_at = as_of - age`، وإلا يبقى `clock_timestamp() - age` حرفياً.

**التعديلات:**
- `core/app/db/testsupport.py` — `age_inbound_messages` يقبل `as_of`.
- `core/tests/test_p3_consent_e2e_db.py` — الاستدعاء يمرّر `as_of=PNOW`.
- الملفات الأربعة الأخرى كانت عُدِّلت بتمرير `created_at=<الوقت المحقون> - timedelta(days=2)` لـ`seed_inbound_message`.

**الأدلة (ملفاً ملفاً، قاعدة نظيفة):**
1. `test_frequency_cap_db.py` ⇒ `2 passed` (فحص تحوّل: حذف `created_at` ⇒ `assert False is True`).
2. `test_policy_gate_db.py` ⇒ `4 passed`.
3. `test_p3_marketing_db.py` ⇒ `31 passed`.
4. `test_p3_consent_e2e_db.py` ⇒ `6 passed` (فحص تحوّل لـ`as_of`: حذفه ⇒ `AssertionError: active_chat`).
5. `test_p3_consent_cli_db.py` ⇒ `3 passed`.

**الختام:**
- الملفات الستة معاً ⇒ `47 passed in 40.86s`.
- `python scripts/static_gate.py` ⇒ `STATIC GATE PASSED — 0 violations.` (EXIT=0).
- `git diff --stat`: 6 ملفات — `testsupport.py` + الخمسة (`test_drip_db.py` كان مُلتزَماً في T18).

**19ب (جرد بلا تعديل):** دوال `testsupport.py` ذات الطوابع الزمنية:
- بـ`now()`/`clock_timestamp()` **وبوسيط حقن**: `seed_inbound_message` (created_at ✓)، `age_inbound_messages` (as_of ✓)، `seed_number_health` (day/next_*/warmup/state ✓)، `insert_cart` (last_activity_at ✓)، `insert_scheduled_job` (run_at ✓)، `age_proactive_ledger` (reserved_at ✓)، `set_cart_last_activity` (last_activity_at ✓).
- بـ`now()`/`clock_timestamp()` **بلا وسيط حقن**: `expire_job_lease` (locked_until، ~سطر 923)، `set_cart_status` (updated_at، ~سطر 963)، `insert_cart_tombstone` (occurred_at، ~سطر 975). هذه تُقارَن بساعة القاعدة الحقيقية (مسار الـclaim/engine) لا بالـ`now` المحقون في البوّابة ⇒ **ليست قنابل من نوع F-P3-35، ولا قنابل كامنة متبقية من هذا النوع.**

## T20 — F-P3-34 (تلوّث حالة الاختبار: عدّ outbox لكل المستأجرين)

**التعديل (اختبار واحد فقط):** `core/tests/test_stock_allocation.py`، اختبار `test_sweep_once_with_none_commerce_allocates_nothing`: العدّ صار مقيّداً بالمستأجر `SELECT count(*) FROM outbox WHERE tenant_id = %s` (بمعامل `TENANT_A`) بدل العدّ على كل المستأجرين.

**الأدلة:**
- (أ) الاختبار وحده على قاعدة نظيفة ⇒ `1 passed in 1.17s`.
- (ب) مناعة التلوّث: أُدرج صفّ outbox لمستأجر ثالث (بأداة testsupport) ⇒ الاختبار `PASS` (`1 passed in 2.19s`).
- (ج) ما زال يمسك الخلل: أُدرج صفّ outbox لـ`TENANT_A` قبل التوكيد ⇒ `assert 1 == 0` (يفشل)، ثم أُعيد وثُبت الاستعادة بـ`git diff`.

## T21 — إعادة قياس الحزمة الكاملة مرّتين (بعد F-P3-35/35b/34)

**lint-imports:** ثُبِّت `import-linter 2.15` (أداة تطوير) ثم شُغِّل من core/ ⇒ `Contracts: 4 kept, 0 broken.` (Analyzed 149 files, 763 dependencies).

**الجولة 1 (قاعدة نظيفة حتى 0019، في الخلفية):** `462 passed, 383 deselected, 9 errors in 942.72s (0:15:42)`
**الجولة 2 (قاعدة نظيفة):** `462 passed, 383 deselected, 9 errors in 1064.51s (0:17:44)`

**المطلوب تحقّق:**
- صفر إخفاق (`FAILED` = 0 في الجولتين) ✅ — الإخفاقات الـ11 الحتمية (F-P3-35/35b) والتذبذب (F-P3-34) أُصلحت كلها.
- الأخطاء التسعة كلها `test_migrate.py` (UNVERIFIED_ENV_LIMIT: تتطلب `sudo -u postgres psql` غير المتاح من Windows) ✅.
- جولتان متساويتان (`462 passed, 9 errors` في كلتيهما) ✅.

**الخلاصة:** الحزمة استقرّت عند `462 passed` بلا أي إخفاق؛ شرط البوّابة G العملي تحقّق (صفر إخفاق خارج أخطاء migrate البيئية، وجولتان متساويتان). `python scripts/static_gate.py` ⇒ `STATIC GATE PASSED — 0 violations.` (EXIT=0).

## T13 — بروفة التفعيل الأول (قسم 8 من MARKETING_RUNBOOK)

**التعديل (توثيق فقط، بلا تشغيل):** أُضيف قسم `## 8. بروفة التفعيل الأول (P3 — قائمة المالك)` إلى `docs/MARKETING_RUNBOOK.md` (شروط مسبقة بمن يملكها + خطوات بروفة تُحيل إلى القسمين 2 و3 + دليل نجاح/تراجع يُحيل إلى القسمين 4 و5)، بسطر علوي صريح «هذا القسم لا يفعّل شيئاً؛ التفعيل فعل المالك وحده (H100/H104)». وحُدِّث `tasks.md`: Task 20 و21 و13 = `[x]` **VERIFIED** (أزيلت عبارة التأجيل من Task 13).

**الأسماء الحرفية (تحقّق منها في `core/app/cli.py`):** `marketing status` (378) · `marketing preview` (379) · `marketing enable` (384) · `marketing disable` (391) · `marketing set-cap` (396) · `policy status` (345). الوسائط: `--tenant-ref` · `--cap` · `--actor` · `--reason` · `--confirm` · `--channel` — كلها موجودة حرفياً.

**التحقق:**
- `python scripts/static_gate.py` ⇒ `STATIC GATE PASSED — 0 violations.` (EXIT=0).
- `git diff --stat` ⇒ ثلاثة ملفات فقط: `docs/MARKETING_RUNBOOK.md` + `tasks.md` + `execution_log.md`.

**فحص التحوّل:** لا ينطبق (توثيق، بلا كود).

## T14 — إغلاق حزمة P3 الهندسي (توثيق)

**التعديل (توثيق فقط، بلا كود):**
- `docs/PHASE_GATE.md`: سطر P3 استُبدل بـ`P3: BUILT & DARK — مُغلَق هندسياً...` (السطر 148).
- `docs/P3_SPECKIT_CLOSEOUT.md`: ملف جديد (النطاق · جدول البنود · قياس T21 · VERIFIED/UNVERIFIED · المفتوح · قرار الدمج).
- `MASTER_ROADMAP_AND_GAPS.md`: خلية P3 استُبدلت.
- `docs/SPECKIT_PROTOCOL.md`: أُضيف صف P3.
- `tasks.md`: Task 13 = VERIFIED بمراجعة المعماري، Task 14 = VERIFIED.

**التحقق:**
- `python scripts/static_gate.py` ⇒ `STATIC GATE PASSED — 0 violations.` (EXIT=0).
- `grep "P3: BUILT & DARK" docs/PHASE_GATE.md` ⇒ السطر 148؛ `P3: OPEN` = 0.
- `git diff --stat` ⇒ ستة ملفات.

**فحص التحوّل:** لا ينطبق (توثيق، بلا كود).

## T18–T21 — إعادة تحقق مستقلة وفتح البوّابة G (المعماري، 2026-10-07)
- **الحالة:** إصلاحات T18 (`f005749`)، T19 (`545eac1`)، T20 (`314715b`) كانت مُودَعة سابقاً؛ مربّعا T18/T19 في tasks.md لم يكونا مُعلَّمين — عُلِّما الآن. لا تغيير في الكود ولا الاختبارات في هذا الالتزام.
- **البيئة:** Ubuntu 24.04، PostgreSQL 16.15 + pgvector 0.6.0 + PostGIS 3.4.2، Redis على 6390، Python 3.12 بالإصدارات المثبّتة في requirements*.txt (عدا pip-audit 2.9.1 غير المتاح في الفهرس). قاعدة جديدة بأدوار ops/postgres/init/10_roles.sh والروابط الافتراضية في conftest، والترحيلات 0001→0019 عبر `python -m app.cli migrate`.
- **الجولة 1 (قاعدة نظيفة):** `471 passed, 383 deselected in 411.98s` — 0 failed، 0 errors.
- **الجولة 2 (بلا أي تنظيف بين الجولتين):** `471 passed, 383 deselected in 389.93s` — 0 failed، 0 errors.
- **test_migrate:** الأخطاء التسعة المصنّفة `UNVERIFIED_ENV_LIMIT` على ويندوز تنجح كلها على Linux (المثبّت يستعمل `sudo -u postgres psql`) ⇒ 462 + 9 = 471.
- **البوّابات الأخرى:** الحزمة النقية 381 passed؛ `-m tools` 2 passed؛ `static_gate.py` PASSED 0 violations؛ `lint-imports --no-cache` 4 kept / 0 broken.
- **الخلاصة: البوّابة G مفتوحة — 100% (471/471) في جولتين متطابقتين.** المتبقّي غير الحاجز: F-P4-02 (جعل مثبّت test_migrate يعمل على ويندوز) — قيد بيئة لا عيب.

