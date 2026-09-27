# P1_05_REPORT — تقرير تسليم دفعة P1.5: «طبقة المزوّد والحوكمة والموجِّه» (LLM Layer, Budgets, Router)

> **الحالة:** `P1.5: COMPLETE — AWAITING AUDIT.`
> **البيئة (قرار المالك):** الفحص الثابت الصارم وحده، بلا حاويات وبلا شبكة.
> **اللغة:** كل بند يبدأ بفعل صريح (أضفتُ · عدّلتُ · شغّلتُ · لم أشغّله · وجدتُه موجوداً)، بلا صيغ مبني للمجهول.

---

## 0. الخطوة صفر (M0) — نُفِّذت قبل أي سطر من P1.5

### 0.1 N1 — `source_version` يقبل NULL

- **عدّلتُ** `core/migrations/0007_p1_catalog.sql`: أضفتُ `UPDATE … SET source_version = 0 WHERE source_version IS NULL` ثم `SET DEFAULT 0` و`SET NOT NULL` لـ`stock_levels` و`kb_chunks`. **تحقّقتُ** أنه لم يُطبَّق على أي قاعدة (لا Docker محلياً)، فعدّلتُه في موضعه.
- **عدّلتُ** `core/app/db/repos_catalog.py`: `COALESCE(<table>.source_version, -1)` في شروط `ON CONFLICT` الأربعة، وجعلتُ `_existing_version` تعيد `-1` عند `NULL`.

### 0.2 N2 — الترحيل غير idempotent

- **عدّلتُ** `0007_p1_catalog.sql`: استبدلتُ `ALTER TABLE … ADD CONSTRAINT` بـ`CREATE UNIQUE INDEX IF NOT EXISTS kb_chunks_tenant_source_uq ON kb_chunks (tenant_id, source)` (يكفي كمفتاح `ON CONFLICT`، و`IF NOT EXISTS` يجعل الترحيل idempotent — H14).

### 0.3 عزل تجهيزة الاختبارات (قرار المالك)

- **عدّلتُ** `core/tests/conftest.py`: جعلتُ `db_pool` و`_clean_kill_switches` **غير** `autouse`، وأضفتُ `pytest_collection_modifyitems` يطبّقهما تلقائياً فقط على الاختبارات الموسومة `@pytest.mark.db`.
- **أضفتُ** إلى `core/pyproject.toml`: `markers` و`addopts = "-m 'not db'"`.
- **وسمتُ** `@pytest.mark.db` الاختبارات التي تحتاج خدمات (module-level في الملفات الخمسة، وفردياً في `test_cli.py`).
- **لم أعدّل** منطق أي اختبار قائم — نقلٌ ووسمٌ فقط (H21).

**الدليل (مخرج حقيقي):**
- `pytest core/tests -q` ⇒ **`106 passed, 247 deselected, 2 failed`** — **106 اختبارات نقية صارت تعمل بلا خدمات**، و**247 بقي موسوماً `db`**.
- الـ`2 failed` هما `test_import_boundaries.py` (يحتاج `lint-imports` غير المثبّت — قيد بيئي مُوثَّق منذ P1.0).

---

## 1. ما بُني (L1–L11)

| الرمز | التنفيذ |
|---|---|
| L1 | `app/llm/port.py` — `LlmProvider` (واجهة `complete_json`) + `LlmUsage`/`LlmJsonResult`/أخطاء. |
| L2 | `app/llm/registry.py` — اختيار المزوّد (fake فقط) + `validate_llm_provider` في `config.py`. |
| L3 | `tests/fake_llm.py` — `FakeLlmProvider` حتمي بلا شبكة، يقارب قواعد نصّية ويعيد JSON + usage ÷4. |
| L4 | `app/llm/budget.py` (منطق نقي) + `app/db/repos_llm.py` (SQL) — فحص قبل ومحاسبة بعد. |
| L5 | `app/llm/breaker.py` — قاطع closed→open→half_open بعتبات مكتوبة، حالة في الذاكرة. |
| L6 | `app/llm/router.py` — تقنيع H41 + مخطط pydantic صارم + `run_router` (قاطع + إعادة محاولة + عتبة ثقة). |
| L7 | `app/workers/compose.py` — وحدة التركيب الوحيدة. |
| L8 | `app/workers/turn.py` — دمج الموجِّه والنداء **خارج** أي معاملة (H40). |
| L9 | `core/migrations/0008_p1_llm.sql` — `app.budget_state_counts()` فقط؛ لا جدول/عمود/فهرس جديد. |
| L10 | 11 عائلة مقاييس + 4 قواعد تنبيه + المرحلة S8. |
| L11 | `tests/test_llm.py` (17 اختباراً نقياً) + `tests/fake_llm.py`. |


---

## 2. مصفوفة القبول M0–M16 (بلا خلية فارغة)

| # | البند | الدليل |
|---|---|---|
| M0 | الخطوة صفر | **شغّلتُ** `pytest core/tests -q` ⇒ `106 passed, 247 deselected, 2 failed` (importlinter) |
| M1 | البوابة صفر (S1–S8) | **شغّلتُ** `STATIC GATE PASSED — 0 violations.` كآخر أمر (زمن `2026-09-27T15:44:52Z`) |
| M2 | S8 بثلاثة بنودها | **أثبتُّها** بحقن 3 بنود ⇒ 3 مخالفات `[S8]`، ثم حذفتُها ⇒ PASS |
| M3 | H38 | **شغّلتُ** نقية: التركيب لا يحمل نصّاً من النموذج (عناوين/نصّ حرفياً + القالب) |
| M4 | مخطط الموجِّه | **شغّلتُ** جدولاً نقياً: صالح/تالف/مجهول/ناقص/نصّي/زائد ⇒ الصحيح، و`other` بلا إعادة نداء |
| M5 | سلّم الميزانية | **شغّلتُ** نقية: 79%⇒ok، 80%⇒warn_80، 100%⇒degraded، والتكلفة من جدول الأسعار |
| M6 | المحاسبة على الفشل | **كتبتُ** صف `llm_calls` بـ`status='timeout'` بلا توكنز؛ التشغيل **مؤجَّل — يحتاج PG** |
| M7 | القاطع | **شغّلتُ** نقية: 5 إخفاقات⇒open⇒بعد RESET⇒half_open⇒نجاح⇒closed |
| M8 | H40 | **كتبتُ** التقسيم؛ **فتّشتُ** لا نداء مزوّد داخل `tenant_tx`؛ التشغيل **مؤجَّل — يحتاج PG** |
| M9 | H41 التقنيع | **شغّلتُ** نقية: هاتف⇒`***`+آخر 3 خانات، وصفر معرّف داخلي |
| M10 | التركيب | **شغّلتُ** نقية: بلا سعر/توفر، والنصّ حرفي ضمن السقف |
| M11 | سقف الردود المتتالية | **قرأتُ** `decide` سارية؛ ردّ المنتجات `handoff=False` فيُعدّ في السقف؛ التشغيل **مؤجَّل — يحتاج PG** |
| M12 | `expected_epoch` | **كتبتُ** ردّ بلا تسليم يحمل `conv.epoch`؛ التشغيل **مؤجَّل — يحتاج PG** |
| M13 | رفض الإقلاع | **شغّلتُ** نقية: `validate_llm_provider("production","fake")` ⇒ `ConfigError` |
| M14 | المقاييس والتنبيهات | **شغّلتُ** S2/S3/S4 خضراء، بلا تسمية عالية التعدد |
| M15 | لا انحدار | **شغّلتُ** `pytest` ⇒ النقية خضراء بلا تعديل اختبار قائم (17 جديداً + 89) |
| M16 | دفتر الدَيْن | **وسّعتُه** §4 بمعايير P1.5 |

---

## 3. الفحوص الفعلية (بمخرجها)

1. **`python scripts/static_gate.py`** (آخر أمر): `STATIC GATE PASSED — 0 violations.` (S1–S8) — زمن `2026-09-27T15:44:52Z`.
2. **إثبات S8:** حقنتُ ثلاثة بنود ⇒ `STATIC GATE FAILED — 3 violation(s)`؛ حذفتُها ⇒ PASS.
3. **`pytest core/tests -q`**: `106 passed, 247 deselected, 2 failed` (الـ2 = `importlinter` غير مثبّت).
4. **استيراد الوحدات:** `13/13` (بلا استيراد دائري).
5. **`node scripts/hunt_gate.mjs`**: ملفات P1.5 الجديدة صفر مخالفة.

---

## 4. دفتر الدَيْن التقني (V3) — بلا خلية فارغة

| المعيار | الحالة |
|---|---|
| H40 المحاسبة الحيّة على PG (صف `llm_calls` + تحديث `tenant_budgets`) | **مؤجَّل — يحتاج PG** |
| M8 (لا نداء داخل معاملة) تحقّق تشغيلي | **مؤجَّل — يحتاج PG** |
| M12 (`expected_epoch` لا يُسقطه dispatcher) | **مؤجَّل — يحتاج PG** |
| سلّم الميزانية التكاملي (تجاوز 80%/100% فعلي) | **مؤجَّل — يحتاج PG** |
| مزوّد حقيقي (DeepSeek/Anthropic) ومفتاح API | **مؤجَّل — P1.5b/لاحقاً** |
| قاطع مشترك عند تعدد نسخ `api`/العامل | **دين مُعلَن** — OQ-P1-16 |
| جدول أسعار حقيقي عند اختيار المزوّد | **دين مُعلَن** — OQ-P1-17 |

---

## 5. الإقرار الذاتي (H38–H41)

| المادة | الحالة |
|---|---|
| H38 النموذج لا يكتب للعميل | **التزمتُ**: كل نصّ صادر من `compose.py` (قالب + نصّ التاجر حرفياً)؛ الموجِّه يعيد JSON فقط للتوجيه |
| H39 الميزانية قبل والمحاسبة بعد | **التزمتُ**: فحص قبل النداء، و`llm_calls`+`tenant_budgets` بعد (نجاحاً أو فشلاً) |
| H40 لا نداء شبكي داخل معاملة | **التزمتُ**: قراءة/قرار في معاملة قصيرة، النداء خارجها، كتابة/محاسبة في معاملة ثانية |
| H41 تقنيع ما يُرسَل | **التزمتُ**: `mask_phones` يمسح كل ما عدا آخر 3 خانات، ولا `wa_id`/`customer_id`/ملاحظة داخلية |

---

## 6. الملفات

**جديدة:** `app/llm/` (port/registry/breaker/budget/router) · `app/db/repos_llm.py` · `app/workers/compose.py` · `tests/fake_llm.py` · `tests/test_llm.py` · `migrations/0008_p1_llm.sql`

**معدَّلة:** `workers/turn.py` (H40 + الموجِّه) · `workers/realtime.py` (خيط + بناء الموجِّه + gauge) · `workers/config.py` (إعدادات LLM + M13) · `obs/metrics.py` (11 عائلة) · `scripts/static_gate.py` (S8) · `migrations/0007_p1_catalog.sql` (N1/N2) · `db/repos_catalog.py` (COALESCE) · `ops/prometheus/alerts.yml` (4 قواعد) · `tests/conftest.py` + `pyproject.toml` (عزل) · `.env.example`

---

## 7. قيود معروفة بصدق

1. **لم أشغّله** أي استعلام PG (المحاسبة/السلّم/العزل): بلا حاويات — مؤجَّل في §4.
2. **لم أشغّله** `mypy --strict`/`ruff`/`importlinter`/`pip-audit`: غير مثبّتة. كتبتُ بأنواع صريحة، و`static_gate.py` (S1) تحلّ كل الأسماء.
3. **الـ2 failed** في `pytest`: `test_import_boundaries` تحتاج `lint-imports` غير المثبّت (قيد بيئي مُسبق، غير ناتج عن هذه الدفعة).
4. **لا مزوّد حقيقي** (قرار المالك): `FakeLlmProvider` هو الوحيد، و`adapters/` بقي فارغاً (H1: لا كود ميت).

---

## 8. الختام

`P1.5 COMPLETE — STOPPING. AWAITING AUDIT. NO P1.5b WORK STARTED.`

