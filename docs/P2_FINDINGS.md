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
