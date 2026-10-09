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

## T4ب — السرّ والإقلاع

### التعديلات
- `config.py`: أضيف `commerce_api_secret: str = ""` بعد `commerce_base_url` (سطر 369) وحدّث التعليق (366–367). الحارس في `load()` (649–654) وتمرير القيم (710–711).
- `realtime.py:188–192`: `CommerceClient(self.settings.commerce_base_url, secret=self.settings.commerce_api_secret, timeout_s=...)`.
- `conftest.py:26`: `os.environ.setdefault("COMMERCE_API_SECRET", "test-commerce-api-secret")`.
- `.env.example:100`: `# COMMERCE_API_SECRET=   (required when COMMERCE_BASE_URL is set; shared HMAC secret with Sharwa, OQ-P4-03)`.

### diff الحارس (config.py) حرفياً:
```
+        commerce_base_url = _optional("COMMERCE_BASE_URL", "")
+        commerce_api_secret = _optional("COMMERCE_API_SECRET", "")
+        if commerce_base_url and not commerce_api_secret:
+            raise ConfigError(
+                "COMMERCE_API_SECRET is required when COMMERCE_BASE_URL is set (see .env)"
+            )
```

### سطر realtime حرفياً:
```
190:                    secret=self.settings.commerce_api_secret,
```

### المخرجات الحرفية (VERIFIED)
- `pytest tests/test_commerce_config.py tests/test_commerce_client.py tests/test_workers_config.py -q` → `16 passed in 2.15s` (3+4+9).
- الحزمة النقية الكاملة → `388 passed, 473 deselected in 9.93s`.
- `python ..\scripts\static_gate.py` → `STATIC GATE PASSED — 0 violations.`
- `lint-imports` → `Contracts: 4 kept, 0 broken.`
- `findstr /n "commerce_api_secret" app\workers\realtime.py` → `190: secret=self.settings.commerce_api_secret,`

### فحص التحوّل (VERIFIED)
- حذف سطري الحارس مؤقتاً ⇒ `test_base_url_without_secret_refuses_boot` فشل بـ`Failed: DID NOT RAISE ConfigError` (1 failed, 2 passed).
- استعادة الحارس (مُثبتة بـ`git diff`) ⇒ `3 passed`.

### الملف الجديد
`core/tests/test_commerce_config.py` (3 اختبارات): رفض الإقلاع بـbase_url بلا سرّ، قبول بلا base_url، تمرير السرّ عند ضبطهما معاً.


## T4ب — حكم المعماري (converge)
VERIFIED: الحارس يرفض الإقلاع بلا سرّ (فحص التحوّل: DID NOT RAISE)؛ realtime.py:188 يمرّر السرّ؛ الحزمة النقية 388 passed؛ lint-imports 4 kept 0 broken؛ البوّابة 0. Task 4 مكتملة.

## قرارات المعماري قبل Task 5/6 (2026-10-07)
- **F-P4-01 (محسوم):** `/changes` بلا `since` يبدأ من أول التاريخ: يعيد الكتالوج الحالي كاملاً كأحداث upsert مُقسَّمة صفحات بـ`next_cursor`. `/snapshot` محجوز في العقد ولا يُطلب من شروه تنفيذه الآن (لا ندّاء له في المحرك).
- **F-P4-03 (جديد، فقدان بيانات صامت محتمل):** `workers/catalog.py:56–74` يقصّ الأحداث إلى `catalog_reconcile_max_events_per_tenant` (500) ثم يحفظ `next_cursor` القادم من المنصة كما هو، فإن أعادت المنصة أكثر من 500 حدث في صفحة ضاعت البقية إلى الأبد. العلاج مزدوج: (أ) العقد يُلزم شروه بصفحة ≤ 500 حدث و`next_cursor` يشير بعد آخر حدث مُعاد؛ (ب) حارس في المحرك (Task 4ج): صفحة أكبر من الحدّ لا تُطبَّق ولا يتقدّم المؤشر، وتُسجَّل خطأ عقد.

## T5 — عقد المسارات والتوقيع (VERIFIED)

أُنشئ `docs/PLATFORM_COMMERCE_CONTRACT.md` بسبعة أقسام. مطابقة كل مفتاح/بند في العقد مع سطر كود:

| بند العقد | سطر الكود |
|---|---|
| `events` / `next_cursor` | `adapter.py:19` |
| `order` | `commerce_client.py:128` |
| `observation` | `commerce_client.py:155` |
| `available` (int ≥ 0) | `stock.py:137–138` |
| `observed_at` (ISO-8601) | `stock.py:37` (+ `142`) |
| `X-Sharwa-AI-Timestamp` | `commerce_client.py:64` |
| `X-Sharwa-AI-Signature` | `commerce_client.py:65` |
| `ORDER_CARD_FIELDS` | `port.py:16` |
| حقول الأحداث (`type`/`version`/…) | `repos_catalog.py:64–76`, `421–427` |
| صفحة ≤ 500 | `config.py:373` / `715` |
| القاطع 5/30ث + مهلة 3ث | `commerce_client.py:73`, `70` |

## T6 — قسم SSO (VERIFIED)

| بند العقد | سطر الكود |
|---|---|
| RS256 فقط | `jwt.py:81–82`, `96` |
| إلزامية `exp,iss,aud,sub` | `jwt.py:100` |
| `sub/tenant/role` مطلوبة | `jwt.py:108–111` |
| `ALLOWED_ROLES` | `jwt.py:20`؛ `permissions.py:4` |
| leeway=clock_skew_s=30 | `jwt.py:99`؛ `config.py:76` |
| PyJWKClient(lifespan=300) | `jwt.py:42` |
| مطالبات mint_admin_token | `mint_admin_token.py:54–58` |
| issuer/audience | `config.py:167–168` |

`python scripts\static_gate.py` ⇒ `STATIC GATE PASSED — 0 violations.` (لم يُمَسّ أي كود).

## T4ج — حارس صفحة الكتالوج (F-P4-03)

### التعديل (core/app/workers/catalog.py)
- docstring (سطر 35): «A page larger than the limit is a contract violation: not applied, cursor not advanced (F-P4-03).»
- الحارس داخل `reconcile_once` (الأسطر 67–80): صفحة أكبر من `catalog_reconcile_max_events_per_tenant` لا تُطبَّق، ولا يتقدّم مؤشرها، وتُعدّ إخفاقاً للمستأجر، وتُسجَّل `catalog.reconcile.contract_violation`.

diff الحارس حرفياً:
```
+        limit = settings.catalog_reconcile_max_events_per_tenant
+        if len(events) > limit:
+            # F-P4-03: a page larger than the contract allows is NOT applied and its
+            # cursor is NOT advanced - truncating and advancing would silently drop
+            # every event past the limit (docs/PLATFORM_COMMERCE_CONTRACT.md §5).
+            failed_tenants += 1
+            obs_logging.log_event(
+                _log, event="catalog.reconcile.contract_violation", component="catalog",
+                tenant_id=str(tenant_id), level=logging.ERROR,
+                page_events=len(events), limit=limit,
+            )
+            if last_ok is not None and last_ok < now:
+                max_staleness = max(max_staleness, (now - last_ok).total_seconds())
+            continue
```

### المخرجات الحرفية (VERIFIED)
- `pytest tests/test_catalog_page_guard.py tests/test_catalog.py -q` → `15 passed in 0.84s`.
- الحزمة النقية الكاملة → `390 passed, 473 deselected in 9.93s` (= 388 + 2).
- `python ..\scripts\static_gate.py` → `STATIC GATE PASSED — 0 violations.`
- `lint-imports` → `Contracts: 4 kept, 0 broken.`

### فحص التحوّل (VERIFIED)
- حذف كتلة الحارس مؤقتاً ⇒ `test_oversized_page_is_not_applied_and_cursor_not_advanced` فشل بـ`assert 'ok' == 'failed'` (1 failed, 1 passed).
- استعادة الكتلة (مُثبتة بـ`git diff`) ⇒ `2 passed`.

### الملف الجديد
`core/tests/test_catalog_page_guard.py` (نقي، بلا علامة db): 2 اختباران بـmonkeypatch لطبقة القاعدة (`system_tx`/`tenant_tx` = nullcontext، و`apply_catalog_events`/`set_catalog_sync_cursor` تسجّلان استدعاءاتهما بـ`**kwargs`).


## T7 — تنفيذ جانب شروه (المعماري، بطلب المالك 2026-10-07)
الفرع: `feature/sharwa-ai-commerce-api` في `sharwa_saas` (لم يُلتزم بعد — بيد المالك).
ملفات جديدة: `products/services/sharwa_ai_commerce_api.py`، `tenants/sharwa_ai_commerce_views.py`، `products/services/sharwa_ai_sso.py`، `products/views/sharwa_ai_sso.py`، `products/templates/products/sharwa_ai_console_launch.html`، `products/test_sharwa_ai_commerce_api.py`، `docs/SHARWA_AI_COMMERCE_API.md`. تعديل: `config/urls_public.py` (4 مسارات)، `config/urls_tenant.py` (مسار الإطلاق).
VERIFIED (المعماري): `py_compile` لكل الملفات؛ **تشغيل بيني حقيقي**: `CommerceClient` الحقيقي من المحرك ضد `verify_signature` الحقيقية من شروه على خادم HTTP محلي ⇒ `/changes` بلا مؤشر وبمؤشر و`/order_lookup` بهواتف مُرمَّزة (`%2C`, `%2B`) كلها True، والسرّ الخاطئ ⇒ `commerce API returned 401`.
UNVERIFIED: اختبارات Django (`python manage.py test products.test_sharwa_ai_commerce_api`) لم تُشغَّل — بيئة المعماري بلا Django ولا قاعدة شروه. أي تشغيل حيّ ضد الـVPS لم يحدث.

### T7 — تحديث التحقق (2026-10-07)
- VERIFIED: `python manage.py test products.test_sharwa_ai_commerce_api -v 2` في venv شروه بتشغيل المالك → Ran 16 tests, OK (System check: 0 issues).
- VERIFIED: `git diff config/urls_public.py config/urls_tenant.py` لا يحوي إلا أسطر T7.
- الفرع `feature/sharwa-ai-commerce-api` أُنشئ في sharwa_saas؛ الـcommit والدمج بيد المالك.

## T6ب — استقبال الدخول الموحد في لوحة المحرك

### التعديلات
- `core/app/config.py`: `import re`؛ `_PLATFORM_ORIGIN_RES` + `parse_platform_origins` (بعد `_csv`)؛ حقل `console_sso_platform_origins: tuple[str, ...] = ()` في Settings؛ وقراءته في `load()` من `CONSOLE_SSO_PLATFORM_ORIGINS`.
- `core/app/main.py`: مسار `/console/sso-config.json` (يُرجع `{"platform_origins": [...]}`) يُسجَّل قبل الـmount، فقط عند وجود `CONSOLE_STATIC_DIR`.
- `frontend/js/sso.js` (جديد): `READY`/`TOKEN`/`MAX_TOKEN_LEN`/`isValidPattern`/`originAllowed`/`acceptTokenMessage`/`startSsoHandoff`.
- `frontend/js/app.js`: استيراد `startSsoHandoff` + دالة `boot()` بدل `render();` الأخيرة.
- `.env.example`: `CONSOLE_SSO_PLATFORM_ORIGINS` (فارغ = تعطيل).
- `docs/PLATFORM_COMMERCE_CONTRACT.md` §8: استبدال «لم يُبنَ بعد» بوصف جهة اللوحة.
- `frontend/README.md`: قسم «الدخول الموحد من شروه (P4)».

### المخرجات الحرفية (§9)
- `pytest tests/test_console_sso_config.py -q` → `6 passed in 1.67s`.
- `pytest -q` → `396 passed, 473 deselected in 18.14s`.
- `python ..\scripts\static_gate.py` → `STATIC GATE PASSED — 0 violations.`
- `lint-imports` → `Contracts: 4 kept, 0 broken.`
- `node --test --test-reporter=spec core/tests/js/console_sso.test.mjs` → `ℹ tests 9 · ℹ pass 9 · ℹ fail 0`.

### فحص التحوّل (M1–M3، كلها VERIFIED مع `fc` لا فروق)
- **M1 (المصدر):** `if (!ev || !opener || ev.source !== opener)` → `if (!ev)` ⇒ `ℹ fail 2` (rejects a sender that is not the opener + hand-off sends…). استعادة ⇒ `FC: no differences encountered` ⇒ `ℹ pass 9`.
- **M2 (العلامة الواحدة):** `if (LABEL.test(...)) return true;` → `return true;` ⇒ `ℹ fail 1` (wildcard allows exactly one label under the parent). استعادة ⇒ لا فروق ⇒ `ℹ pass 9`.
- **M3 (Python):** `raise ConfigError(...)` → `pass` ⇒ `1 failed, 5 passed` (test_origins_reject_unsafe_values: DID NOT RAISE). استعادة ⇒ لا فروق ⇒ `6 passed`.

### UNVERIFIED
- اختبار متصفح حقيقي (نافذتان، أصلان) لم يُطلب وغير ممكن هنا ⇒ UNVERIFIED_ENV_LIMIT.

## T8 — SizeAdvisor (خوارزمية المقاسات النقية)

### الملفات
- `core/app/fit/__init__.py` (جديد): سطر واحد.
- `core/app/fit/size_advisor.py` (جديد): خوارزمية حتمية نقية (§5.1) — `SizeRow`/`SizeChart`/`SizeAdvice`، تحقق الجدول (رتيب)، الحارس الصلب للوزن، مسارا الطول/الوزن والقياسات، ease افتراضية مكتوبة، ناتج `{size, alt_size, confidence, reasons, out_of_range}`.
- `core/tests/test_size_advisor.py` (جديد): 15 اختباراً (فرع لكل مسار).
- `core/.importlinter` (إلحاق): عقد `fit-is-pure`.

### المخرجات الحرفية (§9)
- `pytest tests/test_size_advisor.py -q` → `15 passed in 0.16s`.
- `pytest -q` → `411 passed, 473 deselected in 22.46s`.
- `python ..\scripts\static_gate.py` → `STATIC GATE PASSED — 0 violations.`
- `lint-imports --no-cache` → `Contracts: 5 kept, 0 broken.`
- `ruff check app/fit tests/test_size_advisor.py` → UNVERIFIED_ENV_LIMIT: `No module named ruff` (غير مثبَّت؛ لم يُثبَّت).
- `mypy app/fit` → UNVERIFIED_ENV_LIMIT: `No module named mypy` (غير مثبَّت؛ لم يُثبَّت).

### فحص التحوّل (M1–M4، كلها VERIFIED مع `fc` لا فروق)
- **M1 (حارس الوزن):** `if weight_kg is not None and not chart.allow_under_weight:` → `if False:` ⇒ `1 failed, 14 passed` (test_95kg_never_gets_m). استعادة ⇒ لا فروق ⇒ `15 passed`.
- **M2 (علم خارج المدى):** `..., ("nearest_out_of_range",), True)` → `..., False)` ⇒ `2 failed, 13 passed` (test_outside_every_range_is_flagged_not_guessed + test_merchant_can_opt_out_of_the_weight_guard). استعادة ⇒ لا فروق ⇒ `15 passed`.
- **M3 (المرونة):** `stretch = Decimal("1") + chart.stretch_pct / Decimal("100")` → `stretch = Decimal("1")` ⇒ `1 failed, 14 passed` (test_stretch_lets_a_smaller_size_fit). استعادة ⇒ لا فروق ⇒ `15 passed`.
- **M4 (عقد النقاء):** إضافة `import httpx  # mutation` ⇒ `Contracts: 4 kept, 1 broken` (`app.fit.size_advisor -> httpx (l.19)`). استعادة ⇒ لا فروق ⇒ `Contracts: 5 kept, 0 broken`.

### UNVERIFIED
- `ruff` و`mypy` غير مثبَّتين في هذه البيئة ⇒ UNVERIFIED_ENV_LIMIT (المخرج الحرفي: `No module named ruff` / `No module named mypy`). قيم ease الافتراضية قرار مكتوب لا قياس.

## T8 — تدقيق المالك (استجابة + أسئلة مفتوحة)

### مخرج الحزمة الموسومة مرّتين (run_db_suite.py، DSN على 5433)
- `[run 1] 3 failed, 459 passed, 413 deselected, 9 errors in 885.88s (0:14:45)`
- `[run 2] 4 failed, 458 passed, 413 deselected, 9 errors in 936.38s (0:15:36)`
- `DB SUITE: FAILED (a run errored)` — **غير مستقرة** (3≠4 إخفاقاً) وبها 9 أخطاء في كل شوط.
- **لا علاقة بـTask 8:** الحزمة `-m db` لا تجمع أي كود من Task 8 (كله نقي بلا علامة db)، فالإخفاقات/الأخطاء سابقة الوجود. ملاحظة بيئة: `conftest.py` يضع `CORE_DATABASE_URL` افتراضياً على 5432 بينما القاعدة الفعلية على 5433 (تحقق: 5432 = password authentication failed، 5433 = OK)؛ شُغِّلت بـ5433 صراحة.

### أسئلة مفتوحة
- **OQ-P4-08 (`allow_under_weight`):** حقل `allow_under_weight` في `SizeChart` (dataclass نقية) **غير موجود** في `0001_baseline.sql` (جدول `size_charts` فيه `fit_type`/`stretch_pct`/`version` فقط). قرار الربط لاحقاً مطلوب: عمود جديد (ترحيل) أم إعداد على مستوى التاجر (مكان آخر)؟ — سؤال مفتوح للمالك.
- **OQ-P4-09 (ease + confidence):** قيم ease الافتراضية قرار مكتوب لا قياس وتحتاج مراجعة المالك قبل عميل حقيقي (مثل OQ-P2-12). اشتقاق `confidence` دالة نقية من الإشارات المعدودة (high = قياسات متوافقة مع طول/وزن في المدى؛ medium = مصدر واحد أو تعدّد أو تعارض؛ low = لا توصية أو خارج المدى) بلا ثابت سحري (H65).

### dump.rdb
- `git ls-files dump.rdb` ⇒ فارغ (غير مُتتبَّع، لم يدخل أي commit).
- أُضيف `dump.rdb` إلى `.gitignore` (المصدر: تشغيل redis-server من داخل مجلد المستودع فيكتب لقطة RDB في cwd).

### جولة إصلاح Task 8
- **OQ-P4-10 (`الجدول الرتيب لأبعاد الجسم`):** `_chart_is_valid` يتحقق من رتابة `height_cm` و`weight_kg` فقط، **لا** أبعاد الجسم (`chest_cm`/`waist_cm`/`hips_cm`). سؤال مفتوح: هل تُضاف رتابة أبعاد الجسم لاحقاً (مثل بندي الطول/الوزن)؟
- `.gitignore` عُدِّل (إضافة `dump.rdb`) — انظر أعلاه.
- **بند 1 (الحدّ الأدنى في مسار القياسات) — مُنفَّذ:** بعد اختيار `chosen` في مسار القياسات، إن وُجد بُعد يكون فيه `chosen.dim(d)[0] − need[d] > SLACK_CM` (مقارنة بـ`need` = القياس + ease، لا بالقياس الخام؛ `>` صارمة فالتساوي لا يرفع العلم) ⇒ `SizeAdvice(chosen.label, alt, "low", ("nearest_out_of_range",), True)`. لا تغيير على ease/_fits/_score/REASONS، ولا سبب جديد. 4 اختبارات جديدة + M5 (`if False:` ⇒ `2 failed, 18 passed` ثم fc لا فروق ⇒ `20 passed`).

- **OQ-P4-11 (`فجوة الجدول بين مقاسين`):** مدخلات العميل الواقعة في فجوة بين مدى مقاسين متجاورين (لا تقع في أيٍّ منهما) تنتج `nearest_out_of_range` — والتسمية «nearest» فضفاضة (لا تحدّد اتجاه «الأقرب» ولا تميّز الفجوة عن «أصغر/أكبر من كل المقاسات»). القرار للمالك: اعتماد التسمية الحالية أم تفصيلها (مثل `between_sizes`/`gap_between_sizes`).

## T6ب — حكم المعماري (converge، 2026-10-07)
**معتمد.** طابقتُ الشجرة بالتقرير: `git diff --stat` = 7 ملفات المتوقعة + 3 جديدة؛ `config.py`/`main.py`/`app.js` حرفياً كما في الأمر؛ `sso.js` يحوي حارس المصدر (`ev.source !== opener`) وحارس العلامة الواحدة وREADY إلى `"*"` بلا بيانات. الأدلة VERIFIED بالمخرج الحرفي: 396 pure، 6/6 Python، 9/9 Node، البوابة 0، العقود 4/0، وM1–M3 بالفشل المتنبَّأ به حرفياً.
ملاحظة: Node على مضيف المنفّذ v24.20.0 (الأمر اشترط ≥22.7). UNVERIFIED_ENV_LIMIT: تجربة متصفح حقيقية بأصلين — تُغلق في Task 18 (بعد الدومين).
يُلتزم في الخطوة 0 من أمر Task 8.

## T11 — الأداة ومطابقة الـVerifier (2026-10-07)
بدء مشروط: رفع الحجز بقرار المنفّذ وفق مصفوفة قرار المعماري (فحص قراءة واحد: الـrunner انتهى بـ`DB SUITE: FAILED (a run errored)` — 462 passed + 9 errors في الشوطين — و`DONE.txt` لم يُكتب، ولا عمليات DB حيّة ⇒ فرع «لا runner عامل ولا عمليات حيّة»: رفع الحجز عن Task 11 وحده؛ F-P4-01-DB تبقى مفتوحة، وTask 10 وكل ما يمسّ القاعدة يبقى محجوباً). التنفيذ على فرع نظيف `p4-task11-size` من `ad58dbd`، بلا commit، و`scripts/run_db_suite.py` (تعديل مسبق من جلسة الـrunner) خارج أي commit.
- `app/tools/size_advise.py`: أداة نقية بوسائط صريحة (جدول/طول/وزن/قياسات/تفضيل) تعيد `SizeAdviseResult` مجمَّدة تغلّف `SizeAdvice`؛ مسجَّلة في `TOOLS` الحرفي (S11-c).
- `app/tools/size_extract.py` (H51): مستخرج حتمي للطول (سم/م/قدم+انش) والوزن (كجم/رطل) عبر `arabic.normalize`؛ تحويلات Decimal دقيقة (رطل=0.45359237 كغ، إنش=2.54 سم، قدم=30.48 سم، م=100 سم)؛ لا رقم بلا كلمة مجاورة (الوحدة من الجانبين، وكلمة الحقل من اليسار فقط — «وزني 95 وطولي 175» تُقرأ صح)، تعارض ⇒ None، ولا فحص معقولية هنا (لدى advise).
- `verify_rules.py`: `SizeContext(size, alt_size, all_labels)` مجمَّدة + قاعدة `size_mismatch` (فئة "size") بعد قواعد البنية والقوائم؛ التقاط بتساوي التوكن (لا substring) من تسميات الجدول والمفردات العامة المغلقة (S/M/L/XL/… + سمول/ميديوم/لارج وتركيباتها) بخريطة شكل قياسي؛ المركّبة («اكسترا لارج») تستهلك توكنيها فلا يُقرأ رأسها منفرداً؛ التسمية الرقمية البحتة تُلتقط فقط خلال توكنين بعد (مقاس/مقاسك/قياس/size)؛ size=None ⇒ أي مقاس مخالفة؛ سياق مشوَّه أو فشل داخلي ⇒ مخالفة (H47)؛ `CLOSED_RULE_IDS` صار ثابتاً معلناً واختبار القائمة المغلقة يضم size_mismatch فقط.
- `verify.py`: `size_context=None` اختياري عبر `insert_verified_outbox` فقط (مسار التعطيل المعلن لا يفحصه؛ السلوك القائم عند الغياب بلا تغيير — الاختبارات القديمة خضراء كما هي).
- `core/.importlinter`: عقدان جديدان — `tools-are-pure` (يمنع psycopg/httpx/redis/app.db/app.llm/app.channels/app.workers) و`verify-rules-stays-lexical` (يمنع app.fit وapp.tools). إصلاح بيئي في `test_import_boundaries.py`: `shutil.which("lint-imports")` بدل المسار بلا امتداد — كانت اختباراته الأربعة تفشل أصلاً على Windows (FileNotFoundError) قبل أي تعديل من هذه المهمة.
- الأدلة: الحزمة النقية 450 passed (خط الأساس قبل التغيير 416 + 34 جديدة، لا انحدار)؛ `lint-imports --no-cache` = 7 kept/0 broken؛ `static_gate.py` = PASSED 0 violations؛ `-m tools` = 4 passed. M1–M5 بالفشل المتنبَّأ به حرفياً مع استعادة موثقة بـ`fc` (لا فروق) لكل تحوّل؛ ملاحظة M2: صيغة التعطيل الأولى (`if False`) سقطت في حارس H47 فبقي الناتج مخالفة (حماية مزدوجة)، فأعيدت صياغة التحوّل إلى «جعل حالة None تُمرِّر» فانكشف بإخفاق واحد. UNVERIFIED_ENV_LIMIT: ruff وmypy غير مثبَّتَين على المضيف.
- **لا جدول ولا ترحيل**: صفر (تعديلات قائمة + ملفات نقية جديدة فقط، السادسة على التوالي).

### أسئلة مفتوحة
- **OQ-P4-12 (استثناء «مقاسات المنتج المتوفرة» في قاعدة المقاس):** حُسم بتوجيه المرحلة 1 (2026-10-07): لا استثناء. الالتقاط من تسميات الجدول (`all_labels`) والمفردات العامة المغلقة فقط، والمسموح حصراً `size`/`alt_size`، والتسمية الرقمية البحتة مشروطة بكلمة مقاس؛ `all_labels` مفردات التقاط لا قائمة سماح.
- **OQ-P4-13 (موضع حقن SizeAdvice في سياق الدور):** حُسم بتوجيه المرحلة 1 (2026-10-07): الأداة بوسائط صريحة، والنتيجة تصل الـVerifier بيانات مجمَّدة `SizeContext` عبر `check_text`/`approve`/`insert_verified_outbox` فقط؛ لا نية في الراوتر ولا حقن في turn/compose داخل Task 11 — أسلاك المنسّق (قراءة الجدول وبناء السياق) مهمة لاحقة، وTask 10 وما يمسّ القاعدة يبقى محجوباً إلى إذن المعماري.

## T11 — جولة إصلاح تدقيق الكود (R1–R4، 2026-10-07)
حكم التدقيق: غير معتمد بأربعة بنود حاجبة سلوكية (تتبّع يدوي للنص الملصوق، بلا تشغيل). البروتوكول: اختبار فاشل أولاً لكل بند ثم الإصلاح ثم النجاح — وكل التشغيلات بلا قاعدة (الـrunner يعمل في الخلفية على worktree ad58).
- **R1 (سرقة الوحدة):** وحدة تلي عدداً تخصّه ولا تُستعار وحدةً يسرى للعدد التالي: «175cm 80kg»/«175 سم 80 كيلو»/«80kg 175cm» تقرأ الاثنين؛ «سم 175» تبقى مدعومة حين لا يسبقها عدد. الفاشل أولاً: test_number_then_unit_belongs_to_that_number.
- **R2 (m/م مقابل المقاس M):** المختصر m/م وحدة متر فقط للقيمة ≤ 3، وإلا يسقط وتحكم كلمة الحقل («وزني 80 M» ⇒ 80 كغ؛ «175 M» ⇒ لا طول؛ «الساعة 5 م» ⇒ لا طول)؛ الكلمات الكاملة (متر/مترا/مترات) بلا قيد.
- **R3 (قدم/إنش الصامت):** قدم يليها (بعد موصل اختياري) عدد عارٍ من وحدة إنش، أو قدم بكسر عشري ⇒ لا طول (يُسأل العميل)؛ «5 قدم و7 انش» ⇒ 170.18 و«6 قدم» ⇒ 182.88 كما هي. عُدّل اختبار «5.5 قدم» من 167.64 (السلوك القديم) إلى None بتوجيه التدقيق.
- **R4 (ثغرة التسميات الرقمية):** تُنزَع البادئات (و ف ب ل ال وال بال لل) تكرارياً ولاحقة ملكية واحدة (ها نا ي ك ه) قبل فحص كلمة المقاس، وأُضيف «مقاسات»/«sizes» ⇒ «بمقاس/المقاس/مقاسي/ولمقاس/مقاساتك 44» تُلتقط كلها؛ «بمقاس 42» تمرّ؛ «175 سم» لا تُلتقط.
- غير حاجب: طيّ 2XL–5XL ⇒ xl في الشكل القياسي (كشفه اختبار التنويعات الفاشل: «2XL» مع advice XL كانت تُحجب)؛ `size_context: verify_rules.SizeContext | None` في verify.py بدل Any؛ اختبارات التنويعات (2XL/(L)/xl/اكس سمول/اكسترا لارج، و«لارج» المنفردة = l).
- إصلاح بوابة: S1 رفض `app.tools.__file__` في test_size_advise_tool.py (ظهر في التشغيل الختامي exit 1) ⇒ استُبدل بـ`Path(__file__).resolve().parent.parent / "app" / "tools"`؛ البوابة بعده: PASSED 0 violations.
- تحوّلات: M6 (تعطيل استهلاك الوحدة اللاحقة) ⇒ 1 failed؛ M7 (تعطيل نزع السوابق) ⇒ 1 failed؛ M1–M5 أعيدت: 7/1/4/1 failed + tools-are-pure BROKEN — كلٌّ باستعادة موثقة بـfc (لا فروق).
- الأبواب النهائية (بلا قاعدة): الملفات الخمسة 77 passed (+4 deselected)، الحزمة النقية 456 passed، -m tools 4 passed، static_gate PASSED 0، lint-imports 7 kept/0 broken. UNVERIFIED_ENV_LIMIT: ruff/mypy غير مثبتين.

### أسئلة مفتوحة
- **OQ-P4-14 (امتدادات المستخرج):** المتر المركّب («متر و75»)، والفاصلة العشرية اللاتينية («1,75»)، واستخراج تفضيل الارتداء وقياسات الجسم — كلها خارج Task 11 وتُضاف إلى مهمة الأسلاك (OQ-P4-13).
- **(ردود «المقاسات المتوفرة S M L»):** تُحجَب بقرارنا الصارم بلا استثناء؛ أثر ذلك على قوالب المقاس المستقبلية يُراجَع عند كتابتها.

## T11 — جولة الإصلاح الثانية (R5–R7، 2026-10-07)
حكم التدقيق الثاني: R1–R4 مُغلقة، لكن الجولة الأولى أدخلت ثلاثة عيوب. الفاشل أولاً لكل بند ثم الإصلاح ثم النجاح؛ كل التشغيلات بلا قاعدة.
- **R5 (طيّ 2XL الخاطئ):** اعتراف صريح — اختبار التنويعات كُتب بتوقّع خاطئ («2XL» مع advice XL تمرّ) ثم مُيّئ الكود ليوافقه. الصحيح بتوجيه التدقيق: **2xl ⇒ xxl و3xl ⇒ xxxl** (اسمان لمقاس واحد)، **4xl/5xl تسميتان قائمتان بذاتهما** ضمن `_SIZE_GENERAL`؛ التوصية XL والرد «2XL/3XL/XXL» ⇒ مخالفة؛ التوصية XXL والرد «2XL» وتوصية 2XL والرد «XXL» وتوصية XXXL والرد «3XL» ⇒ تمرّ.
- **R6 (إسقاط الطول مع الوزن):** العدد التالي للقدم «عارٍ» فقط إن لم تتبعه أي وحدة معروفة؛ «6 قدم 80 كيلو» ⇒ 182.88 + 80 و«6 feet 180 lbs» ⇒ 182.88 + 81.6466266؛ «5 قدم و7»/«5 قدم 7» تبقيان غامضتين؛ «5 قدم و7 انش» ⇒ 170.18.
- **R7 (المختصر m/م والأعداد الصحيحة):** وحدة متر فقط لقيمة **غير صحيحة** في [1.00، 2.50]؛ «2 M»/«1 M»/«ابغى 2 M» ⇒ لا طول؛ «1.75 م/م» ⇒ 175؛ «1 متر» ⇒ 100؛ «طولي 175 M» ⇒ 175 عبر كلمة الحقل.
- تحوّلات: **M8** (إعادة طيّ 2xl إلى xl) ⇒ 1 failed؛ **M9** (حذف شرط «العدد التالي عارٍ») ⇒ 1 failed؛ **M10** (السماح بالأعداد الصحيحة للاختصار) ⇒ 1 failed؛ وإعادة M1–M7 (مطبقة مجتمعة مرة واحدة): 10 failed تغطي كل تحوّل + M6 مستقلاً 2 failed + M5 (فشل الاختبار + tools-are-pure BROKEN) — الاستعادات موثقة بـfc (لا فروق)، ومنها استعادة size_advise.py بعد خطأ نسخ في دورة M5 (أزيلت الملحقة حرفياً وأثبتت fc مقابل نسخة أولية).
- الأبواب النهائية (بلا قاعدة): الحزمة النقية 458 passed، -m tools 4 passed، static_gate PASSED 0، lint-imports 7 kept/0 broken.

### أسئلة مفتوحة
- **OQ-P4-15 (نافذة التسمية الرقمية):** النافذة توكنان فقط، فجملة مثل «المقاس الأنسب لك هو 44» (كلمة المقاس على بُعد أربعة توكنات) تمرّ. لا يُعالَج الآن لأن ردود المقاس من تركيب الكود؛ مهمة الأسلاك (OQ-P4-13) يجب أن تفرض اختبار عقد: كل قوالب المقاس تكتب «مقاس N» ملتصقاً — أو يُوسَّع النافذة إلى أربعة.

## T11 — R8 + الإيداع (2026-10-07)
- **R8 (بوابة قيمة الاختصار):** التوجيه عرّف الثغرة بتحوّله M11 («إعادة الاعتداد بالاختصار دون فحص القيمة»). اختبار `test_meter_shorthand_value_gate_boundaries` يثبت بوابة `_is_meter_shorthand_value` على حدودها مباشرة: 1.75/1.25/2.49/2.50 ⇒ وحدة متر؛ 0.99/2.51/1/2/1.00 ⇒ لا؛ وعبر المستخرج: «2.6 M»/«3 M»/«0.5 م» ⇒ لا طول، «1.25 م» ⇒ 125، «2.49 م» ⇒ 249. الفاشل أولاً تحقق تحت M11 على الكود الحالي (المخرج: 1 failed)، والبوابة نفسها (إصلاح R7 المدقَّق) لم تحتلق تغييراً — الاختبار هو أسنانها الدائمة.
- **M11:** (الـpredicate ⇒ return True) ⇒ `FAILED test_meter_shorthand_value_gate_boundaries — 1 failed`؛ الاستعادة fc: لا فروق.
- الأبواب قبل الإيداع (بلا قاعدة): الحزمة النقية 459 passed، -m tools 4 passed، static_gate PASSED 0، lint-imports 7 kept/0 broken.

### أسئلة مفتوحة
- **OQ-P4-16 (مفردات المقاس العامة):** `_SIZE_GENERAL` لا تتضمن small/medium/large ولا صغير/وسط/كبير؛ وعقد القوالب (اختبار عقد في مهمة الأسلاك OQ-P4-13) يجب أن يفرض استخدام S/M/L/XL… فقط في ردود المقاس أو يوسّع المفردات.
- **OQ-P4-17 (حدود بوابة اختصار المتر):** القيمة غير الصحيحة حصراً ضمن [1.00، 2.50] (2.50 تُقبل ⇒ 250 سم، 1.00 تُرفض)؛ حالات الحدود قرار مشتق من التوجيه — تُراجَع مع قوالب المقاس.


## T11 — R8 (الجولة الثالثة، 2026-10-07)
- **R8:** في `_ambiguous_feet` كان العدد التالي للقدم يُعدّ «ذا وحدة» إن تبعته أي وحدة معروفة، بما فيها اختصار m/م دون فحص قيمته ⇒ «5 قدم و7 M» تُقرأ 152.4. أُضيفت `_has_own_unit` التي لا تحتسب الاختصار إلا إذا اجتازت قيمة ذلك العدد بوابة `_is_meter_shorthand_value`.
- اختبار `test_feet_ambiguity_does_not_trust_meter_shorthand_as_unit`: فشل أولاً (152.40 بدل None) ثم نجح. «5 قدم و7 M»/«5 قدم 7 م» ⇒ None؛ «5 قدم و7 انش» ⇒ 170.18؛ «6 قدم 80 كيلو» ⇒ 182.88 و80؛ «6 قدم 1.75 م» ⇒ None (تعارض 182.88 مقابل 175).
- **M12:** حذف فحص القيمة في `_has_own_unit` ⇒ 1 failed؛ الاستعادة مثبتة بمقارنة بايتية مع النسخة الجيدة (`cmp`: متطابق).
- التشغيل على الـVM (بايثون 3.10، حزمة جزئية): ملفات size الثلاثة 46 passed. الحزمة الكاملة و`-m tools` و`static_gate` و`lint-imports` لم تُشغَّل هنا (تبعيات ناقصة) — تُشغَّل على ويندوز قبل الدمج.
- **F-P4-01-DB (تشخيص):** السبب مؤكد — `fp401_runner.ps1` ضبط `CORE_DATABASE_URL` فقط (5433)، بينما `conftest` يُسقط `CORE_MIGRATION_DATABASE_URL`/`CORE_SYSTEM_DATABASE_URL` إلى 127.0.0.1:5432 ⇒ `password authentication failed for user p07_migration` في إعداد كل اختبار db (471 خطأ، ~35 ثانية، عدّادات الصفوف ثابتة). ليس تلوّث بيانات ولا عيب كود Task 8. العلاج: تمرير الروابط الثلاثة جميعاً على 5433 (أو ضبطها في `run_db_suite.py`)، ثم إعادة تشغيل واحدة.


## Task 9 — [DEFERRED - TO BE EXECUTED LAST] (قرار المالك، 2026-10-07)
- تأجيل Task 9 (اختبارات ذهبية وخاصية للمستشار) إلى آخر المهام؛ لا يُصدَر له توجيه الآن. الاختبارات الفرعية لكل فرع موجودة من Task 8.

## T11 — R9 (دين lint) + الإغلاق والدمج (2026-10-07)
- **UNVERIFIED_ENV_LIMIT:** لا شبكة على مضيف المنفّذ (pip: from versions: none حتى بلا تثبيت نسخة، ولا ثنائيات عامة) — تعذّر تثبيت ruff==0.14.10/mypy==1.18.2، فتعذّر قياس 303/103 محلياً؛ المعماري يتحقق على Linux. التعديلات نُفِّذت حرفياً بتوجيه R9.
- **R9 (a–e):** (a) `ToolSpec.run: Callable[..., Any]` + سطر العهدين في docstring السجلّ (ملفوف سطرين — النص الحرفي 148 حرفاً كان سيولّد E501 جديداً يخالف هدف الجولة). (b) `left_field` في `_keyword_at` (mypy :159). (c) `_has_own_unit` ⇒ `return not (found[2] and not _is_meter_shorthand_value(_number_value(tokens[j])))` (SIM103، مكافئ منطقياً). (d) أربعة E741: `l` ⇒ `label` في `_captured_size_tokens`/`_check_size_mismatch`؛ وSIM114 بدمج فرعَي `captured.add(canon)` بـor دون تغيير المعنى. (e) ثلاثة E501 ملفوفة + SIM300 معكوسة. لم تُمس UP035/I001 القديمة ولا B905.
- **قرار ToolSpec:** `Callable[..., Any]` — الأدوات ذات الوسائط الصريحة (resolve_address، size_advise؛ H51: الكود يستخرج الوسائط) لا تستقبل ToolContext، والسجلّ الحرفي لا يفرض توقيعاً واحداً؛ التوثيق الجديد يكتب العهدين صراحةً.
- **البوابات (المرحلة 2، مرة واحدة من core/):** الحزمة النقية **460 passed**, 475 deselected · `-m tools` **4 passed** · static_gate **PASSED — 0 violations** · lint-imports **7 kept, 0 broken** — مطابقة للمتوقع كله.
- **F-P4-02:** أخطاء test_migrate التسعة (sudo -u postgres على Windows) قيد بيئة ويندوز يمكن تجاهله — المرجع 471 على Linux.
- **الإيداعان:** (1) R9 + التوثيق؛ (2) scripts/run_db_suite.py (تعديل -rfE + طباعة المعرّفات). ثم الدمج في p4-links-ai بدمجين --no-ff (p4-task11-size ثم p3-f-p3-24-tombstone).

## T10 — مستودع قراءة جداول المقاسات تحت RLS (2026-10-07)
**F-P4-04: tenants=1 عند البدء.** التعريف (قراءة واحدة): `e9825a8a-41fa-4bd0-be7f-024058d9e870` (Burst Tenant، platform_ref `burst-5f1e8aec-…`، created_at 2026-10-07 02:41 +03) مع 1 channel_account و30 conversations و30 customers و30 outbox. ليس من مستأجري الاختبار الثابتين (1111…/2222…) ⇒ الحالة (ب) HALT. قرار المعماري: الصف من fixture `burst_ctx` (test_dispatch_burst_db.py:87) — teardown لم يُنفَّذ؛ حُذف بـ`delete_tenant_full` (أمر واحد) ⇒ **tenants 0** حرفياً. الدرس: fixtures ذات uuid4 لا تُنظَّف ذاتياً في الجولة التالية بخلاف مستأجري 1111…/2222….
- الملفات المُسلَّمة (المعماري، Linux PG16 @ cdd9312): sha256 (مقطع 16): `8ee9ebdaa43a96d7` repos_size.py · `705bb6def8c68974` test_size_charts_db.py · `ce54fdb7b5318736` testsupport.patch — مطابقة قبل التطبيق.
- التطبيق: `M core/app/db/testsupport.py` + `?? core/app/db/repos_size.py` + `?? core/tests/test_size_charts_db.py`؛ diff --stat: testsupport.py | 44 +++.
- **(3) اختبارات الملف الجديد، جولتان:** الجولة 1: `11 passed in 8.25s`؛ الجولة 2 (`-rfE`): `11 passed in 6.67s`. (6 اختبارات + 5 parametrize.)
- **(4) البوابات بلا قاعدة:** الحزمة النقية **460 passed, 486 deselected** · `-m tools` **4 passed** · static_gate **STATIC GATE PASSED — 0 violations** · lint-imports **Contracts: 7 kept, 0 broken**.
- **(8) الحزمة -m db الكاملة (PG18، 5433، مرة واحدة، 20:04):** `473 passed, 464 deselected, 9 errors in 1204.44s` و**EXIT=1**. الأخطاء التسعة كلها `tests/test_migrate.py` (9 أسطر ERROR منه، 0 خارجها) — F-P4-02 قيد بيئة ويندوز، تنجح على Linux.
- **(10) التحوّلات (استعادة cp+cmp بعد كل منها):** M1 (عكس الأولوية) ⇒ `FAILED …::test_product_chart_beats_category_chart — 1 failed, 10 passed` ⇒ RESTORED_M1؛ M2 (قبول نصف مغلقة) ⇒ `FAILED [[150,165)` + `FAILED [(150,165]] — 2 failed, 9 passed` ⇒ RESTORED_M2؛ M3 (حذف حارس الجدول الفارغ) ⇒ `FAILED …::test_chart_without_rows_is_none — 1 failed, 10 passed` ⇒ RESTORED_M3. الهاش النهائي: `8ee9ebdaa43a96d7` (مطابق للمُسلَّم).
- **RLS:** test_rls_isolates_charts_between_tenants يثبت العزل بالاتجاهين بالبيانات — VERIFIED بالاتجاهين، بلا تحوّل سياسة (الترحيلات ممنوعة التعديل).
- **OQ-P4-18:** لا عمود في size_charts لإعفاء التاجر من حارس الوزن؛ allow_under_weight=False دائماً حتى ترحيل لاحق بقرار المالك.
- **قرار:** SizeChartDataError لا تُترجَم إلى invalid_chart هنا؛ الترجمة مسؤولية مهمة الأسلاك (OQ-P4-13).
- **UNVERIFIED_ENV_LIMIT:** ruff/mypy (لا شبكة) — المرجع: المعماري 303/103 على بيئته.

## T12 — الحلّال النقي GiftCurator (2026-10-07)
الملفات المُسلَّمة (المعماري، Linux فوق cdd9312): sha256 (مقطع 16) `5037514cef2c692c` curator.py · `205ee7a8b00b2f76` gift_init.py · `78d820fc9fe726b3` importlinter.patch · `1d2a7a5f0e87b38a` test_gift_curator.py — مطابقة ×4 قبل التطبيق.
- التطبيق: `M core/.importlinter` (+16: عقد gift-is-pure) + `?? core/app/gift/` + `?? core/tests/test_gift_curator.py`.
- **(3) اختبارات الحلّال:** `22 passed in 0.21s`.
- **(4) البوابات بلا قاعدة:** الحزمة النقية **482 passed, 486 deselected** (= 460 + 22) · `-m tools` **4 passed** · static_gate **PASSED — 0 violations** · lint-imports **8 kept, 0 broken** (العقد الثامن gift-is-pure KEPT).
- **(10) التحوّلات (استعادة cp+cmp بعد كل منها):** M1 (حدّ النافذة الأدنى بالتقريب لأسفل) ⇒ `FAILED …::test_window_low_edge_rounds_up_without_floats — 1 failed, 21 passed`؛ M2 (خمسة عناصر) ⇒ `FAILED …::test_at_most_four_items — 1 failed, 21 passed`؛ M3 (تجاهل نفاد الخطوات) ⇒ `FAILED …::test_step_budget_exhaustion_falls_back_to_greedy` + `FAILED …::test_exhaustion_with_no_greedy_basket — 2 failed, 20 passed`؛ M4 (حذف مكافأة التنويع) ⇒ 3 failed (`test_best_three_ranked_score_then_total_then_ids`، `test_diversity_bonus_breaks_equal_relevance`، `test_step_budget_exhaustion_falls_back_to_greedy`)، 19 passed؛ M5 (استيراد psycopg) ⇒ `app.gift is pure … BROKEN — Contracts: 7 kept, 1 broken`؛ M6 (إلغاء سقف K) ⇒ `FAILED …::test_more_than_k_candidates_are_truncated_by_relevance — 1 failed, 21 passed`. بعد الستة: **22 passed · 8 kept, 0 broken · sha256 `5037514cef2c692c`** (مطابق للمُسلَّم).
- **OQ-P4-19:** «البحث تعداد دقيق محدود بميزانية خطوات حتمية MAX_STEPS=40_000 (≈80ms على Linux) بدل DP مكمَّم ومهلة بالساعة — للحفاظ على دقة النافذة وعدم جمعية مكافأة التنويع والحتمية H45؛ المهلة بالساعة وprocess pool في المستدعي (Task 14)؛ قياس الأداء الرسمي في Task 13.»
- **OQ-P4-20:** «ثوابت التقييم (DIVERSITY_BONUS=50، SHORTFALL_PENALTY=10 لكل نقطة نسبة، relevance عدد صحيح غير سالب) افتراضات مكتوبة؛ مقياس relevance يحدّده مسار الاسترجاع في Task 14.»
- **UNVERIFIED_ENV_LIMIT:** ruff/mypy (لا شبكة) — المرجع: المعماري 303/103.

## T13 — اختبارات حدود وأداء الحلّال (2026-10-07)
- **الالتزام (Step 0):** `ecadef6f4bce7a85720ffe8f1703655dadd28de1` (Task 12، الرأس الحالي؛ tasks.md Task 12 = `[x]` VERIFIED).
- **(3) الملف الجديد:** `18 passed in 0.66s`؛ الأبطأ `test_greedy_quality_floor_on_the_seeded_corpus` (0.18s) — لا اختبار يتجاوز 3 ثوانٍ.
- **(4) البوابات (بلا قاعدة، من core/):** الحزمة النقية **500 passed, 486 deselected** (= 482 + 18) · `-m tools` **4 passed, 982 deselected** · static_gate **STATIC GATE PASSED — 0 violations** · lint-imports **Contracts: 8 kept, 0 broken**.
- **(5) القياس الزمني:** `worst_case_ms median=109.2 max=226.2 reasons=('search_budget_exhausted', 'greedy_fallback')` — reasons حرفي مطابق؛ الوسيط 109.2ms (< 200ms ⇒ لا F-P4-05)؛ أعلى من مرجع Linux (≈80–93ms) وهو متوقع على Windows.
- **(10) التحوّلات (استعادة cp+cmp بعد كل منها):**
  - N1 (الجشع يقبل سلة تحت النافذة) ⇒ `3 failed, 15 passed` (test_greedy_is_always_well_formed_and_labelled · test_greedy_never_beats_the_exhaustive_optimum · test_greedy_quality_floor_on_the_seeded_corpus) ⇒ RESTORED.
  - N2 (الجشع يأخذ 5 عناصر) ⇒ `2 failed, 16 passed` (test_greedy_is_always_well_formed_and_labelled · test_greedy_never_beats_the_exhaustive_optimum) ⇒ RESTORED.
  - N3 (إلغاء تقليم النافذة) ⇒ `1 failed, 17 passed` (test_unreachable_window_is_pruned_without_exhausting_the_budget) ⇒ RESTORED.
  - N4 (إلغاء ميزانية الخطوات) ⇒ `1 failed, 17 passed` (test_dense_k60_case_hits_the_step_budget_and_falls_back) ⇒ RESTORED.
  - N5 (الأرخص أولاً بدل الأعلى relevance) ⇒ `2 failed, 16 passed` (test_greedy_quality_floor_on_the_seeded_corpus · test_dense_k60_case_hits_the_step_budget_and_falls_back) ⇒ RESTORED.
  - بعد الخمسة: **18 passed** · sha256 `5037514cef2c692c` (curator.py غير معدَّل).
- **القرار:** زمن الساعة يُقاس ولا يُؤكَّد في الاختبارات (H7)؛ الضمان البنيوي = ميزانية الخطوات الحتمية (اختبار الحالة الكثيفة) + سقف K؛ المهلة الصلبة بالساعة في المستدعي (Task 14، OQ-P4-19).
- **أرضية الجودة المقاسة:** 181 / 163 / 65 على seed 2026.
- **UNVERIFIED_ENV_LIMIT:** ruff/mypy (لا شبكة) — المرجع: المعماري 303/103.

## T18ب-1 — امتدادات المستخرج وعقد قوالب المقاس (2026-10-07)
- **(2) التطبيق:** M ×3 (`size_extract.py` · `compose.py` · `templates.py`) + ?? ×2 (`test_size_extract_ext.py` · `test_size_templates_contract.py`). diff --stat: size_extract.py 132 · compose.py 38 · templates.py 18 (= 174 إضافة، 14 حذف).
- **(3)-أ الملفان الجديدان:** `49 passed in 0.35s`.
- **(3)-ب الاختبارات المعتمدة:** `101 passed in 0.79s` (test_size_extract.py · test_size_advise_tool.py · test_size_advisor.py · test_verify_rules.py · test_verify.py) — 0 failed، لا انحدار.
- **(4) البوابات (بلا قاعدة):** الحزمة النقية **549 passed, 486 deselected** (= 500 + 49) · `-m tools` **4 passed, 1031 deselected** · static_gate **STATIC GATE PASSED — 0 violations** · lint-imports **Contracts: 8 kept, 0 broken**.
- **(8) الحزمة -m db:** `tenants 0` قبل التشغيل · **473 passed, 553 deselected, 9 errors in 833.58s (0:13:53)** · 9 ERROR كلها `tests/test_migrate.py` (F-P4-02) · 0 FAILED · EXIT=1 — مطابق للرقم بعد Task 10، لا انحدار (مرجع Linux: 482).
- **(10) التحوّلات (استعادة cp+cmp بعد كل منها):**
  - Q1 (حذف بديل الفاصلة من _TOKEN_RE) ⇒ `FAILED …::test_latin_comma_decimal — 1 failed, 69 passed` ⇒ RESTORED.
  - Q2 (تعطيل المتر المركّب) ⇒ `FAILED …::test_compound_meter_reads_one_meter_plus_centimeters — 1 failed, 69 passed` ⇒ RESTORED.
  - Q3 (لا تستثنِ أعداد الجسم من الطول/الوزن) ⇒ `2 failed, 68 passed` (test_body_measurement_is_never_read_as_height · test_body_measurement_with_a_wrong_unit_is_dropped_and_not_reused) ⇒ RESTORED.
  - Q4 (تجاهل النفي) ⇒ `FAILED …::test_fit_preference_negated_or_conflicting_is_unknown — 1 failed, 69 passed` ⇒ RESTORED.
  - Q5 (تسمية ليست بعد «مقاس») ⇒ `3 failed, 67 passed` (test_every_label_placeholder_follows_the_word_maqas · test_a_label_other_than_the_advice_is_caught[42-44] · test_the_advised_size_is_the_primary_one_in_the_text) ⇒ RESTORED. (التوجيه توقّع 2 failed؛ الثالث فشل مشروع إضافي — انظر F-P4-06.)
  - Q6 (الشكل المجهول يسأل بدل التحويل) ⇒ `FAILED …::test_unknown_advice_shape_hands_off — 1 failed, 69 passed` ⇒ RESTORED.
  - Q7 (تبديل المقاس الأساسي بالبديل) ⇒ `FAILED …::test_the_advised_size_is_the_primary_one_in_the_text — 1 failed, 69 passed` ⇒ RESTORED.
  - بعد السبعة: **70 passed** · sha256 `b2f8157391349f5b` · `f8daa307073a7664` · `5c198375c1c1a1af` (مطابقة للمسلَّم).
- **إصلاح ثلاث قراءات خاطئة قائمة:** "1 متر و75"⇒كان 100، "طولي 1,75 م"⇒كان 1، "صدري 100 سم"⇒كان طولاً 100.
- **OQ-P4-21:** «نصوص قوالب المقاس الستة مقترحة وتبقى حرفية حتى اعتماد المالك أو تعديله؛ لا تُرسَل لعميل قبل ذلك (18ب-2 يوصل ولا يفعّل)».
- **القرار:** «الـVerifier يقبل size وalt_size معاً فلا يميّز ترتيبهما؛ الترتيب مثبّت باختبار نصّ حرفي (Q7)».
- **F-P4-06:** نتيجة Q5 تعطي 3 failed لا 2: `test_the_advised_size_is_the_primary_one_in_the_text` (حارس Q7) يفشل أيضاً تحت Q5، لأن نصّ قالب size_recommend مُثبَّت حرفياً في ذلك الاختبار وإزالة «مقاس» منه تكسره. لا يدلّ على عيب في الكود (الملفات بايتية التطابق مع المسلَّم، والحزم كلها خضراء)؛ إنه سدّ ثغرة إضافي في التوجيه وليس فشل تطبيق.
- **UNVERIFIED_ENV_LIMIT:** ruff/mypy (لا شبكة) — المرجع: المعماري 303/103 (بلا زيادة).

- **تصحيح المعماري:** F-P4-06 ليس Finding؛ التوقّع «2 failed» في Q5 قيس قبل إضافة اختبار النصّ الحرفي (سدّ Q7)، والنتيجة الصحيحة 3 failed كما سجّلها المنفّذ. لا تغيير في الكود.

## T18ب-2a — ذاكرة المنتج المعروض (F-P4-07) (2026-10-08)
- **القاعدة:** الفرع `p4-links-ai` · HEAD `309971f` · الحالة نظيفة · تجزئات الملفات الثمانية في HEAD والمسلَّمات العشرة مطابقة.
- **(2) التطبيق:** M ×8 + ?? ×2 (`test_shown_products.py` · `test_shown_products_db.py`). diff --stat: repos_stock 16 · repos_summary 19 · testsupport 14 · join_waitlist 24 · stock 20 · turn 15 · test_p3_consent_cli_db 10 · test_stock_worker 13 ⇒ **111 إضافة، 20 حذف** (مطابق للمعماري).
- **(3) نظافة القاعدة:** `tenants 0`.
- **(4) المستهدفة (نقية + db):** `24 passed in 2.67s`.
- **(5) البوابات (بلا قاعدة):** الحزمة النقية **558 passed, 491 deselected in 6.76s** (= 549 + 9) · `-m tools` **4 passed, 1045 deselected** · static_gate **STATIC GATE PASSED — 0 violations.** · lint-imports **Contracts: 8 kept, 0 broken.**
- **(8) الحزمة -m db:** `EXIT=1` · **478 passed, 562 deselected, 9 errors in 423.57s (0:07:03)** · `9 ERROR tests/test_migrate.py` (F-P4-02) · 0 FAILED (= 473 + 5؛ مرجع Linux: 487). ملاحظة بيئة: الإطلاق الأول بـ`nohup … &` من PowerShell→Git Bash لم يبدأ (لا ملف سجل)؛ أُعيد بنفس أمر pytest ونفس ملفي log/done كمهمة مقدّمة للأداة بإذن المالك.
- **(10) التحوّلات (استعادة cp+cmp بعد كل منها):**
  - W1 (`!= 1` ⇒ `== 0` في stock.py) ⇒ `2 failed, 22 passed` (test_multi_variant_product_never_guesses · test_shown_multi_variant_product_does_not_join) ⇒ RESTORED.
  - W2 (joined بـplatform_variant_id في join_waitlist.py) ⇒ `7 failed, 17 passed` (test_tool_picks_the_last_shown_product · test_single_variant_product_joins_with_that_variant · test_shown_single_variant_product_joins_the_waitlist_end_to_end · test_join_waitlist_registers_once · test_join_waitlist_duplicate_rejected · test_duplicate_metric_delta_exactly_one · test_rejoin_after_stop_restores_the_availability_notice) ⇒ RESTORED.
  - W3 (التسجيل بلا outcome.ok في turn.py) ⇒ `1 failed, 23 passed` (test_shown_products_are_recorded_only_after_the_reply_is_accepted) ⇒ RESTORED.
  - W4 (بلا سقف 3 في repos_summary.py) ⇒ `1 failed, 23 passed` (test_record_shown_products_merges_and_caps_at_three) ⇒ RESTORED.
  - W5 (بلا `p.active = true` في repos_stock.py) ⇒ `1 failed, 23 passed` (test_variant_ids_for_product_under_rls) ⇒ RESTORED.
  - W6 (كل البطاقات بدل أول 3 في turn.py) ⇒ `1 failed, 23 passed` (test_product_list_action_carries_the_three_shown_product_ids) ⇒ RESTORED.
  - بعد الستة: **24 passed** · sha256 `b10410761ee2027b` · `e1c98edf7a9c151f` · `cbd307a4ef57a1d6` · `b96d61c0b6db78ef` · `0aad11d615577437` (مطابقة للمسلَّم).
- **F-P4-07 (مُغلق هنا):** slots.last_shown_product_ids لم يكتبه أي كود إنتاج، وقُرئ كـvariant؛ الآن يُكتب platform_product_id لأول 3 بطاقات بعد قبول الـVerifier، ويحلّ المنسّق المنتج إلى متغيّره الوحيد. قرار المالك 2026-10-08 أجاز تعديل test_stock_worker.py (3) وtest_p3_consent_cli_db.py (1) — القيمة فقط، التوقعات كما هي.
- **F-P4-08 (مفتوح، مهمة منفصلة لاحقة):** repos_summary.update_summary يمرّر 3 معاملات لـ4 علامات وبترتيب خاطئ ⇒ ProgrammingError في كل استدعاء ⇒ ملخّصات المحادثات لم تُحفظ قط (أثبته المعماري على PG16).
- **القرار:** منتج متعدد المتغيّرات ⇒ no_variant (قالب stock_unavailable القائم)؛ اختيار المقاس/اللون للانتظار تحسين لاحق.
- **UNVERIFIED_ENV_LIMIT:** ruff/mypy (لا شبكة) — المرجع: المعماري 302/103.

## F-P4-08 — إصلاح update_summary (المعماري منفّذاً، 2026-10-08)
- **السبب (مُثبَت):** `repos_summary.update_summary` مرّر 3 معاملات لـ4 علامات وبترتيب خاطئ `(tenant_id, Jsonb(slots), conversation_id)` ⇒ `psycopg.ProgrammingError: the query has 4 placeholders but 3 parameters were passed` في كل استدعاء ⇒ ملخّصات المحادثات لم تُحفظ قط. لم يكن هناك اختبار db يغطي الدالة.
- **الإصلاح (سطر واحد):** `(summary, Jsonb(slots), tenant_id, conversation_id)`.
- **اختبار جديد** `core/tests/test_summary_db.py` (4 اختبارات db، RLS): حفظ النص + دمج slots مع حفظ ذاكرة المنتج · ملخّص ثانٍ يستبدل النص وsummary_seq · مستأجر آخر لا يكتب · مفتاح slot مجهول يُرفض قبل أي كتابة.
- **الفاشل أولاً (على الكود قبل الإصلاح، Linux PG16):** `3 failed, 1 passed` — الثلاثة بـ `ProgrammingError: the query has 4 placeholders but 3 parameters were passed` (الرابع: رفض المفتاح المجهول يسبق الكتابة).
- **بعد الإصلاح:** `4 passed in 1.05s`.
- **التحوّلات (استعادة cp+cmp بعد كلٍّ):** S1 تبديل tenant_id/conversation_id ⇒ `2 failed, 2 passed` · S2 نص الملخّص = tenant_id ⇒ `2 failed, 2 passed` · S3 استبدال slots بدل الدمج ⇒ `1 failed, 3 passed` (test_update_summary_stores_the_text_and_merges_slots) · S0 الخطأ الأصلي ⇒ `3 failed, 1 passed`. كلها RESTORED.
- **البوابات (Linux PG16 فوق 9c6b2f9):** النقية `558 passed` · `-m tools` `4 passed` · static_gate `STATIC GATE PASSED — 0 violations` · lint-imports `Contracts: 8 kept, 0 broken` · ruff `Found 302 errors` (بلا زيادة) · mypy `Found 103 errors` (بلا زيادة).
- **الحزمة -m db الكاملة (Linux PG16، قاعدة نظيفة tenants=0):** `491 passed, 562 deselected in 304.79s (0:05:04)` — 0 failed، 0 errors (= 487 + 4).
- **UNVERIFIED_ENV_LIMIT:** لم تُشغَّل على PG18 لديك (قاعدة 5433 غير قابلة للوصول من بيئة المعماري). المتوقع هناك: 482 passed + 9 errors في test_migrate (F-P4-02).
- **التطابق على جهاز المالك:** `repos_summary.py` = `9fa468924d2cc72e` · `test_summary_db.py` = `31b9e6b4569343c6` — مطابقان بايتياً لما اختُبر.
- **أثر تشغيلي:** بعد الإيداع يبدأ عامل الملخّصات بحفظ الملخّصات فعلاً للمرة الأولى (كان يفشل بصمت في كل محاولة).

## Task 18ب-2b — توصيل مستشار المقاس بدورة المحادثة، مُطفأ افتراضياً (المعماري منفّذاً، 2026-10-08)
- **قرارات المالك المنفَّذة:** كشف سؤال المقاس بقاعدة كود لا بنيّة موجِّه (لا تغيير في ROUTER_SYSTEM_PROMPT)؛ المنتج = المنتج الوحيد المعروض آخر مرة (slots.last_shown_product_ids، F-P4-07)؛ النصوص الستة المعتمدة (OQ-P4-21).
- **الملفات:** `app/workers/config.py` (`size_advice_enabled: bool = False` + `SIZE_ADVICE_ENABLED` اختياري، ليس _required ⇒ لا مسّ لـdocker-compose/S15) · `app/workers/size.py` (جديد: `is_size_question` + `advise_for_turn`) · `app/workers/turn.py` (`_TurnPlan.size_turn`، `_Action.size_context`، `_size_action`، فرع في `_resolve_action` بعد order_lookup، كشف في المرحلة 1 فقط حين `route and settings.size_advice_enabled` ثم `route = False`، وتمرير `size_context` إلى `insert_verified_outbox`) · اختباران جديدان.
- **السلوك مُطفأً:** لا يُستدعى `is_size_question` أصلاً ⇒ الدورة كما هي حرفياً (اختبار طرفي: نفس handoff_notice).
- **السلوك مُشغَّلاً:** يسبق الكشفَ كلُّ مسار حتمي (موافقة/إيقاف، kill switch، طلب موظف، سقف الردود) لأنه لا يعمل إلا عند bot_cannot_answer؛ لا نموذج يُستدعى لسؤال مقاس؛ منتج غير وحيد ⇒ handoff (size_no_product)؛ جدول تالف ⇒ invalid_chart ⇒ size_no_chart (handoff)؛ size_no_fit ⇒ handoff؛ شكل مجهول ⇒ handoff (size_unknown)؛ need_inputs/recommend/nearest ⇒ بلا handoff؛ الـVerifier يتلقى `SizeContext(size, alt, labels)` بتسميات الجدول.
- **الاختبارات:** `tests/test_size_turn.py` (26 نقية) · `tests/test_size_turn_db.py` (7 db، أول اختبار طرفي لـ`process_turn` الحقيقي مع قواعد الـVerifier الحقيقية).
- **النتائج (Linux PG16 فوق 50bdae0):** المستهدفة `33 passed in 2.42s` · النقية `584 passed, 502 deselected` (= 558 + 26) · `-m tools` `4 passed` · static_gate `STATIC GATE PASSED — 0 violations` · lint-imports `Contracts: 8 kept, 0 broken` · ruff `Found 302 errors` (بلا زيادة) · mypy `Found 103 errors` (بلا زيادة).
- **الحزمة -m db الكاملة (Linux PG16، tenants=0):** `498 passed, 588 deselected in 300.07s (0:05:00)` — 0 failed، 0 errors (= 491 + 7).
- **التحوّلات (استعادة cp+cmp بعد كلٍّ، كلها RESTORED):** Z1 تجاهل العلم ⇒ `1 failed` (test_flag_off_keeps_todays_generic_handoff) · Z2 العلم مُشغَّل افتراضياً ⇒ `1 failed` (test_flag_defaults_to_off) · Z3 تخمين المنتج من عدة معروضة ⇒ `2 failed` · Z4 عدم تمرير size_context ⇒ `1 failed` · Z5 no_chart بلا handoff ⇒ `1 failed` · Z6 الكشف بالكلمات فقط ⇒ `3 failed` · Z7 عدم تخطّي الموجِّه ⇒ `1 failed` (test_size_turn_never_calls_the_model).
- **التطابق على جهاز المالك:** turn.py `3a99f313bc1571b8` · size.py `392a5b8e07136b76` · config.py `b63f3ab0f556b0ab` · test_size_turn.py `e50721438816bc3f` · test_size_turn_db.py `7ea3a9b69a52273c`.
- **UNVERIFIED_ENV_LIMIT:** لم تُشغَّل على PG18 لديك (5433 غير قابلة للوصول من بيئة المعماري). المتوقع هناك: 489 passed + 9 errors (test_migrate، F-P4-02).
- **F-P4-09 (مفتوح، حرج، خارج نطاق هذه المهمة — لم يُصلَح):** كل دورة تصل الموجِّه (LLM router) تنهار في `turn._account`: `run_router` يعيد `RouteResult(usage=result)` حيث result من نوع `LlmJsonResult`، بينما `_account` يقرأ `usage.provider` كأنه `LlmUsage` ⇒ `AttributeError: 'LlmJsonResult' object has no attribute 'provider'` ⇒ تُلغى معاملة الكتابة ولا يُرسَل أي رد. أعاد المعماري إنتاجه على 50bdae0 دون أي تغيير من هذه المهمة. يطال كل المسارات الموجَّهة: بحث المنتجات، أسئلة السياسة، تتبّع الطلبات، العنوان، قائمة الانتظار. لا اختبار طرفي كان يغطيه (هذا أول اختبار لـprocess_turn).

## F-P4-09 — إصلاح انهيار كل دورة موجَّهة (المعماري منفّذاً، 2026-10-09)
- **السبب (مُثبَت على 50bdae0 ثم على 7e80cbd):** `app/llm/router.run_router` كان يعيد `RouteResult(usage=result)` حيث `result` من نوع `LlmJsonResult` (data + usage)، بينما `turn._account` يقرأ `usage.provider/model/input_tokens` كأنه `LlmUsage` ⇒ `AttributeError: 'LlmJsonResult' object has no attribute 'provider'` داخل معاملة الكتابة ⇒ تُلغى المعاملة ولا يُكتب أي رد. يطال كل مسار موجَّه: بحث المنتجات، أسئلة السياسة، تتبّع الطلبات، العنوان، قائمة الانتظار، والرد الافتراضي نفسه. لم يكشفه mypy لأن `_account` يستقبل `router_result: Any`، ولم يكن هناك اختبار طرفي لدورة موجَّهة.
- **الإصلاح (في المصدر، router.py):** `usage=result.usage` + تصحيح النوع `RouteResult.usage: LlmUsage | None` (الاسم «usage» يعني الاستهلاك نفسه). لا تغيير في turn.py.
- **testsupport:** `seed_catalog_product(..., title=...)` معامل اختياري بقيمة افتراضية سابقة (لا أثر على الاستدعاءات القائمة) + `fetch_llm_calls(dsn, tenant_id)`.
- **اختبار جديد** `core/tests/test_router_turn_db.py` (4): دورة موجَّهة حقيقية (process_turn + FakeLlmProvider + قواعد الـVerifier الحقيقية) تكتب ردّاً وتُحتسب في llm_calls `('router','fake','ok')` · بحث منتج موجَّه يعرض البطاقة ويُسجّل `last_shown_product_ids == ['P-SHIRT']` (أول إثبات طرفي لـF-P4-07) · رسالة غير مقاسية مع SIZE_ADVICE_ENABLED تُوجَّه كما كانت (النصف الذي حجبه F-P4-09 من اختبار 18ب-2b) · عقد `run_router`: `usage` من نوع `LlmUsage`.
- **الفاشل أولاً (على الكود قبل الإصلاح، Linux PG16):** `4 failed` — ثلاثة بـ `AttributeError: 'LlmJsonResult' object has no attribute 'provider'` (turn.py:321) والرابع بفشل عقد النوع.
- **بعد الإصلاح:** `4 passed in 1.23s`.
- **التحوّلات (استعادة cp+cmp، كلها RESTORED):** R1 إعادة الخطأ الأصلي `usage=result` ⇒ `4 failed` · R2 حذف استدعاء `_account` ⇒ `2 failed, 2 passed` (الاحتساب في llm_calls مُثبَت) · R3 عدم تسجيل المنتجات المعروضة ⇒ `1 failed, 3 passed`.
- **البوابات (Linux PG16 فوق 7e80cbd):** النقية `584 passed, 506 deselected` · `-m tools` `4 passed` · static_gate `STATIC GATE PASSED — 0 violations` · lint-imports `Contracts: 8 kept, 0 broken` · ruff `Found 302 errors` (بلا زيادة) · mypy `Found 103 errors` (بلا زيادة).
- **الحزمة -m db الكاملة (Linux PG16، tenants=0):** `502 passed, 588 deselected in 284.80s (0:04:44)` — 0 failed، 0 errors (= 498 + 4).
- **التطابق على جهاز المالك:** router.py `94bacfb88b526b51` · testsupport.py `8ae5d5937ff941ce` · test_router_turn_db.py `ce58b079c1414e86`.
- **UNVERIFIED_ENV_LIMIT:** لم تُشغَّل على PG18 لديك. المتوقع هناك: 493 passed + 9 errors (test_migrate، F-P4-02).
- **ملاحظة:** تعليق «NOTE (F-P4-09, open)» في `test_size_turn_db.py` (اختبار معتمد) صار قديماً؛ لم يُعدَّل احتراماً لقاعدة عدم تعديل الاختبارات المعتمدة — يُحدَّث بقرار المالك إن رغب.
- **أثر تشغيلي:** بعد النشر تعود الدورات الموجَّهة للرد فعلاً؛ تبقى SIZE_ADVICE_ENABLED=false حتى قرار المالك.

## Size Advisor Activation — تشغيل مستشار المقاس افتراضياً (المعماري منفّذاً، 2026-10-09)
- **قرار المالك:** `SIZE_ADVICE_ENABLED` مُشغَّل. التنفيذ: `WorkerSettings.size_advice_enabled = True` و`_bool("SIZE_ADVICE_ENABLED", True)` في `core/app/workers/config.py`؛ ضبط `SIZE_ADVICE_ENABLED=false` في البيئة يعيد السلوك السابق حرفياً.
- **تعديل اختبار معتمد بقرار المالك:** `tests/test_size_turn.py::test_flag_defaults_to_off` ⇒ `test_flag_defaults_to_on` (يثبت الافتراضي True). وتحديث التعليق القديم «NOTE (F-P4-09, open)» في `tests/test_size_turn_db.py` إلى «fixed in f7eb5f1» — لم يكن قد حُدِّث في f7eb5f1 خلافاً لملاحظة الحالة (git show --stat f7eb5f1 لا يتضمن الملف).
- **النتائج (Linux PG16 فوق f7eb5f1، العلم مُشغَّل):** النقية `584 passed, 506 deselected in 6.65s` · `-m tools` `4 passed` · static_gate `STATIC GATE PASSED — 0 violations` · lint-imports `Contracts: 8 kept, 0 broken` · ruff `Found 302 errors` (بلا زيادة) · mypy `Found 103 errors` (بلا زيادة).
- **الحزمة -m db الكاملة (العلم مُشغَّل، tenants=0):** `502 passed, 588 deselected in 282.94s (0:04:42)` — 0 failed، 0 errors.
- **F-P4-10 (مفتوح، انحدار سلوكي لا تكشفه الحزمة):** مع التشغيل، أي رسالة فيها كلمة «مقاس/مقاسي» تُحوَّل لمسار المقاس قبل الموجِّه، فطلب بحث يذكر مقاساً يفقد قائمة المنتجات ويُحوَّل لموظف. فحص طرفي مؤقت (process_turn + FakeLlmProvider + قواعد الـVerifier، نفس البذرة: منتج «منتج قميص قطني»، لا منتج معروض سابقاً):
  - `'منتج قميص مقاس L'`: مُطفأ ⇒ `product_list` (router_calls=1) · مُشغَّل ⇒ `size_no_product` / `handoff_notice` (router_calls=0)
  - `'عندكم القميص مقاس L؟'`: مُطفأ ⇒ `handoff` (router_calls=1) · مُشغَّل ⇒ `size_no_product` / `handoff_notice` (router_calls=0)
  - `'ابي منتج قميص مقاسي 42'`: مُطفأ ⇒ `product_list` (router_calls=1) · مُشغَّل ⇒ `size_no_product` / `handoff_notice` (router_calls=0)
  السبب: قاعدة الكشف في `size.is_size_question` تقبل كلمة المقاس وحدها. لا اختبار قائم يغطي «بحث منتج يذكر مقاساً». **توصية المعماري:** عدم النشر مُشغَّلاً قبل تضييق القاعدة (مثلاً: مسار المقاس فقط عند وجود طول/وزن/قياس جسم، أو كلمة مقاس مع منتج معروض واحد وبلا تسمية مقاس صريحة؛ وإلا يُترك للموجِّه) + اختبار طرفي يثبت بقاء بحث المنتج.
- **التطابق على جهاز المالك:** config.py `37ac37a6a9123bc5` · test_size_turn.py `c1387958452500dc` · test_size_turn_db.py `305e2fb379d5a480`.
- **UNVERIFIED_ENV_LIMIT:** لم تُشغَّل على PG18 لديك. المتوقع: 493 passed + 9 errors (test_migrate، F-P4-02).

## F-P4-10 — تضييق قاعدة كشف سؤال المقاس (المعماري منفّذاً، 2026-10-09)
- **السبب:** `size.is_size_question` كانت تقبل كلمة «مقاس/size» وحدها ⇒ مع التشغيل، بحث منتج يذكر مقاساً («منتج قميص مقاس L») يُخطف إلى مسار المقاس ويُحوَّل لموظف بدل عرض المنتجات (سُجِّل في 8d7fa44).
- **الإصلاح (`core/app/workers/size.py` وحده):** سؤال المقاس = وجود قياس جسم **معقول** واحد على الأقل: طول 100–230 سم أو وزن 30–250 كغ (ثوابت المستشار نفسها `HEIGHT_CM_MIN/MAX`، `WEIGHT_KG_MIN/MAX`) أو صدر/خصر/ورك 30–250 سم. حُذفت مفردات `_SIZE_WORDS` والاعتماد على `arabic` من الوحدة. بلا قياس ⇒ يبقى للموجِّه كما كان.
- **اختبار جديد** `core/tests/test_size_trigger_db.py` (5، دورات حقيقية بـprocess_turn + FakeLlmProvider + قواعد الـVerifier، والعلم مُشغَّل صراحة): «منتج قميص مقاس L» و«ابي منتج قميص مقاسي 42» ⇒ `product_list` عبر الموجِّه (router_calls=1) · «عندكم القميص مقاس L؟» ⇒ يذهب للموجِّه · «طولي 170 ووزني 65 ايش مقاسي» ⇒ `size_recommend` «المقاس المناسب لك: مقاس M ✅» بلا نموذج · العلم مُطفأ ⇒ بحث المنتج كما هو.
- **الفاشل أولاً (على الكود قبل الإصلاح):** `3 failed, 2 passed` (اختبارا بحث المنتج + اختبار كلمة المقاس بلا قياس).
- **تعديل اختبارات معتمدة بقرار المالك (لأنها ترمّز القاعدة القديمة):** `tests/test_size_turn.py` — «كم مقاسي؟»/«ايش المقاس المناسب»/«what size» انتقلت من «مكتشَف» إلى «غير مكتشَف»، وأُضيفت «منتج قميص مقاس L»، «ابي منتج قميص مقاسي 42»، «وزني 2 كيلو»، «طولي 50» إلى غير المكتشَف، و«وزني 70»، «طولي 170 ايش مقاسي» إلى المكتشَف · `tests/test_size_turn_db.py::test_flag_on_missing_inputs_asks_without_handoff` — الرسالة «كم مقاسي؟» ⇒ «طولي 170 ايش مقاسي» (طول بلا وزن ⇒ size_need_inputs) + تعليق F-P4-09 المحدَّث («fixed in f7eb5f1»). قبل تعديلها: `4 failed, 29 passed` (الأربعة هي بالضبط ما يرمّز القاعدة القديمة).
- **بعد الإصلاح:** الملفات الثلاثة `44 passed`.
- **التحوّلات (استعادة cp+cmp، كلها RESTORED):** F1 إعادة كلمة المقاس كمحفّز ⇒ `8 failed` (5 نقية + 3 طرفية) · F2 أي وزن بلا حدّ معقولية ⇒ `1 failed` («وزني 2 كيلو») · F3 حذف فرع قياسات الجسم ⇒ `1 failed` («صدري 100 سم») · F4 أي طول بلا حدّ ⇒ `1 failed` («طولي 50»).
- **البوابات (Linux PG16 فوق 8d7fa44):** النقية `590 passed, 511 deselected` (= 584 + 6) · `-m tools` `4 passed` · static_gate `STATIC GATE PASSED — 0 violations` · lint-imports `Contracts: 8 kept, 0 broken` · ruff `Found 302 errors` (بلا زيادة) · mypy `Found 103 errors` (بلا زيادة).
- **التشغيل A (العلم مُطفأ افتراضياً كما سيُودَع):** -m db الكاملة `507 passed, 594 deselected in 280.67s (0:04:40)` — 0 failed، 0 errors.
- **التشغيل B (العلم مُشغَّل افتراضياً مؤقتاً في config.py ثم استُعيد بـcmp):** النقية `1 failed, 589 passed` — الوحيد `test_flag_defaults_to_off` (يثبت الافتراضي المُطفأ بالتعريف) · -m db الكاملة `507 passed, 594 deselected in 286.32s (0:04:46)` — 0 failed، 0 errors.
- **التطابق على جهاز المالك:** size.py `b3b1a44592d1a2c7` · test_size_turn.py `da2fb67e0b7c50f8` · test_size_turn_db.py `6ab7344418026278` · test_size_trigger_db.py `73bd5bf94aee011e` · config.py بلا تغيير `b63f3ab0f556b0ab`.
- **UNVERIFIED_ENV_LIMIT:** PG18 لديك غير مُشغَّل. المتوقع: 498 passed + 9 errors (test_migrate، F-P4-02).
- **أثر سلوكي مقصود:** «كم مقاسي؟» بلا أي قياس تذهب الآن للموجِّه (لا تتلقى قالب طلب الطول والوزن)؛ قالب size_need_inputs يصل حين يذكر العميل قياساً واحداً ناقصاً.

## Size Advisor Default ON — تشغيل مستشار المقاس افتراضياً (المعماري منفّذاً، 2026-10-09)
- **قرار المالك:** بعد إغلاق F-P4-09 وF-P4-10، مستشار المقاس مُشغَّل افتراضياً. `core/app/workers/config.py`: `size_advice_enabled: bool = True` و`_bool("SIZE_ADVICE_ENABLED", True)`؛ `SIZE_ADVICE_ENABLED=false` يعيد السلوك السابق حرفياً.
- **تعديل اختبار معتمد بقرار المالك:** `tests/test_size_turn.py::test_flag_defaults_to_off` ⇒ `test_flag_defaults_to_on` (يثبت True).
- **النتائج (Linux PG16 فوق 0f94936، العلم مُشغَّل افتراضياً):** النقية `590 passed, 511 deselected in 7.96s` · `-m tools` `4 passed, 1097 deselected` · static_gate `STATIC GATE PASSED — 0 violations` · lint-imports `Contracts: 8 kept, 0 broken` · ruff `Found 302 errors` (بلا زيادة) · mypy `Found 103 errors` (بلا زيادة).
- **الحزمة -m db الكاملة (tenants=0):** `507 passed, 594 deselected in 277.06s (0:04:37)` — 0 failed، 0 errors.
- **التطابق على جهاز المالك:** config.py `413afc2f41bbccf0` · test_size_turn.py `e7607b4cd5e64d8a`.
- **UNVERIFIED_ENV_LIMIT:** PG18 لديك غير مُشغَّل. المتوقع: 498 passed + 9 errors (test_migrate، F-P4-02).
- **أُودِع بتفويض المالك** برسالة `feat(p4): enable size advisor by default`.

## Task 17 — الدومين وTLS والبروكسي المحلي (المعماري منفّذاً، 2026-10-09)

**قرار المالك:** النطاق الرسمي `sharwa.app`؛ المحرك على النطاق الفرعي المخصّص **`api.sharwa.app`** (لأن `sharwa.app` و`*.sharwa.app` لمتاجر شروه). لا SSH ولا VPS ولا DNS ولا commit في هذه الجولة.

**الملفات:**
- جديد `ops/proxy/Caddyfile` (الموصى به — شهادة Let's Encrypt تلقائية). قائمة سماح: `/console/*` `/v1/*` (ومنها `/v1/ws`) `/healthz` والـwebhookان (سقف 512 KB)؛ كل ما عداه 404 من البروكسي (ومنه `/metrics` `/readyz` `/docs` `/openapi.json`). HSTS سنة، لا ترويسة Server، سجل JSON يحذف `ticket` من الـquery.
- جديد `ops/proxy/nginx-api.sharwa.app.conf` (بديل فقط إن كان nginx يملك 80/443). القائمة نفسها؛ certbot webroot؛ السجل بـ`$uri` (بلا query).
- جديد `ops/proxy/check_proxy.sh` — فحص ما بعد التشغيل للمالك (GET/HEAD + POST كبير يرفضه البروكسي فقط؛ لا توكن ولا توقيع).
- جديد `core/tests/test_proxy_config.py` — 5 اختبارات ثابتة (pure).
- `docker-compose.yml` + `.env.example`: تمرير `CONSOLE_ALLOWED_ORIGINS` و`CONSOLE_SSO_PLATFORM_ORIGINS` إلى `api` (افتراضي فارغ = سلوك اليوم بالضبط) — F-P4-11.
- `docs/VPS_DEPLOYMENT_PLAYBOOK.md`: §12 جديد (الدومين/TLS)، و§7 صار يحيل إليه بدل مقطع Caddy القديم (`console.<دومينك>` ⇐ 8000).
- `tasks.md`: Task 17 ⇐ [x].

**F-P4-11 (اكتُشف وأُصلح هنا):** compose لم يكن يمرّر `CONSOLE_ALLOWED_ORIGINS` ولا `CONSOLE_SSO_PLATFORM_ORIGINS` إلى `api`. الأثر عبر أي نطاق: `api/ws.py` يرفض مصافحة `/v1/ws` من المتصفح (Origin ليس في قائمة فارغة ⇒ 1008) فيفقد صندوق الوارد التحديث الحيّ، والدخول الموحد (Task 6ب) معطّل دائماً في الإنتاج. الإصلاح تمرير بافتراضي فارغ.

**OQ-P4-22 (لشروه):** يجب حجز اسم المتجر/الـschema `api` في شروه — سجل DNS الصريح `api` يتقدّم على `*.sharwa.app`، فمتجر باسم `api` سيصبح غير قابل للوصول.

**التحقق (حرفياً، الحاوية Linux):**
```
$ caddy version                         -> v2.10.2
$ caddy fmt --diff Caddyfile            -> FMT_OK (لا فرق)
$ caddy validate --config Caddyfile --adapter caddyfile -> Valid configuration
$ nginx -v                              -> nginx/1.24.0 (Ubuntu)
$ nginx -t (الملف كاملاً، شهادة ذاتية التوقيع، بلا listen [::] لأن الحاوية بلا IPv6)
nginx: the configuration file /tmp/ngt/nginx.conf syntax is ok
nginx: configuration file /tmp/ngt/nginx.conf test is successful
$ nginx -t (المرحلة 1 من §12.3-ب: كتلة 80 فقط)  -> test is successful
```
اختبار توجيه حيّ: كل بروكسي يعمل على 80/443 أمام خادم بديل على `127.0.0.1:8100` يعيد المسار الواصل (+ WebSocket صدى):
```
Caddy  check_proxy.sh -> 20 passed, 0 failed   (SKIP شهادة: -k مع شهادة محلية)
nginx  check_proxy.sh -> 20 passed, 0 failed
كلاهما:
forwarded: api.sharwa.app https /v1/me
300KB webhook reached api: {'upstream_path': '/webhooks/platform/cart', 'body_len': 300000, 'xfp': 'https', 'host': 'api.sharwa.app'}
ws through proxy: echo:hi:origin=https://api.sharwa.app
ticket in access log: False | /v1/ws logged: True
```
أول تشغيل لـnginx كشف عيبين أُصلحا قبل الجولة الخضراء: `/v1` ⇐ 301 (أُضيف `location = /v1 { return 404; }`)، و`Server: nginx` لا يُحذف بلا وحدة إضافية (الفحص صار «لا رقم إصدار» — Caddy لا يرسل الترويسة أصلاً).

**فحص التحوّل (mutation):**
```
Caddy: catch-all ⇐ reverse_proxy + حذف فلتر ticket  -> check_proxy 10 passed, 10 failed؛ ticket in access log: True
nginx: catch-all ⇐ proxy_pass + $request_uri في السجل -> check_proxy 11 passed, 9 failed؛ ticket in access log: True
test_proxy_config.py، 5 تحوّلات (حذف سطر compose، حذف delete ticket، catch-all Caddy، $request_uri، catch-all nginx) -> كل واحد: 1 failed, 4 passed
بعد الاستعادة -> 5 passed
```
**الحزم (الحاوية على 0f94936 + ملفات الجهاز المعدّلة):**
```
pytest -q                      -> 595 passed, 511 deselected in 8.11s   (590 + 5 الجديدة)
pytest -q -m tools             -> 4 passed, 1102 deselected
static_gate.py                 -> STATIC GATE PASSED — 0 violations.
lint-imports                   -> Contracts: 8 kept, 0 broken.
ruff check .                   -> Found 302 errors.   (دون تغيير)
mypy app                       -> Found 103 errors in 33 files   (دون تغيير)
scripts/check_env.py (الجهاز) -> ENV CHECK PASSED — 0 required keys missing.
```
`-m db` لم يُعَد: لا كود منتج تغيّر (compose/env/وثائق/اختبار ثابت فقط)؛ آخر نتيجة خضراء 507 passed عند 3d67ef2.

**UNVERIFIED:** كل شيء على الـVPS (DNS، إصدار الشهادة، من يملك 80/443، IPv6، إصدار Caddy/nginx هناك)؛ و`nginx -t` بسطري `listen [::]` (الحاوية بلا IPv6).

**توقّف:** لا commit. بانتظار مراجعة المالك.

**مراجعة المالك (2026-10-09):** Task 17 معتمدة، وإصلاح F-P4-11 معتمد. **قرار OQ-P4-22:** يُضاف `api` إلى قائمة أسماء المستأجرين/المتاجر المحجوزة — لا يُسمح لمتجر باستخدام نطاق فرعي للبنية التحتية. التنفيذ في مستودع شروه (Django، مُنشئ الـschema) لا في هذا المستودع؛ يبقى بنداً مفتوحاً هناك حتى يُنفَّذ. ضُمَّ `.env.example` و`tasks.md` إلى الـcommit لأنهما جزء من تغييرات Task 17 نفسها.

## Task 18 — النشر على الـVPS (المالك ينفّذ، المعماري يراجع) — الجولة 1 (2026-10-09)

مخرجات المالك من الخادم (Contabo، Ubuntu 24.04.5، `217.216.77.222`):
- المنفذان 80/443: **لا مستمع** ⇒ الطريق Caddy (§12.3-أ).
- `dig +short api.sharwa.app A` ⇒ **فارغ**: سجل DNS غير موجود/لم ينتشر بعد ⇒ لا تثبيت لـCaddy قبل ظهوره (إصدار الشهادة سيفشل).
- IPv6 يعمل: `2407:3641:2359:5272::1` (`curl -6` أعاده) ⇒ سجل AAAA آمن.
- ufw: active، 22/tcp فقط ⇒ 80/443 مغلقان.
- نسخة احتياطية: `/root/backup_2026-10-09_0119.sql.gz` (27K) — لم يُتحقَّق من اكتمالها بعد.
- الدمج: `master` c0a8e6e + `p4-links-ai` ⇒ `517c143` (106 ملفاً)؛ compose يحوي `CONSOLE_ALLOWED_ORIGINS` (سطر 309).
- الحاويات تعمل **بالصور القديمة** (api منذ 4 أيام على 127.0.0.1:8100، worker-realtime منذ 3 أيام): كود P4 لم يُفعَّل بعد. الترحيل `0019_p3_cart_tombstones` جديد ⇒ يُطبَّق قبل إعادة البناء (وإلا يرفض entrypoint الإقلاع).
- فحص المعماري لـ`origin/main..d79e2ec`: لا مفاتيح `_required` جديدة (فقط اختيارية: COMMERCE_BASE_URL، COMMERCE_API_SECRET، SIZE_ADVICE_ENABLED)، ولا تغيير في الاعتماديات/Dockerfile.

## Task 18 — الجولة 2: النشر مكتمل (تقرير المالك، 2026-10-09)

**بحسب تقرير المالك (لم يرَ المعماري مخرجات الخادم لهذه الجولة ⇒ مُسجَّل كما أُبلِغ):**
- المرحلتان أ وب مكتملتان على Contabo (`217.216.77.222`).
- **النطاق الإنتاجي الفعلي: `ai.sharwaah.com`** (لا `api.sharwa.app` الموثّق في Task 17) — قرار المالك.
- Caddy مثبّت وأصدر شهادة Let's Encrypt بنجاح.
- `check_proxy.sh` عُدِّل يدوياً على الخادم للنطاق الجديد ⇒ **21 passed, 0 failed** (20 فحصاً + فحص الشهادة الذي يُتخطّى محلياً مع `-k`).
- حاويتا api وworker-realtime تعملان بالصورة الجديدة خلف البروكسي.

**بنود مفتوحة ناتجة عن تغيير النطاق (لم تُنفَّذ؛ تُعرض على المالك):**
- **F-P4-12 (توثيق/إعداد):** المستودع ما زال يذكر `api.sharwa.app` في `ops/proxy/nginx-api.sharwa.app.conf`، والقيمة الافتراضية في `ops/proxy/Caddyfile` و`check_proxy.sh`، و§12 من الدليل، و`test_proxy_config.py` (اسم الملف فقط). على الخادم يعمل Caddy بالنطاق الجديد (تعديل محلي أو `SHARWA_API_DOMAIN`) ⇒ نسخة الخادم تنحرف عن المستودع حتى يُوحَّد.
- **تحقق مطلوب:** `CONSOLE_ALLOWED_ORIGINS` في `.env.prod` يجب أن يكون `https://ai.sharwaah.com` (لا `https://api.sharwa.app` من الجولة 1)، وإلا يرفض `/v1/ws` اللوحة. UNVERIFIED.
- **OQ-P4-22 يتحوّل:** إن كانت متاجر شروه على `*.sharwaah.com` فالاسم المحجوز الواجب هو **`ai`** (إضافة إلى `api`).
- **بقية Task 18 كما في tasks.md:** تحديث روابط الـwebhook في شروه إلى `https://ai.sharwaah.com/webhooks/platform/{catalog,cart}` وإغلاق نفق SSH — لم يُذكرا في التقرير. Task 18 عُلِّمت مكتملة بأمر المالك.
- إعادة تشغيل الخادم المعلّقة (`System restart required`) لم تُنفَّذ بعد.

## F-P4-12 — توحيد نطاق المستودع على `ai.sharwaah.com` (المعماري منفّذاً، 2026-10-09)

**أمر المالك:** مطابقة المستودع لما يعمل على الـVPS. التغييرات: `ops/proxy/Caddyfile` (الافتراضي `{$SHARWA_API_DOMAIN:ai.sharwaah.com}`)، `check_proxy.sh` (الافتراضي `https://ai.sharwaah.com`)، `git mv ops/proxy/nginx-api.sharwa.app.conf ops/proxy/nginx-ai.sharwaah.com.conf` (server_name + مسار الشهادة)، §12 في الدليل (النطاق، DNS `ai`، روابط الـwebhook، `CONSOLE_ALLOWED_ORIGINS=https://ai.sharwaah.com`، `CONSOLE_SSO_PLATFORM_ORIGINS=https://*.sharwaah.com`، وحجز `ai` و`api` — OQ-P4-22)، تعليقات `.env.example` و`docker-compose.yml`، و`test_proxy_config.py` (+ اختبار سادس `test_configs_name_the_production_domain`، ويتأكد أن `sharwa.app` لم يبقَ في ملفات البروكسي).

**التحقق (حرفياً، الحاوية):**
```
pytest tests/test_proxy_config.py -> 6 passed
caddy validate -> Valid configuration ; caddy fmt --diff -> FMT_OK
nginx -t (بلا listen [::]) -> configuration file /tmp/ngt/nginx.conf test is successful
bash -n check_proxy.sh -> SH_OK
توجيه حيّ أمام الخادم البديل بالنطاق الجديد: Caddy 20 passed, 0 failed ; nginx 20 passed, 0 failed
```
**مستمر بعد الـcommit:** النسخة المعدّلة يدوياً على الخادم يمكن الآن استبدالها بـ`ops/proxy/Caddyfile` من المستودع (مطابقة). UNVERIFIED على الخادم.

## Task 15 — محوّل التضمين الدلالي (OpenAI) + F-P4-13 (المعماري منفّذاً، 2026-10-09)

**قرار المالك (يحسم OQ-P4-02):** لا تأجيل. مزوّد التضمين = OpenAI بالنموذج `text-embedding-3-small`، مع بقاء `local` بديلاً. DeepSeek يبقى نموذج اللغة الوحيد (استبعاد OpenAI في P4.2 يخصّ الـLLM فقط).

**الكود:**
- جديد `core/app/llm/adapters/openai_embedding.py`: `OpenAIEmbeddingProvider` عبر الـSDK الرسمي (`max_retries=0`، H39)؛ يطلب دائماً `dimensions=EMBEDDING_DIM` (1024، H44 — العرض الأصلي 1536)؛ يعيد ترتيب المتجهات حسب `index`؛ النص الفارغ ⇐ متجه صفري بلا استدعاء مدفوع؛ نقص متجه ⇐ `EmbeddingProviderError`؛ أخطاء الـSDK ⇐ `EmbeddingTimeoutError`/`EmbeddingProviderError` (القاطع + الفشل المفتوح H42 يبقيان المتحكّمَين)؛ `model_name`.
- `app/llm/registry.py`: `build_embedding_provider` صار يستورد `app/llm/adapters/<name>_embedding.py` بالاسم (مثل `build_provider`) ⇒ السجلّ لا يسمّي مزوّداً (S8).
- `app/workers/config.py`: `REAL_EMBEDDING_PROVIDERS={"openai"}`؛ `OPENAI_API_KEY` إلزامي عند اختياره (H5)؛ `EMBEDDING_MODEL` (افتراضي text-embedding-3-small)، `OPENAI_BASE_URL`؛ سطر سعر بوحدة الجدول (20 micro-USD/1k = ‎$0.02/M؛ ‎-large 130).
- `docker-compose.yml` (worker-realtime): `OPENAI_API_KEY`، `EMBEDDING_MODEL`؛ الافتراضي يبقى `EMBEDDING_PROVIDER=local` (مُظلم، المادة 11). `.env.example` + الدليل §8.6.
- تكلفة تضمين الكتالوج: كانت تُسجَّل 0 دائماً؛ الآن الرموز الحقيقية مقسومة على المستأجرين بطول النص ومسعّرة (fake/local يبقيان 0 و`chars//4`).

**F-P4-13 (اكتُشف هنا، أُصلح):** نموذج التضمين لم يكن جزءاً من أي شيء: (1) المرشِّح `app.list_products_needing_embedding` يعيد التضمين عند تغيّر المحتوى فقط ⇒ بعد تبديل المزوّد تبقى متجهات `local` للأبد؛ (2) البحث المتجهي يقارن متجه الاستعلام بكل المتجهات دون النظر للنموذج ⇒ مقارنة فضاءين لا علاقة بينهما؛ (3) مفتاح كاش الاستعلام بلا نموذج ⇒ متجه النموذج السابق يُعاد ساعة كاملة. الإصلاح: ترحيل جديد `0020_p4_embedding_model.sql` (الدالة تأخذ `p_model` وتعيد ما كُتب بنموذج آخر؛ حُذفت نسخة المعامل الواحد)، `repos_catalog.QueryVector(model, values)` والبحث يرشّح `ce.model = <نموذج الاستعلام>`، الكتابة بـ`handle.model_name`، ومفتاح الكاش `emb:q:<model>:<sha>`. (اختبار الكاش المعتمد في test_vector.py لم يُعدَّل: المعامل `model` اختياري.)

**F-P4-14 (سُجّل، لم يُغيَّر — قرار المالك):** أسعار DeepSeek في `DEFAULT_LLM_PRICE_TABLE` أقل بـ1000 مرة من وحدة الجدول: الوحدة micro-USD لكل 1k رمز، و‎$0.30/M = 300 لا 0.3. الأثر: 1000 رمز ⇒ `int(1000*0.3//1000)=0`؛ ميزانية المستأجر (20$) لا تكاد تُستهلك فلا تحمي. التصحيح يغيّر سلوك الإنتاج (الميزانية ستعمل فعلاً) ⇒ بانتظار قرار.

**التحقق (حرفياً، الحاوية Linux، PG16):**
```
pytest tests/test_openai_embedding.py        -> 20 passed
pytest -m db tests/test_embed_model_db.py     -> 4 passed
pytest -q                                     -> 616 passed, 515 deselected   (596 + 20)
pytest -q -m tools                            -> 4 passed
pytest -q -m db (كاملة)                       -> 511 passed, 620 deselected in 277.20s   (507 + 4)؛ tenants بعدها = 0
static_gate.py -> STATIC GATE PASSED — 0 violations.   (أول تشغيل: S9 ×2 لأن تعليقَي المحوّلين ذكرا اسم الجدول — أُعيدت صياغتهما)
lint-imports -> Contracts: 8 kept, 0 broken.
ruff -> Found 302 errors (دون تغيير؛ B905 الجديد أُصلح بـ zip(strict=True))
mypy app -> Found 103 errors (دون تغيير؛ no-any-return الجديد في registry أُصلح)
check_env.py -> ENV CHECK PASSED
app.cli migrate -> applied: 0020_p4_embedding_model
```
**فحص التحوّل:**
```
بحث بلا "ce.model = %s"            -> 1 failed, 3 passed
مفتاح كاش بلا نموذج                 -> 1 failed, 3 passed
الكتابة بـ result.usage.model       -> 1 failed, 3 passed
الدالة SQL بلا شرط النموذج           -> 2 failed, 2 passed
المحوّل بلا dimensions               -> 4 failed, 16 passed
المحوّل بلا ترتيب index              -> 1 failed, 19 passed
حارس المفتاح معطّل                   -> 1 failed, 19 passed
تعيين المهلة معطّل                   -> 1 failed, 19 passed
بعد الاستعادة                        -> 20 passed / 4 passed
```
**UNVERIFIED:** أي استدعاء حقيقي لـOpenAI (لا مفتاح في البيئة؛ الاختبارات بعميل SDK بديل)؛ جودة الدلالة للعربية (هي موضوع Task 16)؛ سعر ‎$0.02/M مأخوذ من المعرفة السابقة ولم يُتحقق منه من صفحة الأسعار الرسمية (قابل للتغيير عبر `LLM_PRICE_TABLE`)؛ PG18 على جهاز المالك؛ الترحيل 0020 على الـVPS.

**توقّف:** لا commit لـTask 15. بانتظار مراجعة المالك قبل Task 16 ثم Task 14.

## Task 15 معتمدة + F-P4-14 مُصلَح (أمر المالك، 2026-10-09)

**قرار المالك:** اعتماد Task 15 والالتزام بها، وإصلاح F-P4-14 في الـcommit نفسه.
**F-P4-14:** `DEFAULT_LLM_PRICE_TABLE` — deepseek-chat/flash ⇐ `input 300, output 1200`؛ deepseek-v4-pro ⇐ `1320, 3960` (micro-USD لكل 1k رمز؛ ‎$0.30/M = 300). تحديث مثال `LLM_PRICE_TABLE` في `.env.example` (+ سطرا openai) ووصف `budget.compute_cost_micro_usd`. اختبار جديد `test_default_deepseek_prices_are_in_the_budget_unit` (1k+1k ⇐ 1500؛ 1M flash ⇐ 300,000؛ 1M+1M v4-pro ⇐ 5,280,000). اختبار الرياضيات المعتمد `test_llm.py::test_cost_math_fractional_deepseek_rates` يستعمل جدوله الخاص فلم يُمسّ ويبقى أخضر.
**أثر تشغيلي:** ميزانية المستأجر (20$/شهر) صارت تُستهلك بالتكلفة الحقيقية؛ عند بلوغها تصبح الحالة degraded (الموجّه/تضمين الاستعلام يُتخطّى ⇐ قوالب/بحث معجمي). إن كان `.env.prod` على الـVPS يحوي `LLM_PRICE_TABLE` فهو يتقدّم على الافتراضي — UNVERIFIED.
**التحقق (حرفياً):**
```
pytest -q        -> 617 passed, 515 deselected
pytest -q -m db  -> 511 passed, 621 deselected, 2 warnings in 272.91s   (بعد إعادة تشغيل PG في الحاوية؛ أول محاولة: 511 errors = PG متوقف، لا فشل اختبار)
static_gate -> PASSED 0 ; lint-imports -> 8 kept ; ruff 302 ; mypy 103
```

## Task 15 + F-P4-14 — commit (2026-10-09)
`509a0b9 feat(p4): OpenAI embedding adapter with model-scoped vectors (Task 15, F-P4-13) and real DeepSeek budget prices (F-P4-14)` — بأمر المالك.

## Task 16 — المجموعة الذهبية للبحث (دلالي مقابل معجمي) + F-P4-15 (المعماري منفّذاً، 2026-10-09)

**الملفات:** `core/tests/search_golden.py` (كتالوج عربي 13 منتجاً، 21 استعلاماً في 4 فئات: exact 6، morph 5، synonym 6 (جزمة/شنطة/جوال/برفان/لبس)، english 4؛ + 3 استعلامات لمنتجات غير موجودة ABSENT)؛ `tests/test_search_golden_db.py` (يمرّ عبر `search_products` الإنتاجية)؛ `tests/test_search_golden.py` (نقي).
**المقياس المعلن (قبل أي تشغيل):** Recall@3 لكل فئة (الدور يعرض ≤ 3 منتجات) + MRR@8. شروط النجاح للمزوّد الدلالي الحقيقي: لا انحدار (exact = 1.0 ولا يُفقد أي استعلام يجيبه المعجمي في أعلى 3) + Recall@3 على synonym+english ≥ 0.70 وأعلى من المعجمي.
**النتيجة دون اتصال (حرفياً):**
```
mode                  class      n  recall@3   mrr@8
lexical               exact      6      1.00   1.000
lexical               morph      5      0.80   0.800
lexical               synonym    6      0.17   0.167
lexical               english    4      0.00   0.000
hybrid/local          exact      6      1.00   1.000
hybrid/local          morph      5      0.80   0.800
hybrid/local          synonym    6      0.50   0.500
hybrid/local          english    4      0.00   0.000
hybrid/oracle         exact      6      1.00   1.000
hybrid/oracle         morph      5      1.00   1.000
hybrid/oracle         synonym    6      1.00   1.000
hybrid/oracle         english    4      1.00   1.000
absent-empty-rate local-embedding: 1.00 ; golden-oracle: 1.00
```
`oracle` = مزوّد حتمي يتصرّف دلالياً بالبناء (مرادفات/جمع/إنجليزي ⇒ مفهوم واحد) — **ليس ادعاء جودة**؛ يثبت السباكة: متجهات النموذج نفسه + دمج RRF ترفع الاسترجاع بلا انحدار. **قياس OpenAI الحقيقي:** `test_golden_set_live_openai` يعمل فقط مع `OPENAI_API_KEY` (تخطّي هنا: لا مفتاح) — UNVERIFIED؛ يطبع الجدول ومسافات المعايرة.

**F-P4-15 (اكتُشف هنا، أُصلح):** مصدر المتجهات بلا حدّ أدنى للصلة: يعيد دائماً حتى 50 منتجاً مرتّبة بالمسافة ⇒ استعلام عن شيء لا يبيعه المتجر يعود بـ8 بطاقات عشوائية بدل «لا نتائج». مسبار حرفي قبل الإصلاح (local): `قلم رصاص lexical: 0 hybrid: 8 ['P-SHOE','P-HEEL','P-SHIRT']`، `ثلاجة كبيرة … hybrid: 8`، `xyz … hybrid: 8`. **حيّ اليوم** على الخادم إن كان العامل يضمّن الكتالوج (EMBEDDING_PROVIDER=local افتراضياً). الإصلاح: `QueryVector.max_distance` (افتراضي 0.99 = يسقط ما لا تداخل فيه) و`AND (ce.embedding <=> q) < max_distance` في الاستعلامين؛ `SEARCH_VECTOR_MAX_DISTANCE` (0 < x ≤ 2) يُعايَر لكل نموذج دلالي من مخرجات الاختبار الحي. أثر جانبي مُعلَن: `hybrid/local` كان 1.00 morph و0.25 english بفضل نتائج عشوائية محظوظة؛ بعد الحدّ صارت الأرقام صادقة (0.80/0.00).
**تعديل اختبار معتمد (مُعلَن):** `test_embed_model_db.py::test_vector_search_compares_only_same_model_vectors` صار يمرّر `max_distance=2.0` صراحةً — نيته (عزل النموذج) لم تتغير، لكن استعلامه لا يشارك الحذاء أي كلمة فكان سيسقطه الحدّ الجديد.

## Task 14 — سلات الهدايا ورابط الدفع (المعماري منفّذاً، 2026-10-09)

**قرار المالك (OQ-P4-06):** الرابط `https://sharwaah.com/checkout/gift/{cart_id}`؛ شروه يتولّى المسار.
**التصميم (قرارات المعماري، للمراجعة):**
- `cart_id` = UUID يولّده المحرك لكل سلة ويخزّنها في `gift_carts` (ترحيل `0021`، RLS صريحة، إلحاق فقط، صلاحية 7 أيام) — معرّفات المنتج/المتغيّر فقط، بلا سعر.
- شروه يحلّ المعرّف عبر `POST /webhooks/platform/gift-cart` موقّعاً بنفس HMAC الـwebhooks (`routes_gift.py`)؛ 404 للمتجر الآخر/المجهول/المنتهي. أُضيف المسار إلى قائمة سماح Caddy وnginx و`check_proxy.sh` والعقد §4.5.
- الكشف قاعدة كود (مثل المقاس): كلمة هدية + `GIFT_ENABLED` (افتراضي **false**)؛ `app/tools/gift_extract.py` نقي يقرأ الميزانية («20 الف»، «50,000»، أرقام عربية، يتجاهل الأعمار) والعملة المذكورة.
- لا تحويل عملة: العملة المذكورة يجب أن تكون عملة المتجر؛ «ريال» مجرّد يطابق YER أو SAR فقط إن كان المتجر بواحدة منهما؛ متجر مختلط بلا ذكر عملة ⇒ تسليم بشري. الوحدات الصغرى: افتراض منزلتين (YER/SAR/USD/AED) — OQ-P4-25.
- المرشّحون: أرخص متغيّر مسعّر لكل منتج ≤ الميزانية و`stock_hint` غير صفري (NULL = غير معروف ⇒ مسموح)؛ الصلة من ترتيب بحث المتجر لكلمات الاهتمام؛ ≤ K_MAX=60؛ الحلّال يعمل داخل المعاملة (محدود بـMAX_STEPS، يُسجَّل زمنه) — **بلا process pool** (انحراف عن §5.2 مُعلَن).
- الرد: ≤ 3 سلات = عناوين التاجر حرفياً + رابط لكل سلة؛ **لا رقم** (الإجمالي من المنصة). نصوص القوالب الأربعة **مقترحة** — OQ-P4-24.
**التحقق (حرفياً):**
```
pytest tests/test_gift_extract.py          -> 23 passed
pytest -m db tests/test_gift_turn_db.py     -> 7 passed
caddy validate -> Valid configuration ; nginx -t -> test is successful
check_proxy (Caddy / nginx، المسار الجديد ضمن قائمة الوصول) -> 21 passed, 0 failed / 21 passed, 0 failed
```
**فحص التحوّل:** حذف فلتر المخزون، التحويل الضمني للعملة، تجاهل GIFT_ENABLED، تعطيل التوقيع، تجاهل الصلاحية، قراءة العمر كميزانية، أكثر من 3 سلات ⇒ كل واحد: 1 failed؛ بعد الاستعادة 7 passed / 23 passed.

**الحزم بعد Task 16 + 14 (الحاوية، PG16):**
```
pytest -q        -> 648 passed, 526 deselected
pytest -q -m db  -> 521 passed, 1 skipped, 652 deselected in 264.15s ; tenants بعدها = 0
pytest -q -m tools -> 4 passed
static_gate -> PASSED 0 ; lint-imports -> 8 kept ; ruff -> 302 ; mypy -> 103 ; check_env -> PASSED
app.cli migrate -> applied: 0021_p4_gift_carts
```
**UNVERIFIED:** قياس OpenAI الحي؛ PG18؛ الترحيلان 0020/0021 على الخادم؛ مسار شروه `/checkout/gift/{id}` غير موجود بعد.
**توقّف:** Task 16 وTask 14 غير ملتزمتين، بانتظار مراجعة المالك.

## Tasks 16 و14 معتمدتان (أمر المالك، 2026-10-09)
- **OQ-P4-24:** نصوص قوالب الهدايا الأربعة معتمدة كأساس إنتاجي (تحديث تعليقات templates/config/compose فقط — لا تغيير نص).
- **OQ-P4-25:** منزلتان عشريتان في قاعدة البيانات لـ YER/SAR/USD/AED معتمدة (حتى لو عُرض الريال اليمني بلا كسور في الواجهة).
- **الحلّال داخل المعاملة:** مقبول؛ حدّ الخطوات الثابت يحدّ الزمن.
- **مسار شروه `/checkout/gift/{id}`:** ينفّذه فريق شروه منفصلاً؛ `GIFT_ENABLED` يبقى false حتى ذلك.

## Tasks 16 و14 — commit (2026-10-09)
`8810d04 feat(p4): search golden set with vector relevance floor (Task 16, F-P4-15) and gift baskets with checkout links (Task 14)` — بأمر المالك.

## Task 9 — اختبارات ذهبية وخاصية لمستشار المقاسات + F-P4-16 (المعماري منفّذاً، 2026-10-09)
**الملف:** `core/tests/test_size_advisor_golden.py` — 17 حالة ذهبية على جدول مرجعي (S/M/L/XL) منها مثال الكارثة «95 كغ لا يحصل على M» بكل الأطوال 165–178، وحدّ تسامح الوزن (90 مقبول / 91 مرفوض بلا XL)، ومسار القياسات؛ وخصائص بمسح شبكي حتمي (بلا مولّد عشوائي): رتابة الوزن (طول 150–200 × وزن 40–105 × 3 تفضيلات × جدولين)، رتابة الطول، رتابة الصدر، الحارس الصلب على كل مقاس مقترح (والبديل)، اتساق out_of_range مع أسبابه، المجموعة المغلقة للأسباب، الحتمية.
**أول تشغيل (حرفياً):** `9 failed, 25 passed` — الحالات الذهبية كلها ناجحة؛ الفشل كله في الرتابة: `AssertionError: (176, 47, 'fitted')` … `(151, 64, …)`.
**F-P4-16 (اكتُشف هنا):** فرع «أقرب مقاس» (nearest_out_of_range) جمع مسافتين مطبّعتين بعرض المدى، فلا رتابة: طول 176 ووزن 40–46 ⇒ M، ثم 47–50 ⇒ S، ثم 51 ⇒ M (كلها out_of_range). يخالف §5.1 صراحةً («زيادة الوزن لا تُنزل المقاس»).
**الإصلاح (قرار المعماري، للمراجعة):** الأقرب يُختار بالوزن أولاً (كغ خارج المدى)، ثم الطول لكسر التعادل (سم)، ثم الأكبر — الوزن يقود لأنه ما يحميه الحارس الصلب؛ الناتج يبقى مُعلَّماً out_of_range. التغيير في فرع واحد (`app/fit/size_advisor.py`، دالة `_outside_kg_cm`). بعده: `54 passed` (الجديد + `test_size_advisor.py` المعتمد كما هو).
**فحص التحوّل:** تعطيل الحارس ⇒ `4 failed, 30 passed`؛ العودة للمسافة المطبّعة ⇒ `9 failed, 25 passed`؛ تسامح 3 كغ ⇒ `1 failed, 33 passed` (g10). ملاحظة: إعادة التشغيل الأولى بعد الاستعادة أظهرت فشلاً من ملف bytecode قديم (نفس الحجم والثانية) — حُذف `__pycache__` ⇒ `54 passed`.

## Task 19 — الحزمة الكاملة مرّتين (2026-10-09، الحاوية Linux PG16)
```
[run 1] 521 passed, 1 skipped, 686 deselected, 2 warnings in 269.78s (0:04:29)
[run 2] 521 passed, 1 skipped, 686 deselected, 2 warnings in 272.22s (0:04:32)
DB SUITE: STABLE (521 passed twice)
pure run 1: 682 passed, 526 deselected in 6.68s
pure run 2: 682 passed, 526 deselected in 6.41s
-m tools: 4 passed ; node --test tests/js/console_sso.test.mjs: pass 9 fail 0
static_gate PASSED 0 ; check_env PASSED ; lint-imports 8 kept 0 broken ; ruff 302 ; mypy 103 ; tenants = 0
```
لا انحدار: المرجع القديم 471 (P3 Gate G) ⇒ 521؛ لم يُحذف أي اختبار. التخطّي الوحيد: الاختبار الحي لـOpenAI. UNVERIFIED: PG18 لدى المالك.

## Task 20 — التوثيق (2026-10-09)
جديد `docs/P4_SPECKIT_CLOSEOUT.md`؛ `docs/PHASE_GATE.md` (P4: BUILT & PARTLY DEPLOYED)؛ `MASTER_ROADMAP_AND_GAPS.md` (صف P4 + تحديث 2026-10-09، حظر P5/P6 قائم)؛ `docs/SPECKIT_PROTOCOL.md` §6 (صف P4)؛ tasks.md: 9/19/20 [x].
**توقّف:** Task 9 (إصلاح F-P4-16) ووثائق الإغلاق غير ملتزمة — بانتظار اعتماد المالك، ثم دمج `p4-links-ai` إلى `main` بيد المالك.

## إغلاق P4 النهائي (أمر المالك، 2026-10-09)
- Task 9 وإصلاح F-P4-16 (الوزن قبل الطول في المقاس الأقرب خارج المدى) معتمدان؛ Task 19 (521 ×2) مُقَرّ؛ Task 20 معتمد.
- يتوقّف كل تنفيذ في P4. الدمج إلى `main` والنشر على الخادم بيد المالك.

## INT-1 — رابط الهدية المقيّد بالمتجر `{tenant_ref}` (2026-10-09) — VERIFIED، بانتظار المراجعة، لا commit
**قرار المالك:** الرابط `https://<store>.sharwaah.com/checkout/gift/{id}`؛ ينفَّذ في المحرك قالباً بعنصر نائب `{tenant_ref}`.

**التغيير:**
- `core/app/workers/gift.py`: `checkout_base_url(template, tenant_ref)` — يستبدل `{tenant_ref}` بـ`platform_ref` المتجر إن كان تسمية DNS (`[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?`) وإلا None؛ قالب بلا عنصر نائب يُستعمل كما هو. `curate_for_turn(..., link_template)` يحلّ الرابط **قبل أي كتابة**؛ تعذّره ⇒ `GiftTurn("no_link")`.
- `core/app/workers/turn.py`: `no_link` ⇒ تحويل لموظف بقالب `gift_no_basket` (نص معتمد قائم، لا نص جديد)؛ الرابط من `turn.link_base`.
- `core/app/workers/config.py`: الافتراضي `https://{tenant_ref}.sharwaah.com/checkout/gift/`؛ `validate_gift_checkout_base_url`: https، ينتهي بـ`/`، عنصر نائب واحد على الأكثر ولا أقواس غيره.
- `docker-compose.yml`: `GIFT_CHECKOUT_BASE_URL: ${GIFT_CHECKOUT_BASE_URL:-}` (الفارغ ⇒ افتراضي التطبيق؛ `}` داخل قيمة compose الافتراضية تُنهي التعبير). `docker compose --profile '*' config` ⇒ `GIFT_CHECKOUT_BASE_URL: ""`.
- `.env.example`، `docs/PLATFORM_COMMERCE_CONTRACT.md` §4.5.
- **تعديل اختبارات معتمدة بقرار المالك (تغيّر الرابط نفسه):** `test_gift_turn_db.py` (`BASE` ⇒ `https://tenant-a.sharwaah.com/checkout/gift/` = `platform_ref` المزروع) و`test_gift_extract.py` (القيمة الافتراضية). بلا تغيير في أي تأكيد آخر.
- جديد: `test_gift_link.py` (نقي، 27) و`test_gift_link_db.py` (db، 2).

**المخرج:**
```
pytest -q tests/test_gift_link.py tests/test_gift_extract.py      -> 50 passed
pytest -q -m db tests/test_gift_turn_db.py tests/test_gift_link_db.py -> 9 passed
pytest -q (pure)          -> 709 passed, 528 deselected in 9.95s
pytest -q -m db core/tests -> 523 passed, 1 skipped, 713 deselected, 2 warnings in 417.14s (0:06:57)
static_gate.py            -> STATIC GATE PASSED — 0 violations.
lint-imports              -> Contracts: 8 kept, 0 broken.
ruff                      -> Found 302 errors. (الأساس)
mypy app                  -> Found 103 errors in 33 files (الأساس)
```
(db 521 ⇒ 523: الاختباران الجديدان.)

**فحوص التحوّل** (الاختبارات الأربعة، كل تحوّل ثم استعادة، مع مسح `__pycache__`):
| التحوّل | النتيجة |
|---|---|
| حذف فحص تسمية DNS | 10 failed |
| إرجاع القالب بلا استبدال | 9 failed |
| استعمال القالب الخام بدل التحويل عند التعذّر | 1 failed |
| `turn.py` يستعمل الإعداد بدل `turn.link_base` | 4 failed |
| حذف فحص الأقواس في الإعداد | 5 failed |
| إرجاع الافتراضي القديم | 2 failed |
| بعد الاستعادة | 59 passed |

**UNVERIFIED:** قيمة `GIFT_CHECKOUT_BASE_URL` في `.env.prod` على الـVPS (إن كانت مضبوطة بالقيمة القديمة فستتجاوز القالب — OQ-INT-04 في حزمة شروه)؛ متاجر قائمة `platform_ref` فيها ليس تسمية DNS (تُحوَّل لموظف).
