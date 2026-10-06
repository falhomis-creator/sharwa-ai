# سجل تنفيذ P4

## T1 — جرد الروابط

### الحالة قبل البدء (VERIFIED)
- `git branch --show-current` → `main`
- `git status --short` → `?? dump.rdb` (فقط)
- `git log --oneline -3` → أعلاها `c06d297 docs(p4): Speckit pack for P4 Links & AI Integration`
- لا `.git/index.lock`؛ كل أوامر git نجحت من أول مرة.

### أ) عميل التجارة — core/app/channels/commerce_client.py (VERIFIED)

جدول استدعاءات HTTP (كلها GET عبر `self._client.get(path, params=params)`):

| الدالة | المسار | المعاملات المرسلة | طريقة HTTP | رقم السطر (def / المسار) |
|---|---|---|---|---|
| `get_changes` | `/changes` | `tenant_ref` + `since` (اختياري) | GET | 71 / 75 |
| `get_snapshot` | `/snapshot` | `tenant_ref` + `page` (اختياري) | GET | 77 / 81 |
| `lookup_order` | `/order_lookup` | `tenant_ref`, `order_ref`, `path`, `phones` (بـ `,`) | GET | 83 / 95 |
| `get_stock_observation` | `/stock_observation` | `tenant_ref`, `variant` (= platform_variant_id) | GET | 111 / 122 |

#### غياب التوثيق/التوقيع (VERIFIED — مخرج فارغ)
الأمر:
```
findstr /n /i "header auth sign hmac token secret" core\app\channels\commerce_client.py
```
المخرج حرفياً: **فارغ** (exit code 1 = لا تطابقات). لا Authorization ولا توقيع ولا HMAC ولا token ولا secret في الملف كله.

سطر بناء العميل حرفياً (رقم 54):
```
        self._client = httpx.Client(base_url=base_url, timeout=timeout_s)
```
يُنشأ بـ `base_url` و`timeout` فقط — بلا `headers=` ولا `auth=`.

#### القاطع والمهلة وأصناف الأخطاء (VERIFIED)
- القاطع `_Breaker`: `fail_threshold=5` و`reset_s=30.0` — السطر 53.
- المهلة: `timeout_s: float = 3.0` افتراضياً — السطر 52.
- `CommerceUnavailableError` (سطر 19): خطأ عابر (5xx / timeout / اتصال).
- `CommerceClientError` (سطر 23): خطأ دائم (non-2xx ليس 5xx).
- `lookup_order` و`get_stock_observation` يعيدان `None` عند 404 (H53) — السطور 99–101 و126–128.

### ب) الربط بالإعداد (VERIFIED)

مخرج `findstr /n "commerce_base_url commerce_timeout_s" core\app\workers\config.py core\app\workers\realtime.py` حرفياً:
```
core\app\workers\config.py:366:    # P1.4 catalog reconciliation (PROMPT §5.4/§5.5). commerce_base_url is OPTIONAL
core\app\workers\config.py:370:    commerce_base_url: str = ""
core\app\workers\config.py:371:    commerce_timeout_s: float = 3.0
core\app\workers\config.py:704:            commerce_base_url=_optional("COMMERCE_BASE_URL", ""),
core\app\workers\config.py:705:            commerce_timeout_s=float(_optional("COMMERCE_TIMEOUT_S", "3.0")),
core\app\workers\realtime.py:186:        if self.settings.commerce_base_url:
core\app\workers\realtime.py:188:                CommerceClient(self.settings.commerce_base_url, timeout_s=self.settings.commerce_timeout_s)
```

- يُقرأ `COMMERCE_BASE_URL` عبر `_optional("COMMERCE_BASE_URL", "")` — `workers/config.py:704`.
- الافتراضي: `""` — `workers/config.py:370` (و`commerce_timeout_s` افتراضي `3.0` — `371/705`).
- عند كونه فارغاً: `realtime.py:186` الشرط يفشل فلا يُنشأ `CommerceClient`، ويُسجَّل `catalog.reconcile.disabled` بالسبب `COMMERCE_BASE_URL empty` — الأسطر 190–194.

### ج) تدفق المزامنة — core/app/workers/catalog.py (VERIFIED)

- الدوال العامة: دالة واحدة فقط `reconcile_once(port, *, settings, seen_unknown_types) -> str` — السطور 28–85 (الملف 85 سطراً، لا دوال عامة أخرى).
- الدورة تُدار من `workers/realtime.py` عند السطر 795 (`catalog.reconcile_once(...)`)، داخل خيط دوري يستيقظ كل `CATALOG_RECONCILE_INTERVAL_S`.

#### متى تُستدعى get_changes ومتى get_snapshot
- `get_changes` تُستدعى كل دورة لكل مستأجر: `catalog.py:56` — `events, next_cursor = port.get_changes(platform_ref, cursor)`.
- `get_snapshot` **غير مستدعاة إطلاقاً في مسار التزامن** ولا في أي مكان إنتاجي في `core/app`. الدليل (VERIFIED): `catalog.py` لا يذكر snapshot؛ `realtime.py` لا يستدعيه؛ `get_snapshot` معرَّفة في `commerce_client.py:77` و`adapter.py:21` و`port.py:38`، والاستدعاء الوحيد لها في الكود كله هو في الاختبارات (`tests/test_catalog.py:188` عبر `fake.get_snapshot`). أي أن `/snapshot` مكشوف في العميل والمنفذ والمحوّل لكنه غير مستهلك بعد (جاهز للبذر، بلا ندّاء حالي).

#### حفظ المؤشر (catalog_sync_cursor)
- بعد نجاح `get_changes` داخل `tenant_tx`: `apply_catalog_events` ثم `set_catalog_sync_cursor(conn, tenant_id, cursor=next_cursor, last_reconcile_ok=now)` — `catalog.py:66–74`.
- `set_catalog_sync_cursor` (`repos_catalog.py:688–699`) يكتب في جدول `catalog_sync_cursor` الأعمدة `(tenant_id, cursor, last_event_at, last_reconcile_ok)` بـ `INSERT ... ON CONFLICT (tenant_id) DO UPDATE` (يُحدِّث `cursor`, `last_event_at=now()`, `last_reconcile_ok`).
- `next_cursor` من حقل الاستجابة `payload["next_cursor"]` (`adapter.py:19`) ويُعاد تمريره `since` في الدورة التالية (`commerce_client.py:73–74`).

#### ما يُكتب في القاعدة لكل حدث (الجداول والأعمدة) — `repos_catalog.py`
| نوع الحدث | الجدول | الأعمدة | السطر |
|---|---|---|---|
| `product.upserted` | `catalog_products` | tenant_id, platform_product_id, title, description, category, attributes, active, source_version | 269–283 |
| `product.deleted` | `catalog_products` (تحديث) | active=false, source_version, updated_at (حذف ناعم) | 303–307 |
| `variant.upserted` | `catalog_variants` | tenant_id, product_id, platform_variant_id, sku, options, price_hint_minor, currency, source_version | 334–341 |
| `variant.stock` | `stock_levels` | tenant_id, platform_variant_id, observed_available, source_version | 367–375 |
| `variant.stock` (إضافي) | `catalog_variants` (تحديث) | stock_hint = qty (مؤشر ترتيب فقط، لا يُعرض) | 377–381 |
| `kb.upserted` | `kb_chunks` | tenant_id, source, content, source_version | 400–405 |

حارس `source_version` (H37): أي كتابة بـ `version <=` المخزَّنة تُعدّ `stale` (لا-عملية) عبر `ON CONFLICT ... WHERE EXCLUDED.source_version > ...`.

#### عند فشل المنصة
- أي استثناء من `port.get_changes` لمستأجر: `failed_tenants += 1` + `catalog.reconcile.tenant_failed` (WARNING) + تحديث `max_staleness` — ثم `continue` (لا يوقف البقية) — `catalog.py:57–65`.
- النتيجة: `ok` / `partial` (بعضها فشل) / `failed` (الكل فشل) — `catalog.py:76–81`.
- فشل قراءة قائمة المستأجرين: `return "failed"` + `catalog.reconcile.list_failed` — `catalog.py:41–48`.

#### حقول الاستجابة التي يقرؤها الكود (حرفياً)
- من `/changes`: `payload["events"]` و `payload["next_cursor"]` — `adapter.py:19`.
- من `/snapshot`: `payload["events"]` و `payload.get("next_page")` — `adapter.py:23`.
- من `/order_lookup`: `resp.json().get("order")` — `commerce_client.py:108`.
- من `/stock_observation`: `resp.json().get("observation")` — `commerce_client.py:135`.

حقول الحدث داخل `events` (قائمة السماح `EVENT_ALLOWED_FIELDS`، `repos_catalog.py:64–76`) — يُقرأ مفتاح `type` للحدث (سطر 421) ثم حسب النوع:
- `product.upserted`: platform_product_id, title, description, category, attributes, active, version
- `product.deleted`: platform_product_id, version
- `variant.upserted`: platform_variant_id, platform_product_id, sku, options, price_hint_minor, currency, version
- `variant.stock`: platform_variant_id, qty, version
- `kb.upserted`: source, content, version

(هذه هي الحقول التي سيطلبها عقد Task 5 من شروه.)

### د) محقق التوكن — core/app/security/jwt.py + core/app/config.py (VERIFIED)

- الخوارزمية المقبولة: **RS256 فقط**. `alg` يُقرأ من الترويسة غير الموثَّقة، وأي شيء غير `"RS256"` يُرفض `UNAUTHENTICATED` — `jwt.py:76–82`؛ و`jwt.decode(..., algorithms=["RS256"])` — السطر 96.
- المطالبات المتحقَّق منها (حرفياً):
  - إلزامية في التحقق: `exp`, `iss`, `aud`, `sub` — `jwt.py:100` (`options={"require": ["exp", "iss", "aud", "sub"]}`).
  - `iss` يُطابَق مع `cfg.issuer` و`aud` مع `cfg.audience` — `jwt.py:97–98`.
  - يجب توافر `sub`, `tenant`, `role` (غير فارغة) — `jwt.py:107–111`.
  - `role` ضمن `ALLOWED_ROLES = ("merchant_admin", "staff", "platform_admin")` — `jwt.py:20,112–113` (وإلا `FORBIDDEN_ROLE`).
  - `tenant` (platform_ref) مصدر الهوية الوحيد؛ لا يُوثَق `tenant_id` من أي مكان آخر (H2) — `jwt.py:5–6,36,108`.
- التسامح الزمني: `leeway=cfg.clock_skew_s` — `jwt.py:99`؛ الافتراضي `30` ثانية — `config.py:76`.
- حل `kid` عبر JWKS: `PyJWKClient(jwks_url, cache_keys=True, lifespan=300)` — `jwt.py:42`؛ `get_signing_key_from_jwt(token).key` — `jwt.py:45`.
- مدة الكاش: `lifespan=300` ثانية — `jwt.py:42`.
- عند تعذّر JWKS: أي استثناء يسقط إلى `JWT_PUBLIC_KEY_PEM` إن وُجد؛ إن لم يوجد → `TokenError("UNAUTHENTICATED", "unable to resolve signing key (JWKS unreachable, no fallback configured)")` — `jwt.py:51–63`.
- أولوية `JWKS_URL` مقابل `JWT_PUBLIC_KEY_PEM`: JWKS يُجرَّب أولاً إن وُجد (`jwt.py:49,52–54`)، وPEM احتياطي فقط عند فشل JWKS أو غيابه (`jwt.py:61–62`). غياب الاثنين → `TokenError("UNAUTHENTICATED", "no signing key source configured")` — `jwt.py:63`.
- رفض الإقلاع بلا مصدر مفاتيح: `JwtConfig.__post_init__` يرفع `ConfigError` إذا كان الاثنان فارغين — `config.py:79–85`.
- القراءة من البيئة: `jwks_url` من `JWKS_URL` — `config.py:169`؛ `public_key_pem` من `JWT_PUBLIC_KEY_PEM` (مع فك `\n`) — `config.py:170`؛ `issuer` من `JWT_ISSUER` (إلزامي) و`audience` من `JWT_AUDIENCE` (إلزامي) — `config.py:167–168`.

#### scripts/mint_admin_token.py — المطالبات التي يصدرها (VERIFIED)
الترويسة: `{"kid": ...}` فقط إن مُرِّر `--kid` (سطر 57). المطالبات (سطر 53–56):
```
"iss", "aud", "sub" (افتراضي uuid4), "tenant" (افتراضي "ops"), "role" (افتراضي "platform_admin"), "iat", "exp"
```
الخوارزمية `RS256` (سطر 58). `--role` محصور في `("platform_admin", "merchant_admin", "staff")` (سطر 32). `--ttl-s` بين 60 و`MAX_TTL_S = 8*3600` (سطر 23,38). هذه هي المطالبات التي سيُطلب من شروه إصدارها.

### هـ) الاختبارات النقية (VERIFIED)

من مجلد `core`:
```
python -m pytest tests/test_jwt.py tests/test_catalog.py -q
```
السطر الأخير حرفياً:
```
24 passed in 3.67s
```
(24 ناجح، 0 إخفاق.)

هل يوجد اختبار لـ `commerce_client.py` نفسه؟
```
findstr /s /m "commerce_client" tests\*.py
```
المخرج حرفياً:
```
tests\test_orders.py
```
النتيجة: **لا يوجد اختبار مخصص** لـ `commerce_client.py`. الملف الوحيد المطابق هو `tests/test_orders.py`، ومَرجعه الوحيد استيراد فئة الخطأ فقط (`from app.channels.commerce_client import CommerceUnavailableError` — سطر 15)، لا اختبار لسلوك العميل نفسه.

### فحص التحوّل
لا ينطبق: مهمة قراءة فقط بلا كود أو اختبار أو ترحيل. لم يُعدَّل أي ملف غير سجل التنفيذ هذا.

## T1 — حكم المعماري (converge)
VERIFIED: عميل التجارة بلا أي توثيق (commerce_client.py:54، findstr فارغ)؛ test_jwt + test_catalog = 24 passed.
**F-P4-01 (مفتوح، للعقد في Task 5):** get_snapshot / المسار /snapshot مكشوف في العميل والمنفذ والمحوّل لكنه بلا أي ندّاء إنتاجي؛ المزامنة تبدأ بـget_changes من مؤشر فارغ. يجب أن يحدد العقد صراحة: إما أن /changes بلا مؤشر يعيد الكتالوج كاملاً، أو توصيل get_snapshot للتهيئة الأولى.
اختبار وحدة لـcommerce_client.py (توقيع، رفض غير الموقَّع، القاطع) يُضاف في Task 4.

## T2 — جرد الذكاء

### الحالة بعد الخطوة 0 (VERIFIED)
- `git branch --show-current` → `p4-links-ai`
- `git status --short` → `?? dump.rdb` (فقط)
- commit T1: `a1fe544 docs(p4): T1 links inventory (commerce client unauthenticated; F-P4-01 snapshot unused)`
- انحراف مُسجَّل: فرع `p4-links-ai` كان موجوداً مسبقاً عند `c06d297` (نفس main) فاستخدمت `git checkout p4-links-ai` بدل `-b`. ووُجد قفلان خاملان `.git/HEAD.lock` و`.git/objects/maintenance.lock` (لا عملية git تعمل — مؤكَّد بـGet-Process) فحذفتُهما ثم نجح checkout.

### أ) جداول المقاسات — core/migrations/0001_baseline.sql (VERIFIED)

مخرج `findstr /n "size_chart" core\migrations\0001_baseline.sql` حرفياً:
```
463:CREATE TABLE size_charts (
474:CREATE TABLE size_chart_rows (
477:  chart_id   uuid NOT NULL REFERENCES size_charts(id) ON DELETE CASCADE,
```

التعريف حرفياً (الأسطر 463–487):
```
463:CREATE TABLE size_charts (
464:  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
465:  tenant_id   uuid NOT NULL REFERENCES tenants(id),
466:  name        text NOT NULL,
467:  scope_type  text NOT NULL CHECK (scope_type IN ('product','category')),
468:  scope_ref   text NOT NULL,
469:  fit_type    text NOT NULL DEFAULT 'regular' CHECK (fit_type IN ('slim','regular','oversized')),
470:  stretch_pct numeric(4,1) NOT NULL DEFAULT 0 CHECK (stretch_pct BETWEEN 0 AND 50),
471:  version     integer NOT NULL DEFAULT 1,
472:  UNIQUE (tenant_id, scope_type, scope_ref)
473: );
474:CREATE TABLE size_chart_rows (
475:  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
476:  tenant_id  uuid NOT NULL REFERENCES tenants(id),
477:  chart_id   uuid NOT NULL REFERENCES size_charts(id) ON DELETE CASCADE,
478:  size_label text NOT NULL,
479:  sort_order integer NOT NULL,
480:  height_cm  numrange,
481:  weight_kg  numrange,
482:  chest_cm   numrange,
483:  waist_cm   numrange,
484:  hips_cm    numrange,
485:  length_cm  numeric(5,1),
486:  UNIQUE (chart_id, size_label)
487: );
```

الأعمدة والقيود:
- `size_charts`: id (uuid PK)، tenant_id (FK→tenants)، name (text NOT NULL)، scope_type (CHECK product/category)، scope_ref (NOT NULL)، fit_type (DEFAULT 'regular'، CHECK slim/regular/oversized)، stretch_pct (numeric(4,1)، CHECK 0–50)، version (int DEFAULT 1). UNIQUE (tenant_id, scope_type, scope_ref).
- `size_chart_rows`: id (PK)، tenant_id (FK)، chart_id (FK→size_charts ON DELETE CASCADE)، size_label (NOT NULL)، sort_order (int NOT NULL)، height_cm/weight_kg/chest_cm/waist_cm/hips_cm (numrange)، length_cm (numeric(5,1)). UNIQUE (chart_id, size_label).

RLS: نعم. كتلة DO (0001_baseline.sql:750–764) تفعّل RLS وتنشئ سياسة `tenant_isolation` (USING/CHECK tenant_id = app.current_tenant()) على كل جدول public يحمل عمود tenant_id وليس فرع قسم وليس geo_gazetteer ⇒ تشمل `size_charts` و`size_chart_rows`.

كود منتج يقرأ/يكتب؟ `findstr /s /n "size_chart" core\app\*.py` → فقط `testsupport.py:154–155` (DELETE FROM في تنظيف المستأجر). **لا أي كود منتج** يقرأ أو يكتب الجدولين (يؤكد «لا كود» لـSizeAdvisor/G2).

المقارنة مع §5.1 (docs/01_FIVE_TASKS_DESIGN.md:193) — الناقص حرفياً:
1. **وحدة القياس (unit)** — لا عمود لها.
2. **ease لكل نوع ارتداء** — لا عمود/جدول.
3. **length كمدى (min/max)** — المخطط فيه `length_cm numeric(5,1)` رقماً واحداً لا numrange (بخلاف height/weight/chest/waist/hips التي هي numrange).

### ب) جدول التضمين — catalog_embeddings (VERIFIED)

التعريف حرفياً (0001_baseline.sql:428–438):
```
428:CREATE TABLE catalog_embeddings (
429:  id           uuid NOT NULL DEFAULT gen_random_uuid(),
430:  tenant_id    uuid NOT NULL REFERENCES tenants(id),
431:  product_id   uuid NOT NULL REFERENCES catalog_products(id) ON DELETE CASCADE,
432:  content_hash text NOT NULL,
433:  model        text NOT NULL,
434:  embedding    vector(1024) NOT NULL,
435:  updated_at   timestamptz NOT NULL DEFAULT now(),
436:  PRIMARY KEY (tenant_id, id),
437:  UNIQUE (tenant_id, product_id)
438:) PARTITION BY HASH (tenant_id);
```
(الأسطر 439–444: حلقة تنشئ 8 أقسام `catalog_embeddings_p0..p7` بـ MODULUS 8.)

- عرض المتجه: `vector(1024)` (سطر 434).
- التقسيم: `PARTITION BY HASH (tenant_id)`، 8 أقسام (MODULUS 8).
- الفهرس المتجهي: **لا يوجد** — `0009_p1_vector.sql:18–20` «No ANN/HNSW index in this batch»؛ المسح الدقيق `<=>` L2 داخل قسم المستأجر (repos_catalog.py:518–521).
- RLS: نعم (`tenant_isolation` على الأب؛ الأقسام لا تُمنح — 0001:427 و787–798).

المزوّدات المقبولة حرفياً (`validate_embedding_provider` config.py:284–292 + `build_embedding_provider` llm/registry.py:40–51):
- `"fake"` — تطوير/اختبار فقط (يُرفض في الإنتاج: ConfigError «EMBEDDING_PROVIDER=fake is forbidden when ENV=production» config.py:291–292).
- `"local"` — آمن للإنتاج (local_embedding.py).
- أي اسم آخر → `RuntimeError("unknown EMBEDDING_PROVIDER ...")` (registry.py:51).
- العرض ثابت: `EMBEDDING_DIM_CONTRACT = 1024` (config.py:298)، و`validate_embedding_dim` يرفض أي `EMBEDDING_DIM != 1024` (301–308). الافتراضي: provider="fake" (394/567)، dim=1024 (395/569).

### ج) المحوّل المحلي — local_embedding.py + embed.py (VERIFIED)

local_embedding.py:
- الثوابت: `PROVIDER_NAME="local"`، `MODEL_NAME="local-embedding"`، `EMBED_DIM=1024` (31–33).
- `_embed_text` (39–50): **معجمي لا دلالي** — bag-of-words فوق فهارس tokens مُجزَّأة بـmd5 ثم L2-normalized. `idx = int(md5(tok).hexdigest(),16) % dim` ثم `vec[idx] += 1`؛ التطبيع L2؛ النص الفارغ → متجه صفري. (md5 لا hash() لأن الأخير مُملَّح لكل عملية.)
- كيف يصل العرض إلى 1024: `dim=EMBED_DIM=1024` (أو settings.embedding_dim)، والفهرس دائماً `md5(token) % dim`، فطول المتجه ثابت 1024.
- `embed` (57–65): متجهات + `LlmUsage(input_tokens=0, output_tokens=0, latency_ms=1)` — صفر تكلفة (لا مدخل 'local' في جدول الأسعار).

embed.py:
- متى يُضمَّن منتج: `embed_once` (225–320) كل EMBED_INTERVAL_S، للمنتجات **النشطة** التي لا صف لها في catalog_embeddings أو تغيّر `content_hash = md5(title+'\n'+description)`. المرشّحون من `list_products_needing_embedding` (SECURITY DEFINER، 0009). قراءة→إغلاق→نداء→فتح→كتابة (H40).
- ماذا يُخزَّن: `upsert_catalog_embedding(tenant_id, product_id, content_hash, model, embedding)` في catalog_embeddings (305–308).
- الاستخدام في الاسترجاع: `query_vector` (94–194) مضمّن الاستعلام بفشل مفتوح (H42: أي فشل يعيد None)؛ و`search_products` (repos_catalog.py:600–643) يجمع FTS(arabic) + pg_trgm + (اختيارياً) المتجه ثم يدمجها بـ`rrf_merge`. `rrf_merge` (121–138): RRF `score(d)=Σ 1/(k+rank_d)` مع `k=60`، تيسير متعادل بالـdoc_id؛ كل مصدر مقيَّد بـ`SEARCH_CANDIDATE_K=50` والنتيجة ≤`SEARCH_RESULT_MAX=8`. إن `query_vector=None` يظل البحث يعمل بالمصدرين المعجميين (H42).

### د) سجل الأدوات — tools/registry.py (VERIFIED)

- `ToolContext` (15–34): tenant_id, conversation_id, tenant_ref, channel_phone_e164, message_texts, commerce (CommercePort مُحقون), settings, order_ref, phone_candidates, path, last_shown_product_ids (افتراضي ()). التعليق: «No DB connection here» (16).
- `ToolSpec` (37–40): `name` + `run: Callable[[ToolContext], Any]`.
- `TOOLS` (49–53): `track_order`, `resolve_address`, `join_waitlist`.
- النقاء (H50): نعم. track_order.py (3–6): «No DB, no direct httpx... the coordinator (orders.py) owns transactions and audit logging»؛ join_waitlist.py (3–7): «no DB, no network, no .execute(), no app.db import... coordinator (workers/stock.py) owns the dedupe and the actual insert». الأداة تُرجع قراراً مُجمَّداً (OrderLookup / JoinWaitlistDecision)، والمنسّق ينفّذ الأثر.
- خطوات تسجيل أداة جديدة: (1) `from app.tools import <module>` بعد تعريف ToolContext/ToolSpec؛ (2) إضافة `"<name>": ToolSpec("<name>", <module>.run)` إلى القاموس الحرفي TOOLS؛ (3) الوحدة تكشف `run(ctx)` نقية. لا register() ولا import ديناميكي ولا globals() (registry.py:4–5).

### هـ) مطابقة الـVerifier — verify.py + verify_rules.py (VERIFIED)

- ما يطابقه اليوم بين نص الرد والحقائق: **لا شيء دلالي**. `check_text` (verify_rules.py:172–197) ينفّذ فحوصاً بنيوية (empty→oversize→control_chars→placeholder، 176–183) ثم قوائم حظر (profanity→competitor→disclosure، 187–196) بمطابقة عبارات (equality + squeeze + joined-window، لا substring). **لا يقارن أسعاراً ولا أرقام طلبات ولا مخزوناً ولا مقاساً**.
- مصدر «الحقائق»: لا حقائق خارجية؛ القوائم سياسات تأتي من الإعدادات عبر `build_rules` (verify.py:49–53: verify_profanity_ar/en، verify_competitors، verify_disclosure).
- مكان إضافة فحص «مقاس مذكور يجب أن يساوي ناتج SizeAdvisor» (بلا تعديل): أقرب نقطة امتداد هي `check_text` (verify_rules.py:172) مع `BlocklistSet`/`build_rules` (154–165) وقائمة rule_id المغلقة في `RuleVerdict`. الدستور يفرض ذلك (constitution.md §1: «الـVerifier يطابق كل مقاس/سعر في الرد مع ناتج الحساب H45–H51») لكن **لا كود مطابقة حقائق قائماً بعد**.

### و) الاختبارات القائمة (VERIFIED)

من مجلد `core`:
```
python -m pytest tests/test_verify.py tests/test_verify_rules.py tests/test_vector.py tests/test_tools_extract.py tests/test_llm.py -q
```
السطر الأخير حرفياً:
```
101 passed in 3.05s
```
(101 ناجح، 0 إخفاق.)

`findstr /s /m "local_embedding size_advisor gift" tests\*.py` المخرج حرفياً:
```
tests\test_vector.py
```
(وحده `local_embedding` يُطابَق في test_vector.py؛ لا ملف اختبار لـ`size_advisor` ولا `gift` — يطابق «لا كود» لـG2/G3.)

### فحص التحوّل
لا ينطبق: مهمة قراءة فقط بلا كود أو اختبار أو ترحيل. لم يُعدَّل أي ملف غير سجل التنفيذ (بعد commit الخطوة 0).


## T2 — حكم المعماري (converge)
VERIFIED: جداول المقاسات بلا كاتب منتج وتنقصها وحدة القياس وease ومدى الطول؛ catalog_embeddings vector(1024) بلا فهرس ANN؛ التضمين المحلي معجمي؛ الاختبارات الخمسة 101 passed.
**F-P4-02 (مفتوح، يُبنى في Task 11):** الـVerifier لا يطابق الحقائق دلالياً (بنيوي + قوائم حظر). ضمانة «النموذج لا يغيّر المقاس» غير موجودة: المقاس يُعرض من ناتج الأداة بقالب، وأي مقاس آخر في النص الحر يُرفض عبر قاعدة في verify_rules.check_text.
ملاحظة إجرائية: حذف المنفّذ HEAD.lock وmaintenance.lock (خارج نص الأمر) بعد التحقق من غياب عملية git — الأثر سليم؛ الأوامر القادمة تسمّي الأقفال الثلاثة صراحة.

## T3 — قرارات المالك (2026-10-07: «أعتمد التوصيات»)
- OQ-P4-03: توثيق مسارات التجارة بـHMAC-SHA256 بسرّ مشترك + طابع زمني + نافذة 5 دقائق، على الطلب المُرسَل حرفياً (الطريقة + المسار الخام مع الاستعلام).
- OQ-P4-04: جانب شروه بحزمة Speckit مستقلة في sharwa_saas بعد العقد (Task 5–6).
- OQ-P4-05: جداول المقاسات يُدخلها التاجر في شروه وتُزامَن، بالسنتيمتر فقط؛ ease قيم افتراضية في الكود لكل fit_type؛ لا ترحيل الآن.
- OQ-P4-06: مسار في شروه ينشئ جلسة دفع للهدايا ويعيد رابطاً (ضمن حزمة شروه)؛ الحلّال يُبنى الآن.
- OQ-P4-02: مزوّد التضمين مؤجَّل؛ البحث المعجمي الحالي باقٍ.
- OQ-P4-01: بروكسي عكسي بشهادة TLS تلقائية؛ الاسم والشراء من المالك (لم يُحدَّد بعد).

## T4أ — توقيع عميل التجارة

### التعديل (core/app/channels/commerce_client.py)
- استيراد `hashlib` و`hmac` بجانب `import time` (سطر 13–14).
- سطر docstring جديد (سطر 10): «Every outbound request is HMAC-signed with a shared secret and timestamp (OQ-P4-03).»

صنف `_HmacAuth` (الأسطر 54–66) حرفياً:
```
class _HmacAuth(httpx.Auth):
    """Signs every request (OQ-P4-03): HMAC-SHA256 over
    b"<ts>." + METHOD + b" " + raw request target (path + '?' + query, exactly as sent)."""

    def __init__(self, secret: bytes) -> None:
        self._secret = secret

    def auth_flow(self, request):
        ts = str(int(time.time()))
        msg = ts.encode() + b"." + request.method.encode() + b" " + request.url.raw_path
        request.headers["X-Sharwa-AI-Timestamp"] = ts
        request.headers["X-Sharwa-AI-Signature"] = hmac.new(self._secret, msg, hashlib.sha256).hexdigest()
        yield request
```

المُنشئ `__init__` (الأسطر 69–74) حرفياً:
```
class CommerceClient:
    def __init__(self, base_url: str, *, secret: str, timeout_s: float = 3.0):
        if not secret or not secret.strip():
            raise ValueError("commerce API secret is required and must be non-empty")
        self._breaker = _Breaker(fail_threshold=5, reset_s=30.0)
        self._client = httpx.Client(base_url=base_url, timeout=timeout_s, auth=_HmacAuth(secret.strip().encode()))
```

### المخرجات الحرفية (VERIFIED)
- `pytest tests/test_commerce_client.py -q` → `4 passed` (إعادة بعد الاستعادة).
- `pytest tests/test_orders.py tests/test_catalog.py -q` → `30 passed in 1.40s`.
- `python ..\scripts\static_gate.py` → `STATIC GATE PASSED — 0 violations.`
- `lint-imports` → `Contracts: 4 kept, 0 broken.`
- تحقق httpx: `0.28.1`، `request.url.raw_path` نوعه `bytes` = `b'/a/b?phones=a%2Cb%2Cc&tenant_ref=t1'` (يشمل الاستعلام المُرمَّز — يطابق `self.path` في الخادم).

### فحص التحوّل (VERIFIED)
- حذف `auth=_HmacAuth(...)` مؤقتاً ⇒ `test_signed_request_is_accepted` و`test_signature_covers_raw_query` فشلا بـ`CommerceClientError: commerce API returned 401` (2 failed, 2 passed).
- استعادة السطر ⇒ `4 passed`.

### الثغرة الانتقالية المعلنة (تُصلح في Task 4ب)
`findstr /n "CommerceClient(" core\app\workers\realtime.py`:
```
188:                CommerceClient(self.settings.commerce_base_url, timeout_s=self.settings.commerce_timeout_s)
```
السطر 188 يبقى **بلا secret** عمداً (يُصلح في Task 4ب). لا أثر ما دام `COMMERCE_BASE_URL` فارغاً (لا يُنشأ العميل أصلاً).

### الملف الجديد
`core/tests/test_commerce_client.py` (4 اختبارات): خادم HTTP حقيقي على 127.0.0.1 في خيط (لا نقل وهمي) يعيد حساب التوقيع نفسه (HMAC-SHA256 + طابع زمني + نافذة 300ث) ويرفض عدم التطابق بـ401.
