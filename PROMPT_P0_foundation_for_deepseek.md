# PROMPT التنفيذي — sharwa_ai — المرحلة P0 حصراً: «الأساس والشبكة الآمنة»

> **المُوجَّه إليه:** DeepSeek (المهندس المنفّذ).
> **المُدقِّق:** Claude (المعماري) ثم المالك (فارس). لا تنتقل لأي شيء خارج P0 دون اعتماد مكتوب.
> **الإصدار المعتمد للمعمارية:** v1.1 (2026-09-20). **هذا الملف يحكم عملك من الآن حتى تسلّم تقرير P0 وتتوقف.**

---

## 0. دورك وقواعد الاشتباك

أنت مهندس تنفيذ صارم. لا تُصمّم معمارية جديدة ولا «تحسّن» القرارات المعتمدة؛ تنفّذها بدقة، وتثبت بمخرجات حقيقية أنها تعمل. المعمارية قرّرت *ماذا* و*لماذا*؛ أنت مسؤول عن *كيف* بجودة إنتاجية.

**ثلاث قواعد فوق كل ما عداها:**

1. **لا تدّعِ ما لم تشغّله.** كل «يعمل/نجح/مُختبَر» يجب أن يرافقه الأمر الفعلي ومخرجه الفعلي (الأسطر الأخيرة على الأقل) في التقرير. ادّعاء بلا مخرج = غير موجود عند المدقق. اختلاق رقم أو مخرج = رفض كامل للمرحلة.
2. **لا تخمّن عقداً أو سلوكاً موجوداً.** اقرأ الكود والملفات أولاً. ما لا تستطيع التحقق منه سجّله في `docs/P0_OPEN_QUESTIONS.md` واختر أكثر الخيارات أماناً دون مخالفة الوثائق الحاكمة.
3. **لا تتجاوز نطاق P0 ولا حدود الإيقاف (القسم 2).**

---

## 1. الوثائق الحاكمة — اقرأها كاملةً قبل أي سطر كود (بهذا الترتيب)

| # | الملف | لماذا |
|---|---|---|
| 1 | `SHARWA_AI_PROJECT_SUMMARY.md` | العقد المجمّد مع Django + المبادئ الحاكمة + المخاطر المفتوحة |
| 2 | `PROMPT_phase3a_step1_gateway_foundation.md` و`PROMPT_phase3a_step2_contract_alignment.md` | أسلوب الصرامة السابق ومعايير القبول التي أُغلقت سابقاً (لا تُنقض) |
| 3 | `docs/00_ARCHITECTURE.md` (v1.1) | القرارات المعتمدة (ADR-01…14)، §4.1 (فجوات البوابة G1–G12)، §4.2 الاستيعاب، §4.10 مفتاح الطوارئ، §6 النشر والموارد، §10 الخارطة |
| 4 | `docs/02_RISK_SOLUTIONS_21.md` | حلول الكوارث **4، 5، 16، 17، 21** (وأساس 19) — هي معيار قبول P0 |
| 5 | `docs/01_FIVE_TASKS_DESIGN.md` | للسياق فقط (تصميم المهام مؤجّل لمراحل لاحقة) |
| 6 | `docs/reference/*` (`schema.sql`، `schema_selftest.sql`، `docker-compose.reference.yml`، `README.md`) | المرجع التنفيذي الملزِم |
| 7 | كود `gateway/` كاملاً (`src/*.js` والاختبارات) | ما ستعدّله فعلاً |

**عند التعارض بين مصدرين، الأسبقية:** هذا الملف ← `00_ARCHITECTURE.md` ← `schema.sql`/compose المرجعيين ← بقية الوثائق ← الكود القائم. أي تعارض تجده لا تحلّه بصمت: سجّله في `docs/P0_DEVIATIONS.md` (القسم 3، قاعدة H15).

---

## 2. بروتوكول الإيقاف والتدقيق (Stop-and-Audit Gate) — إلزامي

### 2.1 خارطة المراحل (وضعتُها أنا بحسب المعمارية)

| المرحلة | المحتوى | الحالة الآن |
|---|---|---|
| **P0** — الأساس والشبكة الآمنة | إصلاحات G1–G12، Redis مزدوج، PostgreSQL+PgBouncer، compose بحدود موارد، هيكل مفتاح الطوارئ، مراقبة أساسية، **لوحة تاجر مصغّرة (ربط القناة/مفتاح الطوارئ/صحة الإرسال)**، حزمة E2E وفوضى حقيقية | **مفتوحة — نفّذها الآن** |
| P1 | صندوق المحادثات ووكيل المبيعات (ingest→PG، آلة التسليم، WebSocket، كتالوج، Verifier…) | 🔒 مقفلة |
| P2 | اللوجستيات (AddressResolver، BackInStock) | 🔒 مقفلة |
| P3 | الأتمتة التسويقية (Send Policy الكاملة، حملات، opt-out…) | 🔒 مقفلة |
| P4 | المقاسات والهدايا | 🔒 مقفلة |
| P5 | الجوال، القنوات الإضافية، التصليب | 🔒 مقفلة |

### 2.2 القواعد

- **مرحلة كاملة أو لا شيء:** P0 لا تُعدّ منتهية إلا بـ **باك إند + فرونت إند + اختبارات فحص ناجحة بمخرجات حقيقية**. لا «سأكمل الواجهة لاحقاً»، ولا «الاختبارات في المرحلة التالية».
- **نقاط تحقق داخلية (P0.0 … P0.8):** كل خطوة يجب أن تنجح اختباراتها قبل أن تبدأ التالية. هذه ليست نقاط توقف للمدقق، بل انضباط ذاتي؛ لكنك تسجّل نتيجتها في `docs/P0_PROGRESS.md` فور إنجازها.
- **التوقف الكامل:** عند اكتمال P0.8 وتسليم `docs/P0_REPORT.md` بالقالب في القسم 10، اكتب في آخر رسالة لك حرفياً:
  `P0 COMPLETE — STOPPING. AWAITING AUDIT. NO P1 WORK STARTED.`
  ثم **لا تفعل شيئاً**: لا تفتح ملفات P1، لا تنشئ مجلدات لها، لا تقترح «تحسينات مبكرة» في الكود.
- **بوّابة المرحلة:** أنشئ `docs/PHASE_GATE.md` بهذا المحتوى بالضبط (ولا تعدّل سطور الاعتماد أبداً):

  ```
  P0: SUBMITTED (awaiting audit)
  P1: LOCKED
  P2: LOCKED
  P3: LOCKED
  P4: LOCKED
  P5: LOCKED
  ```
  المدقق وحده يغيّر `P0` إلى `APPROVED` ويفتح `P1`. كلمتك لا تكفي؛ الملف هو الحَكَم.
- **إن رُفضت المرحلة:** ستصلك قائمة عيوب مرقّمة. أصلح **هذه العيوب فقط**، أعد تشغيل الاختبارات المتأثرة والكاملة، حدّث التقرير، وتوقف مجدداً بالعبارة نفسها.

---

## 3. دستور الهنت (Hunt Constitution) — 15 مادة قابلة للفحص الآلي

مخالفة أي مادة عيب رفض تلقائي. المواد H1–H6 يفحصها سكربت `scripts/hunt_gate.mjs` (تكتبه أنت في P0.0 وأشغّله أنا عند التدقيق).

**H1 — لا كود ناقص ولا Placeholders.**
ممنوع نهائياً في كل الملفات غير الاختبارية: `TODO`، `FIXME`، `XXX`، `HACK`، `pass` (في Python؛ للاستثناءات استخدم docstring كجسم)، `raise NotImplementedError`، `throw new Error('not implemented')`، دوال بجسم فارغ، `lorem`، `placeholder`، `dummy`، `foo/bar`، قيم ثابتة تُحاكي بيانات حقيقية في مسار إنتاجي، `return true/[]/{}` كتنفيذ مؤقت، كتل `catch` فارغة، `except:` عارية، `except Exception: pass`. الوظيفة التي لا تستطيع إكمالها **لا تُكتب** وتُسجَّل في `P0_OPEN_QUESTIONS.md`.

**H2 — العزل التام للمتاجر (`tenant_id`).**
- `tenant_id` يُشتقّ حصراً من **هوية القناة** (`channel_accounts.session_id` ↔ tenant) أو من **claim موقّع في JWT**، ولا يُقبل أبداً من جسم/استعلام/مسار/ترويسة يتحكم بها العميل أو المتصفح.
- كل جدول فيه `tenant_id` عليه RLS (موجود في `schema.sql`). كل وصول لقاعدة البيانات يمر **حصراً** عبر `tenant_tx(tenant_id)` (يفتح معاملة ثم `set_config('app.tenant_id', $1, true)` أي `SET LOCAL`) أو عبر `system_tx()` التي لا تستدعي إلا دوال `SECURITY DEFINER` المسمّاة. أي استعلام خارج هذين المدخلين = عيب.
- مفاتيح Redis المرتبطة بمتجر تحمل `session_id`/`tenant_id` في اسمها. السجلات تحمل `tenant_id`/`session_id` دائماً.
- **اختبار العزل شرط قبول** (القسم 9): متجران، معرّفات متطابقة، استعلام بلا `SET LOCAL` يعيد صفراً، ومحاولة قراءة قناة متجر آخر عبر الـAPI = 404 (لا 403، حتى لا يُكشف وجودها).

**H3 — معالجة الأخطاء في كل سطر.**
كل عملية I/O (شبكة، قرص، Redis، PG، S3) لها: مهلة صريحة، تصنيف للخطأ (`retryable` / `permanent` / `fatal`)، سقف محاولات، backoff مع jitter. لا خطأ يُبتلع: إما يُعاد رميه، أو يُسجَّل بسياق كامل **و** يزيد عدّاد مقياس **و** يتخذ مساراً بديلاً صريحاً. `unhandledRejection`/`uncaughtException` = تسجيل + إيقاف رشيق (fail fast) ليعيد المشرف التشغيل.

**H4 — حماية من الكوارث: كل شيء محدود.**
كل طابور، مخزن مؤقت، تزامن، حجم حمولة، حجم ملف، عدد محاولات، طول سلسلة، وقت انتظار **له سقف مكتوب في الإعدادات** (بقيمة افتراضية معقولة وموثّقة). ممنوع أي مصفوفة/خريطة/ذاكرة تنمو بلا حد. لكل كتابة قابلة للإعادة مفتاح idempotency. وحدّد لكل قرار «اتجاه الفشل»: *تسويق ⇒ fail-closed، رد خدمة للعميل ⇒ fail-open بحدود، رسالة موظف ⇒ لا تُحجب أبداً*.

**H5 — الأسرار والخصوصية.**
لا سرّ في الكود أو الصور أو السجلات. سرّ فارغ/مفقود = **رفض إقلاع** (لا «اقبل الجميع»). أرقام الهواتف في السجلات مقنّعة (آخر 3 خانات فقط). لا كلمات مرور افتراضية في compose (`${VAR:?message}`). لا تُشارك أي سرّ مع مشروع `SharwaConnect` أو غيره.

**H6 — الحتمية وحدود الذكاء الاصطناعي.**
P0 لا يستخدم أي LLM. ممنوع إضافة مكتبة/استدعاء نموذج لغوي. (المبدأ الحاكم: النموذج يقترح والكود الحتمي يقرّر — سيُفرض في P1.)

**H7 — الاختبارات حقيقية.**
لا mocks لما هو قيد الاختبار: Redis وPostgreSQL وPgBouncer وMinIO حقيقية (حاويات أو ثنائيات محلية). ممنوع `ioredis-mock`/`fakeredis`/SQLite بديلاً. ممنوع `skip`/`only`/`xit`/`retry حتى ينجح`/تخفيف assertion لتمرير اختبار. الاختبار المتذبذب (flaky) = عيب يُصلَح لا يُعاد. الاختبارات الأربعة عشر القائمة **لا تُعدَّل ولا تُضعَّف** (يجوز إضافة اختبارات).
الاستثناء الوحيد: **واتساب الوهمي** (`FakeWaSocket`) بديل مشروع لشبكة واتساب فقط، ولا يُقبل إلا في `gateway/test-support/` ويُرفض إقلاعه في الإنتاج (القسم P0.2).

**H8 — الدليل.**
لا تقرير بلا مخرجات حقيقية. لا أرقام مخترعة. ما لم تستطع تشغيله (بيئة تنقصك) تقولُه صراحةً وتشرح السبب ولا تخفيه.

**H9 — العقد المجمّد.**
عقد Django الحالي (القسم 7) لا يُكسر. كل توسعة **إضافية اختيارية**. اختبارات العقد الأربعة تبقى خضراء.

**H10 — النطاق.**
ممنوع أي كود أو مجلد أو جدول أو stub لمراحل P1–P5. «تحضير مبكر» = مخالفة H1 وH10 معاً.

**H11 — الاعتماديات.**
ثبّت الإصدارات بدقة (lockfile). لكل مكتبة جديدة: الاسم، الإصدار، السبب، الترخيص، سبب عدم كفاية المكتبة القياسية — في `docs/P0_DEPENDENCIES.md`. شغّل `npm audit --omit=dev` و`pip-audit` وأرفق النتيجة؛ أي ثغرة High/Critical تُعالَج أو تُبرَّر.

**H12 — المراقبة.** كل منظومة فرعية (ingest، media، outbound، session، kill-switch، forwarder) لها مقاييس Prometheus وسجلات JSON منظّمة بحقول ثابتة (`event`, `tenant_id`, `session_id`, `msg_id?`, `duration_ms`, `outcome`).

**H13 — الوثائق.** تعليق الكود يشرح «لماذا» لا «ماذا». كل ملف إعداد (`redis-durable.conf`، `pgbouncer.ini`…) فيه تعليق يذكر رقم الكارثة/القرار الذي يخدمه.

**H14 — الترحيلات.** SQL فقط، أمامية فقط (forward-only)، معاملة واحدة، idempotent، بمنفّذ يأخذ advisory lock ويسجّل checksum. `0001_baseline.sql` **مطابق بايت-ببايت** لـ `docs/reference/schema.sql` (اختبار hash يفرضه). أي إضافة = ملف ترحيل جديد، ولا يُعدَّل المرجع.

**H15 — الانحرافات.** أي انحراف عن الوثائق الحاكمة يُسجَّل في `docs/P0_DEVIATIONS.md` (ماذا، لماذا، أثره على أي كارثة). انحراف يمسّ مبدأ أمان (عزل، idempotency، fail-safe) **ممنوع** حتى مع التسجيل.

### 3.1 سكربت Hunt Gate (تكتبه في P0.0)

`scripts/hunt_gate.mjs` (Node، متعدد المنصات — المالك على Windows) يفشل بـ exit code ≠ 0 ويطبع الملف:السطر عند:
1. أي رمز من H1 في غير ملفات الاختبار.
2. `pass` مستقل في `*.py`، و`except:` عارية، و`except Exception:` بجسم `pass`/`...`.
3. `catch (…) {}` فارغة أو تحوي فقط تعليقاً.
4. `console.log` خارج ملفات الاختبار (استخدم pino)، و`print(` في Python.
5. `.skip(` / `.only(` / `xit(` / `xdescribe(` / `@pytest.mark.skip` في الاختبارات.
6. أي استيراد لـ `psycopg`/`asyncpg` خارج `core/app/db/`.
7. أي ملف `.env` حقيقي مُلتزَم، أو نمط سرّ (مفاتيح `AKIA…`، `-----BEGIN`، `sk-…`).
8. سلاسل `SELECT/INSERT/UPDATE/DELETE` في `core/` خارج `core/app/db/` أو `core/app/repos/`.

مع ESLint (`no-empty`, `no-floating-promises` عبر typescript-eslint للفرونت، `eqeqeq`) وruff (`E722`, `BLE001`, `S`, `T20`, `B`, `ASYNC`) وmypy `--strict` للبايثون — **كلها تمر بلا استثناءات مُعطَّلة** (`# noqa` و`eslint-disable` ممنوعان إلا بتبرير في `P0_DEVIATIONS.md`).

---

## 4. دستور «اللا-التباس» للواجهة (Zero-Confusion UX & Microcopy)

**المبدأ:** التاجر غير تقني. إن احتاج أن يسأل «ما هذا؟» أو «ماذا أفعل؟» فالواجهة فاشلة. كل مادة أدناه تُفرَض **بالكود** لا بالنيّة (القسم 4.1).

**U1 — كل شاشة معرَّفة:** عنوان، جملة واحدة تشرح غرضها، وزر مساعدة «؟» يفتح شرحاً أطول.

**U2 — تلميح سياقي (Tooltip) لكل إعداد/حقل/مفتاح/زر يغيّر حالة:** يشرح *ماذا يفعل* و*ماذا يحدث إن غيّرته* بلغة التاجر، في جملتين كحد أقصى. يعمل بالفأرة والكيبورد ولمسة الجوال (`aria-describedby`، ليس `title` فقط).

**U3 — لا أكواد أخطاء تقنية أبداً.** يُمنع عرض: رمز HTTP، اسم استثناء، `error.message` الخام، JSON، stack، معرّفات داخلية. كل خطأ يُعرض بثلاثة أجزاء: (أ) **ماذا حدث** بلغة بشرية، (ب) **ماذا يعني لعملك**، (ج) **زر إجراء (Action Button)** يحلّ المشكلة أو يوجّه لحلّها («أعد المحاولة»، «اربط الرقم من جديد»، «تواصل مع الدعم»…). رقم مرجعي للدعم يوضع فقط داخل «تفاصيل للدعم الفني» مطويّة مع زر نسخ.

**U4 — Empty State لكل قسم/قائمة جديدة:** يشرح لماذا هذا القسم موجود، وماذا سيظهر فيه، وزر واحد رئيسي للخطوة الأولى.

**U5 — حالات الانتظار والنجاح:** هياكل تحميل (skeleton)، حالة انقطاع الشبكة، وتأكيد نجاح بلغة عادية («تم ربط رقمك. ستصلك رسائل عملائك هنا»).

**U6 — الإجراءات الخطرة تُؤكَّد بعواقبها التجارية:** مثل: «إيقاف الردود الآلية: سيتوقف المساعد عن الرد على عملائك فوراً حتى تعيد تشغيله. رسائل موظفيك لن تتأثر.»

**U7 — معايير الصياغة:** عربية مبسّطة ودودة؛ أفعال أمر على الأزرار؛ **ممنوع مصطلحات** مثل: session، token، webhook، epoch، queue، Redis، gateway، API، JWT، payload، sync، latency في نصوص التاجر. المسرد: session ⇒ «قناة»، QR ⇒ «رمز الربط»، queue ⇒ «رسائل بانتظار الإرسال»، kill switch ⇒ «مفتاح الطوارئ».

**U8 — الوصول والجوال:** واجهة RTL أولاً، تعمل على عرض 360px، أهداف اللمس ≥ 44px، تباين AA، تنقّل بالكيبورد.

**U9 — كتالوج الأخطاء شامل:** كل رمز خطأ يمكن أن يعيده الـAPI له مدخل في كتالوج الرسائل (عربي + إنجليزي مكتملان). خطأ غير معروف يُعرض بصياغة عامة بشرية + زرّين («حاول مجدداً»، «تواصل مع الدعم»).

### 4.1 فرض الدستور بالكود (Ux Gate)

1. **وقت الترجمة (TypeScript):** مكوّنات `Field`/`Switch`/`Select`/`ActionButton` تتطلب prop اسمه `help: string` (إجباري، بلا قيمة افتراضية)؛ وكل شاشة تصدّر `screenMeta: { title, purpose, help, emptyState }` إجبارياً؛ وكتالوج الأخطاء `Record<ApiErrorCode, HumanError>` شامل بالنوع (إضافة رمز جديد بلا رسالة = فشل بناء).
2. **وقت التشغيل (Vitest + Testing Library):** لكل شاشة اختبار يمرّ على **كل** رمز خطأ في الكتالوج فيتحقق أن النص الظاهر عربي، **لا** يطابق `/[A-Z_]{4,}|\b[45]\d{2}\b|Error|Exception|undefined|null/`، وأن زر الإجراء موجود وقابل للنقر. اختبار Empty State لكل شاشة. اختبار أن كل عنصر تفاعلي له وصف يمكن الوصول إليه (`aria-describedby` مربوط بنص غير فارغ).
3. **فحص المسرد:** سكربت `console/scripts/ux_gate.mjs` يفشل إن وجد أي كلمة من قائمة المصطلحات الممنوعة (U7) في ملفات الترجمة العربية، أو أي مفتاح ترجمة ناقص بين `ar` و`en`.
4. **لقطات للمدقق:** Playwright يحفظ لقطة لكل شاشة وكل حالة رئيسية (فارغة، تحميل، عادية، خطأ) بعرضي 360 و1280 في `docs/p0_screens/` (يراجعها المدقق بصرياً).

---

## 5. نطاق P0

### 5.1 داخل النطاق (وحده)

| الرمز | البند | كوارث |
|---|---|---|
| G1–G12 | إصلاحات بوابة القنوات كما في `00 §4.1` | 4، 5، 6، 16، 17، 21 |
| — | Redis مزدوج (`redis-durable` + `redis-cache`) | 5، 17 |
| — | PostgreSQL 16 + PgBouncer + تطبيق `schema.sql` كما هو + `schema_selftest.sql` | 1، 2، 16 |
| — | Docker Compose بحدود موارد (خط الأساس 8GB/4vCPU) | 16، 21 |
| — | هيكل مفتاح الطوارئ (جدول + وظائف + نسخة Redis + Pub/Sub + فحص في البوابة) | 19 (أساس) |
| — | مراقبة أساسية (Prometheus + قواعد تنبيه) | 16، 21 |
| — | **حزمة `core/` بايثون مصغّرة** (`api` فقط): مصادقة JWT RS256، `tenant_tx`، واجهات القنوات ومفتاح الطوارئ | 1 (أساس) |
| — | **لوحة التاجر المصغّرة `console/`** (3 شاشات) وفق دستور اللا-التباس | UX |
| — | حزمة E2E + الفوضى + الفحص الآلي (Hunt Gate + Ux Gate) | كل ما سبق |

### 5.2 خارج النطاق (ممنوع بتاتاً في P0)

مستهلك الاستيعاب إلى PostgreSQL (`inbound_events/messages`)، محرك الدور، LLM، Router، Verifier، الكتالوج، تتبع الطلبات، رابط الدفع، Send Policy الكاملة، الحملات، المقاسات، الهدايا، العناوين، WebSocket للموظفين، الجوال، عمّال Celery (`worker-*`)، أي تكامل مع `sharwa_saas` (لا تلمس ذلك المشروع نهائياً).

### 5.3 هيكل المستودع المستهدف

```
C:\sharwa_ai\
  gateway\            # موجود — تعدّله (Node 22)
  core\               # جديد — بايثون 3.12 (api فقط)
    app\{config,db,security,killswitch,channels,api}\
    migrations\       # 0001_baseline.sql (= المرجع بايت-ببايت) + 0002_p0_*.sql
    tests\
  console\            # جديد — React 18 + Vite + TypeScript (RTL)
  ops\                # redis-durable.conf, redis-cache.conf, pgbouncer.ini, postgres.conf, prometheus\
  tests\e2e\          # حزمة E2E والفوضى
  scripts\            # hunt_gate.mjs وأدوات
  docs\               # الوثائق + P0_*.md
  docker-compose.yml  # مشتق من reference مع تعديل P0
  docker-compose.test.yml
  .env.example  .gitattributes (LF)  .editorconfig
```
المالك على **Windows**: كل السكربتات بـ Node (`.mjs`) أو داخل الحاويات، لا bash فقط، ولا مسارات بفواصل يدوية. تُشغَّل الاختبارات عبر `docker compose` أو WSL.

---

## 6. خطوات التنفيذ (P0.0 → P0.8) — بهذا الترتيب

### P0.0 — التمهيد وخط الأساس

1. اقرأ الوثائق (القسم 1). ثم شغّل الاختبارات القائمة: `cd gateway && node --test` ⇒ **يجب 14 ناجحاً**. الصق المخرج في `docs/P0_PROGRESS.md` كخط أساس.
2. اكتب `scripts/hunt_gate.mjs` (3.1) وشغّله على الكود القائم؛ سجّل أي مخالفات موروثة **وأصلحها ضمن الخطوات اللاحقة** حين تلمس الملف.
3. **تحقق `@lid`** (مفتوح 4): افحص شيفرة `baileys@6.7.24` في `node_modules` (حقول مثل `remoteJidAlt`, `participantAlt`, `jidDecode`, أي `lid-mapping`) واكتب في `docs/P0_FINDINGS.md` ما الذي تدعمه هذه النسخة فعلاً **بالدليل (مسار الملف والسطر)**. لا تخمّن.
4. اكتب في `P0_FINDINGS.md` أيضاً: كيف تعامل الكود القائم مع رسائل الوسائط بلا نص (`message_text` فارغ/caption) لأنك ستحاكيه للأنواع الجديدة (P0.2)، وما هي صيغة `mapConnectionStatus` (المفردات الحالية لـ `status`).
5. تحقق من بيئتك: Docker وCompose، Node ≥ 22، Python 3.12، PostgreSQL client. ما ينقص وتعذّر تثبيته: أوقف واكتب السبب (لا تلتفّ بمحاكاة).

**قبول P0.0:** 14/14 ✔، `hunt_gate` يعمل، `P0_FINDINGS.md` بأدلة، `P0_PROGRESS.md` منشأ.

### P0.1 — طبقة البيانات والبنية (Redis ×2، PostgreSQL، PgBouncer، Compose)

1. **`docker-compose.yml`:** اشتقّه من `docs/reference/docker-compose.reference.yml`. الخدمات في P0: `postgres`, `pgbouncer`, `redis-durable`, `redis-cache`, `gateway`, `gateway-forwarder` (نفس صورة البوابة، أمر تشغيل مختلف، `mem_limit: 192m`), `api` (يقدّم `console` المبني كملفات ساكنة), `prometheus`. **لا تضف** `worker-*`/`scheduler` (تأتي في P1). لا تترك خدمات معلّقة/معطّلة بتعليقات.
   - لكل حاوية: `mem_limit` + `memswap_limit` + `cpus` + `pids_limit` + سقف سجلات (json-file، `max-size`, `max-file`) + `stop_grace_period: 30s` للبوابة والـforwarder + `restart: unless-stopped` + healthcheck.
   - PostgreSQL: `oom_score_adj: -900`، شبكة `data` داخلية (`internal: true`) لا تصلها الخارج.
   - المنافذ: لا تنشر PostgreSQL/Redis على المضيف إلا في `docker-compose.test.yml`.
   - الأسرار من `.env` عبر `${VAR:?اذكر السبب}`؛ لا قيم افتراضية.
   - **مجموع `mem_limit` لا يتجاوز 5,360 MB** (رقم المرجع) — احسبه واطبعه، واذكر كم يتبقى من 8GB للنظام وpage cache.
2. **PostgreSQL** (`ops/postgres.conf`): `max_connections=60`، ترميز UTF8 إلزامي (`--encoding UTF8 --locale=C.UTF-8`)، `shared_buffers`/`work_mem`… كما في المرجع (اشرح أي انحراف). أدوار: `sharwa_app` (غير مالكة، محكومة بـ RLS)، `sharwa_system` (دوال `SECURITY DEFINER` فقط)، ومستخدم ترحيلات منفصل. أدوار `LOGIN` تُنشأ من `.env` عند أول تشغيل (سكربت init) وترث الأدوار الأساسية.
3. **PgBouncer:** `pool_mode=transaction`، `default_pool_size=25` + `reserve_pool_size=5`، `max_client_conn=400`، `auth_type=scram-sha-256`، ملف `userlist` يُولَّد عند الإقلاع من `.env` (لا يُلتزم). ثمانية اتصالات مباشرة محجوزة للصيانة (ميزانية الاتصالات `00 §6`).
4. **الترحيلات:** `core/migrations/0001_baseline.sql` نسخة **بايت-ببايت** من `schema.sql`. منفّذ ترحيلات (`core/app/db/migrate.py`) يأخذ `pg_advisory_lock`، يسجّل `schema_migrations(version, checksum, applied_at)`، ويرفض إن تغيّر checksum لترحيل مطبَّق. اختبار يثبت: التشغيل مرتين آمن؛ تعديل ملف مطبَّق يُرفض.
5. **Redis:** `redis-durable`: `appendonly yes`، `appendfsync always`، `maxmemory` صريح، `maxmemory-policy noeviction`، `save ""` (AOF فقط). `redis-cache`: بلا استمرارية، `allkeys-lru`، `maxmemory` صريح. كلمة مرور لكليهما و`protected-mode` وربط بالشبكة الداخلية فقط. **قاعدة:** لا يكتب أي كود شيئاً «يجب ألا يضيع» في `redis-cache`.

**قبول P0.1 (الصق المخرجات):**
- `docker compose config` يُظهر كل حدود الموارد؛ `docker inspect postgres` يُظهر `OomScoreAdj=-900`؛ جدول حسابات مجموع الذاكرة.
- تشغيل `schema_selftest.sql` عبر اتصال مباشر ينتهي بـ **`ALL SELF-TESTS PASSED`** (34 بنداً).
- **اختبار PgBouncer/RLS:** ألف معاملة متداخلة متزامنة لمتجرين مختلفين عبر PgBouncer (transaction pooling): لا تسرّب صف واحد بين المتجرين، ومعاملة بلا `SET LOCAL` تعيد صفراً.
- **اختبار Redis durable:** كاتب يسجّل في ملف كل معرّف `XADD` تلقّى إقراراً به، ثم `docker kill -s KILL redis-durable`، إعادة تشغيل، والتحقق أن **كل** المعرّفات المُقَرّة موجودة. كرّره 10 مرات. (`redis-cache` مُفرَّغة بالكامل بعد قتلها وهذا سلوك سليم — وثّقه.)
- `docker stats --no-stream` بعد الإقلاع (قبل الحمل) مع الاستهلاك الفعلي.

### P0.2 — استيعاب البوابة الدائم (G1، G2، G4، G11، G12) + الـForwarder

**بنية الكود:** فكّك `gateway/src/sessions.js` إلى وحدات صغيرة قابلة للاختبار دون كسر تصديراته الحالية: `driver/` (واجهة `WaDriver`: `BaileysDriver` للإنتاج، `FakeWaDriver` للاختبار)، `ingest/{normalize,dedupe,wal,spool,identity}.js`، `forwarder.js`، `config.js` (كل الحدود من البيئة مع تحقق إقلاع صارم).

**`FakeWaDriver` (اختبار فقط):** في `gateway/test-support/`. يُحمَّل فقط عند `WA_DRIVER=fake` **و** `NODE_ENV=test` **و** `ALLOW_FAKE_WA=1`؛ وأي توليفة أخرى مع `NODE_ENV=production` = **رفض إقلاع صريح** (اختبار يثبته). يُتحكَّم به عبر أوامر Redis (`fakewa:cmd`) ليحقن: رسائل واردة (كل الأنواع)، إعادة تسليم، أحداث `connection.update` بأكواد الانقطاع، إيصالات تسليم/قراءة، رسالة `fromMe` من «الهاتف»، وتدفق وسائط بأي حجم (يُولَّد بلا تخزينه كاملاً في الذاكرة).

**المتطلبات:**

1. **WAL (G1):** عند `messages.upsert` (نوع `notify` فقط؛ تجاهل `append`/التاريخ) ⇒ تطبيع ⇒ dedupe ⇒ `XADD in:{shard}` على `redis-durable` (`shard = crc32(session_id) % INGEST_SHARDS`, الافتراضي 4) ⇒ **بعد نجاحه فقط** تُعتبر الرسالة آمنة. ممنوع أي معالجة قبل `XADD`. حقول المدخل (`v=1`): `kind`، `session_id`، `tenant_id?`، `channel_account_id?`، `provider_message_id`، `type`، `ts`، `identity`، `text?`، `media?`، `location?`، `contact?`، `reaction?`.
2. **Dedupe (G2):** الترتيب المطلوب لمنع الضياع مع تقليل التكرار:
   `SET dedupe:{sid}:{id} pending NX EX 60` ⇒ فشل = تجاهل ⇒ `XADD` ⇒ نجاح: `SET … done EX 172800` / فشل: `DEL` ثم مسار الـspool. (هذا تحسين مقصود لصيغة `00 §G2`: XADD **قبل** ختم "done" كي لا تضيع رسالة إن سقطت العملية بينهما؛ التكرار الناتج مسموح لأن UNIQUE في PostgreSQL خط الدفاع الثاني — سجّل ذلك في `P0_DEVIATIONS.md` كـ«تحسين متوافق».)
3. **Spool (كارثة 5/17):** إن تعذّر `XADD` (Redis غير متاح/ممتلئ): اكتب المدخل في ملف append-only على قرص دائم (`/data/spool`) مع `fsync` قبل الإقرار، بسقف حجم (`SPOOL_MAX_MB` الافتراضي 200). يُفرَّغ بالترتيب فور عودة Redis. عند بلوغ السقف: مقياس `ingest_spool_overflow_total` + سجل حرج + `readyz`=503 — لا إسقاط صامت أبداً.
4. **التطبيع (G4):** `type ∈ text|image|audio|video|document|location|contact|sticker|reaction|unsupported`. الموقع: `lat,lng,name?,address?,is_live`. جهة الاتصال: الاسم والأرقام. الاستجابات التفاعلية (أزرار/قوائم) تُطبَّع إلى `text` بمحتوى الاختيار. **`shouldIgnoreInbound` يُصحَّح** بحيث لا يتجاهل الموقع/الملصق/جهة الاتصال (اختبار خاص بدبوس الموقع — هو السبب الأول لهذا البند).
5. **الهوية (G12):** `identity: { wa_id, phone_e164|null, jid_raw, addressing: 'pn'|'lid' }`. لا تفترض أن JID = رقم هاتف. استعمل ما وثّقته في `P0_FINDINGS.md` عن `@lid`؛ حيث لا يمكن استخراج الرقم اجعل `phone_e164=null` ولا تخترعه.
6. **الـForwarder (Legacy):** عملية مستقلة (`node src/forwarder.js`) مجموعة مستهلكين `legacy-forwarder` على `in:*`؛ تحوّل المدخل إلى **الويبهوك القديم بالضبط** عبر `webhook.js` القائم (HMAC، مسارين مع `/` النهائية، `message_text`) ثم `XACK` **بعد 2xx فقط**. الأنواع التي لم يعرفها Django القديم (`location`, `contact`, `sticker`, `reaction`, `unsupported`) تُرسَل بنفس تعامل الكود القائم مع الوسائط بلا نص (وثّقته في P0.0) مع حقول إضافية اختيارية `message_type` و`location`… — **لا تخترع سلوكاً**. جلسات `engine=ai_core` (حقل إضافي عند `POST /sessions`) لا يُرسَل لها ويبهوك: تُترك في الـStream لمجموعة `core-ingest` (تُنشأ عند إقلاع البوابة عبر `XGROUP CREATE … MKSTREAM`، ولا مستهلك لها حتى P1). جلسة بلا `engine` = `django` (توافق قديم).
   - إعادة المحاولة: backoff أُسّي + jitter، `XAUTOCLAIM` للعالق، وبعد `FORWARD_MAX_ATTEMPTS` (10) ⇒ `dlq:in` + مقياس + تنبيه. لا حلقة لا نهائية.
   - **Trimmer:** كل 60 ثانية `XTRIM MINID` لكل Stream بحدّ أدنى = أقدم معرّف لم يؤكّده **كل** groups (بما فيها `core-ingest`) مع أقصى عمر 7 أيام؛ مقياس حجم Stream وذاكرة Redis وتنبيه عند 60% من `maxmemory`.
7. **ميتاداتا الجلسة (إضافية):** `POST /sessions` يقبل اختيارياً `tenant_id`, `channel_account_id`, `engine`، وتُحفظ بجانب بيانات المصادقة (`meta.json`) ليعيدها `rehydrate`. غيابها لا يكسر شيئاً.
8. **G11:** `POST /sessions` idempotent: 201 للجديدة، 200 للموجودة بنفس الجسم.

**قبول P0.2:** اختبارات وحدة (تطبيع كل نوع بحمولات Baileys حقيقية النمط، dedupe، spool، توزيع shards) + تكامل بـRedis حقيقي: (أ) 1000 إعادة تسليم للرسالة نفسها ⇒ **مدخل واحد** في Stream وويبهوك واحد؛ (ب) قتل Redis أثناء الاستيعاب ⇒ لا ضياع (spool ثم تفريغ)؛ (ج) رسالة موقع تصل لـ Stream بإحداثياتها؛ (د) الـ14 اختباراً القديمة خضراء.

### P0.3 — الوسائط (G3، كارثتا 16 و21)

1. **لا تحمّل الملف كاملاً في الذاكرة:** افحص `fileLength` المُعلَن قبل التنزيل؛ نزّل **Stream** ومرّره إلى `@aws-sdk/lib-storage` `Upload` (multipart، `partSize` 5MB، `queueSize` 2) دون تجميعه.
2. **السقوف (كلها من البيئة):** صورة 16MB، صوت 16MB، فيديو 64MB، مستند 64MB، ملصق 2MB. فوق السقف ⇒ لا تنزيل، `media.status='too_large'` مع الحجم. **Semaphore عام** = 3 تنزيلات متزامنة. **حصة يومية لكل متجر** (عدد وبايتات، الافتراضي 500 ملف/1GB) عدّاداتها في `redis-durable`؛ التجاوز ⇒ `media.status='quota_exceeded'`.
3. **فحص النوع بالمحتوى (magic bytes)** لأول 4KB: عدم تطابقه مع النوع المُعلَن ⇒ `media.status='rejected_type'` ولا يُرفع. مفتاح الكائن: `{session_id}/{YYYY}/{MM}/{provider_message_id}.{ext}`.
4. **لا ضياع أثناء التنزيل:** المدخل الأول في Stream يُكتب فوراً بـ`media.status='pending'` مع `download_ref` (الحقول اللازمة لإعادة التنزيل من الرسالة: `url/directPath/mediaKey/fileSha256/fileLength/mimetype`). مهام التنزيل في Stream `media:jobs` (مجموعة مستهلكين، `XAUTOCLAIM`). عند الانتهاء (نجاحاً أو فشلاً نهائياً بعد 3 محاولات) يُكتب مدخل `kind='media_update'` **يحمل الرسالة المطبّعة كاملة** + `media` النهائية، فلا يفقد النص/الـcaption أبداً حتى لو فشلت الوسائط. الـForwarder يتجاهل (ACK) مدخل `pending` ويُرسل الويبهوك القديم عند `media_update` (سلوك Django القديم لا يتغير: ويبهوك واحد فيه رابط الوسائط).
5. **مقاييس:** `media_bytes_total`, `media_inflight`, `media_failures_total{reason}`, `media_duration_seconds`.

**قبول P0.3 (بـMinIO حقيقية في `docker-compose.test.yml`):**
- ملف **50MB** (يولَّده FakeWaDriver بالتدفق): ارتفاع RSS للبوابة أثناء المعالجة **< 100MB** فوق خط الأساس؛ اطبع منحنى RSS (عيّنات كل ثانية).
- 10 ملفات 50MB متزامنة: الذروة `< 600MB` مطلقاً، و`media_inflight ≤ 3` دائماً.
- قتل البوابة (`kill -9`) في منتصف تنزيل 50MB ثم إعادة تشغيلها ⇒ **كائن واحد** مكتمل في MinIO (لا كائن ناقص عالق: تحقق من إلغاء multipart غير المكتمل) وويبهوك واحد.
- ملف أكبر من السقف يُرفض دون تنزيل (تحقق من صفر بايتات عبر الشبكة)؛ ملف بامتداد صورة ومحتوى تنفيذي ⇒ `rejected_type`.

### P0.4 — الإرسال الدائم (G5، G9)

1. **العقد:** `POST /sessions/:id/send` يحتفظ بمساره وترويساته واستجابته الحالية كما هي. الإضافات الاختيارية في الجسم: `client_msg_id`, `origin: bot|human|automation` (الافتراضي عند الغياب `bot`… **تحقق من سلوك Django الحالي** وأبقِ الافتراضي الأكثر أماناً وسجّله), `message_class: service|utility|marketing` (الافتراضي `service`), `priority: interactive|bulk`. إن غاب `client_msg_id` تولّده البوابة وتعيده في الاستجابة (حقل إضافي).
2. **الطابور:** قوائم على `redis-durable`: `out:{sid}:interactive` و`out:{sid}:bulk` (الأولوية للأولى). **Idempotency:** `SET outidem:{sid}:{client_msg_id} <state> NX EX 604800`؛ تكرار الطلب لا يعيد الإرسال ويعيد حالته الحالية (`duplicate:true`). حدّ أقصى لطول الطابور لكل جلسة (`OUT_QUEUE_MAX`) ⇒ عند الامتلاء `429` بجسم بشري/`retry_after` (لا ابتلاع).
3. **الإرسال المحمي من الانهيار:** العامل ينقل العنصر ذرّياً إلى قائمة `out:{sid}:inflight` (BLMOVE)، يولّد **معرّف رسالة واتساب مسبقاً** ويسجّله في `sent_ids:{sid}` (SET بعمر 10 دقائق) **قبل** استدعاء `sendMessage(…, {messageId})`، وبعد النجاح يكتب `sent_marker:{sid}:{client_msg_id}=wa_id` ثم يزيل العنصر من inflight. عند الإقلاع: عناصر inflight مع `sent_marker` تُعتبر مُرسَلة؛ بدونه تُعاد للطابور. (نافذة تكرار ضيقة تبقى بين نجاح الإرسال وكتابة العلامة — **قِسها** في اختبار الفوضى وأبلغ عنها بصدق.)
4. **التباطؤ (Pacing) وToken Bucket لكل رقم:** فاصل عشوائي `PACE_INTERACTIVE_MS=800–1500` و`PACE_BULK_MS=2000–3000` (المحتفَظ به من الكود القائم للـbulk). **دلو رموز** لكل رقم (سكربت Lua ذرّي على `redis-durable`): دلو `service` (سعة/تعبئة عالية) ودلو `marketing` (منخفضة، افتراضي 20 رسالة/دقيقة، سقف يومي `MARKETING_DAILY_CAP`)؛ الرفض ⇒ تأجيل داخل الطابور لا إسقاط. هذا خط الدفاع الأخير المستقل عن Send Policy (التي تأتي في P3).
5. **أحداث الحالة:** `XADD evt:{shard}` بالأنواع `queued|sent|delivered|read|failed` مع `client_msg_id`, `wa_message_id?`, `error_class: retryable|permanent|session_down`. `delivered/read` من إيصالات `messages.update`. الفشل: 3 محاولات مع backoff للأخطاء القابلة للإعادة؛ جلسة غير متصلة ⇒ يبقى العنصر بالطابور حتى `TTL` (interactive 10 دقائق، bulk 24 ساعة) ثم `failed(expired)`. الـForwarder **لا** يمرّر `evt` إلى Django القديم.
6. **مفتاح الطوارئ عند الإرسال:** انظر P0.6 (يُفحص قبل كل إرسال؛ `origin=human` لا يُحجب أبداً).

**قبول P0.4:** 50 رسالة في الطابور ⇒ `kill -9` للبوابة في المنتصف ⇒ إعادة تشغيل ⇒ **الـ50 كلها وصلت لـFakeWa** بلا فقد؛ عدد المكرَّرات مُقاس ومطبوع (الحد: ≤ عدد مرات القتل وكل تكرار موثّق بنافذته). 1000 طلب `send` بنفس `client_msg_id` ⇒ إرسال واحد. دلو التسويق يمنع تجاوز المعدل (قياس زمني حقيقي). طابور ممتلئ ⇒ 429 نظيف. الاختبارات القديمة خضراء.

### P0.5 — دورة حياة الجلسة (G6، G7، G8)

1. **آلة الحالات (G6):** `STARTING → QR_PENDING → CONNECTED ↔ RECONNECTING`، ونهائيات/شبه نهائية: `LOGGED_OUT` (401: مسح بيانات المصادقة، لا إعادة اتصال، تتطلب مسح QR جديد)، `CONFLICT` (440/استبدال الاتصال: **لا تعاند**؛ لا إعادة اتصال 15 دقيقة ثم محاولة واحدة؛ تكرار الصراع ⇒ توقف حتى طلب يدوي)، `BANNED` (403: نهائية + تنبيه + حدث يفتح مفتاح طوارئ القناة). كل إعادة اتصال بـ backoff أُسّي (1s→5min) مع jitter؛ `restartRequired` يعيد فوراً. جدول تحويل أكواد `DisconnectReason` موثّق في الكود ومُختبَر.
2. **التوافق:** `getSessionStatus` يعيد **حرفياً** `{status, qr_image_base64, connected_phone_number}` بمفردات `status` الحالية (راجع `mapConnectionStatus` وأبقها). الحالات الجديدة تُعرَض عبر مسار **إضافي** `GET /sessions/:id/health` (`state`, `since`, `reconnect_attempts`, `last_disconnect_reason`, `lease_holder`, `queue_depth`, `spool`, `kill_switch`).
3. **Lease (G7):** `lease:{sid}` = `{instance_id}:{fencing_token}` (`INCR fence:{sid}`)، `SET NX PX 30000` وتجديد كل 10 ثوانٍ بسكربت Lua مقارِن، وكتابة بيانات المصادقة (`creds`) تتحقق من ملكية الـlease. **فقدان الـlease ⇒ إغلاق المقبس فوراً دون `logout`** ووقف الإرسال. إقلاع متدرّج: `REHYDRATE_CONCURRENCY=2` + jitter 0.5–2s. `MAX_SESSIONS` (الافتراضي نتيجة قياسك، لا تخمينك: قِس ذاكرة جلسة واحدة واذكر الرقم) وتجاوزه ⇒ `POST /sessions` يعيد 503 بسبب سعة واضح + مقياس.
4. **إشارة الاستلام البشري (G8):** رسالة `fromMe` وارد من الهاتف **ليس** معرّفها في `sent_ids:{sid}` ⇒ مدخل `kind='human_takeover_signal'` (هوية العميل + الرسالة المطبّعة + `direction='outbound_human'`). **ممنوع أبداً** تمريرها كرسالة عميل. الـForwarder يتجاهلها. حالة السباق (وصول صدى `fromMe` قبل عودة `sendMessage`) محلولة بتسجيل المعرّف المُسبق (P0.4-3) — اختبارها إلزامي.
5. **مقاييس/سجلات** لكل انتقال حالة (`session_state{state}`، `session_reconnects_total{reason}`).

**قبول P0.5 (بـFakeWa):** أكواد الانقطاع الأربعة تنتج الحالات الصحيحة؛ في `CONFLICT`: **≤ محاولة اتصال واحدة خلال 60 ثانية** (عدّها)؛ `LOGGED_OUT` تمسح creds؛ `BANNED` تصدر الحدث. نسختا بوابة على الجلسة نفسها ⇒ **واحدة فقط** تشغّلها؛ قتل الحاملة ⇒ الأخرى تستحوذ بعد انتهاء المهلة، والكاتب القديم مرفوض بالـfencing. إقلاع 30 جلسة ⇒ لا أكثر من 2 اتصال متزامن (سجّل المنحنى). إشارة الاستلام تصدر لرسالة الهاتف ولا تصدر لصدى رسائل البوابة (بما فيها السباق).

### P0.6 — التشغيلية، مفتاح الطوارئ في البوابة، المراقبة (G10، كارثتا 19 و21)

1. **نقاط النهاية:** `/healthz` يبقى مفتوحاً وبسيطاً كما هو (مجمَّد). جديد: `/readyz` (يعيد 503 إن: `redis-durable` غير متاح، أو spool غير فارغ لأكثر من `SPOOL_STALE_S`، أو تجاوز `MAX_SESSIONS`، أو بدء الإيقاف)، و`/metrics` محمي بـ`METRICS_TOKEN` (Bearer) — سرّ فارغ = رفض إقلاع.
2. **الإيقاف الرشيق (SIGTERM/SIGINT):** إيقاف قبول طلبات جديدة ⇒ إنهاء التنزيلات الجارية بحد زمني ⇒ إغلاق المقابس **بلا logout** ⇒ تفريغ spool ⇒ تحرير الـleases ⇒ خروج خلال ≤ 25 ثانية (< `stop_grace_period`). اختبار `docker stop` يثبت: لا رسالة ضائعة، والـleases محرَّرة.
3. **الذاكرة:** `NODE_OPTIONS=--max-old-space-size=900`، `syncFullHistory:false`، وقياس ذاكرة جلسة واحدة (يغذي `MAX_SESSIONS`).
4. **السجلات:** pino JSON، إخفاء الهواتف (آخر 3 خانات)، حقول H12 الثابتة، مستوى من البيئة.
5. **المقاييس الدنيا (أسماء ثابتة):** `ingest_ack_seconds` (histogram: من وصول الرسالة حتى نجاح XADD)، `ingest_messages_total{type}`، `ingest_duplicates_total`، `ingest_spool_depth`, `ingest_spool_overflow_total`, `stream_length{stream}`, `stream_pending{group}`, `forwarder_attempts_total{outcome}`, `dlq_length`, `out_queue_depth{priority}`, `out_sent_total`, `out_failed_total{class}`, `out_blocked_total{capability}`, `media_*` (P0.3)، `session_state{state}`، `session_reconnects_total{reason}`، `lease_lost_total`، `killswitch_state{scope,capability}`، `process_resident_memory_bytes`، `nodejs_heap_size_used_bytes`، `process_open_fds`.
6. **Prometheus:** `ops/prometheus/prometheus.yml` (يجمع من البوابة والـforwarder والـapi) + `alerts.yml` بالقواعد: البوابة متوقفة، ذاكرة `redis-durable` > 60%، spool غير فارغ > 5 دقائق، `dlq_length > 0`، جلسة `banned`، heap > 85% من الحد، أقدم رسالة معلّقة بالـStream > 60 ثانية، `process_open_fds` > 70% من الحد. تحقّق بـ`promtool check rules`. (Alertmanager/إشعارات التسليم تُضاف في P1؛ في P0 القواعد مرئية في واجهة Prometheus — سجّل ذلك في `P0_DEVIATIONS.md` صراحةً.)
7. **مفتاح الطوارئ — جانب البوابة:** مصدر الحقيقة جدول `kill_switches` (يكتبه `api` فقط). النسخة السريعة في `redis-cache`: hash `ks:global` و`ks:tenant:{tenant_id}` و`ks:channel:{channel_account_id}` (حقل = القدرة، قيمة = `on|degraded|off`) + قناة Pub/Sub `ks:changes`. البوابة تشترك وتحتفظ بنسخة في الذاكرة (تحديث فوري عند الرسالة + مزامنة كاملة كل 30 ثانية). الحالة الفعلية = **الأشد** بين (global، tenant، channel) × (القدرة، `*`) — مطابقة لدلالة `app.effective_switch`. السلوك:
   - **الاستقبال (WAL) لا يُحجب أبداً** (لا نفقد رسائل العملاء مهما كان المفتاح).
   - `origin=bot` ⇒ القدرة `ai_reply`؛ `message_class=marketing` ⇒ `marketing` (و`broadcast` إن كانت `priority=bulk`)؛ الوسائط ⇒ `media` (`off` ⇒ `media.status='disabled_by_switch'` والرسالة نفسها تُحفظ). `off` ⇒ حجب؛ `degraded` ⇒ يحجب التسويق/البث ويسمح بردود الخدمة.
   - **`origin=human` لا يُحجب أبداً.**
   - الحجب ⇒ `423` بجسم `{error:'blocked_by_switch', capability, state, scope}`؛ العناصر التي كانت بالطابور وقت قلب المفتاح تُفحص مجدداً قبل الإرسال وتصبح `failed(error_class='blocked')`.
   - **فشل الوصول لـ`redis-cache`:** استخدم آخر حالة معروفة؛ إن تجاوز عمرها `KS_STALE_MAX_S` (120) فالتسويق/البث **fail-closed** وبقية القدرات تبقى على آخر حالة؛ الموظف لا يُحجب. مقياس `killswitch_stale_seconds`.
   - جلسات بلا ميتاداتا (قديمة) يسري عليها `global` فقط.

**قبول P0.6:** `docker stop gateway` خلال ≤ 25 ثانية بلا فقد (مع حمل صادر/وارد جارٍ)؛ `/readyz` يتحول 503 عند قتل Redis ويعود 200 بعد عودته؛ `/metrics` بلا token ⇒ 401؛ `promtool check rules` ناجح؛ اختبارات مفتاح الطوارئ (بـFakeWa وRedis حقيقي): كل صف من مصفوفة (global/tenant/channel × on/degraded/off × bot/human/marketing) يعطي القرار المتوقع، وزمن الانتشار من `PUBLISH` إلى الحجب **< 1 ثانية** (قِسه)، وسلوك انقطاع `redis-cache` كما هو أعلاه.

### P0.7 — حزمة `core/` المصغّرة (`api`) + لوحة التاجر `console/`

#### أ) الباك إند (`core/`، Python 3.12، FastAPI + Uvicorn)

- **الاتصال بقاعدة البيانات:** `psycopg` 3 (`prepare_threshold=None` لتوافق PgBouncer transaction pooling) عبر PgBouncer، مجمع اتصال 3–5 لكل عملية بلا overflow. مدخلان وحيدان: `tenant_tx(tenant_id)` و`system_tx()` (H2). اختبار بنيوي (`import-linter` أو مكافئه) يفرض أن لا وحدة خارج `core/app/db` تستورد مشغّل PostgreSQL.
- **المصادقة:** JWT **RS256** فقط (رفض `none` وHS*)، التحقق من `iss`, `aud`, `exp` (سماح 30 ثانية)، `kid` عبر JWKS (`JWKS_URL`) مع كاش وسقوط إلى `JWT_PUBLIC_KEY_PEM`؛ غياب كليهما = رفض إقلاع. الـclaims: `sub`, `tenant` (= `tenants.platform_ref`)، `role ∈ merchant_admin|staff|platform_admin`. يُحلّ `tenant` إلى `tenants.id` عبر دالة `SECURITY DEFINER` (تُضاف في ترحيل جديد إن لم تكن موجودة). `tenants.status='suspended'` ⇒ رفض. **لا يُقبل `tenant_id` من أي مكان آخر.** الاختبارات تولّد زوج مفاتيح RSA؛ وفي غير الإنتاج يصدر `python -m app.cli issue-dev-token` رموزاً للتجربة (مرفوض عند `ENV=production`).
- **ترحيل `0002_p0_api.sql` (أمامي، جديد):** جدول `audit_log` (RLS بـ`tenant_id`)، جدول `api_idempotency` (RLS)، فهرس فريد جزئي يمنع أكثر من قناة `whatsapp_baileys` واحدة نشطة لكل متجر في P0، ودوال `SECURITY DEFINER`: `app.resolve_tenant(platform_ref)`, `app.set_kill_switch(...)` (تفرض النطاق: المتجر يكتب فقط على نطاقه أو قنواته؛ `global` لـ`platform_admin` فقط)، `app.list_kill_switches(...)`. راجع `schema.sql` ومنحه (GRANTs) وأبقِ `kill_switches` مغلقاً على `sharwa_app` مباشرةً. إنشاء المتاجر في P0 عبر `python -m app.cli create-tenant` (اتصال أدوار الترحيل، ليس API عام).
- **الواجهات (`/v1`، JSON):**

  | الطريقة والمسار | الوصف |
  |---|---|
  | `GET /healthz`، `GET /readyz` (PG عبر PgBouncer + الـRedisين + وصول البوابة)، `GET /metrics` (محمي) | تشغيلية |
  | `GET /v1/me` | اسم المتجر والدور |
  | `GET /v1/channels` | قنوات المتجر مع حالتها |
  | `POST /v1/channels/whatsapp` (`Idempotency-Key`) | ينشئ `channel_accounts` (`session_id` عشوائي غير قابل للتخمين يولّده `core`، `engine='ai_core'`) ثم `POST /sessions` على البوابة بالميتاداتا الإضافية |
  | `GET /v1/channels/{id}` | حالة القناة (DB + `GET /sessions/:id/health`) |
  | `GET /v1/channels/{id}/qr` | رمز الربط (`Cache-Control: no-store`) |
  | `POST /v1/channels/{id}/reconnect` | مسموح فقط في حالات `conflict/disconnected/logged_out`؛ حدّ معدل 1/30ث لكل قناة |
  | `GET /v1/channels/{id}/delivery-stats` | عدّادات اليوم (استلام/إرسال/فشل/بانتظار) وآخر 20 فشلاً بأسبابها الفئوية — من مسار البوابة الإضافي `GET /sessions/:id/stats` (عدّادات `stats:{sid}:{YYYYMMDD}` بعمر 35 يوماً؛ بلا أرقام هواتف) |
  | `GET /v1/kill-switches` | الحالة الفعلية لكل قدرة معروضة (`*`, `ai_reply`, `marketing`) ومن غيّرها ومتى، مع إظهار إن كان سببها إدارة المنصة |
  | `PUT /v1/kill-switches/{capability}` | جسم `{scope:'tenant'|'channel_account', channel_id?, state, reason?}`؛ للدور `merchant_admin` فقط؛ يكتب DB ثم يحدّث نسخة Redis ويبثّ `ks:changes` **ثم يتحقق من النشر** |
  | `PUT /v1/admin/kill-switches/global/{capability}` | `platform_admin` فقط |

  كل تغيير حالة يكتب `audit_log` **في المعاملة نفسها**. مهمة خلفية داخل `api` (asyncio، لا `BackgroundTasks`) تعيد بناء نسخة Redis من PostgreSQL عند الإقلاع وكل 60 ثانية (لأن `redis-cache` قابلة للفقد).
- **غلاف الأخطاء (الآلي فقط):** `{"error":{"code":"…","request_id":"…","retry_after_s":?}}`. الرموز الثابتة: `UNAUTHENTICATED`, `TOKEN_EXPIRED`, `FORBIDDEN_ROLE`, `TENANT_SUSPENDED`, `NOT_FOUND`, `CHANNEL_ALREADY_EXISTS`, `GATEWAY_UNAVAILABLE`, `CHANNEL_CONFLICT`, `CHANNEL_LOGGED_OUT`, `CHANNEL_BANNED`, `RATE_LIMITED`, `SWITCH_LOCKED_BY_ADMIN`, `VALIDATION_FAILED`, `INTERNAL`. لا نص حر ولا استثناء خام ولا stack في أي استجابة. قناة/مورد متجر آخر ⇒ `NOT_FOUND` (لا `FORBIDDEN`).
- **الحماية:** مهلة 3 ثوانٍ لاستدعاءات البوابة مع قاطع دائرة بسيط، حدّ حجم الجسم، حدّ معدل لكل متجر على `redis-cache` (GET: fail-open، عمليات الكتابة: fail-closed)، ترويسات أمان، CORS مغلق (نفس الأصل)، سجلات H12، ومقاييس (`http_requests_total{route,status}`, `http_request_seconds`).
- **الاختبارات:** pytest على PostgreSQL/PgBouncer/Redis الحقيقية: العزل (متجران، ألف زوج عشوائي من (رمز متجر أ، مورد متجر ب) ⇒ صفر تسرّب و`NOT_FOUND` دائماً)، الأدوار، JWT (منتهٍ/موقّع خطأً/`alg` خاطئ/`kid` مجهول)، idempotency، حدود المعدل، اتساق `set_kill_switch` مع `app.effective_switch`، وكل رمز خطأ قابل للإنتاج.

#### ب) الفرونت إند (`console/`، React 18 + Vite 5 + TypeScript strict، RTL، عربي افتراضي وإنجليزي مكتمل)

- **الدخول:** يصل التاجر من المنصة برابط `/#token=<jwt>`؛ يُقرأ الرمز إلى **الذاكرة** (لا `localStorage`) ويُمسح من الشريط فوراً. انتهاء الصلاحية ⇒ خطأ بشري + زر «سجّل الدخول من جديد» (`SSO_LOGIN_URL`).
- **لافتة المرحلة (`CONSOLE_STAGE=preview`):** شريط دائم بلغة بسيطة: «نسخة تجريبية: يمكنك الآن ربط رقمك ومتابعة وصول الرسائل. الردود الآلية ستُفعَّل لاحقاً.» (تُزال بمتغير عند P1.)
- **الشاشات الثلاث** (كل واحدة تحقق U1–U9؛ نصوص المثال أدناه للنبرة والمراجعة، وتُحسَّن بالتنسيق مع المدقق):

  1. **«قنواتي» (ربط واتساب):**
     - *Empty State:* «لم تربط أي رقم بعد. اربط رقم واتساب متجرك ليصل عملاؤك إلى هنا.» + زر «اربط رقم واتساب».
     - *خطوات الربط:* تعليمات مرقّمة (افتح واتساب ← الأجهزة المرتبطة ← ربط جهاز ← امسح الرمز)، رمز الربط يتجدد تلقائياً مع عدّاد وعبارة «انتهى الرمز، جارٍ إنشاء رمز جديد» (استطلاع كل 3 ثوانٍ أثناء الربط و15 ثانية بعده، ويتوقف عند إخفاء التبويب).
     - *حالات القناة وأزرارها:* **متصلة** (الرقم مقنَّع، «آخر نشاط»)؛ **تعيد الاتصال** («انقطع الاتصال مؤقتاً وسنعيد المحاولة تلقائياً» بلا زر)؛ **اتصال آخر فعّال** (CONFLICT: «واتساب مفتوح لهذا الرقم على جهاز آخر. أغلقه ثم اضغط» ⇒ زر «أعد الاتصال»)؛ **خرجت من واتساب** (LOGGED_OUT ⇒ زر «اربط الرقم من جديد»)؛ **موقوفة من واتساب** (BANNED ⇒ شرح هادئ لما يعنيه + زر «تواصل مع الدعم»)؛ **البوابة غير متاحة** (زر «حاول مجدداً»).
     - تلميحات: على «رمز الربط»، «حالة القناة»، «آخر نشاط»، وزر «أعد الاتصال» (ماذا سيحدث بالضبط).
  2. **«مفتاح الطوارئ»:**
     - غرض الشاشة في جملة: «أوقف الردود الآلية أو الرسائل التسويقية فوراً إن حدث ما يقلقك. رسائل موظفيك لا تتأثر أبداً.»
     - ثلاثة مفاتيح: «كل الأتمتة» (`*`)، «الردود الآلية» (`ai_reply`)، «الرسائل التسويقية» (`marketing`) — لكل واحد تلميح يشرح أثره، وحالته الفعلية (تعمل / تعمل بشكل محدود / موقوفة) ومن غيّرها ومتى، وإن كانت موقوفة من **إدارة المنصة** يظهر «تم الإيقاف من إدارة المنصة» ويُعطَّل المفتاح مع شرح (وزر «تواصل مع الدعم»).
     - **تأكيد بعواقب تجارية** (U6) عند الإيقاف، وتأكيد نجاح عند إعادة التشغيل. لا يُعرَض نجاح إلا بعد أن يتحقق الـAPI من نشر الحالة.
     - لا Empty State هنا (الحالة الافتراضية «كل شيء يعمل»)؛ وثّق ذلك في `screenMeta.emptyState = { reason: 'always_populated' }` وفق واجهة النوع.
  3. **«صحة الإرسال والاستقبال»:**
     - أربع بطاقات (اليوم): وصلتك، أُرسل، فشل، بانتظار الإرسال — لكل بطاقة تلميح يشرحها بلغة عادية.
     - قائمة «آخر ما تعثّر» بأسباب بشرية (مثل «الرقم غير متصل حالياً» لا `session_down`) ولكل سبب زر إجراء مناسب.
     - *Empty State:* «لم تصلك رسائل بعد. أرسل رسالة من رقم آخر إلى رقم متجرك للتجربة.» + زر «تحقق من ربط القناة».
- **حالات عامة:** هياكل تحميل، حالة عدم اتصال بالإنترنت، تأكيدات نجاح، أخطاء وفق U3/U9 عبر `ErrorPanel` موحّد.
- **الاعتماديات (خفيفة):** React Router، TanStack Query، مكتبة اختبار (Vitest, Testing Library, jest-axe, Playwright). **لا مكتبة مكوّنات ضخمة**؛ مكوّنات بسيطة خاصة (`Field`, `Switch`, `ActionButton`, `Tooltip`, `ErrorPanel`, `EmptyState`, `ConfirmDialog`, `Skeleton`).
- **البناء:** `npm run build` ⇒ ملفات ساكنة يقدّمها `api`؛ حجم الحزمة الأولية < 250KB مضغوطة (اطبعه).

**قبول P0.7:** كل اختبارات `core` خضراء (مخرجات حقيقية)؛ `mypy --strict` وruff نظيفان؛ اختبارات الفرونت + Ux Gate (4.1) خضراء؛ Playwright يمرّ على تدفقات: (1) متجر بلا قنوات ⇒ Empty State ⇒ ربط ⇒ QR ⇒ «متصلة» (بـFakeWa)؛ (2) حقن CONFLICT ⇒ رسالة بشرية وزر «أعد الاتصال» يعمل؛ (3) قلب مفتاح الطوارئ ⇒ حجب فعلي في البوابة خلال < 1ث؛ (4) قتل البوابة ⇒ حالة «غير متاحة» بزر «حاول مجدداً» وبلا أي كود تقني؛ (5) رمز منتهٍ ⇒ زر الدخول من جديد. لقطات 360/1280 محفوظة في `docs/p0_screens/`.

### P0.8 — E2E والفوضى والتصليب والتوثيق

`tests/e2e/` بـ`docker-compose.test.yml` (الخدمات كلها حقيقية + MinIO + `fake-django` يتحقق من HMAC ويسجّل كل تسليم + البوابة بـ`FakeWaDriver`). كل سيناريو يطبع أرقامه ويفشل إن خالف العتبة:

| # | السيناريو | العتبة |
|---|---|---|
| E1 | نص وارد ⇒ ويبهوك موقّع لـfake-django ⇒ رد عبر `/send` ⇒ يصل FakeWa | مرة واحدة بالضبط؛ أحداث `queued→sent→delivered` |
| E2 | 1000 إعادة تسليم للرسالة نفسها | مدخل واحد + ويبهوك واحد |
| E3 | `kill -9` للبوابة بين `XADD` والتسليم، 20 مرة | **ضياع = 0**؛ أي تكرار ≤ عدد القتلات ومعرّفه ثابت |
| E4 | `kill -9` أثناء تنزيل 50MB | كائن واحد كامل، صفر multipart عالق |
| E5 | 50 رسالة صادرة + قتل في المنتصف | كلها تصل؛ التكرار مُقاس ومُعلَن |
| E6 | `kill -9` لـ`redis-durable` أثناء ضخ الوارد | ضياع = 0 (spool ثم تفريغ بالترتيب) |
| E7 | تفريغ/قتل `redis-cache` | الاستيعاب والإرسال مستمران؛ سلوك مفتاح الطوارئ حسب P0.6 |
| E8 | وسائط: ملف 50MB، و10 متزامنة | RSS < +100MB، والذروة < 600MB |
| E9 | أكواد الانقطاع (اتصال/صراع/خروج/حظر) | الحالات والأحداث الصحيحة؛ صراع ⇒ ≤ محاولة/60ث |
| E10 | نسختا بوابة على جلسة واحدة + قتل الحاملة | حاملة واحدة؛ الاستحواذ بعد المهلة؛ الكاتب القديم مرفوض |
| E11 | إشارة الاستلام البشري | تصدر لرسالة الهاتف فقط، وتشمل حالة السباق |
| E12 | `docker stop` تحت حمل | ≤ 25ث، لا ضياع |
| E13 | RLS عبر PgBouncer + عزل الـAPI | صفر تسرّب عبر 1000 زوج |
| E14 | انتشار مفتاح الطوارئ | < 1ث؛ الموظف لا يُحجب أبداً |
| E15 | تدفقات الفرونت (Playwright) | كما في P0.7 |
| E16 | ضخ 10,000 رسالة وارد خلال 60ث | `p99(ingest_ack_seconds) < 200ms`، ضياع 0، الـforwarder يستنزفها، الذاكرة مسطّحة |
| E17 | Hunt Gate + Ux Gate + lint + mypy + `promtool` + audits | كلها نظيفة |
| E18 | الانحدار: الاختبارات الـ14 الأصلية | 14/14 دون تعديل |

**التوثيق المطلوب:** إصلاح النقاط الثلاث القديمة في `README.md` (`X-Gateway-Key`→`X-API-Key`، مسارات `/api/`، الحقل `text`→`message_text`) وتوثيق الجديد؛ `docs/P0_RUNBOOK.md` (تشغيل/إيقاف/استعادة من قتل Redis أو PG/قراءة التنبيهات/استخدام مفتاح الطوارئ/تدوير الأسرار)؛ `docs/P0_DEPENDENCIES.md`؛ `docs/P0_DEVIATIONS.md`؛ `docs/P0_OPEN_QUESTIONS.md`؛ `docs/P0_FINDINGS.md`.

---

## 7. العقد المجمّد مع Django (لا يُكسر — H9)

المرجع الحاكم: `SHARWA_AI_PROJECT_SUMMARY.md` واختبارات `contract.test.js`/`webhook.test.js`. باختصار ما يجب أن يبقى حرفياً:
- مصادقة `/sessions/*` بترويسة `X-API-Key` (مقارنة `timingSafeEqual`)، و`/healthz` مفتوح.
- استجابة الحالة بمفاتيح **بالضبط** `status`, `qr_image_base64`, `connected_phone_number`.
- الويبهوك الصادر: مساره بالمسارين مع `/` النهائية، حقل النص `message_text`، توقيع HMAC بالترويسات القائمة، 5 محاولات backoff.
- أي حقل جديد في أي جسم طلب/استجابة **اختياري وإضافي**.
- سرّ `X-API-Key` وسرّ HMAC لا يُشاركان مع أي مشروع آخر ولا يُكتبان في سجل أو صورة.

## 8. المحظورات المطلقة

1. لمس مشروع `sharwa_saas` أو الاتصال بقاعدة بياناته بأي شكل.
2. أي عمل/ملف/مجلد/جدول لمراحل P1–P5 (H10).
3. تعديل `docs/reference/*` أو `docs/00–02_*.md` أو ملفات `PROMPT_*` القديمة (اقترح التعديل في `P0_DEVIATIONS.md`).
4. أي LLM أو مكتبة ذكاء اصطناعي (H6).
5. إضعاف/حذف/تخطي اختبار قائم؛ أو تعديل عتبة هذا الملف لتمرير اختبار.
6. حسابات أسعار أو عملات أو تحويلات (المنصة تملك المال — مبدأ معتمد) — لا محل لها في P0 أصلاً.
7. أسرار مكتوبة/مُلتزَمة، أو `.env` حقيقي في المستودع.
8. تنفيذ `git push --force` أو حذف تاريخ، أو تعديل إعدادات git. الالتزام (commit) بالرسائل الواضحة مسموح بعد نجاح كل خطوة P0.x.
9. طرح أسئلة تستوقفك: اسجل السؤال في `P0_OPEN_QUESTIONS.md` وتابع بالخيار الأكثر أماناً.

## 9. مصفوفة القبول (الكارثة ← الدليل)

| الكارثة | ما يثبتها في P0 |
|---|---|
| 4 (ويبهوك يعلّق على النموذج) | E1، E16 — الإقرار = كتابة Stream `p99 < 200ms`، لا مسار متزامن مع أي معالجة |
| 5 (ضياع/تكرار الرسائل) | E2، E3، E5، E6، اختبار dedupe و`sent_marker` |
| 16 (تعطل الخادم/الذاكرة) | E8، E12، حدود compose، `oom_score_adj`، heap cap |
| 17 (فقد الطوابير/الجدولة) | E3، E5، E6، AOF `always` (10 قتلات) والـspool |
| 21 (الوسائط والتحميل الزائد) | P0.3 + E4 + E8 |
| 19 (أساس مفتاح الطوارئ) | P0.6 + E14 |
| 1 (تسرّب متاجر — أساس) | E13، `schema_selftest` 34/34 |
| 6 (حظر الرقم — أساس) | E9 (CONFLICT/BANNED) + دلو الرموز |

## 10. تقرير التسليم — `docs/P0_REPORT.md` (بهذا القالب، بلا حذف بنود)

1. **الملخص:** جملتان + نتيجة كل خطوة P0.0…P0.8 (✔/✘).
2. **الملفات:** شجرة الملفات المضافة/المعدّلة مع سطر لكل مجموعة.
3. **الأدلة (لكل خطوة):** الأمر الحرفي + آخر 15–30 سطراً من مخرجه الحقيقي (Compose config، selftest، اختبارات كل حزمة بأعدادها، جداول E1–E18 بأرقامها الفعلية، منحنيات RSS، `docker stats`، `promtool`، `npm audit`/`pip-audit`، حجم حزمة الفرونت).
4. **مصفوفة القبول (القسم 9)** مملوءة بإشارات لأرقام الأدلة.
5. **الأرقام المقيسة:** ذاكرة جلسة واحدة و`MAX_SESSIONS` المعتمد، أزمنة `ingest_ack`، نافذة التكرار الصادر المقيسة، زمن انتشار مفتاح الطوارئ، مجموع `mem_limit`.
6. **الانحرافات والأسئلة المفتوحة:** ما في `P0_DEVIATIONS.md` و`P0_OPEN_QUESTIONS.md` مختصراً.
7. **إقرار ذاتي:** جدول H1–H15 وU1–U9: لكل مادة «ملتزم» + كيف يُثبت (سكربت/اختبار).
8. **قيود معروفة بصدق:** ما لم يُختبر أو ما تعذّر (بلا تجميل).
9. **الحالة:** حدّث `docs/PHASE_GATE.md` إلى السطر `P0: SUBMITTED (awaiting audit)` فقط.

## 11. الختام

بعد اكتمال P0.8 وتسليم التقرير واجتياز كل الفحوص: أرسل الرسالة الأخيرة بالعبارة الحرفية:

`P0 COMPLETE — STOPPING. AWAITING AUDIT. NO P1 WORK STARTED.`

ثم توقف تماماً. لا P1. لا مبادرات. المدقق سيعيد تشغيل الأوامر بنفسه؛ أي فرق بين تقريرك والواقع = رفض.
