# P3 — إغلاق حزمة Speckit الهندسي (Marketing Automation)

**الحالة:** P3 «مبنيّ ومُظلَم، مُغلَق هندسياً» — **غير مُفعَّل ولا إرسال حيّ.** التفعيل الحي متوقف على بنود المالك.

## 1. النطاق
حزمة `.speckit/P3_MarketingAutomation/` (المهام 1–21). أُغلقت هندسياً: F-P3-24 (القبور) · F-P3-32 (نقل cart_events + psycopg) · F-P3-34 (عدّ outbox) · F-P3-35 (seed_inbound_message) · F-P3-35b (age_inbound_messages). بقي مفتوحاً: بنود المالك (G1/G2/G3) والتدقيق المرجعي على PostgreSQL 16.

## 2. جدول البنود
| البند | الوصف | الإصلاح | الدليل الحرفي | الالتزام |
|---|---|---|---|---|
| F-P3-24 | حدث نهائي قبل أول `cart.updated` يعيد فتح سلة مشتراة | قبور `cart_tombstones` (ترحيل 0019) + فحص مزدوج في المسار والتذكير | `python -m app.cli migrate` ⇒ `applied: 0019_p3_cart_tombstones`؛ `test_cart_tombstone_db`+`test_routes_carts_db`+`test_cart_reminder_db` ⇒ `31 passed in 11.92s` | 9927373 · 8ea59e4 |
| F-P3-32 | 4 انتهاكات import-linter (`routes_carts→workers.carts` + psycopg مباشر) | نقل `apply_cart_event` إلى `app.cart_events.py` + تحديث S24 + استبدال `import psycopg` بـ`Any` | `lint-imports` ⇒ `Contracts: 4 kept, 0 broken`؛ `static_gate` ⇒ `STATIC GATE PASSED — 0 violations` | 8ea59e4 (و9927373 للـpsycopg) |
| F-P3-34 | `test_sweep_once_with_none_commerce_allocates_nothing` يعدّ outbox لكل المستأجرين | العدّ مقيّد بـ`TENANT_A` | الاختبار ⇒ `1 passed in 1.17s`؛ فحص تحوّل ⇒ `assert 1 == 0` | 314715b |
| F-P3-35 | قنبلة زمنية: `seed_inbound_message` يزرع بـ`now()` الحقيقي مقابل `now` محقون | وسيط `created_at` + تمرير `BASE - timedelta(days=2)` | `test_drip_db.py` ⇒ `1 passed in 5.84s`؛ فحص تحوّل ⇒ `assert 0 == 7` | f005749 |
| F-P3-35b | قنبلة زمنية ثانية: `age_inbound_messages` بـ`clock_timestamp()` | وسيط `as_of` + تمرير `as_of=PNOW` | `test_p3_consent_e2e_db.py` ⇒ `6 passed`؛ فحص تحوّل ⇒ `active_chat` | 545eac1 |

## 3. قياس T21 الحرفي
- `lint-imports` ⇒ `Contracts: 4 kept, 0 broken.` (Analyzed 149 files, 763 dependencies).
- الجولة 1 ⇒ `462 passed, 383 deselected, 9 errors in 942.72s (0:15:42)`.
- الجولة 2 ⇒ `462 passed, 383 deselected, 9 errors in 1064.51s (0:17:44)`.
- `static_gate.py` ⇒ `STATIC GATE PASSED — 0 violations.`
- الأخطاء التسعة كلها `test_migrate.py` (UNVERIFIED_ENV_LIMIT: تتطلب `sudo -u postgres psql` غير المتاح من Windows).

## 4. VERIFIED / UNVERIFIED
- **VERIFIED (بمخرج حرفي):** كل ما سبق (F-P3-24/32/34/35/35b، T21، lint-imports، static_gate).
- **UNVERIFIED (بالسبب):** القياس على **PostgreSQL 18.6** لا PostgreSQL 16 المرجعي — دستور P3 يشترط PG16 في حاويات، والبيئة المرجعية لم تكن متاحة للمنفّذ.

## 5. المفتوح (بمالكه)
- التدقيق المستقل لـP3.4/P3.5 على PostgreSQL 16 المرجعي — **المعماري + المالك**.
- F-P2-11 (إرسال مكرر 31≠30): لم يتكرر 3/3 لكن سببه غير مشخَّص — **التشخيص** (يُحال إلى P2.4).
- O-P3-1 (`now()` في نوافذ 24س بـ`repos_policy`، أسطر ~381/398/414) — **المراجعة** (قنابل محتملة للاختبارات).
- G1: اعتماد النصّ/التذييل (OQ-P3-15)، مستأجر الكاناري/السقف/الساعات (OQ-P3-16)، المراجعة القانونية (OQ-P3-18)، checkout_optin/import (OQ-P3-14) — **المالك**.
- G2: رقم واتساب حقيقي + عميل store1 — **المالك**.
- G3: شراء غير COD لـ`cart.recovered` — **المالك**.

## 6. قرار مطلوب من المالك
دمج الفرع `p3-f-p3-24-tombstone` إلى `main` (المنفّذ لا يدمج).
