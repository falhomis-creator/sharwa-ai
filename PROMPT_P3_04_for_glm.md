# PROMPT P3.4 — رفع الإظلام تحت ضبط: قالب `cart_reminder` + تفعيل لكل مستأجر (افتراضيه مُطفأ) + كاناري — إلى GLM

المصادر الملزِمة: `docs/CONSTITUTION.md` (H76–H99 + **H100–H103 الجديدة**)، `docs/P3_03_FINAL_AUDIT.md` (الجولة الثانية)، `docs/PHASE_GATE.md`.

## §0 — المبدأ الحاكم (اقرأه قبل أي سطر)
هذه أخطر دفعة في P3: أول مرّة يستطيع النظام فيها إرسال **رسالة تسويقية على رقم واتساب حقيقي**، وقد يُحظَر الرقم. لذلك **دفعتك تبني القدرة ولا تستعملها**:
1. **أنت لا تُفعِّل أي مستأجر حقيقي ولا تُرسل أي رسالة حيّة، ولا في الاختبارات.** نهاية P3.4 = كل شيء مبنيّ ومُختبَر و**مُطفأ افتراضياً**. أول إرسال حيّ (كاناري) خطوة يؤدّيها المالك بنفسه وفق `docs/MARKETING_RUNBOOK.md` بعد تدقيقي.
2. **الافتراضي دائماً: مُطفأ.** مستأجر بلا صفّ تفعيل = لا تسويق. الترحيلات والبذور والاختبارات والـfixtures لا تُفعِّل شيئاً إلا داخل قاعدة اختبار معزولة.
3. **كل ما تبنيه قابل للتراجع في ثوانٍ وبثلاثة مستويات** (H101).
4. **ممنوع اختراع نصّ مواجه للعميل.** النصّ والتذييل بين الأقواس أدناه «مقترح بانتظار موافقة المالك» (OQ-P3-15)؛ تُسجِّله كما هو، وتُوسَم الدفعة في تقريرك بأن النصّ لم يُعتمَد بعد.

## §1 — الخطوة صفر (التزام مستقل `fix(p3.3): F-P3-31 …`)
`test_p3_consent_e2e_db::test_matrix_explicit_optin_sends_then_stop_suppresses_then_reoptin_sends`: انظر `P3_03_FINAL_AUDIT.md §11`. وكّد `frequency_cap_skip` لإعادة الاشتراك الفورية، ثم أَعتِق الدفتر بما يتجاوز 24 ساعة بمساعد `testsupport` (بلا `sleep`، ولا `now()` بل `clock_timestamp()` حيث يلزم)، ثم وكّد `send is True` والدفتر `reserved` وصفّ outbox `pending`. **لا تغيير في كود المنتج.** قبول: `run_db_suite` ⇒ **0 فشل**، مرّتان متساويتان. **لا تتقدّم للمرحلة A قبل ذلك بناتج حرفي.**

## §2 — المراحل (التزام لكل مرحلة، بوّابتك ونقيّك بعد كل واحدة)

### A — جدول التفعيل ومفتاحه (ترحيل `0017_p3_marketing_activation.sql`، additive وidempotent)
- جدول `marketing_activation(tenant_id uuid PK REFERENCES tenants, enabled boolean NOT NULL DEFAULT false, canary_cap_per_day integer NOT NULL DEFAULT 5 CHECK (canary_cap_per_day BETWEEN 1 AND 500), enabled_by text, enabled_at timestamptz, disabled_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now())` تحت **RLS** كبقيّة جداول المستأجر، و`sharwa_app` يقرأ ويكتب فقط عبر المسار المسموح (أدناه).
- جدول إلحاقي `marketing_activation_log(id, tenant_id, action CHECK IN ('enable','disable','cap_change'), actor text NOT NULL, reason text, created_at default clock_timestamp())` بـ`REVOKE UPDATE, DELETE` من `sharwa_app` (كما `consents`، H94).
- **كاتب واحد:** `app/db/repos_marketing.py` — كل `INSERT/UPDATE` على الجدولين يمرّ منه وحده، ويكتب سطر السجلّ **في المعاملة نفسها**. **S29** (بوّابة ساكنة، بإفشال متعمَّد وناتجه حرفياً): أي كتابة على `marketing_activation*` خارج `repos_marketing.py` ⇒ مخالفة؛ ولا يُنادى `set_enabled(True…)` إلا من `app/cli.py` (مستهلك وحيد) وملفات الاختبار.
- أي تسلسل يُمرَّر لـ`%s` ⇒ `list(...)` (S28)، وأي `RETURNS TABLE` ⇒ `#variable_conflict use_column`.

### B — حكم السياسة: بابان جديدان فقط (يُسمح هنا فقط بلمس `app/policy/**` وبشكل إضافي)
1. مُدخَل نقيّ جديد `marketing_enabled: bool` (وللكاناري `marketing_sent_24h: int` و`canary_cap: int`) في `decision.decide`، تقرؤها `policy_gate` من القاعدة قبل الحكم (غياب الصفّ ⇒ `False`).
2. سبب إسقاط جديد في `DROP_REASONS`: **`marketing_not_enabled`** — يُفحَص **للتسويقي فقط** مباشرةً بعد `kill_switch_off` وقبل `suppressed`/`no_consent`. وسبب تأجيل جديد في `DEFER_REASONS`: **`canary_cap_reached`** (عدد الصفوف التسويقية المحجوزة/المُرسَلة للمستأجر في آخر 24س ≥ `canary_cap_per_day`) — يُفحَص بعد سقف التكرار الفردي، ويُحتسَب من **الدفتر** لا من ذاكرة. الخدمة والـutility **لا يتأثّران إطلاقاً** (اختبار صريح).
3. **كل ما عداهما كما هو حرفياً**: ترتيب الحكم، `frequency_cap_skip`، الساعات الهادئة، الفشل المُغلَق (H80)، النقاء (H84/S21). جدول الحكم الموثَّق يُحدَّث والاختبارات الجدولية تُوسَّع لتغطّي الخلية الجديدة × كل صنف.

### C — تسجيل قالب `cart_reminder` (التسويق يخرج من الإظلام هنا، ولا يُرسَل بدون A+B)
- في `core/app/workers/config.py::PROACTIVE_TEMPLATES`: `TemplateMeta(template_id="cart_reminder", message_class="marketing", consent_scope="marketing", capability="marketing", quiet_hours=True, footer_required=True)`، مفاتيح الدمج المسموحة **فقط**: `items_phrase`. النصّ **مقترح — بانتظار موافقة المالك OQ-P3-15**:
  `مرحباً، تركتَ في سلّتك {items_phrase}. إذا أحببتَ إكمال طلبك فأخبرنا هنا.`
- `MARKETING_FOOTER_AR` (مقترح — بانتظار OQ-P3-15، ومراجعة قانونية يقرّرها المالك OQ-P3-18): `لإيقاف الرسائل الترويجية أرسل: إيقاف`. الإقلاع يفشل مُغلَقاً بتذييل فارغ (القاعدة القائمة)، وتُضيف اختباراً أنه يفشل.
- `items_phrase` تبنيه **دالة نقيّة حتمية** (OQ-P3-08 المعتمد): عنوان أول منتج مقصوص بسقف مكتوب (بين «»)، وإن زاد العدد فـ«و{n} …» بصيغة عدد عربية صحيحة: 1 «منتج آخر» · 2 «منتجان آخران» · 3–10 «منتجات أخرى» · 11+ «منتجاً آخر». لا سعر ولا اسم عميل ولا عنوان. العنوان يمرّ على `verify_rules.check_text` (H83)؛ عنوان مخالف ⇒ لا إرسال (الفشل مُغلَق) والمهمة تُلغى بسبب مسجَّل.
- `cart_reminder.py` (معالج المهمة) يعمل الآن عبر المسار القائم؛ لا تُنشئ مساراً ثانياً. **اختبار الإظلام القديم يُستبدَل** (لا يُحذَف بصمت): بدلاً من «لا قالب تسويقي» يصير «لا قالب تسويقي يُرسَل بلا `marketing_enabled` ولا موافقة»، وتُبقي اختباراً ينصّ أن `PROACTIVE_TEMPLATES` يحوي بالضبط `{stock_available, stock_hold_expired, cart_reminder}`.

### D — أمر المشغّل (`app/cli.py`) — **الطريق الوحيد للتفعيل (H100)**
`python -m app.cli marketing …`:
- `status --tenant <id>`: حالة التفعيل، السقف، عدد المُرسَل في 24س، حالة الرقم (`number_health`)، يوم الإحماء، عدد المشتركين المؤهَّلين (**عدد فقط، لا أرقام ولا نصوص**، H20).
- `preview --tenant <id>`: **جفاف** — يعرض أعداداً: كم سلّة مؤهَّلة، كم ستُسقَط ولماذا (بأسباب الحكم المغلقة)، ونصّ القالب مُصيَّراً ببيانات اصطناعية. لا يكتب شيئاً ولا يرسل.
- `enable --tenant <id> --cap N --actor <name> --reason "<…>" --confirm "ENABLE-MARKETING <tenant-id>"`: يرفض إن لم تتطابق عبارة التأكيد حرفياً، ويرفض (بسبب مكتوب) إن فشل أي فحص تمهيدي: القناة غير متّصلة · `number_health.state` ليست سليمة (paused/throttled) · يوم الإحماء دون عتبة مكتوبة في الإعداد · `MARKETING_FOOTER_AR` فارغ · مفتاح `marketing` العام مُطفأ · صفر مشتركين مؤهَّلين. يكتب صفّ `enable` في السجلّ.
- `disable --tenant <id> --actor <name> --reason "<…>"`: **يُطفئ فوراً وفي المعاملة نفسها يُلغي كل صفوف outbox التسويقية `pending/reserved` للمستأجر وتُعاد فتحاتها (H85) ويُلغي مهام `cart_reminder` المعلّقة** بسبب `marketing_disabled`. لا يمسّ utility/service.
- `set-cap --tenant <id> --cap N --actor … --reason …`.
- لا واجهة API ولا ويبهوك ولا لوحة تُفعِّل (P4+). لا متغيّر بيئة يُفعِّل.

### E — الرصد والتراجع
- مقاييس بلا هاتف ولا نصّ ولا معرّف عميل: `marketing_sent_total{tenant_bucket}` (لا tenant_id خام إن خالف القاعدة القائمة — اتبع H20 كما تفعل بقيّة المقاييس)، `marketing_dropped_total{reason}`، `marketing_canary_deferred_total`، `marketing_optout_after_send_total` (STOP خلال 24س من إرسال تسويقي).
- قاعدة تنبيه واحدة على الأقل: نسبة `optout_after_send / sent` فوق عتبة مكتوبة في الإعداد (مقترح 5% بحدّ أدنى 20 إرسالاً). **التنبيه يُنبّه ولا يُطفئ آلياً** — الإطفاء قرار إنسان (روح H81)، والرقم نفسه يحميه `number_health` القائم.
- `docs/MARKETING_RUNBOOK.md` (إنجليزي/عربي مختصر): فحوصات ما قبل التفعيل، أمر الكاناري، **ثلاثة مستويات تراجع** (١) المفتاح العام `kill_switches.marketing=off` — يوقف كل المستأجرين فوراً، وهو الأسرع؛ (٢) `marketing disable --tenant` — لمستأجر واحد مع إلغاء الطابور؛ (٣) حذف `cart_reminder` من `PROACTIVE_TEMPLATES` ونشر — القطع النهائي)، وماذا يُراقَب في الساعة الأولى، ومتى يُوقَف (أي حظر/تقييد من واتساب، أي شكوى، نسبة STOP فوق العتبة).

### F — اختبارات إلزامية (db حيث يلزم، بلا sleep، بلا شبكة، بقناة وهمية فقط)
(أ) نقي: جدول الحكم الموسَّع (التسويقي × {enabled, not} × {قبل/بعد السقف} + utility/service لا يتأثّران). (ب) db: افتراضي غياب الصفّ ⇒ `dropped_policy/marketing_not_enabled`؛ مفعَّل+موافقة صريحة ⇒ `send` و`reserved`؛ مفعَّل بلا موافقة ⇒ `no_consent`؛ المفتاح العام مُطفأ ⇒ `kill_switch_off` (الأولوية قبل التفعيل)؛ سقف الكاناري: N+1 ⇒ `canary_cap_reached` مؤجَّل لا مُسقَط؛ STOP بعد إرسال ⇒ يُسقَط. (ج) `disable` يُلغي الطابور ويُعيد الفتحات ولا يمسّ utility (يُثبَت بصفوف حقيقية). (د) CLI: `enable` بلا عبارة تأكيد مطابقة ⇒ يرفض ولا يكتب؛ فشل كل فحص تمهيدي على حدة ⇒ يرفض؛ `preview` لا يكتب (يُثبَت بعدّ الصفوف قبلها وبعدها)؛ لا خروج يحوي هاتفاً أو نصّ عميل. (هـ) السجلّ الإلحاقي: `UPDATE/DELETE` على `marketing_activation_log` من `sharwa_app` ⇒ `InsufficientPrivilege`. (و) E2E بالمسار الحقيقي: ويبهوك سلّة ⇒ مهمة ⇒ المحرك ⇒ البوّابة ⇒ outbox ⇒ `dispatch_cycle` بقناة وهمية: العميل المشترك + مستأجر مفعَّل ⇒ **رسالة واحدة** بالتذييل، وكل مسار آخر ⇒ صفر. (ز) `run_db_suite` أخضر **مرّتين متساويتين**.

## §3 — حدود صارمة
- **ممنوع:** تفعيل أي مستأجر حقيقي أو لمس قاعدة إنتاج أو رقم حقيقي · أي إرسال حيّ · لمس `gateway/` · لمس `claim_*` · إضافة قالب تسويقي ثانٍ · فتح `checkout_optin`/`import` للتسويق (OQ-P3-14 باقٍ محجوباً) · أزرار تفاعلية · لمس `PHASE_GATE`/`CONSTITUTION` · أي لمس لـ`app/policy/**` خارج البابَين في §B · نصّ مواجه للعميل غير ما في §C.
- كل SQL جديد: تسلسل ⇒ `list(...)`، `RETURNS TABLE` ⇒ `#variable_conflict use_column`، `UNION … ORDER BY` ⇒ استعلام فرعي، لا `row_factory` في `execute`، `clock_timestamp()` للترتيب داخل المعاملة.
- لا بند «fixed/verified» بلا ناتج قاعدة (الأمر + UTC + السطر الأخير)؛ وإلا `UNVERIFIED (no db)` وأشغّله أنا.

## §4 — شروط التسليم
البوّابة الساكنة **آخر أمر** بآخر سطر حرفي و`rc=0` (S29 مثبَتة بإفشال متعمَّد) · `pytest tests -q` نقي · `git status --porcelain` فارغ · التزام لكل مرحلة (+ التزام الخطوة صفر). التقرير يفصل VERIFIED عن `UNVERIFIED (no db)` ويذكر صراحةً: «لم يُفعَّل أي مستأجر، ولم يُرسَل شيء حيّ، والنصّ/التذييل بانتظار اعتماد المالك».

**سطر الإغلاق:** `P3.4 COMPLETE — MARKETING WIRED, DEFAULT OFF, NO TENANT ENABLED, NO LIVE SEND. STOPPING. AWAITING AUDIT.` (أو مع `— UNVERIFIED (no db)`).

## §5 — أسئلة مفتوحة للمالك (لا تنتظرها؛ ابنِ على المقترحات)
OQ-P3-15 نصّ `cart_reminder` والتذييل · OQ-P3-16 مستأجر الكاناري وسقفه وساعاته · OQ-P3-17 التفعيل عبر CLI فقط (مقترح المعماري: نعم) · OQ-P3-18 مراجعة قانونية/سياسة واتساب قبل أول إرسال (مسؤولية المالك) · OQ-P3-14 `checkout_optin`/`import` يبقيان محجوبَين.
