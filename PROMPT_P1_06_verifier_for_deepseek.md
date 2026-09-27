# البرومبت المعماري الثامن — P1.6: المدقّق النهائي للردود (Output Verifier)

**من:** المهندس المعماري
**إلى:** المنفّذ (DeepSeek داخل Cline)
**المشروع:** `sharwa_ai` — `C:\sharwaai\sharwa-ai`
**المرحلة السابقة:** P1.5b — **APPROVED** (صفر أحمر، صفر مخالفة، `130 passed`). لا تعد فتحها.
**نطاق هذه الدفعة:** طبقة واحدة: **لا نصّ يخرج إلى عميل قبل أن يمرّ على مدقّق حتمي، ولا طريق ثانٍ إلى `outbox`.**

---

## §0 — الخطوة صفر (إلزامية، بهذا الترتيب، قبل أي سطر من P1.6)

### 0.1 نقطة حفظ في git — أمر مباشر من المالك

```bash
cd C:\sharwaai\sharwa-ai
git add .
git commit -m "chore: checkpoint before P1.6"
git log --oneline -1
git status --porcelain
```

أرفِق في تقريرك **مخرج `git log --oneline -1` ومخرج `git status --porcelain` حرفياً**. هذا ليس إجراءً شكلياً: حادثة `git checkout` في P1.5b محت دوال القنوات التسع لأن **كل عمل P1 غير مُلتزَم**. أي `checkout` أو `reset` عارض اليوم يمحو أسابيع. لا تبدأ P1.6 قبل أن يصبح `git status --porcelain` فارغاً (أو لا يحوي إلا ملفات تشرح سبب استثنائها).

إن فشل الالتزام لأي سبب (hooks، إعداد identity، ملفات ضخمة) **توقّف واذكر السبب ومخرج الفشل**، ولا تتجاوزه بـ`--no-verify` ولا تحذف ملفات.

### 0.2 F-P1-09 — عيب حاجز أكتشفتُه في كود معتمد، ويجب إصلاحه قبل P1.6 (انظر §3 للإثبات الكامل)

باختصار: **كل إشعار تحويل وكل ردّ آمن يُنتجه محرك الدور اليوم يُسقَط قبل أن يصل العميل**. أصلحه كما في §3 قبل أن تبني المدقّق، لأن رد فعل المدقّق (إيقاف البوت + قالب آمن) يصطدم بنفس الحائط حرفياً.

### 0.3 ملاحظة P1.5b N1 — سطر واحد

في `core/app/db/repos.py::insert_whatsapp_channel_account` أضِف إلى الـdocstring سطراً بهذا المعنى:

> المعاملة تُجهَض في PostgreSQL بعد `UniqueViolation`؛ من يمسك `ChannelAlreadyExistsError` ويريد متابعة عمل في المعاملة نفسها يحتاج `SAVEPOINT`. المسار الحالي يرفع `ApiError` فوراً فيتراجع، وهو سليم.

---

## §1 — المواد الدستورية الجديدة (H45–H49)

هذه الدفعة تضيف خمس مواد. اكتبها في `docs/CONSTITUTION.md` (أو الملف المكافئ) بالصيغة نفسها المستعملة في H1–H44، واذكر في التقرير أين كتبتها.

- **H45 — المدقّق حتمي، ولا نموذج في هذه الطبقة.** طبقة التحقق النهائي تُبنى على التعبيرات النمطية وقوائم الحظر الصريحة وحدها. يُمنع فيها: استيراد `app.llm`، أي نداء شبكة، أي قراءة قاعدة، أي عشوائية، أي وقت (`time`/`datetime`)، وأي حالة عالمية متغيّرة. دالة الفحص **نقية**: نفس النص ⇒ نفس الحكم، دائماً.
- **H46 — نقطة كتابة واحدة إلى `outbox`.** `repos_outbox.insert_outbox` لا يُنادى إلا من وحدتين: وحدة الإنفاذ (`app/workers/verify.py`) لكل ما أصله البوت، ومسار ردّ الموظف في `app/api/routes_inbox.py` بـ`origin="human"` حرفياً. أي نداء ثالث مخالفة بنيوية.
- **H47 — المدقّق يفشل مُغلَقاً.** خلافاً لـH42 (البحث المتجهي يفشل مفتوحاً لأنه أمثَلية)، أي اعتلال في المدقّق نفسه — استثناء، إعداد ناقص، حكم غير مفهوم — يُعامَل **مخالفةً**: لا يُرسَل النص الأصلي. الأمثَلية تفشل مفتوحة، والسلامة تفشل مغلقة.
- **H48 — المدقّق لا يُسجّل النصّ.** السجلات والمقاييس تحمل `rule_id` وفئة القاعدة وحدها؛ لا نصّ الرسالة ولا المقطع المخالف ولا رقم هاتف (H20). الاستثناء الوحيد هو جدول التدقيق `verifier_blocks` بعمود `draft_excerpt` المصمَّم لذلك في `0001`، بسقف مكتوب وبتقنيع الأرقام قبل الكتابة.
- **H49 — الإيجاب الكاذب مقبول، والسلب الكاذب ممنوع.** إن شكّ المدقّق حجب. ثمن الحجب الخاطئ تحويلٌ إلى موظف؛ وثمن التمرير الخاطئ أن يقرأ عميلٌ كلاماً بذيئاً أو اسم منافس باسم التاجر. الكلفتان غير متكافئتين، فالانحياز محسوم.

---

## §2 — ما تكتبه، وما تعدّله، وما لا تلمسه

**ملفات جديدة (ثلاثة فقط):**

| الملف | مسؤوليته الوحيدة |
|---|---|
| `core/app/workers/verify_rules.py` | **القواعد النقية.** لا IO، لا قاعدة، لا شبكة، لا `app.llm`. استيراداته المسموحة حصراً: `re`، `dataclasses`، `typing`، `enum`، `app.text.arabic`. مدخلها الوحيد `check_text(...) -> RuleVerdict` |
| `core/app/workers/verify.py` | **الإنفاذ.** نقطة الكتابة الوحيدة إلى `outbox` لكل ما أصله البوت: تنادي `verify_rules.check_text`، ثم تكتب النص أو تُنفّذ رد الفعل الصارم |
| `core/app/text/redact.py` | `mask_phones` مُنقولة من `app/llm/router.py` لتصير متاحة خارج `app.llm` (نفس نمط P1.4 C6) |

**ملفات تُعدَّل:**

| الملف | التعديل |
|---|---|
| `core/app/workers/turn.py` | يستبدل نداء `repos_outbox.insert_outbox` بنداء `verify.insert_verified_outbox`. لا تغيير آخر في منطق القرار |
| `core/app/workers/dispatch.py` | إصلاح F-P1-09 (§3) |
| `core/app/workers/templates.py` | إضافة `SAFE_FALLBACK_TEMPLATES` (قائمة مغلقة) |
| `core/app/llm/router.py` | يحذف تعريف `mask_phones` **ويعيد تصديره** من `app.text.redact` (H21: كل مواقع النداء والاختبارات القائمة تبقى صحيحة بلا تعديل) |
| `core/app/db/repos_outbox.py` | إضافة `insert_verifier_block` فقط |
| `core/app/workers/config.py` | إعدادات §6 |
| `core/app/obs/metrics.py` | مقاييس §7 |
| `ops/prometheus/alerts.yml` | تنبيهات §7 |
| `scripts/static_gate.py` | المرحلة S10 (§8) |
| `core/migrations/0011_p1_verifier.sql` | `CREATE OR REPLACE` لـ`app.claim_outbox` (§3) — لا جدول جديد |

**ممنوع لمسه في هذه الدفعة:** `core/app/text/arabic.py` (اختبارات `optout` والبحث تعتمد على سلوكه حرفياً — H21)، `core/app/workers/compose.py` (ثلاث دوال لا رابعة — H38)، `core/app/db/repos_summary.py`، أي ملف في `core/app/llm/` غير `router.py`، **و`docs/PHASE_GATE.md` (لا تفتحه ولا تعدّله — وثيقة المعماري وحده).**

---

## §3 — F-P1-09 [حرج] — إشعار التحويل والردّ الآمن لا يصلان العميل أبداً

### الإثبات، بالسطور

`core/app/workers/turn.py::_write_phase`: عندما `action.handoff` صحيحاً، الكود **يوقف البوت أولاً** ثم يكتب الصف:

```python
new_epoch = repos_outbox.set_bot_status(..., new_status="paused_human", reason=reason)
expected_epoch = new_epoch
...
repos_outbox.insert_outbox(..., origin="bot", expected_epoch=expected_epoch, ...)
```

و`core/migrations/0001_baseline.sql::app.claim_outbox` يُسقط الصف قبل أن يُطالَب به:

```sql
UPDATE outbox o SET status = 'dropped_stale'
  FROM conversations c
 WHERE o.conversation_id = c.id
   AND o.status = 'pending' AND o.origin = 'bot'
   AND (c.epoch <> o.expected_epoch OR c.bot_status <> 'active');
```

و`core/app/workers/dispatch.py::_pre_send_checks` يكرّر نفس الشرط في Python:

```python
if cur is None or cur[1] != "active" or (row.expected_epoch is not None and cur[0] != row.expected_epoch):
    ... status="dropped_stale"
```

النتيجة، حسابياً: الصف يحمل `expected_epoch == c.epoch` (فالحقبة تطابق)، لكن `c.bot_status == 'paused_human'` بفعل نفس المعاملة التي أنشأت الصف، فالشرط الثاني يتحقّق دائماً ⇒ **`dropped_stale` دائماً**.

### الأثر، بلا تهويل

`decide()` يُرجع `handoff=True` في **أربعة** من خمسة مسارات: `kill_switch_off`، `customer_requested`، `bot_reply_cap`، و`bot_cannot_answer` — وهذا الأخير هو المسار الافتراضي. أي أن:

1. **كل** إشعار تحويل (`handoff_notice`) يُسقَط ⇒ العميل يطلب موظفاً فيصله **صمت**.
2. الردّ الآمن `safe_ack` عند إطفاء مفتاح `ai_reply` يُسقَط أيضاً ⇒ سلوك §4.10 («route to staff + we got your message») **معطَّل تماماً**، ولا يعصمه أنه في `POLICY_EXEMPT_TEMPLATES` لأن فحص الحقبة/الحالة يسبق فحص المفتاح.
3. ما يصل العميل فعلاً اليوم هو `product_list` و`policy_answer` وحدهما (`handoff=False`).

هذا عيب في كود **معتمد** (P1.2 وP1.5)، ولم تكتشفه أي بوابة لأن البوابات تفحص الأسماء والبنية لا تفاعل جدولَي حالة عبر وحدتين ومهاجرة SQL.

### الإصلاح الصحيح، ولماذا هو هذا

الغرض من الشرط هو H26: **ردّ بوت أُلِّف قبل أن يتدخّل إنسان لا يُرسَل.** والدليل على ذلك مُشفَّر أصلاً في `expected_epoch`: `app.set_bot_status` يزيد `epoch` في كل انتقال، فمطابقة الحقبة تُثبت وحدها أن الصف أُلِّف على الحالة الراهنة. أما `bot_status <> 'active'` فهو أوسع من الغرض: يُسقط الصفّ الذي أنشأه الانتقال نفسه.

المطلوب — في الموضعين، بنفس المنطق حرفياً:

```
stale  ⇔  c.epoch <> o.expected_epoch  OR  c.bot_status = 'closed'
```

أي: لا نرسل إلى محادثة **مغلقة**، ولا نرسل صفاً أُلِّف على حقبة قديمة. و`paused_human` وحدها لم تبقَ سبباً للإسقاط.

1. `core/migrations/0011_p1_verifier.sql`: `CREATE OR REPLACE FUNCTION app.claim_outbox(...)` بالنصّ كاملاً وقد عُدِّل الشرط. `CREATE OR REPLACE` idempotent فيحترم H14؛ لا تحذف الدالة ولا تعد إنشاء أي جدول أو فهرس.

> **تصحيح صريح لصياغة الأمر الإداري:** ورد في التوجيه «عدّل شرط الإسقاط في `0001_baseline.sql`». **لا تفعل ذلك، ولا تفتح `0001_baseline.sql` أصلاً.** السبب ليس شكلياً: `0001` ترحيل **مُطبَّق فعلاً** على قاعدة الـVPS، وسجل الترحيلات يمنع إعادة تشغيله. فتعديله في موضعه يعني أن الإصلاح **لن يصل أي قاعدة قائمة أبداً**، بينما قاعدة جديدة تُبنى من الصفر ستحمله — فينشأ مخططان مختلفان تحت نفس رقم الترحيل، وهو أخطر من العيب الذي نُصلحه ومخالف لـH14 (forward-only). الطريق الصحيح الوحيد هو `0011` بـ`CREATE OR REPLACE FUNCTION`: هي تعطي نفس النتيجة على القاعدة الجديدة وعلى القائمة، وidempotent، ومرئيّة في سجل الترحيلات. **قد أبلغتُ المالك بهذا التصحيح.**
2. `dispatch._pre_send_checks`: نفس الشرط، مع تعليق يربطه بـF-P1-09 وبرقم الترحيل.

### الاختبارات المطلوبة على هذا الإصلاح تحديداً (بمزدوجات مسجِّلة، بلا قاعدة)

| # | الحالة | المتوقَّع |
|---|---|---|
| 1 | `origin='bot'`، `bot_status='paused_human'`، `expected_epoch == current_epoch` | **لا يُسقَط** — يمضي إلى الإرسال |
| 2 | `origin='bot'`، `bot_status='paused_human'`، `expected_epoch < current_epoch` | `dropped_stale` |
| 3 | `origin='bot'`، `bot_status='closed'`، الحقبة مطابقة | `dropped_stale` |
| 4 | `origin='bot'`، `bot_status='active'`، الحقبة مطابقة | لا يُسقَط |
| 5 | `origin='human'` مع أي حالة وأي حقبة | لا يُسقَط أبداً (H24/H27) |

والحالة 1 هي الاختبار الذي كان غيابه يخفي العيب؛ اذكر ذلك صريحاً في التقرير.

---

## §4 — `verify_rules.py`: القواعد الحتمية النقية

### 4.1 الشكل

```python
@dataclass(frozen=True)
class RuleVerdict:
    ok: bool
    rule_id: str | None      # None عند القبول؛ وإلا معرّف من القائمة المغلقة أدناه
    category: str | None     # None عند القبول؛ وإلا فئة القاعدة
```

`RuleVerdict` **لا يحمل النصّ المطابَق ولا موضعه** (H48).

**وإضافة أمر بها المالك صريحاً — نوع موثوق (`VerifiedText`):** عرّف في `verify_rules.py`:

```python
@dataclass(frozen=True)
class VerifiedText:
    """نصّ مرّ على check_text وقُبل. لا يُبنى إلا من approve()."""
    value: str

def approve(text: str, *, rules: BlocklistSet, max_chars: int) -> VerifiedText | RuleVerdict
```

`approve` هي **الطريق الوحيد** لبناء `VerifiedText`: تنادي `check_text`، فتُرجع `VerifiedText` عند القبول و`RuleVerdict` عند المخالفة. وفي `verify.py` لا يُمرَّر إلى `insert_outbox` إلا `approved.value` أو نصّ قالب آمن.

وكن صادقاً في التقرير بحدود هذا النوع: Python لا يفرض الأنواع في زمن التشغيل، فـ`VerifiedText("أي شيء")` ممكن نظرياً. قيمته أنه يجعل التجاوز **مرئياً في الكود وقابلاً للكشف بـS10-e وبـmypy**، والفرض الحقيقي يبقى على بنود S10-a/c/d. لا تقل إن النوع «يمنع» التجاوز — قل إنه يكشفه. القائمة المغلقة للمعرّفات — لا تُوسَّع بلا قرار معماري، ولا تُبنى ديناميكياً (تصير تسميات مقاييس، فكاردينالياتها يجب أن تكون محدودة — H20/H4):

| `rule_id` | `category` | ما يمسكه |
|---|---|---|
| `empty` | `structure` | نصّ فارغ أو مسافات فقط |
| `oversize` | `structure` | طول يفوق `VERIFY_MAX_CHARS` |
| `control_chars` | `structure` | محارف تحكّم أو Bidi أو صفرية العرض داخل النصّ |
| `placeholder` | `structure` | قوس تنسيق `{...}` لم يُستبدل (تسريب قالب) |
| `profanity` | `blocklist` | كلمة من قائمة البذاءة |
| `competitor` | `blocklist` | اسم منافس |
| `disclosure` | `blocklist` | كشف أن المتحدّث آلة (مفردات مُعرَّفة في الإعداد) |
| `verifier_error` | `internal` | اعتلال في المدقّق نفسه (H47) — يولّده `verify.py` لا `verify_rules.py` |

### 4.2 الترتيب

الفحوص البنيوية أولاً (`empty` → `oversize` → `control_chars` → `placeholder`) ثم قوائم الحظر (`profanity` → `competitor` → `disclosure`). أول مخالفة تُرجَع فوراً؛ لا تجميع أحكام. الترتيب **مكتوب وثابت** لأنه يحدّد `rule_id` المُبلَّغ، وتقريران على نفس النصّ يجب أن يتطابقا.

### 4.3 المطابقة — هذا هو الجزء الذي تُخفق فيه معظم التنفيذات

**يُمنع البحث عن سلسلة فرعية (substring) في أي قاعدة من قواعد قوائم الحظر.** السبب بمثال عربي صريح: كلمة محظورة من حرفين أو ثلاثة تُطابِق «زبون» و«زبدة» و«مشترك» وعشرات الكلمات السليمة، فيُوقَف البوت على كلام بريء. الإيجاب الكاذب مقبول (H49) لكنه ليس مجاناً، والسلب الكاذب هو ما نمنعه — لا أن نطابق عشوائياً.

الخوارزمية المطلوبة:

1. `norm = app.text.arabic.normalize(text)` — المطبِّع المشترك، بلا تعديله (H21).
2. `tokens = norm.split(" ")`، ثم لكل رمز: اقشط علامات الترقيم والرموز التعبيرية من **طرفيه** فقط (لا من وسطه).
3. `squeeze(tok)`: احذف الفواصل التي تُستعمل للتهريب داخل الكلمة — `. - _ * + ' "` والمسافات — ثم اجمع أي تكرار لحرف واحد متتالٍ إلى حرف واحد (`كـلـب` و`ك.ل.ب` و`كلللب` كلها تصير شكلاً واحداً).
4. العبارة المحظورة تُطبَّع وتُقشَط بنفس الدالتين **مرّة واحدة عند بناء القائمة**، لا في كل نداء.
5. المطابقة: عبارة من كلمة واحدة تُطابِق إذا `tok == phrase` **أو** `squeeze(tok) == squeeze(phrase)`. عبارة من `n` كلمة تُطابِق نافذة من `n` رمزاً متجاورة بنفس المقارنتين. **مساواة، لا احتواء.**
6. عبارة محظورة تصير بعد التطبيع سلسلة فارغة ⇒ تُتجاهل مع رفع عدّاد (لا تُطابِق كلّ شيء).

اكتب `squeeze` و`_match_phrase` دالتين نقيّتين مستقلتين قابلتين للاختبار وحدهما.

### 4.4 القوائم لا تُكتب في هذه الوحدة

`verify_rules.py` **لا يحوي أي كلمة محظورة حرفياً**. القوائم تأتي من `WorkerSettings` (§6). لهذا سببان، وكلاهما ملزم:

1. المرحلة S8 بندها الثالث تمنع ذكر أسماء المزوّدين (`openai`, `anthropic`, `deepseek`, `claude`, `gpt`) في أي وحدة خارج `app/llm/adapters/` وملفّي الإعداد. وقائمة `disclosure` تحوي بعض هذه الكلمات بطبيعتها. فوضعها في `app/workers/config.py` — وهو **مُستثنى أصلاً** في `_S8_CONFIG_FILES` — هو الموضع الصحيح الوحيد، لا استثناء جديد تخترعه في البوابة.
2. القوائم بيانات سياسة يملكها التاجر/المالك، لا ثوابت برمجية.

الافتراضات مكتوبة في `config.py` (H4)، وتُقرأ من البيئة بـ`_csv` كما في `CORE_OPTOUT_PHRASES_AR`.

### 4.5 التوقيع

```python
def check_text(text: str, *, rules: BlocklistSet, max_chars: int) -> RuleVerdict
```

`BlocklistSet` بنية مجمَّدة تُبنى مرّة واحدة عند الإقلاع من الإعدادات (`build_rules(settings) -> BlocklistSet`) وتحوي العبارات **بعد** التطبيع والقشط. `check_text` لا تقرأ إعداداً ولا بيئةً ولا تبني قوائم.

### 4.6 فحص الإقلاع الذاتي (fail-fast، H5)

عند بناء `BlocklistSet` نفّذ `check_text` على **كل** نصّ في `templates.TEMPLATES` وعلى `compose.PRODUCT_LIST_TEMPLATE` بعد استبدال `{items}` بنصّ محايد. إن أخفق أيّ منها ارفع `ConfigError` باسم القالب و`rule_id` — **ولا تُقلِع**. قالبٌ مُعتمَد يخالف قوائم الحظر خطأُ سياسة يجب أن ينفجر عند الإقلاع، لا أن يتحول إلى حجب صامت في محادثة عميل. وهذا الفحص نفسه يُثبت أن القواعد لا تُنتج إيجاباً كاذباً على النصوص التي اعتمدها المالك.

---

## §5 — `verify.py`: الإنفاذ ورد الفعل الصارم

### 5.1 التوقيع، وهو نقطة الكتابة الوحيدة

```python
def insert_verified_outbox(
    conn, *, settings: WorkerSettings, rules: BlocklistSet,
    tenant_id: uuid.UUID, conversation_id: uuid.UUID, channel_account_id: uuid.UUID,
    idempotency_key: str, message_class: str, expected_epoch: int | None,
    to_wa_id: str, template_id: str, text: str,
) -> VerifyOutcome
```

لا معامل `origin`: هذه الدالة **لِما أصله البوت حصراً** وتكتب `origin="bot"` بنفسها. مسار الموظف لا يمرّ من هنا ولا يُدقَّق ولا يُحجَب (H24/H27).

`VerifyOutcome` مجمَّدة: `(ok: bool, rule_id: str | None, outbox_id: uuid.UUID, paused: bool)`.

### 5.2 مسار القبول

`check_text` تُرجع `ok=True` ⇒ نداء واحد لـ`repos_outbox.insert_outbox` بالنص كما هو، وبنفس `expected_epoch` الذي وصلها. ارفع `verify_checks_total{result="pass"}`. لا شيء آخر.

### 5.3 مسار المخالفة — بالترتيب، في المعاملة نفسها التي وصلت بها

1. **لا تكتب النص الأصلي. أبداً.** لا في `outbox` ولا في `messages`.
2. **أعد قراءة المحادثة**: `read_conversation_epoch_status` و`read_conversation_version`. لا تعتمد على لقطة سابقة، ولا تأخذ راية `already_paused` من المستدعي — فمسار التحويل في `turn.py` قد يكون أوقف البوت قبل قليل في هذه المعاملة نفسها، ومحاولة إيقاف ثانية بنسخة قديمة تفشل بـstale version.
3. **إن كانت الحالة `active`**: `set_bot_status(conversation_id=..., expected_version=<النسخة المقروءة الآن>, new_status="paused_human", reason=f"verifier_{rule_id}")`. إن أعادت `None` ارفع `TransientError` (نفس نمط `turn.py`) ليُعاد الدور، ولا تُرسِل شيئاً. الحقبة الجديدة تصير `expected_epoch` للصف الآمن.
   **وإن كانت `paused_human` أو `closed`**: لا تُوقف ثانية، واستعمل الحقبة المقروءة الآن.
4. **اكتب صف الحجب**: `repos_outbox.insert_verifier_block(conn, tenant_id=..., conversation_id=..., reason=f"verifier_{rule_id}", draft_excerpt=<انظر 5.5>)`.
5. **اكتب القالب الآمن** بنداء واحد لـ`repos_outbox.insert_outbox`: `template_id = settings.verify_safe_template_id`، والنصّ من `templates.template_text(...)`، و`origin="bot"`، و`message_class="service"`، و`expected_epoch` = حقبة الخطوة 3، و`idempotency_key = f"{<المفتاح الأصلي>}:v1"` — مشتقّ حتمياً حتى لا يُرسِل دورٌ مُعاد رسالتين (H22).
6. **أعلِن للواجهة**: `repos_inbox.write_inbox_event` لـ`handoff.requested` ثم `conversation.updated` (بنفس الحمولات والمفاتيح التي يستعملها `turn.py` اليوم — القائمة البيضاء `INBOX_EVENT_ALLOWED_KEYS` **لا تُوسَّع**، فسبب التحويل يُحمَل في `reason` الموجود أصلاً)، وبعد كل منهما `ws_publish.queue_publish`. النشر بعد الالتزام كما في P1.3b.
7. **المقاييس والسجل**: `verify_checks_total{result="violation"}`، `verify_violations_total{rule_id}`، و`obs_logging.log_event(event="verify.violation", rule_id=..., conversation_id=...)` — **بلا نصّ وبلا هاتف** (H48).

### 5.4 لا ارتداد، ولا تكرار

`insert_verified_outbox` **لا تنادي نفسها**، والقالب الآمن **لا يُمرَّر على `check_text`** في زمن التشغيل: سلامته مضمونة بفحص الإقلاع في §4.6. وإن أخفق `check_text` نفسه بالرمي (H47) ⇒ التقط `Exception` وامضِ بمسار المخالفة بـ`rule_id="verifier_error"`، وارفع `verify_errors_total`. المدقّق لا يمنع الإرسال بصمت ولا يترك العميل بلا ردّ: في كل مسار مخالفة يُرسَل قالب آمن ويُحوَّل الأمر إلى موظف.

### 5.5 `draft_excerpt` — الاستثناء الوحيد من H48، بشرطين

جدول `verifier_blocks` معرَّف في `0001` بعمود `draft_excerpt` وبـ`REVOKE UPDATE, DELETE` لحماية أثر التدقيق، وعليه سياسة RLS بالحلقة العامة (فيه `tenant_id`) — فهو موضع مشروع لمقطع من المسوَّدة. الشرطان:

1. `text[:settings.verify_excerpt_max_chars]` (افتراضياً 200) — سقف مكتوب.
2. `redact.mask_phones(...)` قبل الكتابة — لا رقم كامل في أثر التدقيق (H41).

لا تكتب المقطع في سجل ولا في مقياس ولا في حمولة حدث WS. الجدول وحده.

### 5.6 تعديل `turn.py`

استبدل نداء `repos_outbox.insert_outbox` الوحيد بنداء `verify.insert_verified_outbox`، وابنِ `BlocklistSet` مرّة واحدة في `build_router`/تجهيز الخيط (لا في كل دور) ومرّره. `metrics.outbox_written_total` و`metrics.compose_replies_total` ترفعهما **فقط** عند `outcome.ok` — لا نعدّ رداً مركّباً لم يخرج. ولا تغيّر `decide()` ولا `_resolve_action` ولا `compose.py`.

---

## §6 — الإعدادات (كلها بافتراضات مكتوبة — H4)

| المفتاح | الافتراض | ملاحظة |
|---|---|---|
| `VERIFY_ENABLED` | `true` | إطفاؤه قرار سياسة؛ وإذا أُطفئ يجب أن يرفع `verify_disabled` مقياساً دائماً ويُسجَّل سطر تحذير عند الإقلاع |
| `VERIFY_MAX_CHARS` | `4000` | سقف أطول من `MAX_POLICY_CHARS=2000` بفرق واضح |
| `VERIFY_EXCERPT_MAX_CHARS` | `200` | §5.5 |
| `VERIFY_SAFE_TEMPLATE_ID` | `handoff_notice` | **يُتحقَّق عند التحميل** أنه عضو في `templates.SAFE_FALLBACK_TEMPLATES`، وإلا `ConfigError` |
| `VERIFY_PROFANITY_AR` | قائمة مكتوبة | `_csv` |
| `VERIFY_PROFANITY_EN` | قائمة مكتوبة | `_csv` |
| `VERIFY_COMPETITORS` | قائمة مكتوبة | أسماء منصات التجارة المنافسة المعروفة |
| `VERIFY_DISCLOSURE` | قائمة مكتوبة | مفردات كشف الآلة، ومنها أسماء المزوّدين — وموضعها هنا بحكم §4.4 |

`templates.SAFE_FALLBACK_TEMPLATES = frozenset({"handoff_notice", "safe_ack"})` — قائمة **مغلقة** بتعليق يقول إن التوسيع قرار سياسة لا قرار برمجة، على نفس نمط `POLICY_EXEMPT_TEMPLATES`.

للقوائم الافتراضية: اكتب ما تراه معقولاً ومحافظاً، واذكر في التقرير **عدد** العبارات في كل قائمة لا محتواها، ودوّن في `docs/P1_DEVIATIONS.md` أن مراجعة المالك للقوائم بند مفتوح (OQ-P1-14). لا تخترع اسم منافس وهمياً ولا تضع عبارة قد تُطابِق كلمة تجارية شائعة.

---

## §7 — المقاييس والتنبيهات

**المقاييس** (بنفس أسلوب `obs/metrics.py`، وكل تسمية بكاردينالية محدودة من القائمة المغلقة في §4.1):

| المقياس | النوع | التسميات |
|---|---|---|
| `verify_checks_total` | Counter | `result` ∈ {`pass`,`violation`} |
| `verify_violations_total` | Counter | `rule_id` (القائمة المغلقة وحدها) |
| `verify_errors_total` | Counter | — |
| `verify_duration_seconds` | Histogram | — |
| `verify_blocklist_phrases` | Gauge | `category` ∈ {`profanity`,`competitor`,`disclosure`} — يُضبَط عند الإقلاع |
| `verify_enabled` | Gauge | — (`1`/`0`؛ يجعل الإطفاء مرئياً في Prometheus لا مخفياً في ملف بيئة) |
| `verify_safe_template_sent_total` | Counter | — |

**التنبيهات في `ops/prometheus/alerts.yml`** — مبنية على التقادم والمعدّل لا على الغياب (درس P1.3b):

1. `VerifierDisabled` — `verify_enabled == 0` لأكثر من 5 دقائق ⇒ critical. إطفاء المدقّق في الإنتاج حادثة.
2. `VerifierViolationSpike` — `rate(verify_violations_total[15m])` يفوق عتبة مكتوبة ⇒ warning. ارتفاع مفاجئ يعني إما هجوماً على المطالبة أو قاعدة تُنتج إيجاباً كاذباً؛ الحالتان تستحقّان النظر.
3. `VerifierInternalErrors` — `rate(verify_errors_total[10m]) > 0` ⇒ critical. المدقّق يفشل مغلقاً، فكل خطأ فيه يعني عميلاً يصله قالب آمن بدل ردّ صحيح.
4. `VerifierBlocklistEmpty` — `verify_blocklist_phrases{category="profanity"} == 0` ⇒ critical. قائمة فارغة = مدقّق بلا أسنان مع مقياس `verify_enabled == 1` مطمئن كذباً.

تأكّد أن المرحلة S4 في البوابة تُقرّ أن كل مقياس ورد في `alerts.yml` معرَّف فعلاً في `metrics.py`.

---

## §8 — المرحلة S10 في `scripts/static_gate.py`

**صياغة المالك:** «لا يُسمح باستدعاء دالة الإرسال في `outbox` إلا إذا مرّ النص حصرياً عبر وحدة المدقّق أو كان قالباً آمناً.» اجعلها فحصاً بنيوياً لا تعليقاً، بخمسة بنود على نمط S8/S9 (نفس أسلوب `_err("S10", ...)` ونفس التقرير):

**S10-a — نقطة كتابة واحدة (H46).** لكل وحدة تحوي نداءً لـ`insert_outbox` (اسماً أو سمةً، بـ`ast.walk` لا بـ`re`):
- مسموح بلا شرط: `app.db.repos_outbox` (موضع **التعريف**) و`app.workers.verify`.
- مسموح بشرط: أي وحدة أخرى **يجب** أن يحمل كل نداء فيها `origin="human"` كوسيط كلمة مفتاحية بقيمة **حرفية**. وإلا مخالفة. (هذا يُبيح مسار الموظف في `routes_inbox.py` بلا استثناء بالاسم، ويمنع أي مسار بوت جديد.)

**S10-b — نقاء طبقة القواعد (H45).** `app.workers.verify_rules` لا يجوز أن يستورد شيئاً خارج القائمة: `re`, `dataclasses`, `typing`, `enum`, `__future__`, `app.text.arabic`. أي استيراد آخر — وبخاصة `app.llm` أو `psycopg` أو `httpx` أو `redis` أو `time` أو `random` أو `datetime` — مخالفة. هذا هو الإثبات البنيوي لقرار المالك «يُمنع استخدام LLM في هذه الطبقة»: لا يعتمد على تعليق ولا على حسن نيّة.

**S10-c — لا طريق ثانٍ داخل وحدة الإنفاذ.** في `app.workers.verify`: يجب أن توجد دالة `insert_verified_outbox`، وأن يحوي جسمها نداءً لـ`check_text`، وأن لا يزيد عدد نداءات `insert_outbox` في الوحدة كلها على **اثنين** (مسار القبول ومسار القالب الآمن). ثلاثة نداءات تعني طريقاً ثالثاً لم يُراجَع.

**S10-d — لا يبقى نداء بوت خارج الوحدة.** `app.workers.turn` **يجب** أن لا يحوي أي نداء لـ`insert_outbox` (يجب أن يكون قد صار `verify.insert_verified_outbox`). صياغة هذا البند إيجابياً — «غياب مطلوب» — يجعل أي ارتداد مستقبلي في `turn.py` مخالفةً فورية لا نقاشاً.

**S10-e — الإرسال من نوع موثوق (أمر المالك).** في `app.workers.verify`: كل نداء لـ`insert_outbox` يجب أن يكون وسيطه `text=` إمّا سمةً `.value` على متغيّر (أي `approved.value` — مخرج `approve`)، أو نداءً لـ`templates.template_text(...)` (القالب الآمن). أي شكل آخر — سلسلة حرفية، f-string، نتيجة دالة أخرى، متغيّر عاري — مخالفة. وبند S8 الثاني يبقى سارياً على الحرفيات فوق ذلك.

### إثبات البوابة بإفشال متعمَّد (كما في S7/S8/S9)

نفّذ **خمس** مخالفات مصطنعة، واحدة لكل بند، وأرفِق المخرج الذي يعدّها (**`5 violation(s)`** ومعرّف كل واحدة)، ثم احذفها وأرفِق `STATIC GATE PASSED — 0 violations.` مع زمنه:

1. نداء `insert_outbox(..., origin="bot", ...)` في وحدة عاديّة (مثلاً `app/workers/evt.py`).
2. `import time` في `verify_rules.py`.
3. نداء `insert_outbox` ثالث في `verify.py`.
4. إعادة نداء `repos_outbox.insert_outbox` في `turn.py`.
5. تمرير متغيّر عاري (لا `.value`) كـ`text=` في `verify.py`.

**والبوابة على الشجرة كاملةً هي آخر أمر تنفّذه قبل كتابة التقرير (S1-d)**، وهدفها صفر مخالفة على الشجرة لا على الجديد وحده.

---

## §9 — الاختبارات (نقية، بمزدوجات مسجِّلة، بلا قاعدة ولا حاوية)

`core/tests/test_verify_rules.py` — جدول حالات، وكل صفّ باسم ناطق:

| مجموعة | حالات لا بدّ منها |
|---|---|
| البنيوية | فارغ · مسافات فقط · طول = السقف بالضبط (يمرّ) · السقف + 1 (يُحجَب) · `\u200b` مدسوس · `{items}` لم يُستبدل |
| البذاءة | مطابقة مباشرة · مع تشكيل · بأرقام عربية-هندية · `ك.ل.ب` · `كلللب` · متعدّد الكلمات |
| **مصيدة السلسلة الفرعية** | عبارة محظورة قصيرة تكون بادئةً لكلمة سليمة (نمط «زبون»/«زبدة») **يجب أن تمرّ** — هذا الصفّ هو الذي يميّز مطابقة بالمساواة من بحث بالاحتواء، فلا تحذفه |
| المنافسون | اسم منافس منفرداً · داخل جملة · بحروف كبيرة |
| الكشف | مفردة عربية · مفردة إنجليزية · اسم مزوّد (من الإعداد لا من الكود) |
| السلامة | كل نصوص `templates.TEMPLATES` تمرّ · `PRODUCT_LIST_TEMPLATE` بعد الاستبدال يمرّ · نصّ سياسة طويل عادي يمرّ |
| النقاء | نفس المدخل عشر مرات ⇒ نفس `RuleVerdict` بالضبط |
| القوائم | عبارة تصير فارغة بعد التطبيع تُتجاهل ولا تُطابِق كل شيء |

`core/tests/test_verify.py` — بمزدوج مسجِّل لـ`conn` وللمستودعات:

| # | الحالة | التوكيد |
|---|---|---|
| 1 | نصّ سليم | نداء `insert_outbox` **واحد** بالنصّ الأصلي؛ صفر `set_bot_status`؛ صفر `insert_verifier_block` |
| 2 | نصّ مخالف، المحادثة `active` | النص الأصلي **لم يُكتب في أي نداء** · `set_bot_status` مرّة واحدة بـ`paused_human` و`reason="verifier_<rule_id>"` · `insert_verifier_block` مرّة واحدة · `insert_outbox` مرّة واحدة بالقالب الآمن وبالحقبة **الجديدة** ومفتاح `...:v1` · حدثان في `inbox_events` ونشرتان |
| 3 | نصّ مخالف، المحادثة `paused_human` مسبقاً | **لا** نداء لـ`set_bot_status` · القالب الآمن بالحقبة المقروءة |
| 4 | `set_bot_status` تُرجع `None` | `TransientError` · وصفر نداء لـ`insert_outbox` |
| 5 | `check_text` ترمي استثناءً | مسار المخالفة بـ`rule_id="verifier_error"` و`verify_errors_total` يرتفع · ولا تسريب للاستثناء إلى المستدعي (H47) |
| 6 | الترتيب | فحص أن `insert_outbox` الآمن لم يُنادَ **قبل** `set_bot_status` (المزدوج يسجّل الترتيب) |
| 7 | المقطع | `draft_excerpt` المكتوب مُقنَّع الأرقام ولا يفوق السقف |
| 8 | H48 | لا سطر سجل ولا تسمية مقياس تحوي النصّ أو رقماً من 7 خانات |
| 9 | تعطيل المدقّق | `VERIFY_ENABLED=false` ⇒ النص يُكتب كما هو و`verify_enabled == 0` (سلوك مُعلَن، لا صامت) |
| 10 | قالب آمن غير مسموح | `VERIFY_SAFE_TEMPLATE_ID` خارج `SAFE_FALLBACK_TEMPLATES` ⇒ `ConfigError` عند التحميل |
| 11 | فحص الإقلاع | قالب مُلوَّث في `TEMPLATES` ⇒ `ConfigError` باسم القالب و`rule_id` |

`core/tests/test_dispatch_stale.py` — الحالات الخمس في §3.

وأضِف إلى `test_turn.py` القائم — بلا كسر شيء منه (H21) — توكيداً أن `turn.py` صار ينادي `verify.insert_verified_outbox` وأن `outbox_written_total` لا يرتفع عند المخالفة.

**لا تستعمل قاعدة حقيقية ولا حاوية.** وسم `@pytest.mark.db` لأي اختبار يحتاجها، والحزمة النقية تبقى **صفر أحمر** — وهو المستوى الذي بلغته في P1.5b ولا يجوز التراجع عنه (H21).

---

## §10 — الترحيل

`core/migrations/0011_p1_verifier.sql`، وفيه **شيء واحد**: `CREATE OR REPLACE FUNCTION app.claim_outbox(...)` بالتوقيع نفسه وقد عُدِّل شرط الإسقاط (§3). forward-only وidempotent (H14).

**لا تنشئ جدولاً ولا عموداً ولا فهرساً ولا صلاحية:** `verifier_blocks` معرَّف في `0001` بـRLS من الحلقة العامة وبـ`REVOKE UPDATE, DELETE` — استعمله كما هو. وإن وجدتَ أنه يفتقر إلى ما تحتاجه فعلياً **قل ذلك في التقرير ولا تعدّله** حتى أُقرّه؛ تعديل جدول تدقيق محميّ قرار معماري.

اذكر في التقرير صريحاً أن هذه الدفعة **لا تضيف جدولاً** — وأن ذلك قرار مقصود لا سهو.

---

## §11 — التسليم ولغة التقرير

القواعد السارية من P1.3b، وهي غير قابلة للتفاوض:

1. **أفعال صريحة:** `أضفتُ` / `وجدتُه موجوداً` / `عدّلتُه` / `شغّلتُ` / `لم أشغّله (والسبب …)`. لا تكتب «تم التحقق» بلا فاعل، ولا تكتب «أضفتُ» عن شيء كان موجوداً.
2. **كل مخرج بأمره وزمنه (UTC).** لا رقم بلا أمر أنتجه. لا رقم مُقدَّر. لا رقم من ذاكرتك (H18).
3. **`py_compile` ليس فحصاً** ولا يُذكر كدليل.
4. **البوابة آخر أمر** قبل كتابة التقرير (S1-d)، ومخرجها بزمنه.
5. **`docs/PHASE_GATE.md` لا تُفتح ولا تُعدَّل.**
6. إن منعتك البيئة من شيء، **قله بصراحة** مع نصّ الخطأ. القيد المُعلَن مقبول؛ القيد المُجمَّل ليس كذلك.

**ما يُرفَق حرفياً في التقرير:**

- مخرج `git log --oneline -1` و`git status --porcelain` من §0.1.
- مخرج البوابة على المخالفات الأربع المصطنعة (`4 violation(s)` بمعرّفاتها) ثم على الشجرة النظيفة (`0 violations`) بزمنه.
- مخرج `python -m pytest tests -q` كاملاً بسطره الأخير (المتوقَّع: صفر أحمر، والعدد أكبر من `130`).
- عدد العبارات في كل قائمة حظر (لا محتواها).
- أسماء المقاييس السبعة وقواعد التنبيه الأربع كما كُتبت.
- جملة صريحة: **هل بقي أي نداء لـ`insert_outbox` خارج `verify.py` ومسار `origin="human"`؟** بمخرج `grep` أو البوابة.

**اختم تقريرك حصرياً بهذه العبارة:**

`P1.6 COMPLETE — STOPPING. AWAITING AUDIT. NO P1.7 WORK STARTED.`

وقبل هذا السطر مباشرةً اكتب سطر الحصيلة التقنية: `verifier deterministic (no LLM) · single outbox write point · S10 a/b/c/d/e proven · F-P1-09 fixed in 0011 (0001 untouched) · gate 0 violations at <ZULU>`

---

## §12 — أسئلة مفتوحة تُدوَّن ولا تُقرَّر من عندك

- **OQ-P1-14:** مراجعة المالك لقوائم البذاءة والمنافسين والكشف — هل تكون عامّة أم لكل تاجر قائمته؟ (البنية الحالية عامّة عبر البيئة.)
- **OQ-P1-15:** هل يرى الموظف في الواجهة **ما حاول البوت قوله**؟ اليوم المقطع في `verifier_blocks` وحده ولا يخرج إلى WS ولا إلى ملاحظة داخلية (H28/H48). إظهاره قرار سياسة.
- **OQ-P1-16:** هل يُعاد تشغيل البوت تلقائياً بعد حجب واحد أم يبقى `paused_human` حتى يقرّر الموظف؟ التنفيذ الحالي: يبقى — وهو الأسلم.

---

**ابدأ بـ§0 بالترتيب. لا تبدأ §4 قبل أن يصبح `git status --porcelain` نظيفاً وF-P1-09 مُصلَحاً وفحص §3 أخضر.**
