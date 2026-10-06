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


---

# P2.3 (Speckit) — سجل تنفيذ المهام

## P2.3-T1.0 [قيد بيئي، UNVERIFIED] — تشغيل اختبارات P2.3 الثمانية الموسومة db

**التاريخ:** 2026-10-06 · **المنفّذ:** GLM (داخل Cline) · **الحالة:** Task 1 جزئية — التوقف عند قاعدة المهام («أي سؤال مفتوح ⇒ توقف»).

`pytest -m db tests/test_stock_allocation.py` (الاختبارات الثمانية المعتمدة) **لم تُشغَّل** في بيئة
التنفيذ. السبب الحرفي (بلا تجاوز ولا تزييف):

```
psycopg.OperationalError: connection to server at "127.0.0.1", port 5432 failed:
FATAL:  password authentication failed for user "p07_migration"
```

وقائع البيئة المفحوصة فعلياً:

1. خدمة Windows `postgresql-x64-18` تعمل على 127.0.0.1:5432، لكن كل الاعتمادات المجربة
   مرفوضة (`p07_migration/p07_migration_pw`، `p07_app/p07_app_pw`، `postgres/postgres`،
   بلا كلمة مرور) — نفس الرد: `FATAL: password authentication failed`.
2. تثبيت PG18 هذا **لا يملك امتدادات `vector` (pgvector) ولا `postgis`** (فحص
   `C:\Program Files\PostgreSQL\18\share\extension\*.control`: من المطلوب لا يوجد سوى
   `pg_trgm`) ⇒ حتى بتوفر اعتمادات، `0001_baseline.sql` سيفشل عند
   `CREATE EXTENSION IF NOT EXISTS vector/postgis`.
3. لا Docker ولا `.env` محلي ولا متغيرات `CORE_*_DATABASE_URL` في بيئة النظام.


---

# P2.3-T9 [UNVERIFIED/محظور بيئياً خارج نطاق المخزون] — قياس الحزمة الكاملة -m db

**التاريخ:** 2026-10-06 · **المنفّذ:** GLM (داخل Cline)

**1) خط الأساس الموثّق:** `docs/P3_05_REPORT.md` سطر 16 —
`run_db_suite: [run 1] 460 passed (305 s) · [run 2] 460 passed (312 s) ⇒ DB SUITE: STABLE`.
آخر رقم معتمد للحزمة db = **460 ناجحاً** (مرّتين، P3.5).

**2) محاولات التشغيل (بمهلة صريحة + قاعدة نظيفة):**
- `p23_db3.py` (غلاف 3 جولات × مهلة 420s): `[round 1] TIMEOUT (>420s) - FROZEN` · `[round 2] TIMEOUT (>420s) - FROZEN`.
- تشخيص `-v` متدفق: تقدّم طبيعي حتى ~3% ثم **إخفاقات** في `test_dispatch_burst_db.py` (`[12]` و`[30]` بـ`assert 6 == 5` على قاعدة **ملوَّثة**) ثم توقّف على `test_burst_production_batch20_all_sent`.
- **بعد تنظيف القاعدة** (`delete_tenant_full` لكل المستأجرين ⇒ tenants=0/outbox=0): `[5]/[12]/[30]` **نجحت** (فكان `6==5` تلوّث F-P2-06 من جولاتي المنقطعة، لا عيباً)، لكن `test_burst_production_batch20_all_sent` **فشل** وتوقّف ~26s.

**3) السبب الجذري الحرفي (خارج ملفات المخزون — dispatch):**
```
tests\test_dispatch_burst_db.py:119: in test_burst_production_batch20_all_sent
    assert len(client.sent) == 30, client.sent
E   AssertionError: ['96770000006', '96770000000', ...]
E   assert 31 == 30          # إرسال مكرر: 31 بدل 30
```
ومكدس faulthandler يظهر **استنزاف تجمّع اتصالات psycopg_pool**: خيوط العامل محجوبة على
`psycopg_pool/pool.py:602 worker` (`queue.get()`)، والخيط الرئيسي ينتظر اتصالاً — وهو سبب
التوقّف الطويل (لا انتهاء فوري).

**4) التصنيف مقابل خط الأساس (460):**
- (أ) داخل ملفات المخزون `test_stock_*`: **صفر إخفاقات** — الـ13 اختبار db للمخزون كلها خضراء (أُثبتت في Task 5/7/8).
- (ب) خارجها: `test_dispatch_burst_db.py` (dispatch) — إرسال مكرر (31≠30) + استنزاف تجمّع الاتصالات. **عدد الحالات: 1 فشل مؤكَّد + توقّف** (`test_burst_production_batch20_all_sent`) ومرجّح أختها `test_burst_above_cap_defers_never_fails`.

**5) لا إصلاح** (خارج نطاق P2.4/المخزون)، ولا تعديل اختبار/ترحيل. `3` جولات نظيفة **غير قابلة للتحقيق** في بيئتي بسبب توقّف/فشل dispatch المنهجي (وليس تقلّباً): محاولات متعددة بنفس النتيجة.

**ملاحظة بيئة:** بيئة المنفّذ PostgreSQL 18 + WSL2 (شبكات معكوسة) أبطأ ~3× من بيئة المدقّق
(PostgreSQL 16 حاويات محلية)، ما يفضح سباق إعادة-الادّعاء في dispatch؛ وليس من صلاحية P2.3
تشخيصه. لا رقم ناجح/فاشل كامل لأن الحزمة لا تكتمل.



---

# P2.3-T8ج [VERIFIED] — تأكيد مقياس «duplicate» ببيان مستقل (delta)

**التاريخ:** 2026-10-06 · **المنفّذ:** GLM (داخل Cline)

**1) المقياس:** `app/obs/metrics.py`: `waitlist_entries_total = Counter(..., labelnames=["action"])`
بقيم `joined|duplicate|no_variant|cap`. يُسجَّل في `workers/stock.py::join_waitlist`
(§5.1، سطر 77): فرع `has_active_waitlist` ⇒ `metrics.waitlist_entries_total.labels("duplicate").inc()`.
يُقرأ في الاختبارات بالدلتا: `...labels("duplicate")._value.get()` قبل/بعد (نفس نمط
test_p3_step_zero_db.py / test_policy_failclosed_db.py).

**2) اختبار دائم أُضيف (test_stock_worker.py، نقي):** `test_duplicate_metric_delta_exactly_one`
— الانضمام الأول (`has_active_waitlist=False`) ⇒ `delta("duplicate") == 0`؛ الانضمام المكرر
(`has_active_waitlist=True`) ⇒ `delta("duplicate") == 1` بالضبط. يُقرأ بالدلتا لا القيمة المطلقة.

**3) المقياس يزيد فعلاً ⇒ لا خلل، ولا OQ-P2-09.**

**4) فحص التحوّل (محاكاة إزالة الزيادة، دون تعديل الكود الأصلي):**
```
NORMAL duplicate delta (expect 1): 1.0
MUTATED (inc no-op) duplicate delta (expect 0 => the test FAILS): 0.0
```
⇒ لو لم تزدد الزيادة يفشل الاختبار (delta=0 بدل 1).

**5) المخرج الحرفي:**
```
$ python -m pytest tests/test_stock_worker.py -q
.......                                                                  [100%]
7 passed in 1.45s
$ python -m pytest tests/test_stock_allocation.py -q -m db
.............                                                            [100%]
13 passed in 35.99s
$ python scripts/static_gate.py
STATIC GATE PASSED — 0 violations
```
`git diff --stat` (الاختباران فقط):
```
 core/tests/test_stock_allocation.py | 267 ++++++++++++++++++++++++++++++++++++
 core/tests/test_stock_worker.py     |  35 +++++
 2 files changed, 302 insertions(+)    # إضافات فقط، صفر حذف
```

**6) توقّف هنا؛ لم أُنتقل إلى Task 9.**



---

# P2.3-T8ب [VERIFIED] — تكامل sweep_once بمنفذ تجاري وهمي (بلا شبكة)

**التاريخ:** 2026-10-06 · **المنفّذ:** GLM (داخل Cline)

**الخطوة 0 (إثبات عدم تغيير المعتمد):** `git diff core/tests/test_stock_allocation.py`
⇒ `1 file changed, 163 insertions(+), 0 deletions(-)` — كل التغيير **إضافات** فقط؛ الأسطر
1–168 (الاختبارات الثمانية الأصلية + اختبارا السباق) لم تُمس (لا حذف ولا تعديل).

**1) واجهة commerce_port المسجَّلة:** `_sweep_variant` يستدعي
`commerce.get_stock_observation(tenant_ref=..., platform_variant_id=...)` ⇒
`{"available": int, "observed_at": ISO}` أو `None`؛ يلتقط `CommerceUnavailableError`/
`CommerceClientError` فيعود صامتاً؛ يرفض available غير int/سالب؛ وفحص H73:
`_age_seconds(observed_at) > stock_observation_max_age_s` ⇒ لا تخصيص. و`commerce_port=None`
⇒ الحارس في الخيط `_run_stock_sweep` (`if self.commerce_port is not None`, realtime.py:857)
هو ما يُبقي النشر خاملاً؛ و`sweep_once(None,...)` بذاته يرفع AttributeError قبل أي كتابة.

**2) اختبار db تكامل (منفذ وهمي في الذاكرة):** `test_sweep_once_allocates_and_notifies_with_fake_commerce`
— منتظران على variant، منفذ وهمي يرجع available=2 حديثاً، `sweep_once` ⇒ حجوزات==2 بالضبط،
إشعاران (صفّ outbox لكل حجز)، الأقدم أولاً (`["w0","w1"]`).

**3) اختبار db ثانٍ (الخمول):** `test_sweep_once_with_none_commerce_allocates_nothing`
— `sweep_once(None,...)` يرفع AttributeError قبل أي كتابة ⇒ 0 حجوزات و0 إشعار؛ يوثّق خمول
النشر الحالي (حارس الخيط في Task 2).

**5) فحص التحوّل (رصيد قديم ⇒ H73):**
```
holds after STALE observation (expect 0, H73 blocks): 0
AFTER ROLLBACK: waitlist rows (expect 0): 0
```
⇒ لو أعاد المنفذ الوهمي رصيداً قديماً يفشل اختبار التكامل (0 حجوزات بدل 2)؛ الأصل سليم بعد rollback.

**4) لا تعديل على docker-compose.yml ولا كود أصلي ولا ترحيل** (المنفذ الوهمي داخل ملف الاختبار فقط).

**6) المخرج الحرفي:**
```
$ python -m pytest tests/test_stock_allocation.py -q -m db
.............                                                            [100%]
13 passed in 41.92s
$ python scripts/static_gate.py
STATIC GATE PASSED — 0 violations
```

**7) لم يُنفَّذ 8ج (مقياس duplicate).**



---

# P2.3-T8أ [VERIFIED] — عدم تكرار الإشعار لكل حجز

**التاريخ:** 2026-10-06 · **المنفّذ:** GLM (داخل Cline)

**1) أين يقع ضمان التفرّد (قاعدة أم كود):** في **القاعدة**. `0001_baseline.sql:247`
`outbox.idempotency_key text NOT NULL UNIQUE` (فهرس فريد، ليس كوداً فقط).
`repos_outbox.insert_outbox` إدراج عادي بلا معالجة تعارض ⇒ الإدراج المكرر يرفع
`UniqueViolation`. المفتاح من `stock.py::_insert_notice` بصيغة
`stock:{entry_id}:{template_id}`. **الضمان قيد قاعدة حقيقي** (المادة 2)، فلا حاجة
لـOQ-P2-09.

**2) اختبار db دائم أُضيف:** `test_notification_idempotent_per_hold`
- تخصيص حجز + كتابة إشعاره ⇒ صفّ outbox واحد.
- إعادة `_expire_allocate_notify` (الحجز ما زال حيّاً ⇒ 0 حجوزات جديدة ⇒ 0 إشعارات) ⇒ يبقى صفاً واحداً.
- استدعاء `_insert_notice` مباشرة بنفس المفتاح ⇒ `UniqueViolation` من القيد الفريد (معزول بـSAVEPOINT) ⇒ يبقى صفاً واحداً.
- تأكيد بعدّ الصفوف: `count_outbox() == 1` في المواضع الثلاثة.
- تنظيف داخل الاختبار بترتيب FK (outbox → stock_holds → waitlist_entries → conversations) كي لا يعطّل `clean_stock` في الـfixture.

**3) فحص التحوّل (إسقاط القيد الفريد داخل معاملة تُرجَع):**
```
BEFORE: unique index on outbox.idempotency_key: 1
first outbox insert: OK
WITHOUT the constraint: second insert SUCCEEDED -> duplicate notification allowed
rows for key: 2
AFTER ROLLBACK: unique index present? 1
AFTER ROLLBACK: outbox rows (expect 0): 0
```
⇒ إزالة القيد تسمح بإشعار مكرر؛ الأصل سليم بعد rollback، ولم يُمس أي كود/ترحيل.

**4) لا تعديل كود/ترحيل؛ الضمان في القاعدة.** (لا OQ-P2-09).

**5) المخرج الحرفي:**
```
$ python -m pytest tests/test_stock_allocation.py -q -m db
...........                                                              [100%]
11 passed in 19.80s
$ python scripts/static_gate.py
STATIC GATE PASSED — 0 violations
```

**6) لم يُنفَّذ 8ب (تكامل sweep_once بمنفذ وهمي) ولا 8ج (تأكيد مقياس duplicate).**



---

# P2.3-T7 [VERIFIED] — سباق متزامن مكرر ×10 + FIFO تحت التزامن + فحص التحوّل

**التاريخ:** 2026-10-06 · **المنفّذ:** GLM (داخل Cline)

**التحليل الدقيق لتغطية اختبارَي السباق الموجودين:**
- (أ) مجموع الحجوزات الحيّة == الرصيد بالضبط ⇒ **مغطّى** (`sum(results)==1/3` + `held==qty==available`).
- (ب) لا منتظر عنده حجزان حيّان ⇒ **غير مؤكَّد صراحةً** (يُستدَلّ عليه بالعدد + القيد الفريد).
- (ج) الفائزون هم الأقدم تحت التزامن ⇒ **غير مغطّى** (اختبار FIFO الوحيد أحادي الخيط، واختبارا السباق يستخدمان `stagger=False` بلا ترتيب مميّز).
- (د) اتصالات منفصلة + انطلاق معاً (barrier) ⇒ **مغطّى** (`threading.Barrier(n)` + اتصال `psycopg` مستقل لكل متسابق).

**ما أُضيف (دون تكرار ودون تعديل أي اختبار معتمد):** اختبار db دائم
`test_race_repeated_10x_fifo_and_no_double_hold` — 10 جولات متتالية (رصيد 3 < 20 منتظراً،
`stagger=True`)، يؤكد في كل جولة: `sum(results)==3`، `held==qty==3`، الفائزون هم
`[w0,w1,w2]` (الأقدم، FIFO تحت التزامن)، و`len(set(winners))==len(winners)` (لا حجز مزدوج).

**المخرج الحرفي (الاختبار الجديد وحده):**
```
1 passed in 22.02s
```

**فحص التحوّل (نسخة معدّلة من الدالة بدون ORDER BY، داخل معاملة تُرجَع):**
```
winner without ORDER BY: ['w2'] (expect w2, NOT w0 - FIFO broken)
AFTER ROLLBACK: allocate_no_order present? 0
AFTER ROLLBACK: app.allocate_stock_holds present? 1
AFTER ROLLBACK: waitlist rows (expect 0): 0
```
⇒ إزالة `ORDER BY` تكسر FIFO حتماً؛ ولم تُمس الدالة الأصلية ولا أي ترحيل، ولا أثر بعد rollback.

**المخرج الحرفي (الملف كاملاً -m db):**
```
..........                                                               [100%]
10 passed in 42.87s
```

**البوابة الساكنة:** `STATIC GATE PASSED — 0 violations`.

**ملاحظة لـ Task 8 (لن أُنفّذها الآن):** ضمّ تأكيد قيمة مقياس «duplicate» ببيان مستقل.



---

# P2.3-T5 [VERIFIED] — اختبار فاشل أولاً: القيد يرفض الإدراج المكرر الخام + فحص التحوّل

**التاريخ:** 2026-10-06 · **المنفّذ:** GLM (داخل Cline)

**التحليل الدقيق للتغطية القائمة:**
- `test_duplicate_customer_prevented` (db): يقرأ `has_active_waitlist` بعد إدراج واحد ويثبت أنه True. **لا** يُجري INSERT خاماً مكرراً ولا يختبر رفض القيد.
- `test_join_waitlist_duplicate_rejected` (نقي): يغطي المسار الرشيق عبر monkeypatch (`kind=already_waiting` + لا إدراج ثانٍ). **لا** يلمس القيد الحقيقي ولا يؤكد مقياس «duplicate».

**الفجوة الحاسمة:** لا اختبار db يُثبت أن القيد `waitlist_active_uq` نفسه (لا كود التطبيق) يرفض INSERT خاماً مكرراً (المادة 2 من الدستور). أُضيف اختبار db دائم `test_duplicate_live_row_rejected_by_db_constraint` (بلا تعديل أي اختبار معتمد): إدراجان خامان لنفس (مستأجر، عميل، variant) ⇒ الثاني يرفع `UniqueViolation`.

**فحص التحوّل (إسقاط الفهرس داخل معاملة تُرجَع بالكامل):**
```
BEFORE: index present? 1
first live insert: OK
WITHOUT the index: duplicate INSERT SUCCEEDED -> the constraint is the ONLY guard
live rows inside tx (expect 2): 2
AFTER ROLLBACK: index present? 1
AFTER ROLLBACK: waitlist_entries rows (expect 0): 0
```
⇒ الاختبار الجديد ذو معنى (يفشل لو أُسقط الفهرس)، والقاعدة سليمة بعد rollback.

**المخرج الحرفي (8 القديمة + الجديد):**
```
$ python -m pytest tests/test_stock_allocation.py -q -m db
.........                                                                [100%]
9 passed in 9.58s
```

**البوابة الساكنة:** `STATIC GATE PASSED — 0 violations`.

# P2.3-T6 [لا تعديل مطلوب] — الرفض الرشيق موجود ومغطى

الرفض الرشيق عبر المستودع **موجود** في `workers/stock.py::join_waitlist`
(`has_active_waitlist` ⇒ `already_waiting` + مقياس `waitlist_entries_total{duplicate}`
+ لا إدراج ثانٍ) و**مغطى** وظيفياً: `test_join_waitlist_duplicate_rejected` (الناتج
already_waiting + لا سجل ثانٍ) + `test_duplicate_customer_prevented` (قراءة
has_active_waitlist على صف حقيقي). ⇒ **لا تعديل كود**. ملاحظة مراقبة (غير معطِّلة):
قيمة مقياس «duplicate» تُنفَّذ في نفس المسار المغطى لكنها غير مؤكَّدة بمقياس مستقل؛
تُسجَّل للمالك إن أراد إضافة تأكيد لاحقاً، ولم تُمس أي كود.



---

# P2.3-T4 [VERIFIED] — قرار المالك OQ-P2-08 + إثبات القيد على القاعدة المحلية

**التاريخ:** 2026-10-06 · **المنفّذ:** GLM (داخل Cline)

**القرار مطبَّق:** لا تكرارات حيّة، والمؤشر الجزئي الفريد `waitlist_active_uq` قائم
ويُطبَّق فعلياً في القاعدة ⇒ **لا حاجة لأي ترحيل** (لا صفوف مكررة للحذف، ولا لمس للصفوف
المنتهية، ولا قيد شامل — فلا تفعيل لـOQ-P2-08b في هذه الجولة).

**إثبات وجود المؤشر (استعلام pg_indexes):**
```
CREATE UNIQUE INDEX waitlist_active_uq ON public.waitlist_entries USING btree
(tenant_id, customer_id, platform_variant_id)
WHERE (status = ANY (ARRAY['waiting'::text, 'held'::text]))
```

**إثبات رفض الإدراج المكرر الحيّ (داخل معاملة تُرجَع بالكامل):**
```
  first live entry inserted: id = 8edb738e-5c4e-454b-9737-1b0c15730a70
  second live entry REJECTED by DB constraint:
   duplicate key value violates unique constraint "waitlist_active_uq"
(transaction rolled back - no rows left behind)
remaining rows in waitlist_entries: 0
```

لم يُنفَّذ أي DELETE/UPDATE على أي قاعدة غير المحلية؛ الإدراج التجريبي داخل معاملة
ROLLBACK فلم يبقَ أي أثر.



---

# P2.3-T3 [VERIFIED] — عدّ التكرارات على قاعدة الاختبار المحلية

**التاريخ:** 2026-10-06 · **المنفّذ:** GLM (داخل Cline)

على قاعدة الاختبار المحلية `sharwa_ai_p07` (PostgreSQL 18 @ 127.0.0.1:5433، طُبّقت
عليها 0001–0018) نُفّذ عدّ القراءة فقط:

```
total rows in waitlist_entries: 0
duplicate groups: 0
```

**النتيجة:** صفر تكرارات (القاعدة فارغة بعد الترحيل؛ المؤشر الجزئي الفريد
`waitlist_active_uq` يمنع أي تكرار حيّ هيكلياً، ولا صفوف منتهية بعد).

الاستعلام المستخدم (قراءة فقط، يُسلَّم للمالك ليُشغَّله على قاعدة الـVPS):

```sql
SELECT
  tenant_id,
  customer_id,
  platform_variant_id,
  count(*)                                                              AS total_rows,
  count(*) FILTER (WHERE status IN ('waiting','held'))                  AS live_rows,
  count(*) FILTER (WHERE status IN ('converted','cancelled','expired')) AS ended_rows
FROM waitlist_entries
GROUP BY tenant_id, customer_id, platform_variant_id
HAVING count(*) > 1
ORDER BY tenant_id, customer_id, platform_variant_id;
```

`0 صف` مُعاد = لا تكرارات؛ وأي صف مُعاد يُظهر عدد الحيّ (waiting|held) والمنتهي
(converted|cancelled|expired) لكل (مستأجر، عميل، variant).



---

# P2.3-T1.0-resolution [VERIFIED] — اختبارات db الثمانية شُغّلت بمخرج حقيقي

**التاريخ:** 2026-10-06 · **المنفّذ:** GLM (داخل Cline)

بعد قرار المالك بالخيار (أ)، اكتملت بيئة PostgreSQL حقيقية محلية (داخل WSL2 Ubuntu
26.04: PostgreSQL 18 + PostGIS 3.6 + pgvector، منفذ 5433، شبكات معكوسة) بالأدوار
المستندة للمشروع (`ops/postgres/init/10_roles.sh` + `core/tests/conftest.py`):
`p07_app / p07_system / p07_migration` على قاعدة `sharwa_ai_p07`. طُبّقت الترحيلات
0001–0018 بالأمر الرسمي `python -m app.cli migrate`، ثم:

```
$ python -m pytest tests/test_stock_allocation.py -q -m db
........                                                                 [100%]
8 passed in 9.28s
```

**النتيجة:** الاختبارات الثمانية (سباق 20×1، سباق 20×3، صفر متاح، FIFO، إعادة نداء،
انتهاء→ترقية، عزل مستأجرين، منع تكرار) كلها خضراء على قاعدة حقيقية. **Task 1 مغلق.**

# P2.3-T2 [VERIFIED بالقراءة + ملاحظة تشغيلية] — ربط _run_stock_sweep

`core/app/workers/realtime.py:955-957` ينشئ الخيط فعلاً:
```
stock_thread = threading.Thread(target=self._run_stock_sweep, daemon=True)
stock_thread.start()
threads.append(stock_thread)
```

**الربط VERIFIED.** ملاحظة تشغيلية (activation غير مفعّل): `_run_stock_sweep`
(سطر 857) لا يستدعي `stock.sweep_once` إلا إن كان `self.commerce_port` غير فارغ،
و`commerce_port` يُبنى في `_setup()` فقط عند `COMMERCE_BASE_URL` غير فارغ،
و`docker-compose.yml` (خدمة worker-realtime) **لا يمرّر COMMERCE_BASE_URL** اليوم ⇒
الخيط يعمل لكنه خامل (لا قراءة منصة ولا تخصيص) في النشر الحالي كما هو. لم يُغيَّر أي كود
(المهمة قراءة فقط).

4. محاولة بناء بيئة بديلة داخل WSL2 (Ubuntu 26.04): PostgreSQL 18 + PostGIS + pgvector +
   Redis — التنزيل المتوازي بلغ **74MB من 85.5MB** (فحص `du -sm /var/cache/apt/archives`)
   قبل التوقف بأمر المالك؛ لم يكتمل التثبيت ولم تُنشأ قاعدة بعد.

**ما ثبت فعلاً (VERIFIED) في Task 1 حتى التوقف:**
- الاختبارات النقية للمخزون: `python -m pytest tests/test_stock_worker.py -q` ⇒
  `6 passed in 0.59s`.
- الجرد القرائي الكامل (جداول/دوال/عامل/أداة/اختبارات) جاهز في تقرير Task 1.

**الخطوات المعلّقة على قرار المالك:** استكمال بيئة WSL (≈10 دقائق إضافية)، أو اعتمادات
PG محلية صالحة، أو تشغيل المالك `scripts/run_db_suite.py` في بيئته ولصق المخرج.

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


---

# بنود مفتوحة مُحالة من P2.3 (Speckit)

## F-P2-11 [مفتوح، يُحال إلى P2.4] — إرسال مكرر في `test_dispatch_burst_db.py` مع استنزاف تجمّع الاتصالات

**سُجِّل:** 2026-10-06 (مراجعة المعماري لـ Task 9/10) · **المصدر:** سجل `P2.3-T9` أعلاه.

**الوقائع (VERIFIED بمخرج حرفي في P2.3-T9):** على بيئة المنفّذ (PostgreSQL 18 على WSL2) وبعد تنظيف القاعدة كاملاً، فشل `test_burst_production_batch20_all_sent` عند `assert len(client.sent) == 30` بالقيمة `31` (إرسال مكرر)، وأظهر مكدس faulthandler استنزاف تجمّع `psycopg_pool` (خيوط العامل على `queue.get()` والخيط الرئيسي ينتظر اتصالاً). لا ملف مخزون متأثر، ولا كود منتج تغيّر في P2.3 (ملفا اختبار فقط).

**ما هو غير مثبت (UNVERIFIED):**
- السبب الجذري. فرضية المنفّذ (بطء البيئة يفضح سباق إعادة-الادّعاء في dispatch) **فرضية لا حقيقة**، ولا يُنسب الخلل للبيئة قبل إعادة الإنتاج في البيئة المرجعية (PostgreSQL 16 في حاويات، خط الأساس 460 ناجحة مرّتين في `P3_05_REPORT.md`).
- هل يفشل الاختبار منفرداً أم ضمن الحزمة فقط (لم يُنفَّذ اختبار العزل).
- هل يتكرر الإرسال المكرر خارج الاختبار؛ إرسال واتساب مكرر هو من فئة الأعطال التي نمنعها (H لضمانات الإرسال)، فلا يُعامل كخلل بيئي حتى يُنفى.

**الإجراء المطلوب في P2.4:** (١) إعادة إنتاجه في البيئة المرجعية، (٢) تشغيله منفرداً 3 مرات على قاعدة نظيفة، (٣) إن ثبت فهو خلل حرج في الادّعاء/إعادة الادّعاء (lease/reclaim) يُصلَح بقيد قاعدة أو قفل صفّ لا بمهلة زمنية، (٤) لا يُعلَن استقرار الحزمة الكاملة قبل 3 جولات نظيفة.
