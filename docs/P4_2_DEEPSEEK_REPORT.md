# P4_2_DEEPSEEK_REPORT.md — تقرير تسليم: DeepSeek مزوّد الـLLM الحقيقي الوحيد

**التاريخ:** 2026-10-05 · **قرار المالك (توجيه تنفيذي):** استبعاد OpenAI واعتماد DeepSeek كلياً كمزوّد الذكاء الاصطناعي للمحرك التسويقي.

## 1) ما نُفِّذ

| # | البند | الملفات |
|---|---|---|
| 1 | **محوّل DeepSeek الحقيقي** عبر مكتبة `openai` الرسمية (توافق كامل، تم التحقق من api-docs.deepseek.com): `base_url=https://api.deepseek.com`، `model=deepseek-chat`، `temperature=0`، `max_retries=0` (إعادة المحاولة واحدة + القاطع عندنا — H39، لا فاتورة مخفية)، JSON Output للراوتر، خرائط الأخطاء (Timeout/429/5xx/اتصال) إلى مفردات المنفذ `LlmProviderError/LlmTimeoutError` | `core/app/llm/adapters/deepseek.py` (جديد) |
| 2 | **السجل (registry)** يبني المزوّد الحقيقي ديناميكياً من `app/llm/adapters/<name>.py` بلا ذكر أسماء مزوّدين (احترام قاعدة S8-3 حرفياً)؛ الاسم المجهول يرفض الإقلاع | `core/app/llm/registry.py` |
| 3 | **حارس الأمان H-Safety (M13) يتعرّف على deepseek كمزوّد حقيقي**: `REAL_LLM_PROVIDERS={"deepseek"}` — الإنتاج يسمح بـdeepseek فقط من الحقيقي، يرفض fake (كما كان) ويرفض المجهول (جديد: الخطأ المطبعي يوقف الإقلاع لا أن يسقط بصمت). **والعامل يُقلع الآن في الإنتاج عند وجوده** | `core/app/workers/config.py::validate_llm_provider` |
| 4 | **`DEEPSEEK_API_KEY` إلزامي (H5) عند اختيار deepseek** — فارغ ⇒ `ConfigError` باسم المتغيّر الناقص، في كل البيئات | `core/app/workers/config.py::load` |
| 5 | **`LLM_PROVIDER="deepseek"` في الإعدادات** + كتلة `LlmConfig` كاملة (provider/base_url/model/api_key اختياري هنا لأن الـapi لا يستدعي الـLLM إطلاقاً — العامل هو من يفرض المفتاح) | `core/app/config.py` |
| 6 | **`.env.example`**: `DEEPSEEK_API_KEY=change-me` و`LLM_PROVIDER=deepseek` صريحين + توثيق التجاوزات | `.env.example` |
| 7 | **Embedding**: DeepSeek **لا يوفّر** واجهة تضمين نصوص (تحقّق مباشر من الوثائق) — أُضيف مزوّد `local` حتمي بلا شبكة وبلا تكلفة (`EMBEDDING_PROVIDER=local`، افتراضي worker-realtime الجديد) بمتجهات **مطابقة بالبايت** لمزوّد الاختبار (نفس المُطبِّع العربي + نفس هاش md5) فلا تُبطل أي متجهات موجودة؛ `fake` يبقى مرفوضاً في الإنتاج | `core/app/llm/adapters/local_embedding.py` (جديد)، `docker-compose.yml` |
| 8 | **جدول أسعار DeepSeek** (عمود الذروة PEAK — الأعلى، فلا تُحتسب الميزانية أقل من الحقيقة): chat/flash دخول 0.3 وإخراج 1.2، v4-pro دخول 1.32 وإخراج 3.96 (ميكرو-دولار/1k توكن) + دعم الأسعار الكسرية في حساب التكلفة | `core/app/workers/config.py::DEFAULT_LLM_PRICE_TABLE`، `core/app/llm/budget.py` |
| 9 | **التبعية**: `openai==3.24.0` (+ httpx2/jiter/sniffio) مثبّتة في requirements.txt — الصورة تُبنى بها | `core/requirements.txt` |
| 10 | اختبارات جديدة (12) بلا تعديل أي اختبار قائم عدا سطر M13 الذي كان يتوقع «مزوّد حقيقي مستقبلي» وصار موجوداً فعلاً | `core/tests/test_llm.py`, `test_workers_config.py`, `test_vector.py` |

## 2) التحقق المحلي (نتائج فعلية)

- `pytest core/tests` (النقية): **380 passed** (462 db/tools deselected) — صفر فشل؛ بلا تعديل أي اختبار قائم عدا سطر M13 الذي كان أصلاً يتوقع «مزوّد حقيقي مستقبلي» وصار موجوداً فعلاً.
- `python scripts/static_gate.py`: **STATIC GATE PASSED — 0 مخالفات** (يشمل S8-3: أسماء المزوّدين داخل `app/llm/adapters/` وملفي الإعداد فقط).
- محاكاة إقلاع العامل: `ENV=production` + `LLM_PROVIDER=deepseek` + مفتاح + `EMBEDDING_PROVIDER=local` ⇒ `WorkerSettings.load()` نجح وبنى `DeepSeekProvider` (router+summary) و`LocalEmbeddingProvider` (embed)؛ وبدون المفتاح ⇒ `ConfigError: DEEPSEEK_API_KEY is required...`.

## 3) ما يجب أن تضيفه يدوياً إلى `.env.prod` على الـVPS

```bash
DEEPSEEK_API_KEY=<مفتاحك من https://platform.deepseek.com>
LLM_PROVIDER=deepseek
EMBEDDING_PROVIDER=local
# اختياري (هذه الافتراضات):
# DEEPSEEK_BASE_URL=https://api.deepseek.com
# DEEPSEEK_MODEL=deepseek-chat
```

ثم (الأوامر الكاملة في `docs/VPS_DEPLOYMENT_PLAYBOOK.md` §8.5):

```bash
cd ~/sharwa-ai && export COMPOSE_ENV_FILES=.env.prod
git pull --ff-only
docker compose build api          # الصورة مشتركة مع worker-realtime وتضم مكتبة openai الجديدة
docker compose --profile engine up -d worker-realtime
docker compose logs worker-realtime --tail 30
```

## 4) ملاحظات وحدود

- **deepseek-chat مقابل التشكيلة الحالية**: وثائق DeepSeek الرسمية (تاريخ هذا التقرير) تعرض `deepseek-flash` و`deepseek-v4-pro`؛ `deepseek-chat` هو الاسم الذي قرّره المالك وهو الافتراض في الكود. لو أُحيل الاسم مستقبلاً فإن التبديل متغيّر بيئة واحد (`DEEPSEEK_MODEL=deepseek-flash`) بلا نشر جديد، والقاطع يحمي المحرك حتى التبديل.
- **البحث المتجهي** يبقى تحسيناً اختيارياً (H42) على المزوّد المحلي — يفشل مفتوحاً إلى البحث النصي العادي، ولا كلفة له.
- كل ضمانات H38/H39/H40/H41 والميزانية الشهرية/المستأجر كما هي — لا نصّ من إخراج النموذج يصل عميلاً؛ النموذج يصنّف والكود يكتب.
