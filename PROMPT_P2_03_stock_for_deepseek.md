# البرومبت المعماري الثالث عشر — P2.3: وكيل الحجز والتخصيص الذري (Back-In-Stock)

**من:** المهندس المعماري
**إلى:** المنفّذ (DeepSeek داخل Cline)
**المشروع:** `sharwa_ai` — `C:\sharwaai\sharwa-ai`
**المرحلة السابقة:** P2.2 — **APPROVED**. `262 passed` نقية، و**الحزمة الموسومة صارت تعمل: من 9 ناجحة إلى 212** على قاعدة حقيقية شغّلتُها أنا.
**نطاق هذه الدفعة:** أن تُحجَز الوحدة الواحدة لعميل واحد، ولو طلبها عشرون في اللحظة نفسها.

---

## §0 — خبران قبل أي شيء: واحد يسرّك وواحد يوقفك

### 0.1 منطق التخصيص الذري **صحيح بالفعل** — أثبتُّه بنفسي

أمر المالك بتصميم اختبار سباق «20 منتظراً ووحدة واحدة = حجز واحد». **شغّلتُه، لا صمّمتُه فقط**، على `app.allocate_stock_holds` الموجودة في `0001` منذ P0، بعشرين خيطاً يلتقون على حاجز (`threading.Barrier`) ثم ينادون الدالة في اللحظة نفسها، على PostgreSQL 16 حقيقي:

```
20 concurrent callers, available=1
  holds created across all callers : 1
  errors                           : none
  stock_holds rows status=held     : 1
  total held qty                   : 1
  waitlist statuses                : [('held', 1), ('waiting', 19)]
  NO OVERSELLING -> PASS

20 concurrent callers, available=3
  holds created : 3 · qty: 3 · waitlist: [('held', 3), ('waiting', 17)] · errors: none
  NO OVERSELLING -> PASS
```

ولماذا نجحت، بالسطور — وهذا ما عليك أن تحافظ عليه ولا تلمسه:

```sql
PERFORM 1 FROM stock_levels
 WHERE tenant_id = v_tenant AND platform_variant_id = p_variant FOR UPDATE;   -- ① نقطة تسلسل لكل variant

SELECT COALESCE(sum(qty),0) INTO v_active FROM stock_holds
 WHERE ... status = 'held' AND expires_at > now();
v_free := GREATEST(p_available - v_active, 0);                                 -- ② لا بيع زائد، حساباً لا ظنّاً

SELECT id FROM waitlist_entries WHERE ... status = 'waiting'
 ORDER BY created_at, id FOR UPDATE SKIP LOCKED LIMIT v_free;                  -- ③ عدلٌ بلا اختيار مزدوج
```

① يجعل كل المنادين على نفس الـvariant يتسلسلون على صفّ واحد؛ ② يحسب المتاح الحرّ من الحجوزات الحيّة لا من ظنّ؛ ③ يختار المنتظرين بترتيب الوصول ويتخطّى المقفول. **وبلا أي قفل Redis** — كما أمر المالك، ولسبب جوهري: قفل Redis ليس في المعاملة نفسها التي تكتب الصفوف، فانقضاء مهلته أو فشل شبكة يفتح نافذة بيع زائد لا تراها القاعدة. القفل الصحيح هو القفل الذي يحمل البيانات.

**فأنت لا تبني منطق التخصيص. تبني ما حوله: المنتظر، والإشعار، والانقضاء، والتحويل.** وأي محاولة لإعادة كتابة `app.allocate_stock_holds` مرفوضة.

### 0.2 عيبان حرجان أوقفا منتجك، وأحدهما كشفه عملك

**F-P2-03 — واجهة ربط رقم واتساب كلّها 404.** سألتُ وثيقة OpenAPI التي يُصدرها التطبيق المبنيّ عن نفسه:

```
channels paths present -> NONE
/v1/me present        -> False
```

و`app/main.py` يستورد خمسة موجِّهات ويركّب خمسة، **و`routes_channels` ليس فيها**. فـ`GET /v1/me` و`GET /v1/channels` و`POST /v1/channels/whatsapp` و`/qr` و`/reconnect` و`/delivery-stats` كلها 404. **التاجر لا يستطيع ربط رقم واتساب إطلاقاً** — وهي نقطة الدخول للمنتج كلّه.

وهو الفصل الثالث من قصّة واحدة: F-P1-07 (تسعة أسماء غير معرَّفة) · F-P1-11 (`resolve_staff_member` تُرجع `None` دائماً) · F-P2-03 (الموجِّه غير مُركَّب). **وما كشفه إلا إصلاحك لـF-P2-02**: الـ22 فشلاً في `test_routes_channels.py` هي أول مرّة تُنفَّذ تلك الاختبارات في تاريخ المشروع.

**F-P2-04 — كل عنوان يُقبَل من الـgazetteer يُسقط المعاملة.** أثبتُّه بنداء مستودعك على القاعدة:

```
accepted + gazetteer_centroid + location=None  ->  CheckViolation: violates "address_resolutions_check"
accepted + pin + نقطة حقيقية                  ->  stored -> ('pin','POINT(44.2 15.3)')
```

و`grep ST_Centroid|centroid` على الشجرة كلها يعطي **نصّ التوثيق واسم المصدر فقط** — لا سطر يحسبه، و`search_gazetteer` لا تختار `geom` أصلاً. فالمصدر `gazetteer_centroid` مُعلَن ومُقرَّر ولا يمكن أن يُكتب.

---

## §1 — الخطوة صفر

### 1.1 F-P2-03 — ركّب الموجِّه، وابنِ الحارس الذي كان ناقصاً

1. في `app/main.py`: استورد `routes_channels` وركّبه مع الخمسة.
2. **S18 — الموجِّه المُعلَن يجب أن يكون مُركَّباً:** كل وحدة تحت `app/api/` تعرّف متغيّراً اسمه `router` **يجب** أن تكون مستوردة في `main.py` **و** ممرَّرة إلى `include_router`. فحص AST بسيط على `main.py` مقابل جرد وحدات `app/api/`. أي موجِّه معرَّف وغير مُركَّب ⇒ مخالفة.
3. **وأصلِح S13، فهي السبب في أن العيب مرّ:** ترويستها تقول إنها تقارن العقد بـ«المسارات الفعلية `@router` في `app/api/**`» — أي بما **يُعلَن في الملفات** لا بما **يخدمه التطبيق**. اجعلها تقارن `console_api.lock.json` بوثيقة **OpenAPI للتطبيق المبنيّ** (`create_app()` ثم `app.openapi()["paths"]`)، فيصير انحراف العقد وانعدام التركيب مخالفةً واحدة بالأداة نفسها. وإن احتاج ذلك بناء التطبيق داخل البوابة فليكن في بند موسوم يُتخطّى عند غياب الإعداد، **وتُعلَن حدوده صراحةً** لا تُخفى.
4. **الإثبات:** أزِل تركيب `routes_channels` مؤقّتاً ⇒ البوابة تعدّ مخالفة S18 (وS13 إن بنت التطبيق)؛ ثم أعِده ⇒ صفر. **وأرفِق جرد مسارات `/v1` من وثيقة OpenAPI بعد الإصلاح** — فيها `/v1/me` و`/v1/channels` ظاهرتين.

### 1.2 F-P2-04 — احسب مركز الشكل، أو لا تقبل

المصدر `gazetteer_centroid` بلا حساب مركز ليس مصدراً. أمامك طريقان، **واختر الأول**:

- **الأول (المطلوب):** في `search_gazetteer` اختر مركز الشكل مع الصفّ — `ST_Y(ST_Centroid(geom)) AS lat, ST_X(ST_Centroid(geom)) AS lng` — ومرّره في `AddressCandidate` كحقلين جديدين، فيصير لكل مرشّح نقطة حقيقية. ثم في `resolve_and_persist`: `location = pin if decision.source == "pin" else centroid_of(decision.gazetteer_id)`.
- الثاني (إن تعذّر الأول لسبب تكتبه): احذف `accepted/gazetteer_centroid` من جدول القرار فتصير القاعدة الثالثة `confirm_with_customer` دائماً — **قرار سلوكيّ يغيّر تجربة العميل، فلا تتّخذه بلا إعلان**.

**وأصلِح S17-c معه:** البند يفرض اليوم ألّا يُكتب `accepted` بـ`location=None` **في `repos_geo`** — والعيب وقع في `address.py` قبل أن يصل المستودع، فالبند لا يراه. وسّعه ليفحص **كل** نداء لـ`insert_address_resolution` و`persist_decision` في `app/**`: إن كان `decision` حرفياً `"accepted"` فـ`location=` يجب أن يكون متغيّراً غير `None`؛ وإن كان محسوباً، فلا تمرير `None` حرفياً.

**والاختبارات الثلاثة الموسومة `@pytest.mark.db` من §9 في برومبت P2.2 — اكتبها الآن**، فهي لم تُكتب ولم يُعلَن نقصها:
1. `app.point_governorate` تُرجع المحافظة لنقطة داخلها و`NULL` لنقطة خارجها.
2. **`insert_address_resolution` بـ`accepted` و`location=None` يُرفَض بـ`CheckViolation`** — وهذا الاختبار بعينه كان سيمسك F-P2-04.
3. سياسة `gazetteer_read` تُظهر الصفوف المشتركة وصفوف التاجر وتُخفي صفوف تاجر آخر.

### 1.3 F-P2-05 و N1 و N2 من تدقيق P2.2

- **F-P2-05 [منخفض]:** `ST_GeomFromText` تستقبل EWKT فتُصدر `WARNING: OGC WKT expected`. استعمل `ST_GeomFromEWKT` أو أسقط البادئة. (وتصحيحٌ منّي: توقّعتُ فشلاً فاختبرتُ فكنتُ مخطئاً — تحذير لا انهيار.)
- **N1 [متوسط، ويخصّ H65]:** `search_gazetteer` تنفّذ `UNION ALL ... LIMIT` **بلا `ORDER BY`**، وفرز Python مستقرّ، فالتعادل يُفَكّ بترتيب وصول SQL ⇒ قائمة `disambiguate` قد تختلف بين تشغيلين. أضِف `ORDER BY` حتميّاً في SQL (التامّة أولاً ثم `id`) وفكّ تعادل صريحاً بـ`gazetteer_id` في الفرز — نفس القاعدة التي فُرضت على RRF في P1.4. واكتب اختباراً يثبت أن مرشّحَين متعادلَين يُعطيان **نفس** الترتيب في عشر محاولات.
- **N2 [منخفض]:** تحقّق من `DECISIONS`/`SOURCES`/`REASON_CODES` في `__post_init__` (يصير `reason_code` تسمية مقياس فكاردينالياته تُفرَض بالبناء)، واسقف الثقة المشتقّة من الـgazetteer دون 1.0 ليبقى الفرق بينها وبين الدبّوس مرئياً في البيانات.

### 1.4 لقطة git

```bash
git add . && git commit -m "chore: checkpoint before P2.3"
git log --oneline -1 && git status --porcelain
```

---

## §2 — المواد الدستورية (H69–H73)

- **H69 — القفل الذي يحمل البيانات هو القفل الوحيد.** تخصيص المخزون يجري في معاملة PostgreSQL واحدة بأقفال صفوف (`FOR UPDATE` / `FOR UPDATE SKIP LOCKED`). **يُمنع قفل Redis أو أي قفل خارج المعاملة** في مسار المخزون: قفل خارج المعاملة ينقضي أو تفشل شبكته، فيفتح نافذة بيع زائد لا تراها القاعدة.
- **H70 — المتاح يُقاس ولا يُستنتج.** المتاح الحرّ = `observed_available − sum(qty) للحجوزات الحيّة`، محسوباً في المعاملة نفسها. لا ذاكرة مؤقّتة، ولا قيمة محفوظة في العملية، ولا رقم من مخرج سابق.
- **H71 — الحجز مؤقّت بطبعه.** لكل حجز `expires_at` من مهلة مكتوبة، ومنقضيه يُحرَّر بمَكنسة حتميّة فيُرقّى التالي في الصفّ. حجزٌ بلا انقضاء هو مخزون مفقود.
- **H72 — العدل بترتيب الوصول.** الترقية بـ`ORDER BY created_at, id` حصراً. لا أولوية بالسلّة ولا بالمبلغ ولا بشيء — وأي ترتيب آخر قرار مالك لا قرار برمجة.
- **H73 — المنصّة هي سلطة المخزون.** `observed_available` **ملاحظة** مؤرّخة من `sharwa_saas`، لا حقيقة نملكها. ولا نُعلم عميلاً بتوفّر إلا بناءً على ملاحظة لم يمضِ عليها أكثر من `STOCK_OBSERVATION_MAX_AGE_S`؛ وما قدُم ⇒ لا إشعار، لا تخمين.

---

## §3 — البنية في `0001`. استعملها كما هي

**لا جدول جديد ولا ترحيل** (الدفعة الثالثة على التوالي):

```sql
CREATE TABLE waitlist_entries (
  id uuid PK, tenant_id uuid NOT NULL, customer_id uuid NOT NULL,
  conversation_id uuid, platform_variant_id text NOT NULL,
  status text NOT NULL DEFAULT 'waiting'
         CHECK (status IN ('waiting','held','converted','cancelled','expired')),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE stock_levels (                 -- نقطة التسلسل لكل variant
  tenant_id uuid, platform_variant_id text,
  observed_available integer NOT NULL DEFAULT 0 CHECK (observed_available >= 0),
  observed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, platform_variant_id)
);
CREATE TABLE stock_holds (
  id uuid PK, tenant_id uuid NOT NULL, waitlist_entry_id uuid NOT NULL,
  platform_variant_id text NOT NULL, qty integer NOT NULL DEFAULT 1 CHECK (qty > 0),
  status text NOT NULL DEFAULT 'held' CHECK (status IN ('held','converted','expired','released')),
  expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);

app.allocate_stock_holds(p_variant, p_available, p_ttl)  -- ✔ مُثبَتة بالسباق (§0.1)
app.expire_stock_holds(p_variant)                        -- تُحرّر المنقضي ثم يُعاد التخصيص
```

ثلاث نتائج:

1. **`CHECK (observed_available >= 0)`** يعني أن أي حساب يُنتج سالباً ينفجر عند الكتابة. لا تلتفّ عليه بـ`GREATEST` في Python — الدالة في `0001` تفعل ذلك في موضعه الصحيح.
2. **`waitlist_entries` بلا قيد وحدانية** على `(tenant_id, customer_id, platform_variant_id)`. فلا شيء في القاعدة يمنع صفّين لنفس العميل على نفس الـvariant ⇒ **عميل واحد يحتلّ موضعين في الصفّ ويأخذ حجزين**. امنعه في الكود بقراءة قبل الكتابة في المعاملة نفسها، **واكتبه بنداً في `docs/P2_FINDINGS.md`** كقيد قاعدة ناقص لأقرّه أنا — **ولا تُنشئ فهرساً ولا ترحيلاً من عندك**.
3. **`status='cancelled'`** موجود في القيد ولا يستعمله شيء بعد: هو مسار «أزِلني من قائمة الانتظار» الذي تبنيه.

---

## §4 — ما تبنيه

| الملف | مسؤوليته |
|---|---|
| `core/app/db/repos_stock.py` | كل SQL المخزون: تسجيل منتظر · نداء `app.allocate_stock_holds` و`app.expire_stock_holds` · قراءة حالة منتظر · إلغاء · تحويل حجز |
| `core/app/workers/stock.py` | **المنسّق**، بثلاث مراحل كـ`turn.py`: قراءة الملاحظة من المنصّة **خارج المعاملة** (H40) ⇒ تخصيص في معاملة قصيرة ⇒ أحداث وإشعارات |
| `core/app/tools/join_waitlist.py` | أداة ثالثة في السجل (H50: بلا قاعدة وبلا شبكة): تستخرج الـvariant المطلوب وتُعيد قراراً |
| `core/tests/test_stock_allocation.py` | **الموسومة `@pytest.mark.db`** — اختبارات السباق (§6) |
| `core/tests/test_stock_worker.py` | نقية بمزدوجات مسجِّلة |

**تُعدَّل:** `compose.py` (دالة سادسة مصرَّح بها) · `templates.py` (قوالب §5) · `llm/router.py` (نيّة سابعة `stock_waitlist`) · `turn.py` · `realtime.py` (خيط المَكنسة) · `tools/registry.py` · `config.py` · `metrics.py` · `alerts.yml` · `static_gate.py` (S18 + S19 + إصلاح S13 وS17-c).

**ممنوع لمسه:** `app.allocate_stock_holds` و`app.expire_stock_holds` و`0001` وأي ترحيل · `app/text/arabic.py` · `PHASE_GATE.md`.

---

## §5 — المسار، ومصيدته الكبرى

### 5.1 الانضمام إلى قائمة الانتظار

نيّة `stock_waitlist` ⇒ الأداة تستخرج الـvariant من آخر بطاقات معروضة (`slots.last_shown_product_ids` موجود في القائمة البيضاء) أو من بحث صريح ⇒ المنسّق يسجّل `waitlist_entries` بـ`status='waiting'` بعد التحقّق من **عدم وجود صفّ حيّ للعميل نفسه** على الـvariant نفسه (§3 بند 2) ⇒ قالب تأكيد.

### 5.2 الإشعار عند التوفّر — وهنا المصيدة

خيط مَكنسة في `realtime.py` بدورة مكتوبة:

1. **خارج أي معاملة (H40):** اقرأ ملاحظة المخزون من المنصّة عبر `CommercePort` لكل variant له منتظرون.
2. **تحقّق من عمر الملاحظة (H73):** أقدم من `STOCK_OBSERVATION_MAX_AGE_S` ⇒ **لا تُخصّص ولا تُشعر**. ارفع عدّاداً وامضِ.
3. **معاملة قصيرة:** `app.expire_stock_holds(variant)` ثم `app.allocate_stock_holds(variant, available, ttl)`. الدالة تُرجع الحجوزات المُنشأة.
4. **لكل حجز:** صفّ `outbox` واحد عبر `verify.insert_verified_outbox` كالمعتاد (فالنصّ يمرّ على المدقّق — H46).

**والمصيدة:** الخطوة 4 تكتب إلى `outbox`، والخطوة 3 أنشأت الحجز في معاملة. **إن كتبتَ الإشعار في معاملة الخطوة 3 نفسها** فأنت تُطيل معاملةً تحمل قفل `stock_levels` — وهي نقطة التسلسل لكل المنادين على الـvariant. فكل إشعار يُبطئ كل حجز. **وإن كتبتَه في معاملة ثانية** فقد تنجح الحجوزات ويفشل الإشعار، فيبقى حجزٌ لعميل لا يعرف به حتى ينقضي.

**القرار المعماري، وهو ملزم:** الحجز والإشعار في **معاملة واحدة قصيرة** (الخطوتان 3 و4 معاً)، **ونداء المنصّة وحده خارجها** (الخطوة 1). لأن ذرّية «حُجِز ⇒ أُعلِم» أهمّ من زمن القفل: حجزٌ صامت خسارة مزدوجة (العميل لا يعلم، والمخزون مقفول). وزمن القفل يبقى قصيراً لأن الكتابة في `outbox` إدراج واحد بلا شبكة — **ولا نداء شبكة داخل تلك المعاملة أبداً** (H40 تبقى سارية بحرفها).

### 5.3 التحويل والإلغاء والانقضاء

- يردّ العميل بالقبول ⇒ `stock_holds.status='converted'` و`waitlist_entries.status='converted'` في معاملة واحدة، ويُسلَّم إلى موظف لإكمال الطلب (لا مسار دفع في هذه الدفعة).
- يطلب الإلغاء ⇒ `cancelled` للمنتظر، و`released` للحجز إن كان قائماً، ثم **إعادة تخصيص فورية** فيُرقّى التالي.
- انقضاء المهلة ⇒ المَكنسة تُحرّر وتُرقّي. **وأعلِم العميل الذي انقضى حجزه** — وإلا انتظر إلى الأبد.

### 5.4 الردّ

دالة تركيب **سادسة مصرَّح بها**: `compose_stock_notice(kind, title)` — عنوان المنتج من الكتالوج حرفياً (H35: **بلا سعر وبلا كمية متاحة**؛ «توفّر» لا «بقيت 3 قطع»، فالكمية ملاحظة قد تقدُم وتُغري بوعد لا نملكه). قوالب: `stock_joined` · `stock_available` · `stock_hold_expired` · `stock_cancelled` · `stock_already_waiting` · `stock_unavailable`.

---

## §6 — الاختبارات: اختبار السباق هو قلب الدفعة

### 6.1 الموسومة `@pytest.mark.db` — وهذه المرّة تُكتب فعلاً

اكتب `test_stock_allocation.py` بنفس شكل ما شغّلتُه أنا (حاجز `threading.Barrier` يضمن التقاء الخيوط في اللحظة نفسها — لا `sleep`، ولا تشغيل متسلسل يتظاهر بالتزامن):

| # | السيناريو | التوكيد |
|---|---|---|
| 1 | **20 منتظراً · available=1** | حجز **واحد** · `qty=1` · `held 1, waiting 19` · صفر استثناء |
| 2 | **20 منتظراً · available=3** | **ثلاثة** بالضبط · `held 3, waiting 17` |
| 3 | available=0 | صفر حجز، ولا استثناء |
| 4 | **ترتيب العدل** | الحجز يذهب لأقدم `created_at` — لا لأي آخر (H72) |
| 5 | **إعادة النداء** | نداء ثانٍ بنفس `available` **لا يُنشئ حجزاً إضافياً** (الحجوزات الحيّة تُخصَم — H70) |
| 6 | الانقضاء | `expire_stock_holds` ثم إعادة تخصيص ⇒ التالي يُرقّى ولا يتضاعف المجموع |
| 7 | عزل التينانت | منتظرو تاجر آخر لا يُخصَّص لهم شيء تحت سياق تاجرنا (H2) |
| 8 | **عميل مزدوج** | صفّان لنفس العميل على نفس الـvariant ⇒ المنسّق يمنع الثاني (§3 بند 2) |

**وأرفِق مخرج كل واحد كما هو.** وإن تعذّر تشغيلها عندك فأعلِن القيد كما فعلتَ مرّتين — سأشغّلها أنا؛ وقد شغّلتُ 1 و2 سلفاً فأعرف أنهما يمرّان.

### 6.2 S19 — لا قفل خارج المعاملة في مسار المخزون (H69)

بند بوابة: أي وحدة تحت `app/workers/stock.py` أو `app/db/repos_stock.py` أو `app/tools/join_waitlist.py` يُمنع أن تسمّي `lock` أو `setnx` أو `nx=True` أو تستورد `redis`. وأي نداء شبكة (`httpx`، `CommercePort`) داخل كتلة `tenant_tx`/`system_tx` في `stock.py` ⇒ مخالفة (H40 مفروضة بنيوياً على هذا المسار).

**الإثبات:** مخالفتان مصطنعتان ⇒ `2 violation(s)`، ثم صفر.

### 6.3 النقية

`pytest tests -q` صفر أحمر، والعدد أكبر من `262`.

---

## §7 — الإعدادات (بافتراضات مكتوبة)

| المفتاح | الافتراض | ملاحظة |
|---|---|---|
| `STOCK_HOLD_TTL_S` | `3600` | مهلة الحجز (H71) |
| `STOCK_OBSERVATION_MAX_AGE_S` | `300` | أقدم من ذلك ⇒ لا إشعار (H73) |
| `STOCK_SWEEP_INTERVAL_S` | `60` | دورة المَكنسة |
| `STOCK_MAX_VARIANTS_PER_CYCLE` | `50` | سقف تشغيلي |
| `STOCK_MAX_WAITLIST_PER_CUSTOMER` | `10` | **ويُفرَض فعلاً** — لا سقف مكتوب بلا إنفاذ (درس N1 في P1.7) |

مقاييس: `waitlist_entries_total{action}` · `stock_holds_total{action}` · `stock_allocation_duration_seconds` · `stock_observation_stale_total` · `stock_sweep_runs_total`.
تنبيهات: `StockObservationStale` (معدّل التقادم مرتفع ⇒ المنصّة متأخّرة) · `StockHoldsExpiringUnconverted` (نسبة انقضاء عالية ⇒ الإشعار لا يصل أو المهلة قصيرة) · `StockSweepStalled` (المَكنسة توقّفت).

---

## §8 — التسليم

القواعد نفسها. **ما يُرفَق حرفياً:**

- `git log --oneline -1` و`git status --porcelain`.
- **جرد مسارات `/v1` من وثيقة OpenAPI بعد تركيب `routes_channels`** — فيها `/v1/me` و`/v1/channels`.
- إثبات S18 بإزالة التركيب مؤقّتاً ⇒ مخالفة، ثم صفر.
- إثبات F-P2-04: `accepted/gazetteer_centroid` صار يُكتب بنقطة حقيقية — بمخرج استعلام يُظهر `ST_AsText(location)`.
- **مخرج اختبارات السباق** (أو القيد مُعلَناً).
- `pytest tests -q` كاملاً، و`-m db` كاملاً إن توفّرت قاعدة.
- جملة صريحة: **هل بقي أي قفل خارج المعاملة في مسار المخزون؟** بمخرج البوابة.
- جملة صريحة: **هل أُنشئ جدول أو ترحيل؟** (المتوقَّع: لا.)
- جملة صريحة: **هل كُتبت الاختبارات الثلاثة الموسومة للعنوان** (§1.2)؟ بأسمائها.

**اختم بـ:** `P2.3 COMPLETE — STOPPING. AWAITING AUDIT. NO P2.4 WORK STARTED.`

وقبله: `F-P2-03 fixed (channels router mounted, S18 guards it) · F-P2-04 fixed (real centroid) · S13 now compares the served app · no-overselling proven under 20-way concurrency · no Redis locks (S19) · no new table · gate 0 violations at <ZULU>`

---

## §9 — أسئلة مفتوحة

- **OQ-P2-08:** قيد وحدانية ناقص على `waitlist_entries(tenant_id, customer_id, platform_variant_id)` — سجّله ولا تُنشئه؛ القرار لي.
- **OQ-P2-09:** `STOCK_HOLD_TTL_S = 3600` تقدير لا قياس؛ تُعاير على سلوك حقيقي.
- **OQ-P2-10:** هل يُعرَض للعميل موضعه في الصفّ («أنت الثالث»)؟ **التنفيذ الحالي: لا** — رقمٌ يتغيّر بالإلغاءات ويصنع توقّعاً لا نضمنه.
- **OQ-P2-11:** مسار الدفع للحجز المُحوَّل — خارج النطاق، والتحويل يُسلَّم إلى موظف.

---

**ابدأ بـ§1 بالترتيب. ولا تكتب سطراً في `app/workers/stock.py` قبل أن تُظهر وثيقة OpenAPI مسار `/v1/channels`.**
