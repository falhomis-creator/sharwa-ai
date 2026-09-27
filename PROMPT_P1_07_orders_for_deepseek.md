# البرومبت المعماري التاسع — P1.7: استدعاء الأدوات وتتبّع الطلبات

**من:** المهندس المعماري
**إلى:** المنفّذ (DeepSeek داخل Cline)
**المشروع:** `sharwa_ai` — `C:\sharwaai\sharwa-ai`
**المرحلة السابقة:** P1.6 — **APPROVED** (`169 passed`، صفر مخالفة، F-P1-09 مُغلَق بـ`0011`، والعمل مُلتزَم في `a93cb4f`). لا تعد فتحها.
**نطاق هذه الدفعة:** أداة واحدة حقيقية (`track_order`)، وطبقة أدوات لا تلمس القاعدة، وهوية صارمة لا تُكشَف حالة طلب بدونها.

---

## §0 — الخطوة صفر (إلزامية، بهذا الترتيب)

### 0.1 نقطة حفظ في git

```bash
cd C:\sharwaai\sharwa-ai
git add .
git commit -m "chore: checkpoint before P1.7"
git log --oneline -1
git status --porcelain
```

أرفِق المخرجين حرفياً. صار هذا إجراءً دائماً في كل دفعة.

### 0.2 N2 من تدقيق P1.6 — **أمنيّ في هذه الدفعة تحديداً، فأصلحه قبل أي شيء آخر**

في P1.6 أثبتُّ أن التهريب بالمسافات بين الحروف يمرّ من المدقّق:

```
'ك.ل.ب'     -> BLOCK(profanity)
'ك ل ب'     -> PASS        ← الثغرة
's a l l a' -> PASS        ← الثغرة
```

والسبب أن `_SQUEEZE_TRANS` يحوي المسافة، لكن التقسيم على المسافات **يسبق** `squeeze`، فالمسافة لا تصل الدالة أبداً (كود ميّت).

قدّرتُ خطرها في P1.6 بأنه ضعيف، لأن النصّ المدقَّق كان يأتي من `compose.py` حصراً: قالب معتمد أو نصّ التاجر. **وP1.7 هي التي تُبطل هذا التقدير:** بعد هذه الدفعة سيدخل إلى الردّ الصادر نصٌّ مشتقّ من حالة طلب — أي من مصدر خارج التاجر. فالثغرة تصير مادّية.

**الإصلاح في `verify_rules.py`:** أضِف مطابقة على **نافذة مضمومة**: لكل نافذة من `2..N` رمزاً متجاورة، قارِن `squeeze("".join(window))` بـ`squeeze("".join(phrase.tokens))` **بالمساواة** — لا بالاحتواء، فتبقى مصيدة «زبون» مغلقة. `N` سقف مكتوب في الإعداد (`VERIFY_JOIN_WINDOW_MAX`، افتراضه `6`). واحذف المسافة من `_SQUEEZE_TRANS` أو وثّق أنها للتوثيق لا للعمل.

**الاختبارات المطلوبة:** `ك ل ب` و`s a l l a` تُحجَبان؛ و`كل بلد` و`زبون`/`زبدة`/`كلبي`/`كلبين`/`كلاب` تمرّ كلها (ضمّ الرموز لا يخلق إيجاباً كاذباً لأن المقارنة بالمساواة).

### 0.3 N3 من تدقيق P1.6 — سطران يجعلان الحارس يحرس

`test_substring_trap_passes` يستعمل `كلاب` مقابل المحظور `كلب`، و`كلب` **ليست** سلسلة فرعية من `كلاب` (ك‑ل‑ا‑ب) — فمطابِقٌ ساذج بـ`in` يمرّ هذا الاختبار أيضاً، أي أن الاختبار لا يحرس ما تقول ترويسته إنه يحرسه. استبدل الحالة بـ`كلبي` وأضِف `كلبين` و`زبون`.

### 0.4 N1 من تدقيق P1.6 — حدث لا يكذب على الواجهة

في `verify._write_safe`: عندما `paused=False` (البوت كان موقوفاً أصلاً) **لا تُرسِل `conversation.updated` إطلاقاً** — الحالة لم تتغيّر فلا شيء يُعلَن، والحمولة الحالية تُعلن `handoff_reason = verifier_*` بينما العمود في القاعدة يحمل سبب `turn.py`. أبقِ `handoff.requested` وحده (يكفي لإعلام الموظف بأن ردّاً حُجِب).

### 0.5 N4 من تدقيق P1.6 — سطر واحد يحذف عملاً مكرّراً وثغرة نوع

في `verify.insert_verified_outbox`: نادِ `verify_rules.approve` **مرّة واحدة** وافحص `isinstance(result, verify_rules.VerifiedText)` بدل نداء `check_text` ثم `approve` (نداءان لدالّة واحدة، وإسناد اتحاد `VerifiedText | RuleVerdict` إلى معامل موقَّع `VerifiedText`).

> N5 (فرع `closed` يرفع عدّاد إرسال لن يحدث) اتركه كما هو حتى تلمس الملف لسبب آخر.

---

## §1 — المواد الدستورية الجديدة (H50–H55) في `docs/CONSTITUTION.md`

- **H50 — الأداة لا تلمس القاعدة.** أي وحدة تحت `app/tools/` لا تستورد `app.db` ولا `psycopg` ولا `httpx` ولا `redis` ولا `app.llm` ولا `app.channels`، ولا تحوي أي نداء `.execute(`. الأداة دالة تتلقّى `ToolContext` مجمَّداً (قيم قُرئت مسبقاً + منفذ مُحقَّن) وتُعيد نتيجة مجمَّدة. المنسّق (`app/workers/orders.py`) هو من يقرأ ويكتب.
- **H51 — النموذج يختار الأداة، والكود يستخرج الوسائط.** «استدعاء الأدوات» في هذا المشروع يعني: النموذج يصنّف النية (بما فيها `order_status`)، **والكود** يستخرج رقم الطلب والهاتف بتعبير نمطي حتمي. لا نطلب من النموذج وسائط منظَّمة ولا نصدّقها. امتداد مباشر لـH38.
- **H52 — الهوية الصارمة قبل أي كشف.** من رقم واتساب يطابق هاتف الطلب: رقم الطلب يكفي. من رقم مختلف: **رقم الطلب + الهاتف المسجَّل على الطلب معاً في المحادثة**، وإلا لا كشف. وإن استحال إثبات أن هوية القناة رقم هاتف، فالمسار **يتدهور إلى `other_number`** لا إلى `same_number` (الفشل مُغلَق).
- **H53 — لا Oracle.** «طلب غير موجود» و«هاتف غير مطابق» و«طلب لتاجر آخر» تُعيد **نتيجة واحدة لا تفرّق**، ويصل العميل **قالباً واحداً**. البنية هي الضمانة: نوع النتيجة لا يحمل سبباً، فالكود **لا يستطيع** أن يفرّق.
- **H54 — رقم الطلب لا يُخزَّن ولا يُسجَّل صريحاً.** `order_lookup_attempts.order_ref_hash` بصمة HMAC-SHA256 بمفتاح من البيئة — لا SHA-256 عارية، لأن أرقام الطلبات منخفضة الإنتروبيا فالبصمة العارية قابلة للعكس بالتخمين. ولا رقم طلب ولا هاتف في سجل ولا في تسمية مقياس (H20).
- **H55 — نصّ المنصّة ليس قالباً معتمداً.** حالة الطلب تُترجَم عبر **خريطة مغلقة** من رموز حالة المنصّة إلى تسميات عربية معتمدة؛ رمز غير معروف ⇒ قالب عام. لا يُعاد نصّ المنصّة الخام إلى العميل أبداً.

---

## §2 — ما تكتبه، وما تعدّله، وما لا تلمسه

**جديدة:**

| الملف | مسؤوليته الوحيدة |
|---|---|
| `core/app/tools/__init__.py` | توثيق الحدّ |
| `core/app/tools/registry.py` | سجل مغلق: `TOOLS: dict[str, ToolSpec]` بمفاتيح حرفية، و`ToolContext`/`ToolResult` مجمَّدان |
| `core/app/tools/extract.py` | استخراج حتمي نقي لرقم الطلب والهاتف (لا IO) |
| `core/app/tools/track_order.py` | الأداة: تتلقّى `ToolContext` وتُعيد `OrderLookup` — بلا قاعدة وبلا شبكة مباشرة |
| `core/app/workers/orders.py` | المنسّق: الحدّ، القراءة، نداء المنصّة **خارج المعاملة**، التسجيل |
| `core/tests/test_tools_extract.py` · `test_track_order.py` · `test_orders.py` | الاختبارات |

**تُعدَّل:**

| الملف | التعديل |
|---|---|
| `core/app/workers/verify_rules.py` · `verify.py` | §0.2 / §0.4 / §0.5 |
| `core/app/commerce/port.py` | عمليّة ثالثة: `lookup_order` |
| `core/app/commerce/adapter.py` · `core/app/channels/commerce_client.py` | تنفيذها عبر HTTP |
| `core/tests/fake_commerce.py` | `lookup_order` مع **منع الـOracle** (§5) |
| `core/app/workers/compose.py` | دالة رابعة **مصرَّح بها** (§6) |
| `core/app/workers/templates.py` | قوالب §6 |
| `core/app/llm/router.py` | نيّة خامسة `order_status` |
| `core/app/workers/turn.py` | توجيه النيّة الخامسة إلى `orders.py` |
| `core/app/db/repos_outbox.py` | `insert_order_lookup_attempt` و`order_lookup_blocked` فقط |
| `core/app/workers/config.py` · `obs/metrics.py` · `ops/prometheus/alerts.yml` | §7 / §8 |
| `scripts/static_gate.py` | المرحلة S11 (§9)، وتوسيع مسار S7 (§6) |

**ممنوع لمسه:** `core/app/text/arabic.py` (H21)، `core/migrations/0001_baseline.sql` (H14 — ولا أي ترحيل مُطبَّق)، `core/app/db/repos_summary.py`، و**`docs/PHASE_GATE.md`**.

**ولا ترحيل في هذه الدفعة.** كل ما تحتاجه موجود في `0001` (§3). اذكر ذلك صريحاً في التقرير كقرار مقصود.

---

## §3 — البنية موجودة في `0001` — استعملها ولا تخترع بديلاً

هذا أهمّ بند في البرومبت. القاعدة 2.3 التي أمر بها المالك **مُصمَّمة أصلاً في الأساس**، فلا تُنشئ جدولاً ولا دالة:

```sql
CREATE TABLE order_lookup_attempts (
  id              bigserial PRIMARY KEY,
  tenant_id       uuid NOT NULL REFERENCES tenants(id),
  conversation_id uuid REFERENCES conversations(id),
  customer_id     uuid NOT NULL REFERENCES customers(id),
  path            text NOT NULL CHECK (path IN ('same_number','other_number')),
  order_ref_hash  text NOT NULL,
  outcome         text NOT NULL CHECK (outcome IN ('allowed','denied','blocked')),
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION app.order_lookup_blocked(p_customer uuid, p_order_ref_hash text)
RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT (SELECT count(*) FROM order_lookup_attempts
           WHERE customer_id = p_customer AND path = 'other_number' AND outcome = 'denied'
             AND created_at > now() - interval '24 hours') >= 3
      OR (SELECT count(*) FROM order_lookup_attempts
           WHERE order_ref_hash = p_order_ref_hash AND outcome = 'denied'
             AND created_at > now() - interval '24 hours') >= 5
$$;
```

ثلاث نتائج تلزمك:

1. **العتبتان (3 و5 خلال 24 ساعة) تعيشان في SQL وحدها.** لا تُعِد تنفيذهما في Python ولا تجعلهما إعداداً — سلطة واحدة لا اثنتان تتباعدان. الدالة مُمنَّحة لـ`sharwa_app` في `0001` وعليها فهرسان جزئيان للرفض.
2. **`REVOKE UPDATE, DELETE` مطبَّق على الجدول** في `0001` مع `verifier_blocks` و`llm_calls` — «لا شيء يمحو أثر التدقيق». فالكتابة إضافة فقط، وRLS مطبَّقة بالحلقة العامة لوجود `tenant_id`.
3. **`order_ref_hash` اسمه بصمة، لا رقم.** لا تكتب الرقم الصريح فيه (H54).

ولا يوجد جدول `orders` في `sharwa_ai` — **وهذا صحيح**: حقيقة الطلب تعيش في `sharwa_saas` وتُقرأ عبر واجهة المنصّة. لا تُنشئ جدول طلبات ولا تُخزّن حالة طلب في قاعدتنا.

---

## §4 — طبقة الأدوات

### 4.1 السجل المغلق

في `registry.py`:

```python
@dataclass(frozen=True)
class ToolContext:
    """كل ما تحتاجه الأداة، مقروءاً مسبقاً. لا اتصال قاعدة هنا."""
    tenant_id: uuid.UUID
    conversation_id: uuid.UUID
    channel_phone_e164: str | None   # هوية القناة بعد التطبيع، أو None إن استحال
    message_texts: tuple[str, ...]   # نصوص الدور الحالي
    commerce: Any                    # CommercePort مُحقَّن
    settings: Any

@dataclass(frozen=True)
class ToolSpec:
    name: str
    run: Callable[[ToolContext], ToolResult]

TOOLS: dict[str, ToolSpec] = {"track_order": ToolSpec("track_order", track_order.run)}
```

المفاتيح **حرفية**: لا `register()` ولا استيراد ديناميكي ولا `globals()`. من يريد أداة جديدة يكتب مفتاحاً حرفياً ويمرّ على المراجعة.

### 4.2 الاستخراج الحتمي (H51)

`extract.py` نقي بلا IO:

```python
def extract_order_ref(texts: tuple[str, ...], pattern: re.Pattern[str]) -> str | None
def extract_phone_candidates(texts: tuple[str, ...]) -> tuple[str, ...]
```

- `pattern` من الإعداد (`ORDER_REF_PATTERN`) بافتراض مكتوب، ويُترجَم عند التحميل و**يفشل بسرعة** إن لم يُترجم (H5).
- استخراج الهاتف: تسلسلات 7 أرقام أو أكثر، بعد تحويل الأرقام العربية-الهندية إلى لاتينية (استعمل `app.text.arabic.normalize`، ولا تكتب محوّلاً ثانياً — H21/C6)، ثم تطبيع إلى E.164 بدالة واحدة `to_e164(raw, default_country)` مع `DEFAULT_COUNTRY_CODE` في الإعداد.
- **أكثر من مرشّح**: أعِد كل المرشّحين (بسقف مكتوب `ORDER_LOOKUP_MAX_PHONE_CANDIDATES`، افتراضه 3) ودَع المنصّة تحكم على كلٍّ منها. لا تختر واحداً بالتخمين.
- **رقم الطلب المتعدّد**: خُذ الأول فقط، ولا تجرّب عدّة أرقام في دور واحد (وإلا صار الدور أداةً للتخمين).
- **أرقام لا تصلح للتطبيع تُهمَل** ولا تُعدّ مرشّحاً.

> ملاحظة صريحة في التقرير: صيغة رقم الطلب في `sharwa_saas` غير مؤكَّدة عندي. اكتب افتراضاً معقولاً، **ودوّنه سؤالاً مفتوحاً** (OQ-P1-24)، ولا تدّعِ أنه الصيغة الحقيقية.

### 4.3 الأداة `track_order`

```python
def run(ctx: ToolContext) -> OrderLookup
```

منطقها:

1. استخرج رقم الطلب. لا رقم ⇒ `OrderLookup(kind="need_order_ref")`.
2. حدّد المسار: `same_number` **فقط** إذا `ctx.channel_phone_e164 is not None` وكان أحد مرشّحي الهاتف مساوياً له، **أو** لم يذكر العميل هاتفاً وكانت هوية القناة قابلة للتطبيع. وإلا `other_number` (H52 — الفشل مُغلَق).
3. `other_number` بلا هاتف مذكور ⇒ `OrderLookup(kind="need_phone", path="other_number")` — اطلب الهاتف المسجَّل ولا تكشف شيئاً.
4. وإلا: `ctx.commerce.lookup_order(order_ref=..., phone_candidates=(...), path=...)`.
5. النتيجة: `kind="card"` بالبطاقة، أو `kind="unverified"` **بلا سبب**.

الأداة **لا** تنادي القاعدة ولا `httpx` ولا تُسجّل شيئاً ولا تعرف ما هو `outbox` (H50). و`OrderLookup` مجمَّد و`kind` من مجموعة مغلقة: `card | unverified | need_order_ref | need_phone | unavailable`.

### 4.4 المنسّق `orders.py` — وترتيبه يخضع لـH40

هذه هي المصيدة الأولى في هذه الدفعة: نداء المنصّة **نداء شبكة**، ويُمنع داخل معاملة (H40). فالمنسّق ثلاث مراحل، كما في `turn.py`:

**المرحلة 1 — معاملة قصيرة (قراءة وحدّ):**
- اقرأ `customer_id` و`wa_id`/`phone_e164` للمحادثة.
- احسب `order_ref_hash` (بعد الاستخراج الحتمي — وهو نقي فلا مشكلة في موضعه).
- `repos_outbox.order_lookup_blocked(conn, customer_id=..., order_ref_hash=...)`. إن كان `True`: **سجّل محاولة بـ`outcome='blocked'`**، وأعِد `blocked`، ولا تنادِ المنصّة. (الحالة `blocked` موجودة في CHECK الجدول لسبب — لا تُهملها.)
- **أغلق المعاملة.**

**المرحلة 2 — خارج أي معاملة:** نادِ الأداة (وهي تنادي المنصّة عبر المنفذ) بمهلة `ORDER_LOOKUP_TIMEOUT_S`. أي `CommerceUnavailableError` ⇒ `kind="unavailable"` بلا استثناء يتسرّب.

**المرحلة 3 — معاملة قصيرة ثانية (تسجيل):** سجّل المحاولة بـ`path` و`order_ref_hash` و`outcome`:
- `card` ⇒ `allowed`
- `unverified` ⇒ `denied` ← **هذا هو ما يُغذّي عدّاد القوة الغاشمة، فلا تنسه**
- `need_order_ref` / `need_phone` / `unavailable` ⇒ **لا تسجّل شيئاً** (ليست محاولة كشف، وتسجيلها يفتح حجباً على العميل بسبب سؤالٍ لم يُجَب بعد)

ثم يعود التحكّم إلى `turn.py` الذي يُركّب الردّ ويكتبه عبر `verify.insert_verified_outbox` كالمعتاد.

---

## §5 — منع الـOracle (أمر المالك، وهو بنيويّ لا سلوكيّ)

### 5.1 في المنفذ

```python
def lookup_order(self, *, tenant_ref: str, order_ref: str,
                 phone_candidates: tuple[str, ...], path: str
                 ) -> dict[str, Any] | None
```

`None` = «لا بطاقة». **ولا قيمة أخرى ولا حقل سبب ولا رمز خطأ.**

> **تحديد نطاق، لئلا يُقرأ الأمر الإداري أوسع مما هو:** ورد في التوجيه «دالة التتبع يجب أن تُرجع إما بطاقة الطلب أو `None` فقط». هذا يخصّ **`lookup_order` في المنفذ** حصراً — أي الحدّ الذي تعبره إجابة المنصّة. أما `track_order.run` فتُرجع `OrderLookup` بخمس حالات (`card | unverified | need_order_ref | need_phone | unavailable`)، **وهذا ليس تسريباً**: الحالات الأربع غير الـ`card` لا تحمل أي معلومة عن وجود الطلب — `need_order_ref` و`need_phone` تُقال **قبل** أي نداء للمنصّة، و`unavailable` عن حالة الشبكة لا عن الطلب، و`unverified` هي الحالة الواحدة التي تُخفي الأسباب الثلاثة كلّها. لو جمعنا الخمس في `None` لضاع سؤال «ما رقم طلبك؟» ولما عرف العميل ما يفعل — بلا أن يُكسَب شيء في منع الـOracle. هذه هي الضمانة: الكود لا يستطيع أن يفرّق بين «غير موجود» و«هاتف غير مطابق» و«طلب تاجر آخر» لأن **المعلومة غير موجودة في النوع**. لا تُضِف `reason` «للتشخيص» — لحظة إضافته يُعاد بناء الـOracle، ويسرّبه أول سطر سجل.

### 5.2 في `FakeCommerce`

تُعيد `None` بالتساوي في الحالات الأربع: رقم طلب غير موجود · هاتف غير مطابق · الطلب لتاجر آخر · `other_number` بلا مرشّح مطابق. والاختبار المطلوب صريح: **بطاقتان مختلفتان من السبب تُعطيان نفس القيمة `None`**.

وفي الفحص الحتمي: المطابقة تكون **داخل** الوهمي (أو المنصّة)، لا في `core/`. لا تُعِد هاتف الطلب إلى `core` ثم تقارنه هناك — إعادته هي التسريب نفسه.

### 5.3 في الردّ

وهذه أهمّ من الاثنتين: لو أعطى «غير موجود» قالباً و«غير مطابق» قالباً آخر، فقد أُعيد بناء الـOracle في `compose`. **قالب واحد** لكل `kind="unverified"`، ونصّه لا يقول «الطلب غير موجود» ولا «الرقم لا يطابق» بل: لم نتمكّن من التحقّق، وسنحوّلك إلى موظف.

### 5.4 الحدّ المُعلَن

فرق التوقيت (رقم غير موجود قد يرجع أسرع من مطابقة فاشلة) **لا يعالجه هذا التصميم**. اذكره في التقرير كحدّ معروف ودوّنه (OQ-P1-25). لا تدّعِ حصانة لا تملكها.

---

## §6 — الردّ: دالة تركيب رابعة **مصرَّح بها**، وخريطة حالات مغلقة

`compose.py` كان «ثلاث دوال لا رابعة» في P1.6. **أُصرّح الآن بالرابعة وحدها**، وحدِّث الـdocstring ليقول «أربع دوال لا خامسة» مع ذكر هذا البرومبت.

```python
ORDER_STATUS_TEMPLATE = "طلبك {ref_tail}: {status_label}\nآخر تحديث: {updated_label}\n..."

def compose_order_status(card: dict[str, Any], *, labels: dict[str, str]) -> str
```

القيود، وكلّها مكتوبة:

1. **خريطة مغلقة** `ORDER_STATUS_LABELS: dict[str, str]` من رمز حالة المنصّة إلى تسمية عربية معتمدة. رمز غير معروف ⇒ **لا تُركّب بطاقة**؛ أعِد قالب `order_status_unknown` العام. نصّ المنصّة الخام لا يصل العميل (H55).
2. **لا مبالغ ولا أسعار ولا رسوم.** `0001` ينصّ أن `checkout_sessions` هو «المصدر الوحيد للمبالغ المعروضة للعملاء»، وH36 يمنع مفردات الكلفة. بطاقة الحالة في هذه الدفعة: **الحالة + زمن آخر تحديث + ذيل رقم الطلب**، لا شيء غيرها. **ووسّع مجموعة مسارات S7** لتشمل `app.tools.*` و`app.workers.orders` و`compose.py` فتُفرَض المفردات الممنوعة بنيوياً على هذا المسار أيضاً.
3. **`ref_tail` آخر ثلاثة محارف من رقم الطلب فقط** — تأكيد كافٍ للعميل بلا طبع الرقم كاملاً في رسالة قد تُلتقط بلقطة شاشة.
4. **`updated_label` تسمية نسبية معتمدة** («اليوم» / «أمس» / تاريخ قصير)، لا طابع زمني خام من المنصّة.
5. **سقف `ORDER_STATUS_MAX_CHARS`** مكتوب، والنصّ مقصوص عنده.

قوالب جديدة في `templates.py` (نصوص محافظة، بلا وعد بوقت، وبلا كلمة «بوت» أو «ذكاء اصطناعي» — نفس قيد OQ-P1-08):

| المعرّف | الحالة |
|---|---|
| `order_need_ref` | لا رقم طلب في الرسالة |
| `order_need_phone` | رقم مختلف: اطلب الهاتف المسجَّل على الطلب |
| `order_unverified` | **القالب الواحد** لكل فشل تحقّق (H53) — ويُحوِّل إلى موظف |
| `order_blocked` | تجاوز حدّ المحاولات: تحويل إلى موظف حصراً |
| `order_status_unknown` | البطاقة وصلت برمز حالة غير معروف |
| `order_unavailable` | المنصّة غير متاحة |

`order_unverified` و`order_blocked` يُحوّلان المحادثة إلى موظف (`bot_status='paused_human'`) بنفس مسار التحويل القائم. والقوالب كلها تمرّ على فحص الإقلاع الذاتي في `verify.build_rules` تلقائياً لأنها في `TEMPLATES` — تأكّد أن أيّاً منها لا يخالف قائمة حظر (وإلا انفجر الإقلاع، وهو المطلوب).

---

## §7 — الإعدادات (بافتراضات مكتوبة — H4)

| المفتاح | الافتراض | ملاحظة |
|---|---|---|
| `TOOLS_ENABLED` | `true` | إطفاؤه ⇒ نيّة `order_status` تسلك مسار التحويل، ومقياس `tools_enabled` يجعله مرئياً |
| `ORDER_REF_PATTERN` | نمط معقول موثَّق | يُترجَم عند التحميل، وفشل الترجمة ⇒ `ConfigError` |
| `ORDER_LOOKUP_TIMEOUT_S` | `4.0` | أقصر من مهلة الكتالوج: العميل ينتظر |
| `ORDER_LOOKUP_MAX_PHONE_CANDIDATES` | `3` | |
| `ORDER_LOOKUP_MAX_PER_CONVERSATION_PER_DAY` | `10` | سقف تشغيلي فوق حدّ القوة الغاشمة، لا بديلاً عنه |
| `ORDER_STATUS_MAX_CHARS` | `600` | |
| `DEFAULT_COUNTRY_CODE` | `967` | للتطبيع إلى E.164 |
| `ORDER_REF_HASH_KEY` | **لا افتراض — `_required`** | مفتاح HMAC. غيابه ⇒ فشل إقلاع (H5/H54). ولا تولّده عشوائياً عند الإقلاع وإلا تغيّرت البصمة بين العمليات فانهار عدّاد القوة الغاشمة |
| `VERIFY_JOIN_WINDOW_MAX` | `6` | §0.2 |

`ORDER_REF_HASH_KEY` هو البند الذي يُخفق فيه التنفيذ عادةً: البصمة يجب أن تكون **ثابتة عبر العمليات وإعادات التشغيل**، وإلا صار كل عملية تعدّ محاولاتها وحدها وسقط حدّ «5 رفضات لنفس الطلب».

---

## §8 — المقاييس والتنبيهات

| المقياس | النوع | التسميات |
|---|---|---|
| `tool_calls_total` | Counter | `tool`، `result` ∈ {`card`,`unverified`,`need_order_ref`,`need_phone`,`unavailable`,`blocked`} |
| `tool_unknown_total` | Counter | — (نيّة طلبت أداة غير موجودة في السجل) |
| `tools_enabled` | Gauge | — |
| `order_lookup_total` | Counter | `path` ∈ {`same_number`,`other_number`}، `outcome` ∈ {`allowed`,`denied`,`blocked`} |
| `order_lookup_duration_seconds` | Histogram | — |
| `order_lookup_platform_errors_total` | Counter | `kind` ∈ {`timeout`,`unavailable`,`client_error`} |

كل التسميات من مجموعات مغلقة. **لا رقم طلب ولا هاتف ولا بصمة في أي تسمية** (H20/H54).

التنبيهات في `alerts.yml` — بالمعدّل لا بالغياب:

1. `OrderLookupDeniedSpike` — `rate(order_lookup_total{outcome="denied"}[15m])` فوق عتبة مكتوبة ⇒ warning. رفض متكرّر = تخمين، أو نمط رقم طلب خاطئ.
2. `OrderLookupBlockedRising` — `rate(order_lookup_total{outcome="blocked"}[1h]) > 0` ⇒ warning. الحجب يعمل، ويجب أن يراه إنسان.
3. `CommerceLookupUnavailable` — `rate(order_lookup_platform_errors_total[10m])` فوق عتبة ⇒ critical.
4. `ToolUnknownSelected` — `increase(tool_unknown_total[1h]) > 0` ⇒ warning. النموذج طلب أداة غير مسجَّلة: خلل توجيه لا خلل عميل.
5. `ToolsDisabled` — `tools_enabled == 0` لأكثر من 15 دقيقة ⇒ warning.

وتأكّد أن S4 تُقرّ أن كل مقياس في `alerts.yml` معرَّف في `metrics.py`.

---

## §9 — المرحلة S11 في `scripts/static_gate.py`

**صياغة المالك:** «تضمن أن أدوات الذكاء الاصطناعي لا تمتلك صلاحية استدعاء مسارات قاعدة البيانات مباشرة، بل عبر واجهات محدَّدة مسبقاً.» أربعة بنود على نمط S10:

**S11-a — قائمة استيراد مغلقة لطبقة الأدوات.** كل وحدة `app.tools.*` يُمنع أن تستورد: `app.db` وفروعها، `psycopg`، `psycopg_pool`، `httpx`، `redis`، `app.llm` وفروعها، `app.channels` وفروعها، `app.ws_publish`. المسموح: stdlib + `app.text.*` + `app.commerce.port` + `app.tools.*` + `app.workers.config`.

**S11-b — لا SQL في طبقة الأدوات.** أي نداء بسمة `execute` أو `executemany` أو `fetchone` أو `fetchall` أو `cursor` داخل `app.tools.*` مخالفة — ولو لم يكن الاستيراد ظاهراً (اتصال يُمرَّر كوسيط يتجاوز S11-a).

**S11-c — السجل مغلق وثابت.** في `app.tools.registry`: `TOOLS` يجب أن يكون إسناداً لمُظهِر قاموس (`ast.Dict`) كل مفاتيحه **حرفيّة نصّية**، ومجموعتها تساوي المجموعة المكتوبة في البوابة (`{"track_order"}` اليوم). وأي نداء بسمة `register`/`update`/`setdefault` على `TOOLS`، أو استعمال `globals()`/`importlib`/`getattr` داخل `registry.py`، مخالفة. أداة جديدة تعني تعديل البوابة ومراجعتها — وهذا هو المقصود.

**S11-d — مسارات تتبّع الطلب لها مستهلك واحد.** `order_lookup_blocked` و`insert_order_lookup_attempt` لا يُناديهما إلا `app.workers.orders` (ومحلّ تعريفهما `app.db.repos_outbox`). أي مستهلك ثالث مخالفة.

### إثبات بإفشال متعمَّد

أربع مخالفات مصطنعة، واحدة لكل بند، وأرفِق المخرج الذي يعدّها (`4 violation(s)` بمعرّفاتها) ثم احذفها وأرفِق `STATIC GATE PASSED — 0 violations.` بزمنه:

1. `from app.db import repos_outbox` في `track_order.py`.
2. `conn.execute("SELECT 1")` في `track_order.py` بلا استيراد.
3. مفتاح غير حرفي أو `TOOLS.update(...)` في `registry.py`.
4. نداء `order_lookup_blocked` في `turn.py`.

**والبوابة على الشجرة كاملةً هي آخر أمر قبل كتابة التقرير (S1-d).**

---

## §10 — الاختبارات (نقية، بمزدوجات مسجِّلة، بلا قاعدة ولا حاوية)

`test_tools_extract.py`:

| مجموعة | حالات لا بدّ منها |
|---|---|
| رقم الطلب | صيغة صحيحة · داخل جملة · صيغتان في رسالة (تُؤخذ الأولى) · لا شيء ⇒ `None` |
| الهاتف | أرقام عربية-هندية · بفواصل ومسافات · بمقدّمة دولية · بصفر محلي ⇒ نفس E.164 · رقم قصير جداً يُهمَل · فوق السقف يُقتَص |
| النقاء | نفس المدخل عشر مرات ⇒ نفس المخرج |

`test_track_order.py` (بـ`FakeCommerce`):

| # | الحالة | التوكيد |
|---|---|---|
| 1 | هوية القناة تطابق هاتف الطلب، رقم الطلب وحده | `path="same_number"` و`kind="card"` |
| 2 | رقم مختلف، رقم الطلب وحده | `kind="need_phone"` — **ولا نداء للمنصّة أصلاً** |
| 3 | رقم مختلف + الهاتف المسجَّل الصحيح | `path="other_number"` و`kind="card"` |
| 4 | رقم مختلف + هاتف خاطئ | `kind="unverified"` |
| 5 | رقم طلب غير موجود | `kind="unverified"` — **ونفس القيمة تماماً كالحالة 4** |
| 6 | **منع الـOracle** | نتيجة 4 ونتيجة 5 **متساويتان حرفياً** (`==`)، وصفر حقل يفرّقهما |
| 7 | `channel_phone_e164 is None` | المسار `other_number` لا `same_number` (H52) |
| 8 | طلب تاجر آخر | `kind="unverified"` |
| 9 | H50 | الأداة لا تستورد ولا تنادي شيئاً من `app.db` — توكيد على `sys.modules` أو على المزدوج |

`test_orders.py` (المنسّق):

| # | الحالة | التوكيد |
|---|---|---|
| 1 | `blocked=True` | `outcome='blocked'` مسجَّل · **صفر نداء للمنصّة** · قالب `order_blocked` · تحويل لموظف |
| 2 | `card` | `outcome='allowed'` مرّة واحدة |
| 3 | `unverified` | `outcome='denied'` مرّة واحدة — العدّاد يُغذّى |
| 4 | `need_phone` / `need_order_ref` / `unavailable` | **صفر صفّ محاولة** |
| 5 | **H40** | نداء المنصّة وقع **خارج** أي معاملة (المزدوج يسجّل فتح المعاملات وإغلاقها ونداء المنصّة، ويُفحَص الترتيب) |
| 6 | البصمة | `order_ref_hash` ليست الرقم الصريح، وHMAC ثابت لنفس المدخل والمفتاح، ويختلف لمفتاح آخر |
| 7 | H54 | لا سطر سجل ولا تسمية مقياس تحوي رقم الطلب أو الهاتف |
| 8 | مهلة المنصّة | `kind="unavailable"` بلا استثناء يتسرّب، والعدّاد يرتفع |
| 9 | رمز حالة غير معروف | `order_status_unknown` ولا نصّ منصّة خام في المخرج |
| 10 | S7 على المسار الجديد | لا مفردة كلفة/هامش في أي مخرج |

وأضِف إلى `test_verify_rules.py` حالات §0.2، وإلى `test_verify.py` حالة §0.4 (عندما `paused=False` لا يُرسَل `conversation.updated`)، بلا كسر أي اختبار قائم (H21).

**الحزمة النقية تبقى صفر أحمر، والعدد أكبر من `169`.**

---

## §11 — التسليم ولغة التقرير

القواعد السارية، وهي غير قابلة للتفاوض:

1. **أفعال صريحة:** `أضفتُ` / `وجدتُه موجوداً` / `عدّلتُه` / `شغّلتُ` / `لم أشغّله (والسبب …)`.
2. **كل مخرج بأمره وزمنه (UTC).** لا رقم بلا أمر أنتجه، ولا رقم من ذاكرتك (H18).
3. **`py_compile` ليس فحصاً** ولا يُذكر كدليل.
4. **البوابة آخر أمر** قبل كتابة التقرير (S1-d).
5. **`docs/PHASE_GATE.md` لا تُفتح ولا تُعدَّل.**
6. القيد المُعلَن مقبول؛ القيد المُجمَّل ليس كذلك.

**ما يُرفَق حرفياً:**

- `git log --oneline -1` و`git status --porcelain`.
- مخرج المخالفات الأربع المصطنعة لـS11 بمعرّفاتها، ثم `0 violations` بزمنه.
- `python -m pytest tests -q` كاملاً بسطره الأخير.
- إثبات §0.2: `ك ل ب` و`s a l l a` تُحجَبان، و`زبون`/`كلبي`/`كلاب`/`كل بلد` تمرّ — بمخرج حقيقي.
- إثبات منع الـOracle: أن نتيجة «غير موجود» ونتيجة «هاتف غير مطابق» **متساويتان**.
- جملة صريحة: **هل بقي أي استيراد قاعدة أو نداء SQL داخل `app/tools/`؟** بمخرج grep أو البوابة.
- جملة صريحة: **هل أُنشئ أي جدول أو ترحيل في هذه الدفعة؟** (المتوقَّع: لا، وهو قرار مقصود.)

**اختم تقريرك حصرياً بـ:**

`P1.7 COMPLETE — STOPPING. AWAITING AUDIT. NO P1.8 WORK STARTED.`

وقبله سطر الحصيلة: `tools cannot touch the DB (S11 a/b/c/d proven) · strict identity on 0001's order_lookup_attempts · no oracle (identical result + single template) · HMAC order_ref_hash · no new table · gate 0 violations at <ZULU>`

---

## §12 — أسئلة مفتوحة تُدوَّن ولا تُقرَّر من عندك

- **OQ-P1-24:** صيغة رقم الطلب في `sharwa_saas` — الافتراض الذي كتبتَه يحتاج تأكيداً من المنصّة.
- **OQ-P1-25:** فرق التوقيت بين «غير موجود» و«غير مطابق» يبقى قناة تسريب نظرية؛ معالجتها قرار مالك.
- **OQ-P1-26:** هل يُحفَظ نجاح التحقّق في `conversations.slots` ليسأل العميل مرّة واحدة؟ **التنفيذ الحالي: لا** — كل استعلام يُعاد التحقّق فيه، لأن حفظ تصريح دائم على هوية قناة قد يتشارك فيها أكثر من شخص مخاطرة أكبر من كلفة السؤال. وتوسيع `SLOT_ALLOWED_KEYS` قرار معماري لا برمجي.
- **OQ-P1-27:** هل تُعرَض مبالغ الطلب لاحقاً؟ يقتضي `checkout_sessions` كمصدر وحيد (نصّ `0001`)، وهو نطاق دفعة أخرى.
- **OQ-P1-28:** مراجعة المالك لنصوص القوالب الستّة الجديدة.

---

**ابدأ بـ§0 بالترتيب. لا تكتب سطراً من `app/tools/` قبل أن يصبح `git status --porcelain` نظيفاً وتمرّ اختبارات §0.2 خضراء.**
