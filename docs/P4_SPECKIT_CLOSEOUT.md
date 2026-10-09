# P4 — إغلاق حزمة Speckit (الربط والذكاء المتخصص)

**الحالة (2026-10-09):** P4 **مبنيّ ومُغلَق هندسياً، ومنشور جزئياً على الـVPS** (`ai.sharwaah.com` خلف Caddy + Let's Encrypt). مستشار المقاسات **مُفعَّل افتراضياً**؛ منسّق الهدايا **مُظلَم** (`GIFT_ENABLED=false`) حتى ينفّذ شروه مسار `/checkout/gift/{id}`؛ البحث الدلالي جاهز بمزوّد OpenAI لكنه يعمل بـ`local` حتى يُضاف المفتاح. لا تسويق مُفعَّل ولا مستأجر حيّ مُفعَّل (المادة 11 من دستور P4).

## 1. النطاق
حزمة `.speckit/P4_Links_and_AI_Integration/` — المهام 1–20 (+6ب، 18ب). الفرع `p4-links-ai`.

| المحور | ما بُني | المهام | الالتزامات الرئيسية |
|---|---|---|---|
| الروابط الآمنة | توقيع HMAC لكل طلب تجارة، `COMMERCE_API_SECRET` إلزامي، حارس صفحة `/changes`، عقد المنصة (4 مسارات + §4.5 سلة الهدية)، تنفيذ جانب شروه، استقبال SSO في اللوحة | 1–7، 6ب | b67d95e · 7295468 · b3c3006 · 7f1fcde · 0f5c8ba |
| مستشار المقاسات | الخوارزمية النقية §5.1، مستودع الجداول تحت RLS، الأداة والمستخرج، قاعدة size_mismatch في الـVerifier، التوصيل بالدور، اختبارات ذهبية وخاصية | 8–11، 18ب، 9 | ad58dbd · ae13606 · 19da943 · 309971f · 7e80cbd · 0f94936 · 3d67ef2 · (Task 9 غير ملتزم) |
| منسّق الهدايا | الحلّال النقي المحدود (MAX_STEPS)، اختبارات الحدود، التوصيل بالكتالوج + `gift_carts` (0021) + رابط الدفع + مسار القراءة الموقّع | 12–14 | ecadef6 · 9c51250 · 8810d04 |
| البحث الدلالي | محوّل OpenAI (`text-embedding-3-small`، 1024)، متجهات مقيّدة بالنموذج (0020)، المجموعة الذهبية، حدّ أدنى للصلة | 15–16 | 509a0b9 · 8810d04 |
| البنية الدائمة | Caddy/nginx، دليل الدومين/TLS، النشر على الـVPS بالنطاق `ai.sharwaah.com` | 17–18 | d79e2ec · d89f561 |

## 2. جدول البنود (Findings)
| البند | الوصف | الحالة |
|---|---|---|
| F-P4-01 | `/snapshot` بلا ندّاء؛ `/changes` بلا `since` = الكتالوج كاملاً | محسوم في العقد |
| F-P4-01-DB | سكربت تشغيل Windows لم يضبط متغيّرات القاعدة الثلاثة | محسوم |
| F-P4-02 | `test_migrate` (9) تحتاج `sudo -u postgres` غير المتاح على Windows | قيد بيئي معلَن (تنجح على Linux) |
| F-P4-03 | قصّ أحداث `/changes` مع حفظ المؤشر ⇒ فقدان صامت | مُصلَح (حارس الصفحة) |
| F-P4-04 | مستأجر يتيم (Burst Tenant) في قاعدة الاختبار | مُصلَح بالحذف |
| F-P4-05 | (مشروط بزمن الحلّال > 200ms) | لم يقع (الوسيط 109ms Windows) |
| F-P4-06 | توقّع خاطئ في فحص تحوّل (3 لا 2) — خطأ المعماري | مُصحَّح في السجل |
| F-P4-07 | ذاكرة المنتجات المعروضة لا تُكتب أبداً | مُصلَح 9c6b2f9 |
| F-P4-08 | `update_summary` يفشل دائماً (معاملات ناقصة) | مُصلَح 50bdae0 |
| F-P4-09 | كل دور يمرّ بالموجّه ينهار في `_account` | مُصلَح f7eb5f1 |
| F-P4-10 | كلمة «مقاس» تخطف بحث المنتجات | مُصلَح 0f94936 |
| F-P4-11 | compose لا يمرّر أصول اللوحة ⇒ WebSocket/SSO معطّلان عبر النطاق | مُصلَح d79e2ec |
| F-P4-12 | المستودع يذكر `api.sharwa.app` بعد النشر على `ai.sharwaah.com` | مُصلَح d89f561 |
| F-P4-13 | نموذج التضمين غير مسجّل في أي شيء ⇒ خلط فضاءات المتجهات | مُصلَح 509a0b9 (ترحيل 0020) |
| F-P4-14 | أسعار DeepSeek أقل بـ1000× ⇒ الميزانية لا تحمي | مُصلَح 509a0b9 (قرار المالك) |
| F-P4-15 | البحث المتجهي بلا حدّ صلة ⇒ 8 بطاقات عشوائية لمنتج غير موجود | مُصلَح 8810d04 |
| F-P4-16 | «أقرب مقاس» غير رتيب: 40 كغ/176 سم ⇒ M بينما 47 كغ ⇒ S | مُصلَح في Task 9 (غير ملتزم) |

## 3. قياس الإغلاق (Task 19، حرفياً، Linux PostgreSQL 16 في الحاوية)
```
run_db_suite.py:
[run 1] 521 passed, 1 skipped, 686 deselected, 2 warnings in 269.78s (0:04:29)
[run 2] 521 passed, 1 skipped, 686 deselected, 2 warnings in 272.22s (0:04:32)
DB SUITE: STABLE (521 passed twice)
pure run 1: 682 passed, 526 deselected in 6.68s
pure run 2: 682 passed, 526 deselected in 6.41s
-m tools: 4 passed
node --test tests/js/console_sso.test.mjs: pass 9, fail 0
static_gate.py -> STATIC GATE PASSED — 0 violations.
check_env.py -> ENV CHECK PASSED — 0 required keys missing.
lint-imports -> Contracts: 8 kept, 0 broken.
ruff -> Found 302 errors (الأساس، دون زيادة) ; mypy app -> Found 103 errors (الأساس)
tenants بعد الحزمة = 0
```
المرجع القديم في tasks.md («لا انحدار عن 471») تجاوزته الحزمة: 521 (db) — الزيادة كلها اختبارات P4 جديدة، ولا اختبار سابق حُذف. التخطّي الوحيد: `test_golden_set_live_openai` (يحتاج `OPENAI_API_KEY`).

## 4. VERIFIED / UNVERIFIED
- **VERIFIED (بمخرج حرفي):** كل ما في §3؛ النشر على الـVPS بتقرير المالك (Caddy + شهادة، `check_proxy.sh` 21/0)؛ فحوص التحوّل لكل حارس جديد (مسجّلة في execution_log).
- **UNVERIFIED (بالسبب):**
  - PostgreSQL 18 على جهاز المالك (المتوقع: نفس الأعداد + 9 أخطاء test_migrate، F-P4-02).
  - قياس OpenAI الحي للعربية ومعايرة `SEARCH_VECTOR_MAX_DISTANCE` — لا مفتاح في بيئة المنفّذ.
  - الترحيلان 0020/0021 وصورة الـapi/العامل الجديدة على الـVPS، وقائمة Caddy المحدّثة (`/webhooks/platform/gift-cart`).
  - مسار شروه `/checkout/gift/{id}` (خارج المستودع).

## 5. المفتوح (بمالكه)
- **المالك (الخادم):** `git fetch && git merge` ثم `docker compose build api`، `scripts/migrate.sh` (0020 + 0021)، إعادة تشغيل api والعامل، نسخ `ops/proxy/Caddyfile` وإعادة تحميل Caddy؛ تأكيد `CONSOLE_ALLOWED_ORIGINS=https://ai.sharwaah.com`؛ روابط webhook في شروه + إغلاق النفق؛ إعادة تشغيل الخادم (تحديثات النواة).
- **المالك (البحث):** إضافة `OPENAI_API_KEY` + `EMBEDDING_PROVIDER=openai` (الدليل §8.6)، تشغيل الاختبار الحي، ضبط `SEARCH_VECTOR_MAX_DISTANCE` من مخرجه.
- **فريق شروه:** صفحة `/checkout/gift/{id}` (العقد §4.5)، ثم `GIFT_ENABLED=true`؛ حجز أسماء المتاجر `ai` و`api` (OQ-P4-22)؛ JWKS للدخول الموحد.
- **قرارات مفتوحة:** OQ-P4-10 (رتابة أبعاد الجسم في التحقق) · OQ-P4-11 (فجوة بين مقاسين) · OQ-P4-18 (عمود allow_under_weight) · OQ-P4-19/20 (ثوابت تقييم الهدايا) — افتراضات مكتوبة سارية.
- **ممنوع:** التخطيط لـP5/P6 قبل تصفية النواقص التشغيلية في P2/P3/P4 (MASTER_ROADMAP_AND_GAPS.md).

## 6. قرار مطلوب من المالك
اعتماد Task 9 (بما فيه إصلاح F-P4-16 في `app/fit/size_advisor.py`) ووثائق الإغلاق للالتزام، ثم دمج `p4-links-ai` إلى `main` (المنفّذ لا يدمج).
