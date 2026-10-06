# عقد منصة التجارة — Sharwa-AI core ⇄ sharwa_saas

**الجمهور:** فريق منصة المتجر (sharwa_saas) الذي سينفّذ المسارات الأربعة (OQ-P4-04).
**الحالة:** العقد مستخرج حرفياً من كود المحرك الفعلي (`commerce_client.py` / `adapter.py` / `port.py` / `catalog.py` / `repos_catalog.py` / `jwt.py`). كل مفتاح وثابت هنا له سطر مقابل في الكود.
**نطاق هذا المستند:** العقد الحرفي بين الطالب (المحرك) والمجيب (شروه). كل ما ليس هنا لا يُضمن.

---

## 1. الغرض والاتجاه

- **المحرك يطلب، وشروه يجيب.** لا يفتح المحرك أي منفذ استقبال لهذه المسارات؛ كل الطلبات **GET** صادرة من المحرك إلى شروه.
- **الأساس:** `COMMERCE_BASE_URL` — الجذر الذي تُبنى عليه المسارات الأربعة. (يُترك فارغاً في كل البيئات الحقيقية حالياً؛ لا يُفعَّل قبل توقيع العقد.)
- المسارات الأربعة: `/changes` · `/snapshot` · `/order_lookup` · `/stock_observation`.

## 2. التوقيع (إلزامي — OQ-P4-03)

كل طلب GET يحمل ترويستين:

| الترويسة | المعنى |
|---|---|
| `X-Sharwa-AI-Timestamp` | ثواني Unix (UTC) لحظة الإرسال |
| `X-Sharwa-AI-Signature` | سداسية hex لـ HMAC-SHA256 |

**الرسالة الموقَّعة (بايتات):**
```
<ts> + "." + METHOD + " " + الهدف الخام (المسار + "?" + الاستعلام) كما وصل حرفياً
```
- الخوارزمية: HMAC-SHA256 بالسرّ المشترك (`COMMERCE_API_SECRET` عند المحرك).
- **على شروه:** التحقق على `QUERY_STRING` الخام دون إعادة ترميز أو إعادة ترتيب للمعاملات؛ ومقارنة **ثابتة الزمن** (`hmac.compare_digest`)؛ ورفض `|now − ts| > 300` ثانية؛ والرد `401` عند أي فشل (غياب ترويسة، توقيع غير مطابق، أو طابع خارج النافذة).

مرجع التنفيذ حرفياً من `commerce_client.py:54–66`:
```python
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

## 3. التصنيف العام للردود

كما يعامله المحرك حرفياً (`commerce_client.py`):

| الحالة | سلوك المحرك |
|---|---|
| `200` | نجاح — يُقرأ الجسم |
| `404` (فقط `/order_lookup` و`/stock_observation`) | «لا شيء» — يُعاد `None` (حدّ مضاد للأوراكل H53) |
| `5xx` / مهلة / خطأ اتصال | عابر — يفتح القاطع ويرفع `CommerceUnavailableError` |
| أي `4xx` آخر (غير 404 المذكورة) | خطأ دائم — `CommerceClientError` (لا إعادة مجدية) |

- القاطع: يفتح بعد **5** إخفاقات متتالية ويُغلق بعد **30** ثانية (`commerce_client.py:73`).
- المهلة الافتراضية: **3** ثوانٍ (`commerce_client.py:70`).

## 4. المسارات

### 4.1 `GET /changes` — أحداث الكتالوج

المعاملات المرسلة (بأسمائها حرفياً):

| المعامل | القاعدة |
|---|---|
| `tenant_ref` | إلزامي — `platform_ref` الخاص بالمستأجر |
| `since` | اختياري — مؤشر آخر معالجة (يُرسَل فقط إن وُجد) |

الجسم المتوقع — المفاتيح التي يقرؤها المحرك حرفياً (`adapter.py:19`):
```json
{ "events": [ ... ], "next_cursor": "<opaque>" }
```

حقول كل حدث كما تقرؤها `apply_catalog_events` (`repos_catalog.py:64–76` و`421–427`):
- كل حدث يحمل `type` (النوع) و`version` (إلزامي، يُعامل كـ`source_version`؛ غيابه/خلله ⇒ الحدث يُتجاهل `dropped`).
- `product.upserted`: `platform_product_id`, `title`, `description`, `category`, `attributes`, `active`, `version`.
- `product.deleted`: `platform_product_id`, `version`.
- `variant.upserted`: `platform_variant_id`, `platform_product_id`, `sku`, `options`, `price_hint_minor`, `currency`, `version`.
- `variant.stock`: `platform_variant_id`, `qty`, `version`.
- `kb.upserted`: `source`, `content`, `version`.
- أي مفتاح خارج هذه القوائم **يُسقط ولا يُخزَّن ولا يُسجَّل بقيمته** (H36).

### 4.2 `GET /order_lookup` — بطاقة طلب

المعاملات: `tenant_ref`, `order_ref`, `path` (= `same_number` | `other_number`), `phones` (مرشّحو الهاتف مفصولة بـ`,`).

الجسم: مفتاح `order` (`commerce_client.py:128`). المحرك لا يُبقي إلا `ORDER_CARD_FIELDS = ("ref", "status", "updated_at")` (`port.py:16`). **يُمنع إرسال هاتف أو عنوان أو مبلغ** — أي حقل خارج هذه الثلاثة يُسقط عند الحدّ.

### 4.3 `GET /stock_observation` — ملاحظة مخزون

المعاملات: `tenant_ref`, `variant` (= `platform_variant_id`).

الجسم: مفتاح `observation` (`commerce_client.py:155`) بحقلين يقرؤهما المحرك:
- `available`: عدد صحيح ≥ 0 (غير bool) — `stock.py:137–138`.
- `observed_at`: ISO-8601 — يُحلَّل بـ`datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))` (`stock.py:37`). غيابه/خلله ⇒ يعامل كـ«قديمة» (H73: لا تخصيص ولا إشعار لملاحظة أقدم من `stock_observation_max_age_s` = 300 ثانية، `stock.py:142`).

### 4.4 `GET /snapshot` — محجوز

المعاملات: `tenant_ref`, `page` (اختياري). **محجوز — غير مطلوب من شروه تنفيذه الآن** (F-P4-01): لا ندّاء له في المحرك، يبقى في المنفذ للمستقبل.

## 5. الصفحات والمؤشر (F-P4-01 وF-P4-03)

- `/changes` بلا `since` = من أول التاريخ: يعيد الكتالوج الحالي **كاملاً** كأحداث upsert مُقسَّمة صفحات (F-P4-01).
- الصفحة **≤ 500** حدث (`catalog_reconcile_max_events_per_tenant`).
- `next_cursor` يشير **بعد آخر حدث مُعاد بالضبط**.
- المؤشر **معتم** للمحرك (نصّ يُعاد كما هو في `since`).
- **قاعدة صريحة: صفحة أكبر من 500 خرقٌ للعقد** (F-P4-03) — المحرك سيقصّها اليوم (وسيحرسها في Task 4ج).

## 6. الهوية والعزل

- `tenant_ref` = `platform_ref` (= `schema_name` في شروه) كما سُجِّل عند إنشاء المستأجر.
- كل استجابة تخص **هذا المستأجر وحده**؛ لا بيانات عبر مستأجرين.

## 7. الدخول الموحد (SSO)

- **الخوارزمية المقبولة: `RS256` فقط** (`jwt.py:81–82`؛ أي `alg` آخر يُرفض `UNAUTHENTICATED`).
- **المطالبات الإلزامية** التي يتحقق منها `jwt.py` حرفياً:
  - إلزامية في الفك: `exp`, `iss`, `aud`, `sub` (`jwt.py:100`).
  - ثم يجب توافر `sub`, `tenant`, `role` (غير فارغة) (`jwt.py:108–111`).
  - `role` ∈ `merchant_admin | staff | platform_admin` (`jwt.py:20`؛ و`permissions.py:4`).
  - `iss` يُطابَق مع `JWT_ISSUER` و`aud` مع `JWT_AUDIENCE` (`jwt.py:97–98`).
- **التسامح الزمني:** `leeway = clock_skew_s` = **30** ثانية (`jwt.py:99`، `config.py:76`).
- **kid وطريقة حلّه:** عبر `JWKS_URL` باستخدام `PyJWKClient(jwks_url, cache_keys=True, lifespan=300)` (`jwt.py:42`)؛ **مدة الكاش 300** ثانية؛ وعند تعذّر JWKS يُسقط إلى `JWT_PUBLIC_KEY_PEM` إن وُجد (`jwt.py:55–62`).
- **ما يُطلب من شروه:**
  1. إصدار توكن `RS256` بالمطالبات نفسها التي يضعها `mint_admin_token.py`: `iss`, `aud`, `sub`, `tenant`, `role`, `iat`, `exp` (+ `kid` اختياري في الترويسة).
  2. عرض JWKS على HTTPS.
  3. تدوير المفاتيح بإبقاء المفتاح القديم في JWKS حتى انتهاء أقصى `exp` لم يتأثر.
  4. **المفتاح الخاص لا يغادر شروه أبداً.**

