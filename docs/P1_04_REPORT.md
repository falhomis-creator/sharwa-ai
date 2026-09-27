# P1_04_REPORT — تقرير تسليم دفعة P1.4: «نموذج قراءة الكتالوج والبحث الهجين المعجمي»

> **الحالة:** `P1.4: COMPLETE — AWAITING AUDIT.`
> **البيئة (قرار المالك):** الفحص الثابت الصارم وحده، بلا حاويات Docker، حتى إشعار آخر.
> **اللغة:** كل بند يبدأ بفعل صريح (أضفتُ · عدّلتُ · شغّلتُ · لم أشغّله · وجدتُه موجوداً)، بلا صيغ مبني للمجهول.

---

## 0. الموقف الابتدائي

- **وجدتُ** في `PROMPT_P1_04_catalog_for_deepseek.md` توجيهاً صريحاً: لا خطوة صفر، الشجرة نظيفة، ابدأ من القسم 5.
- **قرأتُ** الجداول في `core/migrations/0001_baseline.sql` (نفس `docs/reference/schema.sql`): `catalog_products`/`catalog_variants`/`catalog_embeddings`/`kb_chunks`/`catalog_sync_cursor`/`stock_levels` موجودة، مع `search_tsv` مولَّد وفهرسَي GIN (FTS + trgm).
- **وجدتُ** فجوة مخطط حقيقية (سجّلتُها في `P1_FINDINGS.md`): `kb_chunks` و`stock_levels` **بلا `source_version`**، و`kb_chunks` **بلا مفتاح طبيعي** للـupsert — عالجتُها في `0007_p1_catalog.sql` بلا إعادة إنشاء أي جدول.

---

## 1. ما بُني (C1–C10)

| الرمز | التنفيذ |
|---|---|
| C1 | `core/app/api/routes_catalog.py` — `POST /webhooks/platform/catalog` موقَّع بـHMAC-SHA256 (`hmac.compare_digest`)، نافذة إعادة تشغيل، حدّ جسم مكتوب، `tenant_ref` عبر `app.resolve_tenant` (مجهول ⇒ `200 unknown_tenant_ignored`). **بلا JWT** (التوقيع هو المصادقة). |
| C2 | `EVENT_ALLOWED_FIELDS` + `partition_event_fields` في `repos_catalog.py` — قائمة سماح لكل نوع تُسقط أي حقل خارجها عند الحدّ. |
| C3 | `core/app/workers/catalog.py::reconcile_once` + خيط `catalog` في `worker-realtime` + `catalog_sync_cursor` + مؤشر تقادم `catalog_staleness_seconds`. |
| C4 | `search_products` — FTS عربي + `pg_trgm` + دمج `rrf_merge`، سقف 8 بطاقات بيضاء (H35). |
| C5 | `kb_search` — FTS على `kb_chunks`، سقف 3 `{source, content}`. |
| C6 | `core/app/text/arabic.py` — `normalize()` مستخرجة، و`optout` يستوردها (وحدة واحدة). |
| C7 | `app/commerce/port.py` (`CommercePort`) + `app/commerce/adapter.py` (`SharwaCommerceAdapter`) + `app/channels/commerce_client.py` (HTTP + قاطع دائرة، الوحيد الذي يستورد `httpx`) + `tests/fake_commerce.py`. |
| C8 | المرحلة `S7` في `scripts/static_gate.py` — مبنية ومُثبَتة بإفشال متعمَّد. |
| C9 | 10 عائلات مقاييس في `app/obs/metrics.py` + 3 رموز أخطاء في `errors.py` + 3 قواعد تنبيه في `alerts.yml`. |
| C10 | `tests/test_catalog.py` (نقية) + `tests/fake_commerce.py`. |


---

## 2. مصفوفة القبول K1–K15 (بلا خلية فارغة)

| # | البند | الدليل |
|---|---|---|
| K1 | البوابة صفر مخالفة (S1–S7) | **شغّلتُ** `python scripts/static_gate.py` كآخر أمر: `STATIC GATE PASSED — 0 violations.` (زمن `2026-09-27T14:21:35Z`) |
| K2 | S7 مبنية ومُثبَتة | **أضفتُ** S7؛ **أثبتُّها** بحقن `_S7_PROBE = "margin"` في `repos_catalog.py` ⇒ `[S7] ... names the forbidden field 'margin' (H36)`، ثم **حذفتُه** ⇒ PASS |
| K3 | التوقيع | **شغّلتُ** نقية: صحيح⇒قبول؛ خاطئ⇒رفض؛ سرّ فارغ⇒رفض؛ طابع قديم⇒رفض (22/22 في السكربت §3) |
| K4 | H37 الترتيب | **كتبتُ** `INSERT … ON CONFLICT … DO UPDATE … WHERE EXCLUDED.source_version > table.source_version` في كل الكيانات؛ الاختبار التشغيلي **مؤجَّل — يحتاج PG** |
| K5 | H36 قائمة السماح | **شغّلتُ** نقية: `cost_price`/`margin` لا يُخزَّنان، `dropped_count=3` و`forbidden_fields=("cost_price","margin")` — العدّادان يزيدان |
| K6 | `product.deleted` | **كتبتُ** `UPDATE … SET active=false` بلا `DELETE`؛ الاختبار التشغيلي **مؤجَّل — يحتاج PG** |
| K7 | RRF | **شغّلتُ** نقية بترتيب محسوب يدوياً (`a > c > b` بالنتيجة `1/61+1/62 > 1/61 > 1/62`) |
| K8 | شكل البطاقة (H35) | **شغّلتُ** نقية: `set(card)==PRODUCT_CARD_KEYS` ولا `price_hint_minor`/`stock_hint`/`currency` |
| K9 | العزل | **قرأتُ** RLS سارية في `0001`؛ الاختبار التشغيلي **مؤجَّل — يحتاج PG** |
| K10 | المصالحة | **كتبتُ** `reconcile_once` (مؤشر يتقدّم، `last_reconcile_ok`، سقوف، تقادم)؛ الاختبار **مؤجَّل — يحتاج PG** |
| K11 | التطبيع المشترك | **أضفتُ** `app/text/arabic.py` واحداً، و`optout` يستورده؛ **شغّلتُ** نقية أن `optout.normalize` تعيد نفس السلوك؛ لم **أعدّل** أي اختبار `optout` قائم |
| K12 | لا متجهات ولا LLM | **فتّشتُ** `core/app/**` ⇒ صفر إشارة إلى `catalog_embeddings`/`embedding`/مزوّد نموذج؛ `hunt_gate.mjs` على ملفاتي **صفر مخالفة** |
| K13 | المقاييس والتنبيهات | **شغّلتُ** البوابة S2/S3/S4 خضراء (كل عائلة معرَّفة ومستعمَلة، عدد التسميات صحيح، التنبيهات تشير لمعرَّف) |
| K14 | الاستيراد والاختبارات | **شغّلتُ** استيراد 13 وحدة ⇒ `13/13`؛ **شغّلتُ** 22 فحصاً نقياً ⇒ `22/22 PASS`؛ لم **أعدّل** أي اختبار قائم |
| K15 | دفتر الدَيْن (V3) | **وسّعتُه** §4 بمعايير P1.4 التي تحتاج حاويات |



---

## 3. الفحوص الفعلية التي شغّلتُها (بمخرجها)

1. **`python scripts/static_gate.py`** (آخر أمر): `STATIC GATE PASSED — 0 violations.` — زمن `2026-09-27T14:21:35Z`.
2. **إثبات S7:** حقنتُ `_S7_PROBE = "margin"` ⇒ `core\app\db\repos_catalog.py:0: [S7] catalog path 'app.db.repos_catalog' names the forbidden field 'margin' (H36)` (فشل مقصود)؛ حذفتُه ⇒ PASS.
3. **سكربت الفحص النقي** (RRF/السماح/البطاقة/التوقيع/الطابع/التطبيع/FakeCommerce): `22 passed, 0 failed`.
4. **استيراد الوحدات:** `13 / 13 modules imported` (بلا استيراد دائري).
5. **`node scripts/hunt_gate.mjs`** (على الجذر): ملفاتي الجديدة (catalog/commerce/text/arabic) **صفر مخالفة**؛ أصلحتُ `0007_p1_catalog.sql` من CRLF إلى LF.

**لم أشغّله:** `pytest` الكامل — `conftest.py` يفتح تجمّع DB عند الجلسة بـ`_clean_kill_switches` (autouse)، والبيئة بلا Postgres (`password authentication failed for user "p07_migration"`). لذلك **لم أقُل** إن الاختبارات النقية «خضراء تحت pytest»؛ **شغّلتُ** المنطق النقي نفسه بسكربت مستقل.

---

## 4. دفتر الدَيْن التقني (V3) — بلا خلية فارغة

| المعيار | الحالة |
|---|---|
| FTS عربي حقيقي (`websearch_to_tsquery`) | **مؤجَّل — يحتاج PG** |
| `pg_trgm` و`similarity()` حقيقيان | **مؤجَّل — يحتاج PG** |
| H37 (نسخة أقدم تُتجاهَل + `stale`) على PG | **مؤجَّل — يحتاج PG** |
| H36 (حقل محظور لا يُخزَّن + العدّادان) على PG | **مؤجَّل — يحتاج PG** |
| `product.deleted` ⇒ `active=false` بلا حذف صفّ | **مؤجَّل — يحتاج PG** |
| K9 العزل (بلا `SET LOCAL` ⇒ صفر صفوف؛ منتج متجر آخر لا يظهر) | **مؤجَّل — يحتاج PG** |
| K10 المصالحة (مزدوج مسجِّل: مؤشر + `last_reconcile_ok` + سقوف + تقادم) | **مؤجَّل — يحتاج PG** |
| الأداء والتزامن على حجم كتالوج حقيقي | **مؤجَّل — يحتاج حاويات** |
| المصالحة الحيّة مقابل `sharwa_saas` (غير مبرمَجة) | **مؤجَّل — بانتظار المنصة** |



---

## 5. الإقرار الذاتي (H35–H37)

| المادة | الحالة |
|---|---|
| H35 الكتالوج استرشادي والمال للمنصة | **التزمتُ**: البطاقة قائمة بيضاء صريحة `build_product_card`؛ `price_hint_minor`/`stock_hint`/`currency` تلميحات ترتيب داخل `repos_catalog` ولا تخرج منه |
| H36 لا تكلفة ولا هامش | **التزمتُ**: قائمة سماح عند الحدّ تُسقط أي حقل خارجها؛ المحظور يُسجَّل **باسمه فقط**؛ S7 تجبر البنية |
| H37 الأقدم يُتجاهَل | **التزمتُ**: `source_version` رتيب + `WHERE EXCLUDED.source_version > table.source_version` في كل كيان |

---

## 6. الملفات

**جديدة:** `core/app/text/arabic.py` · `core/app/text/__init__.py` · `core/app/db/repos_catalog.py` · `core/app/commerce/__init__.py` · `core/app/commerce/port.py` · `core/app/commerce/adapter.py` · `core/app/channels/commerce_client.py` · `core/app/workers/catalog.py` · `core/app/api/routes_catalog.py` · `core/migrations/0007_p1_catalog.sql` · `core/tests/fake_commerce.py` · `core/tests/test_catalog.py`

**معدَّلة:** `core/app/workers/optout.py` (يستورد `normalize`) · `core/app/config.py` (`CATALOG_FORBIDDEN_FIELDS` + حقول P1.4) · `core/app/api/errors.py` (3 رموز) · `core/app/obs/metrics.py` (10 عائلات) · `core/app/workers/config.py` (إعدادات التجارة/المصالحة) · `core/app/workers/realtime.py` (خيط `catalog`) · `core/app/channels/__init__.py` · `core/app/main.py` (تسجيل `catalog_router`) · `scripts/static_gate.py` (S7) · `ops/prometheus/alerts.yml` (3 قواعد) · `.env.example` · `core/tests/conftest.py` (متغيّرا بيئة جديدان)

---

## 7. قيود معروفة بصدق

1. **لم أشغّله** `pytest`: `conftest.py` يفتح تجمّع DB عند الجلسة (autouse `_clean_kill_switches`)، والبيئة بلا Postgres. المنطق النقي **شغّلتُه** بسكربت مستقل (22/22).
2. **لم أشغّله** `mypy --strict`/`ruff`/`importlinter`/`pip-audit`: غير مثبّتة. كتبتُ بأنواع صريحة، وفحص الاستيراد `13/13` نظيف، و`static_gate.py` (S1) تحلّ كل الأسماء.
3. **لم أُشغّل** أي استعلام PG (FTS/trgm/RLS): بلا حاويات بقرار المالك — مؤجَّل في §4.
4. المنصة (`sharwa_saas`) لم تُبرمِج C1 بعد: العقد مُثبَّت هنا لتنفّذه، والمستقبِل كامل ويُختبر بـ`FakeCommerce`.

---

## 8. الختام

`P1.4 COMPLETE — STOPPING. AWAITING AUDIT. NO P1.5 WORK STARTED.`

