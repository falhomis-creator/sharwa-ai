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
