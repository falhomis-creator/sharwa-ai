# البرومبت المعماري الخامس عشر — P3.1: بوّابة الإرسال الاستباقي وسياسة التقطير (حارس رقم التاجر)

**من:** المهندس المعماري
**إلى:** المنفّذ (DeepSeek داخل Cline)
**المشروع:** `sharwa_ai` — `C:\sharwaai\sharwa-ai`
**المرحلة السابقة:** P2.4 — **APPROVED (مشروط)** (`docs/P2_04_AUDIT.md`). شرطاها هما **الخطوة صفر** هنا.
**نطاق هذه الدفعة:** **الحارس لا المولّدات.** لا حملة، لا سلّة متروكة، لا مراجعات، لا بثّ. هذه الدفعة تبني البوّابة الوحيدة التي سيمرّ منها كل رسالة استباقية في P3.2–P3.5، وتُثبت أنها تحبس بدل أن تُرسل.

---

## §0 — قرار المعماري، وما وجدتُه في الشجرة

**P3 أخطر مراحل المشروع على التاجر.** كل ما بُني حتى الآن ردٌّ على رسالة وصلت من العميل. P3 يبدأ **المحادثة** — وبرقم واتساب عادي (Baileys، بروتوكول غير رسمي) الحظرُ فيه يأتي من **سلوك** الرقم: حجم مفاجئ، رسائل لمن لم يتوقّعها، بلاغات «سبام». ورقم التاجر هو رأس ماله التشغيلي: حظره يوقف ردوده الخدمية كلها لا حملاته وحدها.

**وأقولها صريحةً قبل أن تكتب سطراً:** لا سياسة تضمن عدم الحظر. هدف هذه الدفعة **خفض احتماله بحدود حتمية قابلة للقياس وللإيقاف التلقائي**، لا الادّعاء بإلغائه. ولهذا لا تكتب في تقريرك «محمي من الحظر»؛ اكتب ما قاسه اختبارك.

**لماذا الحارس أولاً:** إن بنينا مولّد الحملات قبل البوّابة فبوّابتُه ستُصمَّم على قدّ مولّده. نبني البوّابة أولاً، ثم لا يُسمَح لأي مولّد أن يكتب في `outbox` إلا عبر كاتبها الوحيد.

### ما قرأتُه في الشجرة الحيّة (كل بند بمرجعه — راجعه ولا تصدّقني)

| # | الوصف | المرجع | الأثر |
|---|---|---|---|
| **F-P3-01** [حرج] | حدود التسويق في البوابة **مفتاحها المستلِم لا المُرسِل**: `tryConsumeMarketingDailyCap(client, {number: item.to, ...})` و`tryConsumeToken({number: item.to})` | `gateway/src/outbound/queue.js` (≈ السطران 11 و21) · `tokenBucket.js` (التوثيق يقول «destination number») | **لا سقف على ما يخرج من رقم التاجر.** الحدّ الفعلي الوحيد تمهّل 2–3 ثوانٍ بين الرسائل (`PACE_BULK_*`) = 1200–1800 رسالة/ساعة من رقم جديد. و`MARKETING_DAILY_CAP=1000` **للمستلِم الواحد في اليوم** — لا معنى له. |
| **F-P3-02** [حرج] | فحص الـsuppression في `_pre_send_checks` **يُتخطّى** إن كان `conversation_id` فارغاً (`and row.conversation_id is not None`)، والعمود nullable في `0001` | `core/app/workers/dispatch.py` ≈ السطر 122 | صفّ حملة بلا محادثة يتجاوز STOP. |
| **F-P3-03** [حرج] | **STOP لا يوقف إشعار «عاد المنتج».** يكتب الاستيعاب suppressions بنطاقات `marketing/back_in_stock/review_request`، لكن الـdispatcher يطابق `scope = message_class` (`service/utility/marketing`) ويتخطّى `service` كلياً، وإشعار المخزون يُكتب `message_class="service"` | `repos_ingest.OPTOUT_SCOPES` · `dispatch.py` · `workers/stock.py::_insert_notice` | أول مُرسِل استباقي في المشروع **يتجاوز الانسحاب**. نطاقا `back_in_stock` و`review_request` لا يقرؤهما أحد. |
| **F-P3-04** [عالٍ] | `claim_outbox` يرتّب بـ`next_attempt_at` وحده: لا أولوية لصنف | `0011_p1_verifier.sql` | بثٌّ أُدخل قبل رد فوري يؤخّر الردّ. وهو معيار خروج P3 («حمل البثّ مع الفوري»). |
| **F-P3-05** [عالٍ] | `number_health` و`consents` و`scheduled_jobs` و`campaigns` و`campaign_recipients` **جداول بلا أي كود** (لا يمسّها إلا `testsupport`) | `grep` | البوّابة المنصوص عليها في `00_ARCHITECTURE.md §4.7` غير موجودة. |
| **F-P3-06** [عالٍ] | لا سجلّ إرسال استباقي، ولا ساعات هدوء (`tenants.timezone` يقرؤه `cli` وحده)، ولا حدّ تكرار للعميل | — | لا شيء يمنع إزعاج عميل بخمس رسائل في ساعة. |
| **F-P3-07** [عالٍ] | `_fail_and_escalate` يوقف بوت المحادثة (`set_bot_status(... paused_human, "send_failed")`) عند تجاوز المحاولات **أيّاً كان `origin`** | `dispatch.py::_fail_and_escalate` | فشل رسالة **تسويقية** سيوقف بوت عميل يحادثنا الآن. |
| **F-P3-08** [عالٍ] | `claim_outbox` يزيد `attempts` عند كل مطالبة؛ والتأجيل (ساعات الهدوء/السقف) يعني مطالبة بلا إرسال | `0011` | رسالة مؤجَّلة ستستهلك محاولاتها فتُفشَل وتُصعَّد. التأجيل ليس فشلاً. |
| **F-P3-09** | `0012` لا يوجد — وكاتبا `outbox` المسموحان (H46) لا يشملان `origin='automation'` مع أن القيد في `0001` يسمح به | `CONSTITUTION.md H46` | كاتب ثالث يحتاج تعديلاً دستورياً صريحاً. |

---

## §1 — الخطوة صفر (قبل أي سطر في الحارس)

### 1.1 F-P2-07 [حرج] — `seed_gazetteer.py` يُجهِض على قاعدة حقيقية

شغّلتُه على PostgreSQL 16 بعد الترحيل فأعطى:

```
SEED ABORTED - 1 geometric verification failure(s):
  - landmark 'دوار المصباحي': centre not inside parent 'التحرير' (ST_Covers/ST_DWithin)   [RC=1]
```

السبب: «التحرير» مديرية في أمانة العاصمة **وفي عدن** (اسم مكرّر في بياناتنا نفسها)، و`verify()` يبني `by_norm` **بالاسم وحده** فيبتلع الصفّ الثاني الأول، و`_parent_id()` يستعمل `LIMIT 1` بلا `ORDER BY`. **مرجع الأب بالاسم وحده غير صالح.** الإصلاح التصميمي: لكل صفّ في الملف `key` مستقرّ (مثلاً `"capital/tahrir"`) وأبناؤه يُشيرون بـ`parent_key`؛ ويُرفَض عند التحميل تكرار `(level, name_norm, parent_key)`؛ ويُحلّ الأب في `upsert` بالمفتاح لا بالاسم. **لا تخترع إحداثيات ولا تُغيّر القيم الموجودة.** المخرج المطلوب: `seed_gazetteer: loaded 37 new rows` على قاعدة حقيقية، ثم **إعادة التشغيل ⇒ `loaded 0 new rows`**، ثم `test_golden_addresses.py -m db` يمرّ ويطبع المصفوفة (**38/45** كما قست أنا بعد ترقيع محلّي — أي قياس حقيقي لا «متوقَّع»).

### 1.2 F-P1-12 [حرج] — الـdispatcher لا يرسل شيئاً على قاعدة حقيقية

وضعتُ مستأجراً وقناةً وعميلاً ومحادثةً وصفّ `outbox` في PostgreSQL حقيقي ونادَيتُ `dispatch.dispatch_cycle` بعميل بوابة وهمي. **ثلاثة أعطال متتالية:**

1. `repos_outbox.outbox_stats` تقرأ `FROM outbox` عبر `system_tx`، و`sharwa_system` لا يملك `SELECT` على الجدول ⇒ `InsufficientPrivilege: permission denied for table outbox` (أول عبارة في كل دورة).
2. `claim_outbox(limit, lease_s)` يمرّر `int, int` إلى `(integer, interval)` ⇒ `UndefinedFunction: function app.claim_outbox(smallint, smallint) does not exist`.
3. `_outbox_from_row` يقرأ `attempts=row[10]` والعمود 10 هو **`status`** (`attempts` هو 11) ⇒ `TypeError: '>' not supported between 'str' and 'int'` خارج كتلة `try`.

**العائلة:** F-P1-07 اسم مفقود · F-P1-09 تفاعل · F-P1-11 كذب على التوقيع · F-P2-03 موجِّه غير مُركَّب · **هذه: «الموجود لا يُشغَّل مقابل البنية الحقيقية».** لا تكشفها بوابة ساكنة ولا اختبار بمزدوجات.

**الإصلاح (ترحيل `0012_p3_dispatch_wiring.sql`، ويبقى `0001`–`0011` بلا مسّ):**

- `app.outbox_stats()` دالة `SECURITY DEFINER` تُعيد صفّاً لكل `message_class`: `(message_class, depth, oldest_pending_seconds)`، و`GRANT EXECUTE` لـ`sharwa_system`. وتُستعمَل من `dispatch_cycle`، وتُغذّي مقياسَي العمق والتقادم **بوسم الصنف** (مطلوب لـ§3.8 والتنبيه الخدمي).
- **`app.claim_outbox` تُستبدَل بنسخة واحدة** بتوقيع `(p_limit integer, p_lease interval, p_marketing_limit integer DEFAULT 4)` وتُحذَف القديمة بـ`DROP FUNCTION` في الترحيل نفسه — **دالة مطالبة واحدة لا اثنتان** (H74 روحاً). السلوك الجديد (F-P3-04): الصفوف غير التسويقية أولاً بترتيب `next_attempt_at`؛ ثم التسويقية بحدّ **أقصى `p_marketing_limit` في الدورة، وصفّ واحد لكل `channel_account_id`** (`DISTINCT ON (channel_account_id)` بترتيب `next_attempt_at`) — فلا يحتلّ بثّ واحد كل خانات المطالبة، ولا تحتلّ قناةٌ واحدة خانات القنوات الأخرى. ويحافظ على شرط F-P1-09 (`epoch <> expected_epoch OR bot_status = 'closed'`).
- **في بايثون:** المعاملات تُمرَّر بأنواعها (`%s::integer`, `make_interval(secs => %s)`) **والأعمدة بأسمائها** (قائمة `SELECT id, tenant_id, ... FROM app.claim_outbox(...)` صريحة، أو `dict_row`) — **لا تُقرأ أعمدة `SELECT *` بموضعها أبداً** (ثغرة العطل 3 ستعود مع كل `ALTER TABLE` يُضيف عموداً — وهذه الدفعة ستُضيف).
- **اختبار توصيل حقيقي `@pytest.mark.db`** (`test_dispatch_db.py`) — **هذا البند الذي كان غيابه يُخفي F-P1-12**: يُنفّذ `dispatch_cycle` الحقيقي على القاعدة الحقيقية بالأدوار الحقيقية (`system_tx` للمطالبة، `tenant_tx` لما عداها) وعميل بوابة وهمي بأكواد استجابة مبرمجة، على الجدول: (أ) `human/service` ⇒ `sent` · (ب) `bot/service` على محادثة `paused_human` بحقبة مطابقة ⇒ `sent` (حالة F-P1-09 على قاعدة حقيقية) · (ج) `bot` بحقبة قديمة ⇒ `dropped_stale` · (د) تسويقي + suppression ⇒ `dropped_policy` · (هـ) `attempts` عند السقف ⇒ `failed` **وتصعيد** البوت **لمحادثة `origin='bot'` فقط** (F-P3-07 أدناه) · (و) استجابات `202/423/429/400` ⇒ `sent/dropped_policy/requeue/failed` · (ز) **`OutboxRow.attempts` من نوع `int` فعلاً.** وكل حالة تتحقّق من **الحالة في القاعدة** لا من استدعاء وهمي.
- **F-P3-07 (فشل رسالة آلية لا يوقف بوت العميل):** `_fail_and_escalate` يتصرّف على `origin`: `bot` ⇒ كما هو؛ `automation` ⇒ `failed` + سجلّ + مقياس **بلا `set_bot_status`**. اختبار `db`: صفّ `automation` تجاوز المحاولات على محادثة `active` ⇒ تبقى `active` بحقبتها.
- **القاعدة الجديدة الملزِمة من الآن (مادة H86 في §2):** **لا دالة تمرّ بحدّ — دور قاعدة، نوع معامل، ترتيب أعمدة، تسلسل — تُعتمَد قبل اختبار `db` يمرّ بذلك الحدّ فعلاً.** وفي كل دفعة قادمة: لكل دالة `app.*` ولكل دالة تُنادى عبر `system_tx` اختبارُ توصيل يُنادي **الدالة الحقيقية** ويتحقّق من النتيجة.

### 1.3 تنظيف ما كشفه التدقيق (صغير)

- **F-P2-09:** اختبارات `test_migrate.py` الأربعة تفترض ترحيلَين (`first == ["0001_baseline", "0002_p0_api"]`) — **القائمة المتوقَّعة تُشتقّ من محتوى `core/migrations/`** (مرتّبة) لا تُكتَب يدوياً، فتبقى صحيحة مع `0012` و`0013`.
- **F-P2-10:** S20 بتعبير نمطي `(?i)delete\s+from\s+tenants` ونطاق يضمّ `scripts/*.py`؛ والإثبات بإفشال متعمَّد (الحالة الصغيرة بمسافتين تُمسَك).
- **حارس معكوس في `test_golden_addresses.py`:** استبدل `assert mismatches >= 1` بقائمة المخالفات المعلومة (الإدخالات السبعة) **مثبَّتة بأسمائها** في الملف نفسه: مخالفة **جديدة** ⇒ يفشل؛ مخالفة معلومة **اختفت** (تحسّن) ⇒ يطبع تنبيهاً ولا يفشل. ولا تمسّ `resolve.py` ولا `normalize.py` (F-P2-08 مفتوحة بموعدها، ليست من هذه الدفعة).

### 1.4 F-P3-01 — سقف المُرسِل في البوابة (الحدّ الوحيد خارج `core` يجب أن يحمي الرقم)

في `gateway/src/outbound/`: أضِف حدّاً يومياً **مفتاحه `sessionId`** (المُرسِل) إلى الحدّ القائم، بالعدّاد الذرّي نفسه (`INCR` + `EXPIRE 25h` + `DECR` عند الرفض). المتغيّر `MARKETING_SENDER_DAILY_CAP` **الافتراضي 250** (يساوي سقف الدرجة الأخيرة في سلّم `core` — §4)، ويُفحَص في `queue.js` قبل الحدّ لكل مستلِم. **لا تُزل حدّ المستلِم**: أبقِه وخفّض افتراضيَّه `MARKETING_DAILY_CAP` إلى **3** (فهو حاجز تكرار لكل مستلِم)، وصحّح تعليقات `tokenBucket.js` التي تدّعي «per-number» وهي per-destination. اختبار Node في `gateway/src/__tests__/outbound_queue.test.js` (أو ملف جديد): 300 مستلِماً **مختلفاً** من جلسة واحدة ⇒ يمرّ 250 فقط ويُؤجَّل الباقي (يُعاد للطابور لا يُسقَط)؛ وجلستان لا تتشاركان العدّاد. وأضِف `MARKETING_SENDER_DAILY_CAP` إلى `docker-compose.yml` و`.env.example`، وفحصاً في `scripts/check_env.py`: **`SEND_POLICY_DAILY_CEILING ≤ MARKETING_SENDER_DAILY_CAP`** (الحدّ الأدنى في `core` والأعلى في البوابة، لا العكس).

### 1.5 أداة تشغيل الحزمة الموسومة (لأن القيد البيئي تكرّر أربع مرات)

`scripts/run_db_suite.py`: يتحقّق من وجود متغيّرات `CORE_*_DATABASE_URL` الثلاثة وأن القاعدة تستجيب **قبل** أن يبدأ، ثم يشغّل `pytest tests -q -m db` في أمر واحد **مرّتين متتاليتين**، ويطبع **السطر الأخير من كل تشغيل كما هو**، ويخرج بـ`1` إن اختلف الرقمان أو ظهر خطأ. هدفه أن يشغّله المالك بأمر واحد حين تتعذّر القاعدة عندك. **وإن تعذّرت عليك:** اكتب `DB SUITE: NOT RUN (<السبب الحرفي>)` — ولا تؤلّف رقماً، ولا تُسمِّ تشغيلَ ملفٍ ملفاً «حزمة».

### 1.6 لقطة git

```bash
git add . && git commit -m "chore: checkpoint before P3.1"
git log --oneline -1 && git status --porcelain
```
ثم **التزام لكل مرحلة** (A..G في §3) بعنوان `feat(p3.1-<حرف>): ...` — فإن انقطع العمل ضاع أقلّه.

---

## §2 — المواد الدستورية H76–H86 (تُضاف إلى `docs/CONSTITUTION.md` بنصّها)

- **H76 — بوّابة واحدة، وقت الإرسال لا وقت الجدولة.** كل صفّ `origin='automation'` يمرّ في `app/workers/policy_gate.py` **لحظة الإرسال**: يُعاد فحص الموافقة والانسحاب والتكرار والساعات والحالة. جدولةٌ صحيحة يوم الأحد لا تُرسَل يوم الاثنين إن تغيّر العالم.
- **H77 — الصنف يُشتقّ من القالب ويُفرَض بقيد قاعدة.** منتِج الرسالة لا يمرّر `message_class`؛ الكاتب يشتقّه من كتالوج القوالب. والقيد في `0013`: `(origin = 'automation') = (message_class IN ('utility','marketing'))` — فلا آلية تدّعي `service` لتتهرّب من الحدود، ولا ردّ بوت/موظف يدّعي `utility/marketing`.
- **H78 — لا موافقة = لا إرسال، وSTOP يغلب كل موافقة.** الأهلية: آخر صفّ `consents` للنطاق `granted=true` **وغياب** `suppressions` للنطاق **ووجود** تفاعل سابق (رسالة واردة خلال `SEND_POLICY_INTERACTION_WINDOW_D`). الغياب = رفض. والـsuppression لا يُرفَع في هذه الدفعة (إعادة الاشتراك P3.2).
- **H79 — التقطير ذرّي في SQL، والسقف عمودٌ لا ثابت.** فتحة الإرسال على مستوى الرقم تُحجَز بدالة SQL واحدة تحت `FOR UPDATE` على صفّ `number_health`؛ لا عدّاد في الذاكرة ولا في Redis. والسقف اليومي **عمود** تكتبه المكنسة (H81) وتفرضه SQL وحدها — **لا نسخة ثانية في بايثون** (نفس قاعدة عتبات القوة الغاشمة).
- **H80 — الفشل مُغلَق.** أي استثناء في البوّابة أو الحجز (قاعدة، منطقة زمنية خاطئة، صفّ صحة غائب) ⇒ **تأجيل** + مقياس `policy_errors_total` + لا إرسال. عكس H42 (الأمثَلية تفشل مفتوحة)؛ الأمان يفشل مغلقاً (H47).
- **H81 — الإحماء بالأيام، والصحة تُنزل ولا ترفع، والإيقاف بإنسان.** السقف لا يرتفع إلا بمرور أيام الإحماء ولا ينخفض إلا بالصحة؛ حالة `paused` **لا تُرفَع آلياً أبداً** (أمر مشغّل مسجَّل)، و`throttled` تعود آلياً بعد 24 ساعة نظيفة. السقوط سريع والصعود بطيء.
- **H82 — الخدمة لا تُجوَّع.** صفّ الخدمة يُطالَب قبل أي تسويقي دائماً، والتسويقي له سقف مطالبة في الدورة وقناةٌ واحدة لكل صفّ. يُقاس باختبار لا بالادّعاء.
- **H83 — لا نصّ استباقي بلا قالب معتمد، ولا نموذج للمستلِم.** النصّ = قالب معتمد من كتالوج مغلق + حقول دمج من قائمة بيضاء لكل قالب + **تذييل الانسحاب** للتسويقي (ويجب أن تكون الكلمة التي يعِد بها التذييل **ممّا يكتشفه `optout.detect` فعلاً**) + مروره في `verify_rules.check_text` فاشلاً مغلقاً.
- **H84 — الحكم نقيّ وحتميّ ومُسبَّب.** `app/policy/decision.py` دالة نقية بقائمة استيراد مغلقة، بلا ساعة ولا عشوائية ولا قاعدة؛ ساعتها **مُدخَلة**؛ كل حكم برمزٍ من قائمة مغلقة؛ نفس المُدخَل ⇒ نفس الحكم دائماً (حارسها S21).
- **H85 — الحجز متماثل.** إعادة معالجة الصفّ نفسه (انقضاء الإيجار بعد انهيار) **لا تستهلك فتحة ثانية** (`UNIQUE(outbox_id)` في السجلّ)، والفشل قبل التسليم (`429` · `423` · انقطاع) **يُعيد الفتحة**. نحن نخطئ دائماً باتجاه الإرسال الأقل.
- **H86 — لا اعتماد بلا اختبار توصيل حقيقي.** كل دالة تمرّ بحدّ (دور قاعدة · نوع معامل · ترتيب أعمدة · تسلسل · RLS) لها اختبار `db` ينادي **الدالة الحقيقية**. وأعمدة `SELECT *` تُقرأ **بأسمائها**.

وتُعدَّل **H46** بالإلحاق: «كاتبو `outbox` ثلاثة: `verify.py` للبوت، و`routes_inbox.py` بـ`origin="human"` حرفياً، و`proactive.py` بـ`origin="automation"` حرفياً» (S10-a يُعدَّل ولا يُكسَر).

---

## §3 — ما تبنيه (المراحل A–G بالترتيب، التزام لكلٍّ)

**A — الخطوة صفر:** §1 كلّه. **ولا تبدأ B قبل أن يُرى `dispatch_cycle` يرسل صفّاً حقيقياً من كل `origin` على قاعدة حقيقية.**

### B — `0013_p3_send_policy.sql` (idempotent، `IF NOT EXISTS`/كتل `DO` — `ADD CONSTRAINT` لا يقبل `IF NOT EXISTS`)

1. **`number_health` (ALTER):** `warmup_started_at timestamptz` · `next_marketing_at timestamptz NOT NULL DEFAULT now()` · `next_utility_at timestamptz NOT NULL DEFAULT now()` · `utility_daily_cap integer NOT NULL DEFAULT 100 CHECK (>= 0)` · `utility_sent_today integer NOT NULL DEFAULT 0` · `state_reason text` · `state_changed_at timestamptz NOT NULL DEFAULT now()`. (`daily_cap` و`sent_today` و`day` و`state` و`score` موجودة وتُستعمَل كما هي؛ `score` للعرض فقط: `healthy=1.0 · throttled=0.5 · paused=0.0` — **لا صيغة تدّعي دقة لا نملكها**.)
2. **`outbox` (ALTER):** `policy_reason text` (آخر سبب تأجيل/إسقاط — أثر جنائي بلا جدول). وقيدان: **H77** كما في §2، و`CHECK (origin <> 'automation' OR conversation_id IS NOT NULL)` (F-P3-02 يصير **مستحيلاً بنيوياً**: لا حملة بلا محادثة).
3. **`proactive_ledger` (جدول جديد):** `id uuid PK DEFAULT gen_random_uuid()` (**لا `bigserial`** — الصلاحيات على التسلسلات في `0001` مُنحت للموجود وقتها فقط) · `tenant_id` (FK) · `channel_account_id` (FK) · `customer_id` (FK) · `outbox_id uuid NOT NULL REFERENCES outbox(id)` **`UNIQUE`** (H85) · `message_class` ∈ `utility|marketing` · `template_id` · `status` ∈ `reserved|handed_off|released` · `reserved_at timestamptz DEFAULT now()`؛ فهرس `(tenant_id, customer_id, message_class, reserved_at DESC)`. **فخّان:** (أ) حلقة RLS في `0001` مرّت على الجداول الموجودة وقتها فقط ⇒ اكتب **صراحةً** `ENABLE ROW LEVEL SECURITY` + سياسة `tenant_isolation` (`USING`/`WITH CHECK (tenant_id = app.current_tenant())`) + `GRANT SELECT, INSERT, UPDATE ON proactive_ledger TO sharwa_app`، **وأثبته باختبار عزل بمستأجرَين** (لا يرى أحدهما صفوف الآخر ولا يكتب عبر الحدّ)؛ (ب) **أضِف `proactive_ledger` إلى `delete_tenant_full` قبل `outbox` و`customers` و`tenants`** وإلى `seed_tenant_with_rows` وإلى `test_cleanup.py` — **هذا بالضبط ما نسيناه في F-P2-06**.
4. **`app.reserve_send_slot(p_channel uuid, p_class text, p_gap_s integer, p_now timestamptz DEFAULT now())`** — `SECURITY INVOKER` (تعمل داخل `tenant_tx` فتحميها RLS)، تُعيد `(verdict text, defer_until timestamptz, sent_today integer, daily_cap integer)`:
   - `SELECT … FROM number_health WHERE channel_account_id = p_channel FOR UPDATE` — لا صفّ ⇒ `no_health_row` (يُؤجَّل؛ المكنسة تنشئه).
   - **اليوم المحلي للمستأجر:** `(p_now AT TIME ZONE tenants.timezone)::date`؛ إن اختلف عن `day` ⇒ صفّر العدّادين وحدّث `day`. (منطقة غير صالحة ⇒ استثناء SQL ⇒ H80 يؤجّل.)
   - `p_class='marketing'`: `state='paused'` ⇒ `paused`؛ `sent_today >= daily_cap` ⇒ `cap_reached` بـ`defer_until` = منتصف الليل المحلي التالي؛ `next_marketing_at > p_now` ⇒ `spacing` بـ`defer_until = next_marketing_at`؛ وإلا ⇒ `sent_today+1`، `next_marketing_at = p_now + make_interval(secs => p_gap_s)`، و`warmup_started_at = COALESCE(warmup_started_at, p_now)` ⇒ `reserved`.
   - `p_class='utility'`: المنطق نفسه على `utility_sent_today/utility_daily_cap/next_utility_at`، **ولا تمنعه حالة `paused`** (إشعار طلبه العميل نفسه)، وفتحتاه مستقلّتان فلا يُجوَّع الإشعار بحملة.
   - **`p_gap_s` يأتي من بايثون** (بعد سحب الجيتر بمولّد قابل للحقن) — لا `random()` في SQL، فالاختبار حتمي.
5. **`app.release_send_slot(p_channel uuid, p_class text)`** ينقص العدّاد المناسب (لا دون الصفر) ولا يُعيد `next_*_at` (التباعد يبقى — أمان).
6. **`app.policy_sweep_targets()`** `SECURITY DEFINER` تُعيد `(tenant_id, channel_account_id)` للقنوات `engine='ai_core'` من نوع واتساب، و`GRANT EXECUTE` لـ`sharwa_system` (مطالبة عابرة للتينانت على نمط `claim_due_turns`).
7. اختبارات: الترحيل مرّتين متتاليتين ⇒ صفر خطأ (idempotent) · دالة `claim_outbox` واحدة في `pg_proc` · **سباق ذرّي (على نمط اختبار المخزون):** 20 خيطاً على `threading.Barrier` على قناة واحدة، `daily_cap=5`، `gap=0` ⇒ **`reserved` بالضبط 5**؛ وبـ`gap=30` ⇒ **`reserved` واحد** · انتقال اليوم (تجاوز منتصف الليل المحلي بمنطقتين مختلفتين) · `release` يُعيد · عزل التينانت للسجلّ.

### C — `app/policy/` (نقيّ، H84)

`types.py` (frozen dataclasses: `TemplateMeta` · `PolicyInput` · `Verdict(action, reason, defer_until)`) · `decision.py` · `warmup.py` (`cap_for(day_index, ladder)` و`day_index(started_at, now, tz)`) · `health.py` (`classify(signals, cfg) -> (state, reason)`) · `quiet_hours.py` (`next_allowed_at(now_aware, tz, start, end)`).

**قائمة الاستيراد المغلقة (S21):** `__future__ · dataclasses · typing · enum · datetime`(للأنواع والحساب فقط) `· zoneinfo · app.policy.*`. **ممنوع** في المجلد كله: أي نداء `.now()/.utcnow()/.today()/.time()`، `time`، `random`، `app.db`، `psycopg`، `httpx`، `redis`، `app.llm`، `open(`، `os.environ`.

**أكواد الحكم (قائمة مغلقة، تُتحقَّق في `__post_init__` — وكل واحد مقياس بوسم محدود):**

- **إسقاط (`drop`)**: `unknown_template` · `expired` · `kill_switch_off` · `conversation_closed` · `suppressed` · `no_consent` · `no_prior_interaction` · `frequency_cap_skip` (تسويقي فقط).
- **تأجيل (`defer`)**: `channel_not_connected` · `number_paused` (تسويقي) · `human_active` · `active_chat` · `quiet_hours` · `frequency_cap_defer` (مرافق) · `cap_reached` · `spacing` · `no_health_row` · `policy_error` · `invalid_timezone`.
- **إرسال**: `ok`.

**ترتيب التقييم (يُكتب في docstring ويُختبَر كجدول):** كل **الإسقاطات الدائمة** قبل أي تأجيل — فلا يُركَن صفٌّ محكوم بالإسقاط. بالترتيب: `unknown_template` ← `expired` (TTL من `created_at`) ← `kill_switch_off` (النطاق: `marketing` للتسويقي، `back_in_stock` للمرافق؛ `degraded` يُسمَح — القوالب وحدها أصلاً) ← `conversation_closed` ← `suppressed` ← `no_consent` ← `no_prior_interaction` ← `frequency_cap_skip` ثم التأجيلات: `channel_not_connected` ← `number_paused` ← `human_active` (`bot_status='paused_human'`) ← `active_chat` (آخر وارد أحدث من `ACTIVE_CHAT_COOLDOWN_S`) ← `quiet_hours` (إن كان القالب `quiet_hours=True`) ← `frequency_cap_defer` ← `ok`. **STOP يغلب كل شيء:** `suppressed` يسبق `no_consent` فلا يُقرأ تاريخ موافقة بعد انسحاب.

**ملاحظة صدق (تُكتب في docstring `health.py`):** البوابة لا تنتج `delivered/read` (OQ-P1-07)، فمعدّل التسليم الحقيقي **غير مقيس**؛ الصحة هنا تُشتقّ من (فشل التسليم للبوابة · الانسحاب بعد التسويقي · كلمات البلاغ · حالة القناة) **فقط**، ولا تدّعي أكثر. (OQ-P3-01.)

### D — الكاتب الوحيد وكتالوج القوالب (H77/H83)

1. **`app/workers/proactive.py::enqueue_proactive(conn, *, tenant_id, conversation_id, template_id, merge, idempotency_key, rules)`** — **الكاتب الثالث الوحيد** لـ`origin="automation"` (S22). لا معامل `message_class`: يُشتقّ من الكتالوج. يتحقّق: القالب معتمد؛ مفاتيح `merge` ⊆ القائمة البيضاء للقالب (وإلا `ValueError`)؛ يُنتج النصّ؛ يُلحِق **تذييل الانسحاب** إن كان التسويقياً؛ يُمرّره في `verify_rules.check_text` (فاشلاً مغلقاً ⇒ لا كتابة + `verifier_blocks`)؛ ثم `INSERT` واحد بلا شبكة (يعمل داخل معاملة قائمة كمعاملة التخصيص). `idempotency_key` حتمي من المنتِج (`proactive:<kind>:<key>`).
2. **كتالوج القوالب `PROACTIVE_TEMPLATES` في `workers/config.py`** (الجدول مُعفى من S8 كباقي القوائم): لكل قالب `TemplateMeta(template_id, message_class, consent_scope, capability, quiet_hours, footer_required)` مع نصّه ومفاتيح دمجه. **القالب الوحيد المُسجَّل في هذه الدفعة: `stock_available`** (`utility` · نطاق `back_in_stock` · قدرة `back_in_stock` · `quiet_hours=False` — OQ-P3-02: إشعار مرتبط بحجز ينقضي، فالتأجيل لساعات الهدوء قد يُنهي الحجز قبل أن يُقرأ) بحقل دمج وحيد `title`. **لا قالب تسويقي واحد في الإنتاج** — فمهما كُتب صفّ تسويقي ⇒ `unknown_template` ⇒ `drop`: **التسويق موصول ومُظلَم** حتى P3.4. وتحقّقٌ **fail-fast عند الإقلاع** (نمط `verify`): لكل قالب تسويقي مسجَّل يجب أن يكون التذييل غير فارغ وأن **تكتشف `optout.detect` عبارته**. (الاختبار يُسجّل قالباً تسويقياً تجريبياً بحقن الكتالوج.)
3. **نقل إشعار المخزون إلى البوّابة (F-P3-03):** `stock._insert_notice` يستدعي `enqueue_proactive` لا `verify.insert_verified_outbox` (نصّه من الكتالوج بدل `compose_stock_notice`). **ولا تمسّ منطق التخصيص ولا H69** (المعاملة تبقى قصيرة بلا شبكة). وعند انضمام عميل إلى قائمة الانتظار يُكتَب `consents(scope='back_in_stock', granted=true, source='waitlist_join', evidence=<entry_id>)` **في معاملة الانضمام نفسها** — في المنسّق لا في الأداة (H50). **اختبار يفشل على الكود الحالي ويمرّ بعده:** عميل في قائمة الانتظار يرسل «إيقاف» ثم يُخصَّص له حجز ⇒ **صفر رسالة** (`dropped_policy` بسبب `suppressed`).
4. **STOP يُلغي الطابور فوراً:** في معاملة الاستيعاب التي تكتب الـsuppressions (`realtime.py`) يُنفَّذ `repos_outbox.cancel_pending_proactive(conn, tenant_id, customer_id, template_ids)` ⇒ كل صفّ `pending` من `origin='automation'` لقوالب تلك النطاقات ⇒ `dropped_policy` + `policy_reason='suppressed'`. (الصفّ `sending` يلتقطه فحص وقت الإرسال — H76.) تغطية: خريطة `نطاق ← قوالبه` مشتقّة من الكتالوج لا مكتوبة ثانيةً.
5. **اشتقاق نطاق الـsuppression من القالب** لا من `message_class` — يُلغي الفجوة التي في F-P3-03 (نطاقا `back_in_stock` و`review_request` يصيران مقروءَين).

### E — تكامل الـdispatcher (`app/workers/policy_gate.py` + `dispatch.py` + `repos_policy.py`)

لكل صفّ `origin='automation'` بعد المطالبة، **بهذا التسلسل، كل مرحلة في معاملتها وبلا شبكة داخل أي معاملة (H40):**

1. **معاملة 1 (`tenant_tx`):** قفل استشاري لكل عميل (`pg_advisory_xact_lock(hashtextextended(customer_id::text, 0))`) ⇒ لقطة `PolicyInput` (آخر موافقة للنطاق · الـsuppression · آخر وارد · عدّاد السجلّ 24س/7أيام وآخر تسويقي · حالة المحادثة · حالة القناة · `effective_switch` للقدرة · منطقة المستأجر) ⇒ `decision.decide(...)`.
2. **إسقاط** ⇒ `dropped_policy` + `policy_reason`. **تأجيل** ⇒ `repos_outbox.requeue_deferred(outbox_id, until, reason)`: `status='pending'`، `next_attempt_at=until`، **`attempts = GREATEST(attempts-1, 0)`** (F-P3-08: التأجيل ليس فشلاً)، `locked_until=NULL`. 
3. **`ok`** ⇒ في **المعاملة نفسها** `reserve_send_slot` (بجيتر قابل للحقن، وضِعف التباعد عند `throttled`) ⇒ إن لم `reserved` ⇒ تأجيل بسببه ⇒ إن `reserved` ⇒ `INSERT` في `proactive_ledger` (`status='reserved'`، `ON CONFLICT (outbox_id) DO NOTHING` — H85: **إن وُجد سجلّ لهذا الصفّ فلا فتحة ثانية**) ⇒ commit.
4. **بعد الالتزام**: `client.send(...)` بـ`kind`: `marketing` للتسويقي، **`bulk` للمرافق الآلي** (لا `interactive`: تمهّل 0.8–1.5 ث لا يليق بإرسال استباقي).
5. **معاملة 2:** `202` ⇒ `sent` + السجلّ `handed_off`. وأي نتيجة أخرى (`423` · `429` · `GatewayUnavailable` · فشل) ⇒ **`release_send_slot`** + السجلّ `released` + المسار القائم (إعادة بتراجع/فشل).
6. **H80:** أي استثناء من (1)–(3) ⇒ `policy_error` تأجيل + `policy_errors_total{stage}` + **لا إرسال**.

والصفوف غير الآلية **لا تتغيّر سلوكياً** (تبقى فحوص الحقبة والمفتاح كما هي)، إلا أن فحص الـsuppression يُجرى **دون شرط `conversation_id`** (يُغلق F-P3-02 للأبد مع قيد `0013`).

**S23:** في `dispatch.py` أي دالة تُنادي `_send_one` تُنادي أيضاً `policy_gate` (وجود لفظي) — **وسقف الفحص مُعلَن: التعرّف بالاسم، فالتهريب بإسناد الدالة إلى متغيّر يمرّ ويظهر في المراجعة** (لا يُقال «يستحيل»).

### F — المكنسة (إحماء + صحة) وCLI

1. **خيط جديد في `realtime.run`** بجانب الـdispatcher: كل `SEND_POLICY_SWEEP_INTERVAL_S` ثانية: `policy_sweep_targets()` ثم لكل قناة في `tenant_tx`: (أ) أنشئ صفّ `number_health` إن غاب (`ON CONFLICT DO NOTHING`)؛ (ب) **إعادة تصفير الإحماء بعد خمول** `SEND_POLICY_IDLE_RESET_D` يوماً بلا تسليم تسويقي: `warmup_started_at = NULL`؛ (ج) `daily_cap = cap_for(day_index, ladder) × (throttle_factor إن throttled)`؛ (د) احسب الإشارات (§4) ⇒ `health.classify` ⇒ طبّق الانتقال وسجّل `state_reason`/`state_changed_at` ⇒ `policy_state_transitions_total{to}`؛ (هـ) `score` للعرض. **الانتقالات:** `paused` لا تخرج منها المكنسة أبداً (H81). والحالة الصلبة الفورية: `channel_accounts.status ∈ {banned, logged_out, conflict}` ⇒ `paused` في الدورة نفسها، و`reserve_send_slot` نفسها تتحقّق من أن القناة `connected` (H80).
2. **الإشارات (نافذة 24 ساعة، حدّ أدنى لحجم العيّنة مكتوب):** `sends` = تسويقي `sent` · `failures` = تسويقي `failed` · `optouts` = suppressions كُتبت خلال 24 ساعة **بعد** سجلّ `handed_off` تسويقي لنفس العميل · `complaints` = عملاء كتبوا رمزاً (مساواة رمز لا احتواء — نمط H49) من `SEND_POLICY_COMPLAINT_WORDS` المطبَّعة خلال 24 ساعة بعد تسويقي. **لا يظهر نصّ أو هاتف في سجلّ أو وسم (H20/H48).**
3. **CLI:** `python -m app.cli policy status [--channel <id>]` (يطبع الحالة والسقف والعدّادات وسبب الحالة بلا هواتف) و`python -m app.cli policy reinstate --channel <id> --reason "<نص>"` (الطريق **الوحيد** للخروج من `paused`: يضع `healthy`، يصفّر `warmup_started_at` — يبدأ الإحماء من أول درجة — ويكتب `state_reason='reinstated: …'` ويسجّل `kill_switch`-style audit في السجلّ). مع اختبار.
4. **المقاييس (أوسمة مغلقة فقط، لا tenant ولا customer ولا channel ولا هاتف):** `policy_verdicts_total{class,action,reason}` · `policy_reserve_total{class,verdict}` · `policy_errors_total{stage}` · `policy_state_transitions_total{to}` · `number_health_state{state}` (gauge: عدد القنوات) · `policy_sweeper_last_success_timestamp_seconds` · `outbox_depth{class}` و`outbox_oldest_pending_seconds{class}` (من `app.outbox_stats()`).
5. **تنبيهات (`ops/prometheus/alerts.yml`، وS4 تتحقّق أنها تشير لمقاييس موجودة):** `NumberPaused` (critical: `increase(policy_state_transitions_total{to="paused"}[10m]) > 0`) · `PolicySweeperStale` (critical: `time() - policy_sweeper_last_success_timestamp_seconds > 300` لـ5د) · **`ServiceOutboxStalled`** (critical: `outbox_oldest_pending_seconds{class="service"} > 60` لـ2د — **اتفاقية الخدمة التي يحرسها H82**) · `PolicyFailClosed` (warning: `rate(policy_errors_total[5m]) > 0` لـ10د) · `MarketingTTLDrops` (warning: ارتفاع `policy_verdicts_total{reason="expired"}`).
6. **الإعداد:** كل مفتاح في §4 في `workers/config.py` بقيمة افتراضية **وتحقّق fail-fast** (سلّم الإحماء تصاعدي وأعلاه ≤ `DAILY_CEILING`؛ `GAP_MIN ≤ GAP_MAX` و`GAP_MIN ≥ 10` للتسويقي؛ كل السقوف ≥ 1؛ ساعات الهدوء تُحلَّل؛ `tzdata` في `requirements.txt` **مُثبَّتاً بإصدار** — `zoneinfo` بلا قاعدة بيانات المناطق تفشل على صورة slim) و`.env.example` و`docker-compose.yml` و`scripts/check_env.py`.

### G — اختبار «التقطير» الشامل

`@pytest.mark.db` على قاعدة حقيقية بلا `sleep`: 60 عميلاً مُوافقاً ومُتفاعلاً على قناة واحدة، 60 صفاً تسويقياً (قالب تجريبي بحقن الكتالوج)، `daily_cap=10`، `gap=30ث`، ساعة مُحقَنة تتقدّم. **المطلوب:** المُسلَّم ≤ 10 · الفاصل بين كل تسليمين متتاليين ≥ 30ث · الباقي **مؤجَّل لا مُسقَط ولا فاشل** · بعد تجاوز منتصف الليل المحلي يتدفّق 10 أخرى · **إعادة تشغيل الـdispatcher** (نسخة جديدة بلا ذاكرة) في المنتصف ⇒ **لا فتحة زائدة** · 20 خيط dispatcher متزامنة ⇒ السقف لا يُخترَق · وتجربة **الإنصاف:** 2000 صفّ تسويقي ثم 20 صفّ خدمة بعدها ⇒ **كل صفوف الخدمة تُطالَب في أول دورة** وتسويقي ≤ `p_marketing_limit` وصفّ لكل قناة.

---

## §4 — الأرقام الافتراضية (تُكتَب في `config.py` وتُضبَط بالبيئة؛ **مقترَحة بانتظار اعتماد المالك — OQ-P3-03**)

| المفتاح | الافتراضي | المعنى |
|---|---|---|
| `SEND_POLICY_WARMUP_LADDER` | `0:20,3:40,7:80,14:150,30:250` | (يوم الإحماء : السقف التسويقي اليومي للرقم) — اليوم 0 = أول تسويقي يُرسَل |
| `SEND_POLICY_DAILY_CEILING` | `250` | سقف مطلق، `≤ MARKETING_SENDER_DAILY_CAP` في البوابة |
| `SEND_POLICY_UTILITY_DAILY_CAP` | `100` | سقف المرافق اليومي للرقم (فتحة مستقلّة) |
| `SEND_POLICY_MARKETING_GAP_S` | `20,60` | جيتر التباعد بين تسويقيَّين من رقم واحد |
| `SEND_POLICY_UTILITY_GAP_S` | `8,20` | التباعد للمرافق |
| `SEND_POLICY_THROTTLE_CAP_FACTOR` / `GAP_FACTOR` | `0.5` / `2.0` | عند `throttled` |
| `SEND_POLICY_MARKETING_PER_24H` / `PER_7D` | `1` / `2` | سقف التسويقي لكل عميل |
| `SEND_POLICY_UTILITY_PER_24H` / `UTILITY_MIN_GAP_S` | `3` / `60` | سقف المرافق لكل عميل |
| `SEND_POLICY_ACTIVE_CHAT_COOLDOWN_S` | `1800` | لا استباقي بعد آخر وارد بأقل من نصف ساعة |
| `SEND_POLICY_QUIET_START` / `QUIET_END` | `22:00` / `09:00` | بتوقيت المستأجر (`tenants.timezone`) |
| `SEND_POLICY_MARKETING_TTL_H` / `UTILITY_TTL_H` | `24` / `6` | بعدها `expired` ⇒ إسقاط (ترويج بعد يومين ضرر) |
| `SEND_POLICY_INTERACTION_WINDOW_D` | `180` | «تفاعل سابق» = وارد خلال 180 يوماً |
| `SEND_POLICY_MARKETING_CLAIM_PER_CYCLE` | `4` | `p_marketing_limit` |
| `SEND_POLICY_SWEEP_INTERVAL_S` / `IDLE_RESET_D` | `60` / `14` | |
| **عتبات الصحة (نافذة 24س)** | | |
| `THROTTLE` عند | انسحاب ≥ 2٪ **أو** فشل ≥ 10٪ **أو** بلاغ ≥ 1 | عيّنة ≥ 30 تسويقياً للانسحاب، ≥ 20 للفشل |
| `PAUSE` عند | انسحاب ≥ 5٪ **أو** فشل ≥ 30٪ **أو** بلاغ ≥ 3 **أو** حالة قناة `banned/logged_out/conflict` | الحالة الأخيرة بلا حدّ عيّنة |
| عودة `throttled` | بعد 24 ساعة بلا شرط تثبيط | `paused`: **بإنسان فقط** |
| `SEND_POLICY_COMPLAINT_WORDS` | `سبام،ازعاج،إزعاج،بلاغ،ابلاغ،report،spam` | مساواة رمز على النصّ المطبَّع |
| `MARKETING_FOOTER_AR` | `لإيقاف الرسائل الترويجية أرسل: إيقاف` | يجب أن يكتشف `optout.detect` كلمة «إيقاف» |

---

## §5 — الاختبارات الإلزامية (إضافة إلى ما سبق)

**نقية:** جدول `decide` الشامل (كل رمز حكم ≥ مرّة، وترتيب التقييم، وأن `suppressed` يغلب `no_consent`) · خصائص: `next_allowed_at` خارج نافذة الهدوء دائماً (عبر منتصف الليل ومنطقتين زمنيتين) · `cap_for` رتيب · `classify` بجدول عتبات (حدّ العيّنة: انسحاب 1 من 3 لا يوقف) · `paused` لا تخرج منها `classify` · **اختبار التذييل:** كل عبارة انسحاب في `MARKETING_FOOTER_AR` يكتشفها `optout.detect` فعلاً · `enqueue_proactive` يرفض مفتاح دمج خارج القائمة ويفشل مغلقاً عند حظر المدقّق · ثبات الحتمية: مئة استدعاء بالمُدخَل نفسه ⇒ الحكم نفسه.

**`db`:** اختبارات الترحيل والسباق (§3.B) · `test_dispatch_db.py` (§1.2) · STOP-ثم-حجز المخزون (§3.D.3) · STOP يُلغي الطابور (§3.D.4) · قيد `0013`: كتابة `automation` + `service` تُرفَض، و`bot` + `marketing` تُرفَض، و`automation` بلا محادثة تُرفَض · `claim_outbox`: الخدمة قبل التسويقي، صفّ تسويقي واحد لكل قناة، `attempts` مُسترَدّ عند التأجيل · **فشل مُغلَق:** حقن استثناء في `reserve_send_slot` ⇒ الصفّ مؤجَّل لا مُرسَل · قناة `disconnected` ⇒ لا إرسال · **حجز متماثل:** مطالبة ثم محاكاة انهيار قبل الإرسال ثم إعادة مطالبة ⇒ فتحة واحدة وسجلّ واحد (H85) · المكنسة: سلّم الإحماء بالأيام، انتقالات الصحة بجدول، `paused` لا تُرفَع، `reinstate` تعيد البداية الباردة · اختبار G.

**Node:** سقف المُرسِل (§1.4).

---

## §6 — البوّابة الساكنة

- **S21:** نقاء `app/policy/**` (القائمة المغلقة والمحظورات في §3.C) — **أثبتها بإفشال متعمَّد**: أدخل `datetime.now()` ثم `import random` ثم `import psycopg` ⇒ ثلاث مخالفات، ثم صفر.
- **S22:** (أ) `insert_outbox(...)` بـ`origin="automation"` حرفياً **لا يُنادى إلا من `proactive.py`**؛ (ب) `proactive.py` لا يقبل معامل `message_class` ولا يمرّره؛ (ج) S10-a مُعدَّل لا مكسور؛ (د) لا نداء لـ`GatewayClient.send` خارج `dispatch.py`. أثبتها بإفشال متعمَّد.
- **S23:** تكامل الـdispatcher مع `policy_gate` (وسقف الفحص المُعلَن).
- **S20 المُصلَحة (F-P2-10)** و**S15** (مفاتيح الإعداد الجديدة في compose/.env.example/check_env).
- **كل البوابة صفر مخالفة آخر أمر، بزمنها.**

---

## §7 — التسليم

**ما يُرفَق حرفياً (أمر + زمن + مخرج، وإلا لا يُقبَل):**

1. `git log --oneline` للالتزامات (واحد لكل مرحلة) و`git status --porcelain` فارغاً.
2. **مرحلة A:** مخرج `seed_gazetteer.py` مرّتين (37 ثم 0) على قاعدة حقيقية؛ مصفوفة الذهبية؛ وإثبات العطلين الثلاثة **قبل** و**بعد** (الاختبار الذي كان سيكشفها يفشل على الكود القديم — **أرِني ذلك**: أعِد القديم مؤقتاً على نسخة ثم أعِد الجديد).
3. `pytest tests -q` النقية: صفر أحمر والعدد **> 273**.
4. **الحزمة الموسومة كاملةً مرّتين متتاليتين** (`scripts/run_db_suite.py`) والسطران الأخيران، متساويان، **وصفر أخطاء، بما فيها `test_migrate`**. أو `DB SUITE: NOT RUN (<السبب>)` وسأشغّلها أنا.
5. مخرج اختبار G (مصفوفة: مُسلَّم · أقلّ فاصل · مؤجَّل · مُسقَط · فاشل) واختبار الإنصاف.
6. إثبات S21 وS22 وS23 بإفشال متعمَّد (مخالفات ثم صفر).
7. مخرج `policy status` لقناة ومخرج `reinstate`.
8. اختبار Node لسقف المُرسِل.
9. `0012` و`0013` مُطبَّقتان مرّتين بلا خطأ.
10. **جُمَل صريحة بالأفعال:** هل بقي مسار إرسال استباقي لا يمرّ بالبوّابة؟ · هل STOP يوقف إشعار المخزون الآن (بالاختبار)؟ · هل لمستَ شيئاً ممّا في §8؟ · **ما الذي لم تستطع قياسه؟** (اكتبه ولا تُجمِّله.)
11. **ممنوع في التقرير:** عبارة «محمي من الحظر» أو أي ضمان. اكتب ما قاسه اختبارك.

**سطر الإقفال:** `P3.1 COMPLETE — PROACTIVE SENDS GATED, DRIP ENFORCED. STOPPING. AWAITING AUDIT. NO P3.2 WORK STARTED.`
وقبله: `F-P2-07 fixed · F-P1-12 fixed (dispatch_cycle sends a real row of every origin) · send policy: <n> reason codes, <m> decision-table cases · drip test: <delivered>/<cap> with min gap <s> · db suite: <runs or NOT RUN> · migrations: 0012+0013 · gate 0 violations at <ZULU>`

---

## §8 — ما لا تلمسه، وما لا تبنيه

**لا تلمس:** `app.allocate_stock_holds` و`app.expire_stock_holds` وأي منطق تخصيص · `0001`–`0011` · `app/geo/resolve.py` و`app/geo/normalize.py` و`app/text/arabic.py` (F-P2-08 ليست من هذه الدفعة) · `verify_rules.py` (تستدعيه ولا تعدّله) · `docs/PHASE_GATE.md`.

**لا تبنِ:** حملات (`campaigns`/`campaign_recipients` تبقى بلا كود) · سلال متروكة · `scheduled_jobs` وتشغيلها · طلبات مراجعة · التقاط الموافقة (`checkout_optin` · كلمة إعادة الاشتراك) · قوالب تسويقية · Cloud API · واجهة · نموذج لغوي · أي تعديل في الغيتواي غير §1.4. **وأي مولّد يكتب في `outbox` خارج `proactive.py` مخالفة S22.**

---

## §9 — أسئلة مفتوحة

- **OQ-P3-01:** غياب `delivered/read` من البوابة يترك الصحة بلا معدّل تسليم حقيقي. أن تُصدِر البوابة إيصالات التسليم قرار يُتّخذ في P3.x أو P5.
- **OQ-P3-02:** نوافذ الهدوء: هل تُستثنى الجمعة/أوقات الصلاة/رمضان؟ وهل يُستثنى `stock_available` (مفترض في هذه الدفعة: نعم، لارتباطه بحجز ينقضي)؟ قرار مالك.
- **OQ-P3-03:** **كل رقم في §4 مقترَح لا معتمد.** أرقام الإحماء والسقوف والعتبات يقرّها المالك بعد قراءتها؛ تُضبَط بالبيئة بلا نشر. (وأقول بصراحة: هي محافظة عمداً لرقم Baileys؛ تراخيها يزيد خطر الحظر.)
- **OQ-P3-04:** مصدر الموافقة التسويقية: `checkout_optin` يتطلّب من منصة `sharwa_saas` تمرير علم الموافقة (متطلّب C من §11)، والعملاء القائمون بلا موافقة لا يُراسَلون تسويقياً حتى `import` بدليل موثَّق (مسؤولية التاجر القانونية — قرار مالك).
- **OQ-P3-05:** حين يدخل Cloud API (P5) تُعاير هذه الأصناف على قوالب مُعتمَدة ونافذة الـ24 ساعة وسلّم الجودة الرسمي؛ أرقام Baileys هنا **أدنى بكثير** من أي حدّ رسمي عمداً.

---

**ابدأ بـ§1. ولا تكتب سطراً في `app/policy/` قبل أن يُرى `dispatch_cycle` يرسل صفّاً حقيقياً من كل `origin` على قاعدة حقيقية — لأن بوّابةً أمام مُرسِلٍ ميّتٍ لا تُختبَر.**
