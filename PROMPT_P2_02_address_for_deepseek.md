# البرومبت المعماري الثاني عشر — P2.2: حلّ العنوان والـGazetteer

**من:** المهندس المعماري
**إلى:** المنفّذ (DeepSeek داخل Cline)
**المشروع:** `sharwa_ai` — `C:\sharwaai\sharwa-ai`
**المرحلة السابقة:** P2.1 — **APPROVED** (تنفيذ التشغيل مؤجَّل إلى المضيف). `241 passed`، صفر مخالفة، S14 أثبتت نفسها على عيب حقيقي واكتشفت ثانياً.
**نطاق هذه الدفعة:** أن يعرف البوت **أين** يُسلِّم، بلا أن يخترع مكاناً.

---

## §0 — ما شغّلتُه أنا، وما اكتشفه

قيد Docker عندك حقيقي، وامتناعك عن تأليف مخرجات هو ما جعل ما يلي ممكناً. **ثبّتُ PostgreSQL 16.13 بـ`pgvector 0.6.0` و`PostGIS 3.4.2` في بيئتي وشغّلتُ ما لم تستطعه:**

**ما نجح — وهو دَيْن منذ P0:**

```
applied: 0001_baseline, 0002_p0_api, 0003_p1_outbound, 0004_p1_turn, 0005_p1_inbox,
         0006_p1_ws, 0007_p1_catalog, 0008_p1_llm, 0009_p1_vector, 0010_p1_summary,
         0011_p1_verifier
```

والأهمّ، **H2 مُثبَتة بمستأجرَين حقيقيين**:

```
  total customer rows in table: 2
  tenant A session: sees 1 row(s); other tenant visible = False
  tenant B session: sees 1 row(s); other tenant visible = False
  cross-tenant INSERT refused by RLS: InsufficientPrivilege
```

**وما فشل — وهو خطوتك صفر:**

```
233 failed, 9 passed, 243 deselected, 27 errors in 62.04s
```

---

## §1 — الخطوة صفر

### 1.1 F-P2-02 [حرج] — الحزمة الموسومة `db` معطَّلة منذ P1.5

الأسباب مرتّبة بالعدد، من تشغيلي:

| العدد | السبب |
|---:|---|
| **204** | `TenantContextError: db pool not initialized - call init_pool() at startup` |
| **22** | `AttributeError: module 'app.db.testsupport' has no attribute 'delete_tenant_full'` |
| **10** | `TypeError: insert_channel_account() got an unexpected keyword argument 'engine'` |
| 5 | اتصال Redis (بيئي — لم أشغّله، تجاهله) |

**آلية الـ204، وقد شخّصتُها بدقّة:** `tests/test_tenant_isolation.py` يحمل `pytestmark = pytest.mark.db` ويُنتقى فعلاً بـ`-m db`، ومع ذلك:

```
$ pytest "…::test_random_cross_tenant_pairs_leak_nothing" -m db --fixtures-per-test
------- fixtures used by test_random_cross_tenant_pairs_leak_nothing[0] --------
                    ← لا شيء
```

`conftest.pytest_collection_modifyitems` يربط `db_pool` و`_clean_kill_switches` بـ`item.add_marker(pytest.mark.usefixtures(...))`، وعلى `pytest 9.1.1` تُحسب إغلاقة التجهيزات **قبل** هذا الخطّاف، فالعلامة المُضافة حينها بلا أثر.

**وهذا على صحيفتي أنا لا صحيفتك:** التغيير هو N2 من تدقيقي لـP1.5، وتحقّقتُ منه باتجاه واحد («الحزمة النقية تعمل بلا خدمة») ولم أتحقّق من الاتجاه الآخر («واختبارات القاعدة ما زالت تحصل على قاعدتها»).

**الإصلاح — بثلاثة بنود:**

1. **اربط التجهيزتين بطريقة تعمل فعلاً.** الأسلم تجهيزة `autouse=True` **مشروطة** تقرأ الوسم في زمن التشغيل:
   ```python
   @pytest.fixture(autouse=True)
   def _db_for_marked(request):
       if request.node.get_closest_marker("db"):
           request.getfixturevalue("db_pool")
           request.getfixturevalue("_clean_kill_switches")
   ```
   فتبقى الحزمة النقية مستقلّة عن القاعدة (التجهيزة لا تطلب شيئاً حين لا يوجد الوسم)، **وتحصل** الموسومة على قاعدتها. أي حلّ آخر يحقّق الاتجاهين مقبول.
2. **وأثبِت الاتجاهين معاً، لا واحداً:**
   - `pytest tests -q` بلا قاعدة ⇒ يمرّ (العدد الحالي)، **وصفر** اختبار موسوم يُنفَّذ.
   - `pytest tests -q -m db --collect-only` ⇒ **كل** اختبار موسوم يُظهر `db_pool` في `--fixtures-per-test`.
   - وأرفِق مخرج `--fixtures-per-test` لاختبار واحد موسوم قبل الإصلاح وبعده. **هذا هو الإثبات.**
3. **أصلِح العيبين الآخرين:** عرِّف `testsupport.delete_tenant_full` (حذف كامل لمستأجر بترتيب المفاتيح الأجنبية)، ووفّق توقيع `insert_channel_account` مع مواقع ندائه. ولا تحذف اختباراً لتُسكِت فشلاً — إن كان اختبار خاطئاً فصحّحه وقل ذلك.

### 1.2 S16 — حلّال الأسماء يمسح شجرة الاختبارات أيضاً

`delete_tenant_full` **اسم يُنادى ولا يوجد** — نفس عائلة F-P1-07 حرفياً، ومرّ لأن حلّال الأسماء في `static_gate.py` يمسح `core/app/**` ولا يمسح `core/tests/**`.

وسّع نطاقه ليشمل `core/tests/**` بنفس مراحل S1 (a/b/c/d). وتوقّع ضجيجاً في البداية (تجهيزات pytest، وحدات مُرقَّعة بـ`monkeypatch`) — **عالِجه بتضييق القاعدة لا بقائمة استثناءات بالأسماء**: مثلاً استثناء الأسماء المضبوطة بـ`monkeypatch.setattr` في الملف نفسه. وإن بقي إيجاب كاذب حقيقي، اذكره بمثاله في التقرير واقترح التضييق ولا تُسكِته.

**الإثبات:** إعادة حذف `delete_tenant_full` مؤقّتاً ⇒ البوابة تعدّها مخالفة.

### 1.3 لقطة git

```bash
git add . && git commit -m "chore: checkpoint before P2.2"
git log --oneline -1 && git status --porcelain
```

### 1.4 وبعد الإصلاح — شغّل ما تستطيع

إن توفّر Docker أو PostgreSQL محلي عندك، شغّل `-m db` وأرفِق المخرج كما هو. **وإن لم يتوفّر، قل ذلك كما قلتَه في P2.1** ولا تؤلّف — سأشغّلها أنا في التدقيق.

---

## §2 — لماذا حلّ العنوان قبل `BackInStock`

خيّرني المالك بين الاثنين، واخترتُ حلّ العنوان لسببين:

1. **خاصّية `BackInStock` الجوهرية تزامنية**: «لا بيع زائد تحت طلبين متزامنين». ولا تُثبَت بقراءة كود ولا بمزدوج مسجِّل — تحتاج قاعدة حقيقية وعمليتين متسابقتين. وحزمة القاعدة معطَّلة اليوم (F-P2-02)، فبناؤها الآن يعني بناء **أخطر ميزة في النظام بلا وسيلة لإثبات صحّتها**.
2. **حلّ العنوان حتميّ**: جداول بحث ومطابقة نصّية وقواعد قرار مكتوبة — يُثبَت بحزمة نقية كما أُثبتت `optout` و`verify_rules`. ويكمل مسار P1.7 طبيعياً: عرفنا حالة الطلب، وبقي **أين يذهب**.

`BackInStock` يبقى في `P2.3`، بعد أن تعمل حزمة القاعدة وتُثبَت تحت تزامن حقيقي.

---

## §3 — البنية موجودة في `0001`. استعملها ولا تخترع بديلاً

كما في P1.7، القاعدة مصمَّمة أصلاً. **لا جدول جديد ولا ترحيل في هذه الدفعة:**

```sql
CREATE TABLE geo_gazetteer (
  id bigserial PRIMARY KEY,
  tenant_id uuid REFERENCES tenants(id),     -- NULL = مرجع مشترك؛ مضبوط = معلَم تعلّمه التاجر
  level text NOT NULL CHECK (level IN ('country','governorate','district','area','neighborhood','landmark')),
  name_ar text NOT NULL,
  name_norm text NOT NULL,                   -- عربية مُطبَّعة
  parent_id bigint REFERENCES geo_gazetteer(id),
  geom geometry(Geometry, 4326) NOT NULL,
  confidence numeric(3,2) NOT NULL DEFAULT 1.00 CHECK (confidence BETWEEN 0 AND 1)
);

CREATE TABLE geo_synonyms (term_norm text PRIMARY KEY, canonical text NOT NULL, kind text NOT NULL);

CREATE TABLE address_resolutions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  conversation_id uuid REFERENCES conversations(id),
  input jsonb NOT NULL, candidates jsonb NOT NULL DEFAULT '[]'::jsonb,
  decision text NOT NULL CHECK (decision IN ('accepted','confirm_with_customer','disambiguate','ask_for_pin','rejected')),
  confidence numeric(3,2) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  source text NOT NULL CHECK (source IN ('pin','geocoder','gazetteer_centroid')),
  location geometry(Point, 4326), structured jsonb,
  CHECK (decision <> 'accepted' OR location IS NOT NULL)   -- ← اقرأ هذا السطر مرّتين
);

CREATE OR REPLACE FUNCTION app.point_governorate(p_lat, p_lng) RETURNS bigint ...  -- ST_Covers، مُمنَّحة لـsharwa_app
```

ثلاث نتائج تلزمك:

1. **قيد `CHECK (decision <> 'accepted' OR location IS NOT NULL)` هو الدستور مكتوباً في SQL**: لا يُقبَل عنوان بلا نقطة مُتحقَّقة. القاعدة نفسها ترفض «مقبول بلا إحداثيات» — لا تلتفّ عليه بحالة قرار أخرى.
2. **`source` قائمة مغلقة من ثلاثة**: `pin` · `geocoder` · `gazetteer_centroid`. **لا مصدر رابع، ولا مصدر اسمه النموذج.**
3. **`tenant_id` في `geo_gazetteer` يقبل NULL** = بيانات مرجعية مشتركة. فالقراءة تحتاج `tenant_id IS NULL OR tenant_id = current_tenant()` — وسياسة `gazetteer_read` في `0001` تفعل ذلك بالضبط. تحقّق منها ولا تُعِد كتابتها.

---

## §4 — المواد الدستورية (H64–H68)

- **H64 — الإحداثيات لا تأتي من نموذج، أبداً.** `lat`/`lng` تأتي من ثلاثة مصادر حصراً: دبّوس أرسله العميل، أو مُرمِّز جغرافي خارجي، أو مركز شكل من `geo_gazetteer`. النموذج **يستخرج أسماء أماكن نصّية فقط**؛ أي رقم إحداثي في مخرجه يُرفَض ويُسجَّل. (`verifier_blocks.reason` يحوي أصلاً السبب المصمَّم `coord_from_model` — استعمله.)
- **H65 — الثقة تُحسَب ولا تُقدَّر.** درجة الثقة دالّة **نقية حتمية** من إشارات معدودة ومكتوبة (مستوى المطابقة · عدد المرشّحين · اتّساق الأب · وجود دبّوس). لا رقم من نموذج، ولا ثابت سحري بلا اشتقاق مكتوب.
- **H66 — الشكّ يسأل ولا يُخمّن.** دون عتبة القبول: `confirm_with_customer` أو `disambiguate` أو `ask_for_pin` — **ولا قبول صامت**. تسليمٌ إلى عنوان مخطوء أغلى من سؤال.
- **H67 — العنوان بيانات شخصية.** لا عنوان ولا إحداثيات في سجلّ ولا في تسمية مقياس ولا في إطار WS (H20). موضعها `address_resolutions` وحده، وهو تحت RLS.
- **H68 — ما يتعلّمه التاجر يبقى للتاجر.** أي معلَم يُضاف من محادثة يُكتب بـ`tenant_id` مضبوط. **لا يُكتب صفّ بـ`tenant_id IS NULL` من مسار محادثة أبداً** — المرجع المشترك يُملأ بأداة مشغّل فقط.

---

## §5 — ما تبنيه

| الملف | مسؤوليته |
|---|---|
| `core/app/geo/__init__.py` · `normalize.py` | تطبيع أسماء الأماكن: يعيد استعمال `app.text.arabic.normalize` (**لا تكتب مطبِّعاً ثانياً — H21/C6**) ويضيف طبقة المرادفات من `geo_synonyms` |
| `core/app/geo/resolve.py` | **القرار الحتميّ النقي**: يتلقّى مرشّحين مقروئين مسبقاً ويُرجع `AddressDecision` مجمَّدة. لا IO، لا قاعدة، لا نموذج |
| `core/app/db/repos_geo.py` | القراءات والكتابات: بحث الـgazetteer، `app.point_governorate`، كتابة `address_resolutions` |
| `core/app/workers/address.py` | المنسّق: القراءة ⇒ القرار ⇒ الكتابة ⇒ الردّ عبر `compose` |
| `core/app/tools/resolve_address.py` | أداة ثانية في السجل (نفس قيود H50: بلا قاعدة وبلا شبكة) |
| `scripts/seed_gazetteer.py` | أداة مشغّل تُحمّل محافظات اليمن ومدنها الكبرى من ملف بيانات مُودَع — **وهي الطريق الوحيد لكتابة صفّ بـ`tenant_id IS NULL`** |

**تُعدَّل:** `compose.py` (دالة خامسة مصرَّح بها) · `templates.py` (قوالب §7) · `llm/router.py` (نيّة سادسة `delivery_address`) · `turn.py` · `tools/registry.py` · `config.py` · `metrics.py` · `alerts.yml` · `static_gate.py` (S17) · `conftest.py` و`testsupport.py` (§1).

**ممنوع لمسه:** `app/text/arabic.py` · أي ترحيل · `PHASE_GATE.md` · وكل ما جمّدته P1 عدا ما ذُكر.

---

## §6 — القرار الحتميّ

```python
@dataclass(frozen=True)
class AddressCandidate:
    gazetteer_id: int; level: str; name_norm: str; parent_id: int | None
    match_kind: str          # exact | synonym | prefix   (لا مطابقة ضبابية في هذه الدفعة)
    tenant_scoped: bool

@dataclass(frozen=True)
class AddressDecision:
    decision: str            # accepted | confirm_with_customer | disambiguate | ask_for_pin | rejected
    confidence: float        # محسوبة، لا مُقدَّرة (H65)
    source: str | None       # pin | geocoder | gazetteer_centroid — لا رابع
    gazetteer_id: int | None
    reason_code: str         # قائمة مغلقة، بلا نصّ حرّ
```

**قواعد القرار، بالترتيب ومكتوبة في الإعداد:**

1. **دبّوس مُرسَل ⇒ `accepted` بـ`source='pin'`** وثقة قصوى. الدبّوس حقيقة من القناة لا استنتاج. (وتحقّق أنه داخل حدود الدولة عبر `app.point_governorate`؛ خارجها ⇒ `rejected` بسبب `pin_outside_coverage`.)
2. **مرشّح واحد بمطابقة تامّة على مستوى `district` فأدقّ ⇒ `accepted`** بـ`source='gazetteer_centroid'` إن تجاوزت الثقة `ADDRESS_ACCEPT_THRESHOLD`.
3. **أكثر من مرشّح متقارب ⇒ `disambiguate`**: اعرض حتى `ADDRESS_MAX_CANDIDATES` (3) **بأسمائها من الـgazetteer حرفياً**، ولا تخترع وصفاً.
4. **مرشّح واحد بثقة متوسطة ⇒ `confirm_with_customer`**: «هل تقصد …؟» بالاسم الحرفيّ.
5. **لا مرشّح، أو `governorate` وحدها (أخشن من أن يُسلَّم إليها) ⇒ `ask_for_pin`.**
6. **مخرج النموذج فيه رقم يشبه إحداثياً ⇒ `rejected`** بسبب `coord_from_model`، **ويُكتب صفّ في `verifier_blocks`** بذلك السبب (الحقل مصمَّم له في `0001`).

**حساب الثقة (H65)** — دالّة نقية من إشارات معدودة، وكل وزن في الإعداد بافتراض مكتوب:

```
confidence = w_level[level] × w_match[match_kind] × penalty(n_candidates) × parent_bonus
```

`w_level` أعلى كلّما دقّ المستوى؛ `w_match`: `exact > synonym > prefix`؛ `penalty` يقلّ بزيادة المرشّحين؛ `parent_bonus` حين يطابق الأب محافظةً ذُكرت في النصّ نفسه. **مقصوصة إلى `[0,1]`**، وحتمية: نفس المدخل ⇒ نفس الرقم. واكتب اختبار نقاء يثبت ذلك.

---

## §7 — الردّ

دالة تركيب **خامسة مصرَّح بها** في `compose.py` (حدِّث الـdocstring إلى «خمس دوال لا سادسة»): `compose_address_options(candidates, *, template)` — تسرد أسماء المرشّحين **من الـgazetteer حرفياً**، بلا وصف مولَّد وبلا إحداثيات في النصّ (H67).

قوالب جديدة: `address_need_pin` · `address_confirm` · `address_disambiguate` · `address_rejected` · `address_out_of_coverage`. محافظة، بلا وعد بوقت، وتمرّ تلقائياً على فحص الإقلاع الذاتي في `verify.build_rules`.

**ولا إحداثيات في نصّ يصل العميل، أبداً** — الدبّوس يعود إليه كتأكيد باسم المكان لا بأرقام.

---

## §8 — S17 في البوابة

**S17-a — لا إحداثيات من النموذج (H64).** أي وحدة تحت `app/llm/**` أو أي مسار يقرأ مخرج النموذج يُمنَع أن تُسمّي `lat`/`lng`/`latitude`/`longitude`/`coordinates` أو تبني `ST_MakePoint`. الإحداثيات تُلمَس في `app/geo/**` و`app/db/repos_geo.py` وحدها.

**S17-b — القرار نقيّ.** `app.geo.resolve` بقائمة استيراد مغلقة: `dataclasses` · `typing` · `enum` · `math` · `__future__` (**ولا `app.text.arabic`** — التطبيع يقع قبله في `normalize.py`). لا `app.db`، ولا `psycopg`، ولا `app.llm`، ولا `httpx`.

**S17-c — لا قبول بلا نقطة.** أي نداء يكتب `address_resolutions` بـ`decision="accepted"` يجب أن يمرّر `location=` من متغيّر غير `None` حرفياً؛ ولا نداء يكتب `decision` بقيمة خارج القائمة المغلقة الخمس.

**S17-d — مرجع مشترك بيد المشغّل وحده (H68).** كتابة `geo_gazetteer` بـ`tenant_id=None` (أو بحذف الوسيط) مسموحة **فقط** في `scripts/seed_gazetteer.py`. أي كتابة في `app/**` يجب أن تمرّر `tenant_id=` من متغيّر.

**الإثبات:** أربع مخالفات مصطنعة ⇒ `4 violation(s)` بمعرّفاتها، ثم `0 violations` بزمنه.

---

## §9 — الاختبارات

`test_geo_normalize.py`: مرادفات الدارجة (جولة/دوّار/تقاطع) · تشكيل وألف/همزة · أرقام عربية-هندية · نقاء.

`test_geo_resolve.py` — جدول قرارات، وكل صفّ باسمه: دبّوس ⇒ `accepted`/`pin` · دبّوس خارج التغطية ⇒ `rejected` · مرشّح واحد تامّ على `district` ⇒ `accepted` · ثلاثة متقاربة ⇒ `disambiguate` بثلاثة أسماء حرفية · واحد متوسط ⇒ `confirm_with_customer` · صفر ⇒ `ask_for_pin` · `governorate` وحدها ⇒ `ask_for_pin` · **مخرج نموذج فيه `lat`/`lng` ⇒ `rejected` بـ`coord_from_model`** · نقاء (عشر مرّات ⇒ نفس `AddressDecision`) · حدّية الثقة (عند العتبة بالضبط ⇒ مقبول؛ أقلّ بـ0.01 ⇒ تأكيد).

`test_address_worker.py` (مزدوجات مسجِّلة): `accepted` ⇒ صفّ واحد بـ`location` غير فارغة · أي قرار آخر ⇒ صفّ بـ`location=None` ولا يخالف الـ`CHECK` · **لا عنوان ولا إحداثي في أي سطر سجل أو تسمية مقياس** (H67) · معلَم متعلَّم يُكتب بـ`tenant_id` مضبوط لا NULL (H68).

**وثلاثة اختبارات موسومة `@pytest.mark.db`** — الآن بعد أن صارت تعمل فعلاً: `app.point_governorate` تُرجع المحافظة الصحيحة لنقطة داخلها و`NULL` لنقطة خارجها · قيد `CHECK` يرفض `accepted` بلا `location` (أثبِت الرفض) · وسياسة `gazetteer_read` تُظهر الصفوف المشتركة وصفوف التاجر وتُخفي صفوف تاجر آخر.

**الحزمة النقية صفر أحمر والعدد أكبر من `241`، وحزمة `db` تُشغَّل وتُبلَّغ نتيجتها كما هي.**

---

## §10 — التسليم

القواعد نفسها. **وما يُرفَق حرفياً:**

- `git log --oneline -1` و`git status --porcelain`.
- **إثبات F-P2-02 بالاتجاهين**: `--fixtures-per-test` لاختبار موسوم **قبل** الإصلاح (صفر تجهيزة) و**بعده** (`db_pool` ظاهرة)؛ و`pytest tests -q` بلا قاعدة يمرّ.
- مخرج S16 على `delete_tenant_full` المحذوفة مؤقّتاً.
- مخرج المخالفات الأربع لـS17 ثم `0 violations` بزمنه.
- `pytest tests -q` كاملاً، و`pytest tests -q -m db` كاملاً **إن توفّرت قاعدة** — وإلّا فالقيد مُعلَناً كما فعلتَ في P2.1.
- جملة صريحة: **هل بقي في الشجرة أي مسار يكتب إحداثياً من مخرج نموذج؟** بمخرج البوابة.
- جملة صريحة: **هل أُنشئ جدول أو ترحيل؟** (المتوقَّع: لا.)

**اختم بـ:** `P2.2 COMPLETE — STOPPING. AWAITING AUDIT. NO P2.3 WORK STARTED.`

وقبله: `F-P2-02 fixed (db suite runs, both directions proven) · S16 names in tests · deterministic address decision (no model coordinates) · S17 a/b/c/d proven · no new table · gate 0 violations at <ZULU>`

---

## §11 — أسئلة مفتوحة

- **OQ-P2-04:** مصدر بيانات الـgazetteer لليمن ورخصته — المالك يقرّ المصدر قبل أي تحميل.
- **OQ-P2-05:** مُرمِّز جغرافي خارجي (`source='geocoder'`): هل يُستعمل أصلاً؟ وما مزوّده وسقف كلفته؟ **لا تبنِه في هذه الدفعة** — اترك المصدر معرَّفاً وغير مُنفَّذ.
- **OQ-P2-06:** عتبات الثقة وأوزانها تقديرات مكتوبة لا قياسات؛ تُعاير على محادثات حقيقية لاحقاً.
- **OQ-P2-07:** هل يُحفَظ العنوان المقبول على العميل لإعادة استعماله؟ **التنفيذ الحالي: لا** — كل محادثة تُحلّ عنوانها، وحفظه قرار مالك له أثر في الخصوصية.

---

**ابدأ بـ§1 بالترتيب. ولا تكتب سطراً في `app/geo/` قبل أن يُظهر `--fixtures-per-test` تجهيزة `db_pool` على اختبار موسوم.**
