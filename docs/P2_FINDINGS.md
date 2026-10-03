# نتائج P2.1 — التعبئة والإقلاع (Docker & Bootstrap)

**المنفّذ:** DeepSeek (داخل Cline) · **التاريخ:** 2026-09-28
**نطاق هذه الدفعة:** إقلاع ما بُني في P1 لأول مرّة على بنية حقيقية.

---

## 0. قيد بيئي صريح (لا إخفاء له)

لم يُنفَّذ **§4 (أول تشغيل حقيقي)** في هذه البيئة: **Docker غير مثبَّت** على جهاز
التنفيذ (`docker: command not found`). لذلك فإن ما يلي **لم يُشغَّل**، وكلّه بانتظار
بيئة فيها Docker، ولا يُدَّعى العكس:

- `docker compose up` و`docker compose ps` (لا مخرج حالة خدمة).
- `python -m pytest tests -q -m db` (الترحيلات `0003`–`0011` لم تلمس قاعدة حقيقية).
- إثباتات §4.3 الثلاثة (عزل التينانت، `prepare_threshold=None` مع PgBouncer، عدم ظهور الأسرار).
- بناء الصورة واختبار `core/entrypoint.sh` و`scripts/migrate.sh` فعلياً.

كل أعمال الكود والبوابة أدناه **منفَّذة ومُثبَتة** على الجهاز (بوابة 0 مخالفة،
241 اختباراً نقيّاً)، والبنية التحتية (compose/Dockerfile/entrypoint/migrate)
مُوفَّقة وجاهزة، ولم يُدَّع تشغيلها.

---

## 1. F-P1-11 [حرج، مُصلَح] — عودة اللوحة إلى الحياة

`core/app/db/repos_inbox.py::resolve_staff_member` كانت تنتهي عند
`if row is None: return None` بلا إرجاع لحالة الوجود، فتُرجع `None` ضمنياً حتى حين
يوجد صفّ الموظف ⇒ `STAFF_NOT_PROVISIONED` (403) لكل موظف. **الإصلاح:** سطر واحد
`return StaffRow(id=row[0], role=row[1], active=row[2])` في نهاية الدالة، وحذف السطر
الميّت الذي جلس بعد `return` نهائية في `fetch_customer_card`. أُضيفت ثلاثة اختبارات
نقية (`core/tests/test_staff_resolution.py`).

## 2. F-P2-01 [مُكتشَف بواسطة S14، مُصلَح] — سطر ميّت ثانٍ من نفس العائلة

حارس **S14-b** (الذي بُني ليمسك F-P1-11) كشف سطراً ميّتاً **ثانياً** من نفس العائلة
في `core/app/api/routes_inbox.py:482`: سطر `return _assign(conversation_id,
body.version, body.staff_id, staff)` منزاحاً بعد `return` نهائية في
`list_inbox_events`، ويشير إلى أسماء غير معرَّفة في نطاق الدالة. حُذف. هذا دليل أن
العائلة لم تكن حالة معزولة، وأن الحارس يسأل السؤال الصحيح.

| # | الاختبار/الموضع | الخطأ | ترجيح السبب |
|---|---|---|---|
| F-P2-01 | `routes_inbox.py:482` | `[S14] unreachable statement` | نسخ/لصق من مسار `assign` إلى نهاية `list_inbox_events` |
| (قيد الانتظار) | `-m db` | لم يُشغَّل (لا Docker) | ترحيلات `0003`–`0011` لم تلمس قاعدة حقيقية بعد |

## 3. دَيْن مؤجَّل (لا بند منفَّذ)

- **الفصل الرباعي للعمّال** (`realtime`/`bulk`/`batch`/`scheduler` في
  `00_ARCHITECTURE.md` §6): الكود اليوم يملك نقطة دخول عامل واحدة
  (`realtime.py::run`)، و`compose` يعرّف `worker-realtime` وحدها. **دَيْن مؤجَّل**،
  لم تُخترَع حاويات لكود له نقطة دخول واحدة (OQ-P2-02).
- **ذاكرة العامل الواحد** (`worker-realtime` 512m): يحمل اليوم عمل ثلاث حاويات
  مخطَّطة. **لم يُغيَّر `mem_limit`**؛ يُقترَح أن تُراجَع الذروة الفعلية بـ
  `docker stats` عند أول تشغيل حقيقي (OQ-P2-03).

## 4. ما تمّ من البنية التحتية (جاهز، لم يُشغَّل)

- `docker-compose.yml`: أُضيف `PLATFORM_WEBHOOK_SECRET` (api) و
  `ORDER_REF_HASH_KEY` (worker) عبر `${...:?}`، ولم يُغيَّر أي حدّ موارد.
- `.env.example`: أُضيف `ORDER_REF_HASH_KEY` مع تعليق شرط الاستقرار.
- `core/Dockerfile`: أُضيف `bash` (للـentrypoint) و`COPY entrypoint.sh` +
  `ENTRYPOINT`/`CMD` (الدور). لم يُغيَّر منطق البناء ولا الصورة الأساس.
- `core/entrypoint.sh`: انتظار أُسّي لـPG (عبر PgBouncer) ثم redis، ثم **تحقّق** من
  الترحيلات دون تطبيقها، ثم `exec` أمر الدور.
- `scripts/migrate.sh`: يطبّق `python -m app.cli migrate` داخل `api` بـ`run --rm`
  ويطبع القائمتين (قبل/بعد)، ويميّز صراحةً `--adopt-existing-schema 0001_baseline`
  لقاعدة الـVPS القائمة عن قاعدة محلية جديدة فارغة.
- `scripts/check_env.py` + **S15**: جرد AST كامل للمفاتيح الإلزامية/الاختيارية.
- **S14** (a/b) + **S15** في `scripts/static_gate.py`، مُثبَتتان بإفشال متعمَّد.
- `docs/CONSTITUTION.md`: أُضيفت H60–H63.

## 5. حصيلة البوابة والحزمة

- `scripts/static_gate.py` ⇒ `STATIC GATE PASSED — 0 violations`.
- `python -m pytest tests -q` ⇒ `241 passed, 249 deselected` (النقية، بصفر أحمر؛ 238 + 3).

---

# نتائج P2.3 — وكيل الحجز والتخصيص الذري (Back-In-Stock)

**المنفّذ:** DeepSeek (داخل Cline) · **التاريخ:** 2026-10-03

## 0. الخطوة صفر (إصلاحات P2.2 المؤجَّلة)

- **F-P2-03 [مُصلَح]:** `routes_channels` صار مُركَّباً في `app/main.py` (استيراد +
  `include_router`). **S18** الجديدة تحرسه: كل وحدة تحت `app/api/` تعرّف `router`
  يجب أن تكون مستوردة ومُمرَّرة إلى `include_router` في `main.py` — إزالة التركيب
  مؤقتاً ⇒ مخالفة.
- **F-P2-04 [مُصلَح]:** `search_gazetteer` يحسب الآن `ST_Y(ST_Centroid(geom))` و
  `ST_X(ST_Centroid(geom))`، و`AddressCandidate` يحمل `lat/lng`، و
  `resolve_and_persist` يكتب النقطة الحقيقية للـ`gazetteer_centroid` المقبول.
- **F-P2-05 [مُصلَح]:** `ST_GeomFromText` يستقبل الآن WKT نقيّاً (بلا `SRID=`) بلا
  تحذير.
- **N1 [مُصلَح]:** `ORDER BY sort_key, id` حتميّ في `search_gazetteer`.
- **N2 [مُصلَح]:** `AddressDecision.__post_init__` يفرض القوائم المغلقة، وثقة
  الـgazetteer مسقوفة عند `0.99` دون 1.0.
- **S13 [مُصلَح]:** تقارن `console_api.lock.json` بوثيقة OpenAPI للتطبيق **المبنيّ**
  (`create_app().openapi()["paths"]`)، مع بند موسوم يتخطّى عند غياب الإعداد.
- **S17-c [مُوسَّع]:** يفحص `insert_address_resolution` **و** `persist_decision`.
- **الاختبارات الثلاثة الموسومة من P2.2** كُتبت في `core/tests/test_geo_db.py`.

## 1. OQ-P2-08 — قيد وحدانية `waitlist_entries` (مُسجَّل للقرار، لم يُنشأ)

أمر المعماري بمنع التكرار برمجياً (Application Level) وبتسجيل القيد كبند قرار
دون إنشاء ترحيل. **مُلاحظة أمانة:** `0001_baseline.sql` يحمل بالفعل فهرساً جزئياً
وحيداً `waitlist_active_uq ON waitlist_entries (tenant_id, customer_id,
platform_variant_id) WHERE status IN ('waiting','held')` — أي أن قاعدة البيانات
تمنع الصفّ الحيّ الثاني فعلاً. نفّذتُ فوقه **قراءة قبل الكتابة في المعاملة نفسها**
(`repos_stock.has_active_waitlist` ثم `insert_waitlist_entry`) كي يكون الرفض رشيقاً
(تسجيل مقياس `duplicate`) لا انفجار `UniqueViolation`. القرار بشأن قيد وحدانية أكمل
(غير جزئي) يبقى للمعماري.

## 2. ما بُني (مقتطف)

`repos_stock.py` (كل SQL المخزون) · `workers/stock.py` (المنسّق) ·
`tools/join_waitlist.py` (الأداة الثالثة) · نيّة سابعة `stock_waitlist` · دالة
سادسة `compose_stock_notice` · ستة قوالب · خيط المكنسة في `realtime.py` · خمسة
مقاييس · ثلاثة تنبيهات · **S18 + S19** في البوابة. التخصيص الذري بقي كما هو في
`0001` (`allocate_stock_holds` / `expire_stock_holds`) — لم يُلمَس ولم يُعاد بناؤه،
وبلا أي قفل Redis.

---

# نتائج P2.4 — إغلاق P2 (مِنصّة الاختبار · بيانات الـgazetteer · المجموعة الذهبية)

**المنفّذ:** DeepSeek (داخل Cline) · **التاريخ:** 2026-10-03

## 0. F-P2-06 [حرج، مُصلَح] — سلطة تنظيف واحدة

- حُذفت `reset_tenants_and_channels` و`delete_tenant` و`delete_tenant_and_its_audit`
  نهائياً. `delete_tenant_full` هي **سلطة التنظيف الوحيدة** (H74).
- أُصلح ترتيب `delete_tenant_full`: `stock_holds` **قبل** `waitlist_entries`
  (كان يسبّب `ForeignKeyViolation` لأي مستأجر له الاثنان معاً).
- **S20** تحرس القاعدة: أي `DELETE FROM tenants` في `core/tests/**` أو
  `testsupport.py` خارج جسم `delete_tenant_full` ⇒ مخالفة (مُثبَتة بحقن سطر
  مصطنَع ثم صفر).
- اختبار مِنصّة الاختبار: `test_cleanup.py` يزرع مستأجراً بصفوف في ستة جداول
  (`geo_gazetteer`/`waitlist_entries`/`stock_holds`/`address_resolutions`/
  `order_lookup_attempts`/`verifier_blocks`) ثم ينادي `delete_tenant_full` ويؤكّد
  صفر صفوف في كل جدول بلا استثناء.

## 1. الـgazetteer — بيانات وتحميل (H75)

- `data/geo/yemen_admin.json`: **37 صفاً** (22 محافظة + 11 مديرية + 4 معالم) + **4
  مرادفات** (`جوله`→`دوار`، `تقاطع`→`مفرق`، `حاره`→`حي`، `زقاق`→`حي`).
- `scripts/seed_gazetteer.py` أعيد بناؤه: تحقّق هندسي fail-fast (`ST_IsValid` +
  صندوق اليمن `GEO_BBOX_*` + حلّ `parent` + `ST_Covers`/`ST_DWithin` للطفل داخل
  والده)، وإدراج **idempotent** (تحقّق قبل الإدراج لا `ON CONFLICT`)، ويبقى الطريق
  الوحيد لـ`tenant_id IS NULL` (H68/S17-d).
- **مُلاحظة أمانة (OQ-P2-04):** الإحداثيات **تقريبية** (مراكز معرفة عامة)؛ حدود
  المحافظات المضبوطة (مضلّعات) بانتظار إقرار المالك لمصدر مرخّص. لهذا المديريات
  تُختبَر بـ`ST_DWithin` (المكافئ النقطي لـ`ST_Covers`) وفرع `ST_Covers` مُنفَّذ
  ليتفعّل فور وصول المضلّعات.
- **مقياس + تنبيه (H75):** `gazetteer_rows{level}` يُضبط عند الإقلاع، وتنبيه
  `GazetteerEmpty` على `gazetteer_rows{level="governorate"} == 0` ⇒ critical.

## 2. المجموعة الذهبية للعناوين — معيار خروج P2

- `core/tests/golden/addresses.jsonl`: **45 حالة**، و`test_golden_addresses.py`
  (موسوم `db`) يحمّل الـgazetteer الحقيقي ويشغّلها ويطبع مصفوفة النتائج.
- **المتوقَّع (والمطلوب فيه فشل): 38 مطابق / 7 مخالف.** المخالفات السبع مُصمَّمة
  من حالات واقعية: اسم مديرية يتكرر في محافظتين مع ذكر المحافظة (`التحرير، صنعاء`
  لا يُفكّ بالوالد)؛ مرادف دارج على عبارة لا كلمة (`جوله الرياض`، `الجوله كنتاكي`،
  `تقاطع شارع حدة`)؛ حذف ال التعريف (`سبعين`)؛ فاصل شرطة (`صنعاء - شعوب`)؛ مدينة
  كبرى بلا مديرية في البيانات (`المكلا`). هذه حدود النظام الحقيقية، وOQ-P2-13
  (معايرة الأوزان) قرار مالك بعد قراءة الجدول.

## 3. لا جدول ولا ترحيل

الدفعة **الخامسة على التوالي** بلا ترحيل: بيانات الـgazetteer تُحمَّل بسكربت
مشغّل لا بترحيل (بيانات مرجعية تُحدَّث لا مخطّط يُغيّر).
