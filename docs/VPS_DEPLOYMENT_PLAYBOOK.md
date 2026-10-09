# VPS_DEPLOYMENT_PLAYBOOK.md — دليل نشر المحرّك على الـVPS (P4.1)

**الحالة:** مكتوب ومُراجَع محلياً؛ **لم يُنفَّذ على الـVPS** (لا وصول لي إليه). كل أمر هنا **UNVERIFIED على الخادم** حتى تلصق مخرجاته. الهدف: فتح لوحة التحكّم على `/console/` والدخول كمدير منصّة، **بلا تفعيل أي تسويق**.

## 0. ثلاث حقائق قبل أن تبدأ

1. **العامل `worker-realtime` اختياري في هذا النشر (profile باسم `engine`).** منذ P4.2 أصبح قابلاً للتشغيل تحت `ENV=production`: مزوّد الـLLM الحقيقي هو DeepSeek (`LLM_PROVIDER=deepseek` + `DEEPSEEK_API_KEY` — حارس H-safety صار يتعرّف عليه كمزوّد حقيقي) والـEmbedding المحلي `EMBEDDING_PROVIDER=local` (DeepSeek لا يوفّر واجهة تضمين). لوحة التحكّم والـAPI لا يحتاجانه؛ تشغيله خطوة منفصلة واعية (§8.5).
2. **لا تُولِّد كلمات سر قاعدة البيانات من جديد.** مجلد `pgdata` موجود، وأدوار PostgreSQL أُنشئت بكلمات سر `.env` الحالية؛ تغييرها في الملف يكسر الاتصال. لذلك `.env.prod` يُبنى **من نسخة `.env` الحالي** ثم تُضاف إليه المتغيّرات الناقصة فقط.
3. **المنفذ 8000 قد يكون محجوزاً لـDjango على نفس الخادم.** الخطوة 2 تفحصه وتختار 8000 أو 8100 تلقائياً. الـAPI مربوط على **127.0.0.1 فقط** (لا يُكشف للإنترنت)، وتصل إليه بنفق SSH (الخطوة 7).

افتراضات: المستودع في `~/sharwa-ai`، Docker Compose ≥ 2.24، المستخدم له صلاحية docker.

## 1. جلب آخر نسخة

```bash
cd ~/sharwa-ai
git status --short | head        # يجب أن يكون نظيفاً؛ إن ظهر تعديل محلي فاحفظه قبل المتابعة
git pull --ff-only
git log --oneline -3             # تأكد أن الأحدث يتضمن P3.5 وP4.1 وP4.2 (DeepSeek)
docker compose version           # يجب ≥ 2.24 (لدعم COMPOSE_ENV_FILES)
```

## 2. إنشاء `.env.prod` الآمن

```bash
cd ~/sharwa-ai
umask 077
[ -f .env ] || { echo "لا يوجد .env من P0 — توقّف"; exit 1; }
[ -f .env.prod ] && cp .env.prod ".env.prod.bak.$(date +%s)"
cp .env .env.prod

# set_kv: يضبط قيمة (يحذف القديمة). need: يولّد سراً فقط إن كان ناقصاً أو "change-me".
set_kv() { sed -i "/^$1=/d" .env.prod; printf '%s=%s\n' "$1" "$2" >> .env.prod; }
need()   { v=$(grep "^$1=" .env.prod | cut -d= -f2-); if [ -z "$v" ] || [ "$v" = "change-me" ]; then set_kv "$1" "$2"; echo "generated $1"; else echo "kept $1"; fi; }

# أسرار (لا تُطبع قيمها)
need METRICS_TOKEN            "$(openssl rand -hex 32)"
need PLATFORM_WEBHOOK_SECRET  "$(openssl rand -hex 32)"   # يجب أن يطابق ما سيوقّع به Django لاحقاً (P4.2)
need ORDER_REF_HASH_KEY       "$(openssl rand -hex 32)"   # يُولَّد مرة واحدة ويُحفظ للأبد
need SHARWA_AI_GATEWAY_API_KEY "$(openssl rand -hex 32)"

# هوية JWT (يجب أن تطابق أمر توليد التوكن في الخطوة 6)
need JWT_ISSUER   "sharwa-platform"
need JWT_AUDIENCE "sharwa-ai-console"
set_kv JWKS_URL ""                                         # نستعمل المفتاح العام الثابت الآن لا JWKS
need SSO_LOGIN_URL "https://example.invalid/sso"           # استبدله برابط دخول Django حين يُربط

# التشغيل
set_kv API_WORKERS 2
if ss -ltn | grep -q '127.0.0.1:8000\|0.0.0.0:8000\|\*:8000'; then set_kv API_HOST_PORT 8100; echo "8000 محجوز -> 8100"; else set_kv API_HOST_PORT 8000; echo "المنفذ 8000 حرّ"; fi

# التسويق: نصّ التذييل الصريح (شرط H103). سطرك هذا = موافقتك على النص (OQ-P3-15). عدّله إن أردت صياغة أخرى.
set_kv MARKETING_FOOTER_AR "لإيقاف الرسائل الترويجية أرسل: إيقاف"
set_kv MARKETING_MIN_WARMUP_DAYS 3

chmod 600 .env.prod
ls -l .env.prod
git status --short | grep -c env.prod    # يجب أن يطبع 0 (الملف مُتجاهَل في git)
```

إن طُبع `generated METRICS_TOKEN` أعلاه (أي لم يكن حقيقياً من قبل) فزامن ملف Prometheus السري:
```bash
mkdir -p ops/prometheus/secrets && printf '%s' "$(grep '^METRICS_TOKEN=' .env.prod | cut -d= -f2-)" > ops/prometheus/secrets/metrics_token && chmod 600 ops/prometheus/secrets/metrics_token
```
(وإن طُبع `kept METRICS_TOKEN` فلا تفعل شيئاً.)

> مفتاح JWT العام يُضاف في الخطوة 3. **لا تضع ملف `.env.prod` في أي مكان خارج الخادم، ولا تلصق محتواه في محادثة.**

## 3. مفاتيح JWT (على **جهازك أنت** لا على الـVPS)

المفتاح الخاص يبقى عندك. الـVPS يرى العام فقط.

**Git Bash / WSL / macOS / Linux:**
```bash
openssl genrsa -out sharwa_ops_private.pem 3072
openssl rsa -in sharwa_ops_private.pem -pubout -out sharwa_ops_public.pem
echo "JWT_PUBLIC_KEY_PEM=$(awk 'NF{printf "%s\\n",$0}' sharwa_ops_public.pem)"
```
**PowerShell (بديل بلا openssl):**
```powershell
python -c "from cryptography.hazmat.primitives.asymmetric import rsa;from cryptography.hazmat.primitives import serialization as s;k=rsa.generate_private_key(65537,3072);open('sharwa_ops_private.pem','wb').write(k.private_bytes(s.Encoding.PEM,s.PrivateFormat.PKCS8,s.NoEncryption()));open('sharwa_ops_public.pem','wb').write(k.public_key().public_bytes(s.Encoding.PEM,s.PublicFormat.SubjectPublicKeyInfo))"
"JWT_PUBLIC_KEY_PEM=" + ((Get-Content sharwa_ops_public.pem) -join '\n')
```
انسخ **السطر المطبوع** (يبدأ بـ`JWT_PUBLIC_KEY_PEM=-----BEGIN PUBLIC KEY-----\n...`) وألصقه على الـVPS:

```bash
cd ~/sharwa-ai
sed -i '/^JWT_PUBLIC_KEY_PEM=/d' .env.prod
cat >> .env.prod      # الصق السطر ثم Enter ثم Ctrl+D
chmod 600 .env.prod
grep -c '^JWT_PUBLIC_KEY_PEM=-----BEGIN PUBLIC KEY-----' .env.prod   # يجب 1
```
احفظ `sharwa_ops_private.pem` في مكان آمن (مدير كلمات سر/قرص مشفّر). لا تُرفعه إلى git ولا إلى الـVPS.

## 4. البناء، طبقة البيانات، الترحيلات

```bash
cd ~/sharwa-ai
export COMPOSE_ENV_FILES=.env.prod          # كل أوامر compose وscripts/migrate.sh تقرأ .env.prod
# (إن كان compose أقدم من 2.24: cp .env .env.p0.bak && ln -sf .env.prod .env)
docker compose config -q && echo "compose OK"
docker compose build api
docker compose up -d postgres pgbouncer redis-durable redis-cache
docker compose ps                            # الأربعة healthy قبل المتابعة
```

**أي حالة قاعدة بيانات عندك؟**
```bash
docker compose run -T --rm --no-deps --entrypoint python api -c "import os,psycopg;c=psycopg.connect(os.environ['CORE_MIGRATION_DATABASE_URL']);print('schema_migrations =',c.execute(\"select to_regclass('public.schema_migrations')\").fetchone()[0]);print('tenants =',c.execute(\"select to_regclass('public.tenants')\").fetchone()[0])"
```

| النتيجة | الأمر |
|---|---|
| `tenants = None` (قاعدة جديدة) | `scripts/migrate.sh` |
| `tenants = tenants` و`schema_migrations = None` (الأساس طُبّق يدوياً) | `scripts/migrate.sh --adopt-existing-schema 0001_baseline` |
| `schema_migrations = schema_migrations` | `scripts/migrate.sh` (يطبّق الناقص فقط، idempotent) |

```bash
bash scripts/migrate.sh [الخيار حسب الجدول]
```
السكربت يطبع المعلّق قبل التطبيق والمطبَّق بعده؛ آخر قائمة يجب أن تنتهي بـ`0018_p35_admin_tenant_list`. **الترحيل أمر مشغّل منفصل ولا يجري تلقائياً (H62).**

## 5. تشغيل الـAPI (وبقية الخدمات ما عدا العامل)

```bash
cd ~/sharwa-ai && export COMPOSE_ENV_FILES=.env.prod
docker compose up -d                          # لا يشغّل worker-realtime (profile engine)
docker compose ps
PORT=$(grep '^API_HOST_PORT=' .env.prod | cut -d= -f2)
curl -s  http://127.0.0.1:$PORT/healthz ; echo
curl -sI http://127.0.0.1:$PORT/console/ | head -5      # 200 + Content-Security-Policy + Cache-Control: no-store
docker compose logs api --tail 20                       # يجب أن ترى عمليتي uvicorn ("Started server process" مرتين)
```
إن توقّف `api` بخطأ "migrations missing" فالخطوة 4 لم تكتمل. إن توقّف بخطأ "is required" فمتغيّر ناقص في `.env.prod` (الرسالة تسمّيه).

## 6. مدير المنصّة (platform_admin) وتوكن حقيقي

منصّة بلا Django بعد، فالتوكن يُصدَر من **جهازك** بالمفتاح الخاص. أولاً أنشئ مستأجراً إدارياً **مرة واحدة** على الـVPS (التوكن يحتاج `tenant` موجوداً؛ هذا المستأجر **مُطفأ التسويق ولا يُفعَّل أبداً**):
```bash
cd ~/sharwa-ai && export COMPOSE_ENV_FILES=.env.prod
docker compose run --rm --no-deps --entrypoint python api -m app.cli create-tenant --platform-ref ops --name "Sharwa Ops"
```
ثم على **جهازك** (من مجلد المستودع، بعد `pip install pyjwt cryptography`):
```bash
python scripts/mint_admin_token.py --key sharwa_ops_private.pem --issuer sharwa-platform --audience sharwa-ai-console --tenant ops --ttl-s 3600
```
(إن غيّرتَ JWT_ISSUER/JWT_AUDIENCE في الخطوة 2 فضع القيم نفسها.) التوكن صالح ساعة (سقف 8 ساعات). يُطبع في الطرفية فقط. أعد التوليد عند الانتهاء.

اختبار من الـVPS (استبدل `<TOKEN>`):
```bash
curl -s -H "Authorization: Bearer <TOKEN>" http://127.0.0.1:$PORT/v1/admin/marketing/tenants | head -c 600; echo
```
يجب أن يعود JSON فيه مستأجر `ops` و`global_marketing_switch`. و401 = التوكن/المفتاح/issuer/audience لا تتطابق.

## 7. فتح اللوحة بأمان

**الموصى به — نفق SSH (لا منفذ مكشوف، لا نصّ صريح على الإنترنت):** على **جهازك**:
```bash
ssh -N -L 8000:127.0.0.1:8000 <USER>@<VPS_IP>      # إن كان API_HOST_PORT=8100 فاستبدل الرقم الثاني بـ8100
```
ثم افتح `http://127.0.0.1:8000/console/` والصق التوكن. (يعمل كأنه `http://<VPS_IP>:8000/console/` لكن مشفّراً.)

**الدائم — نطاق + TLS:** راجع §12 (`ai.sharwaah.com`، P4 Task 17).

**لا تفتح 8000/8100 في الجدار الناري مباشرة:** التوكن سيعبر الشبكة بنصّ صريح.

## 8. تشغيل/إيقاف/رجوع

```bash
cd ~/sharwa-ai && export COMPOSE_ENV_FILES=.env.prod
docker compose logs -f api            # متابعة
docker compose restart api            # بعد تعديل .env.prod: docker compose up -d api
docker compose stop api               # إيقاف اللوحة والـAPI فقط
# رجوع لنسخة سابقة:
git log --oneline -8 ; git checkout <SHA> && docker compose up -d --build api
```

## 8.5 تشغيل محرك الذكاء الاصطناعي — `worker-realtime` + DeepSeek (P4.2)

> قرار المالك P4.2: DeepSeek هو مزوّد الـLLM الحقيقي الوحيد (OpenAI مستبعَد) — واجهة متوافقة تماماً مع OpenAI على `https://api.deepseek.com` والنموذج `deepseek-chat`.

### أ) المتغيّرات التي تُضاف يدوياً إلى `.env.prod`

```bash
cd ~/sharwa-ai && export COMPOSE_ENV_FILES=.env.prod && umask 077

# 1) المفتاح الحقيقي من https://platform.deepseek.com (يُلصق مرة واحدة، لا يُطبع):
read -rs DS_KEY && printf '\n' 
sed -i '/^DEEPSEEK_API_KEY=/d' .env.prod
printf 'DEEPSEEK_API_KEY=%s\n' "$DS_KEY" >> .env.prod
unset DS_KEY

# 2) صراحةً أوضح من الضمني (هذه قيم docker-compose الافتراضية الجديدة):
sed -i '/^LLM_PROVIDER=/d;/^EMBEDDING_PROVIDER=/d' .env.prod
printf 'LLM_PROVIDER=deepseek\nEMBEDDING_PROVIDER=local\n' >> .env.prod

# 3) اختياري — تجاوز الافتراضات (base_url/النموذج):
# printf 'DEEPSEEK_BASE_URL=https://api.deepseek.com\nDEEPSEEK_MODEL=deepseek-chat\n' >> .env.prod

chmod 600 .env.prod
docker compose config --quiet   # تحقّق أن الملف يُقرأ دون أخطاء
```

### ب) إعادة بناء الصورة ثم الإقلاع

```bash
git pull --ff-only
docker compose build api                     # الصورة sharwa-ai-core مشتركة بين api وworker-realtime (تتضمن الآن مكتبة openai)
docker compose --profile engine up -d worker-realtime
sleep 25 && docker compose ps worker-realtime   # يجب أن تتحول إلى (healthy)
docker compose logs worker-realtime --tail 30   # لا يجب أن يظهر أي ConfigError
```

- `DEEPSEEK_API_KEY is required` ⇒ الخطوة (أ-1) لم تُطبَّق.
- `LLM_PROVIDER=fake is forbidden` ⇒ في `.env.prod` قيمة قديمة؛ أعد (أ-2).
- فشل 401 من DeepSeek وقت التشغيل ⇒ المفتاح غير صحيح؛ أعد (أ-1). القاطع (breaker) سيحمي المحرك meantime والردود تتحول للقوالب/التسليم البشري.

ملاحظات:
- **`EMBEDDING_PROVIDER=local`**: DeepSeek لا يوفّر واجهة تضمين نصوص؛ `local` مزوّد محلي حتمي بلا شبكة وبلا تكلفة، والبحث المتجهي تحسين اختياري (H42) يفشل مفتوحاً إلى البحث العادي. عند اختيار مزوّد تضمين مدفوع لاحقاً يُستبدل بمتغيّر واحد.
- **`DEEPSEEK_MODEL`**: الافتراضي `deepseek-chat` (قرار المالك). التشكيلة الحالية في وثائق DeepSeek تضم أيضاً `deepseek-flash` و`deepseek-v4-pro`؛ التبديل متغيّر بيئة واحد بلا نشر جديد.
- كل مبادئ الحماية باقية: ميزانية شهرية للمستأجر (20$ افتراضياً، `TENANT_MONTHLY_BUDGET_MICRO_USD`)، قاطع + إعادة محاولة واحدة (H39)، «النموذج يصنّف والكود يكتب» (H38) — لا نصّ من إخراج النموذج يصل عميلاً أبداً.

## 8.6 البحث الدلالي — OpenAI embeddings (P4 Task 15)

> قرار المالك 2026-10-09: التضمين الدلالي عبر OpenAI (`text-embedding-3-small`)؛ DeepSeek يبقى نموذج اللغة الوحيد. الافتراضي يبقى `local` (مُظلم) حتى تُضيف المفتاح.

```bash
cd ~/sharwa_ai && export COMPOSE_ENV_FILES=.env.prod && umask 077
bash scripts/migrate.sh                       # يطبّق 0020_p4_embedding_model (بعد docker compose build api)
read -rs OA_KEY && printf '\n'
sed -i '/^OPENAI_API_KEY=/d;/^EMBEDDING_PROVIDER=/d' .env.prod
printf 'OPENAI_API_KEY=%s\nEMBEDDING_PROVIDER=openai\n' "$OA_KEY" >> .env.prod; unset OA_KEY
chmod 600 .env.prod
docker compose --profile engine up -d --no-deps worker-realtime
docker compose logs worker-realtime --tail 30 | grep -iE 'embed|ConfigError'
```
- بلا مفتاح ⇐ `OPENAI_API_KEY is required` ويرفض العامل الإقلاع (H5).
- عند التبديل يُعاد تضمين الكتالوج تلقائياً على دفعات (`EMBED_MAX_PRODUCTS_PER_CYCLE` كل `EMBED_INTERVAL_S`)؛ أثناء ذلك يقارن البحث المتجهي متجهات النموذج نفسه فقط، والمنتج غير المُعاد تضمينه يبقى مغطّى بالبحث المعجمي (H42).
- الرجوع: `EMBEDDING_PROVIDER=local` ثم إعادة تشغيل العامل (يُعاد التضمين محلياً بلا تكلفة).
- التكلفة: 20 micro-USD لكل 1k رمز (‎$0.02/M)؛ استعلامات البحث تُحتسب على ميزانية المستأجر، وتضمين الكتالوج يُسجَّل بتكلفته (حوكمة) دون أن يُحجَب بالميزانية.

## 9. ما يبقى مقفلاً (لا تغيّره في هذا النشر)

- **لا مستأجر مُفعَّل للتسويق** وكل شيء الافتراضي OFF. التفعيل لاحقاً من اللوحة (عبارة تأكيد حرفية) أو CLI، وبقرارك.
- لا إرسال حيّ ما لم تُشغّل العامل صراحةً (§8.5) وتُفعّل التسويق لمستأجر؛ وحتى مع تشغيله، كل مستأجر يبقى مُظلماً حتى `marketing enable`.
- لا `issue-dev-token` في الإنتاج (محظور بتصميمه).

## 10. أعطال شائعة

| العَرَض | السبب | الحل |
|---|---|---|
| `bind: address already in use` على 8000 | Django يحجزه | أعد الخطوة 2 (تختار 8100) ثم `docker compose up -d api` |
| `api` يعيد التشغيل: `JWT ... required` | لا JWKS ولا مفتاح | أضف `JWT_PUBLIC_KEY_PEM` (الخطوة 3) |
| 401 `UNAUTHENTICATED` | issuer/audience/المفتاح لا يطابق | أعد توليد التوكن بالقيم الصحيحة |
| 403 `FORBIDDEN_ROLE` | التوكن ليس platform_admin | أعد التوليد بدون `--role` مخصص |
| `up -d` يعلّق على `gateway` | api يعتمد على gateway healthy | `docker compose logs gateway --tail 30` (من P0) |
| `worker-realtime` يعيد التشغيل: `DEEPSEEK_API_KEY is required` | المفتاح غير موجود/فارغ في `.env.prod` | نفّذ §8.5-أ (أ-1) ثم `docker compose --profile engine up -d worker-realtime` |
| `worker-realtime`: `LLM_PROVIDER=fake is forbidden` | قيمة `LLM_PROVIDER` قديمة في `.env.prod` | نفّذ §8.5-أ (أ-2) |
| اللوحة بيضاء | CSP/مسار | تأكد أن `./frontend` موجود على الخادم (`ls frontend/index.html`) |

## 11. دروس النشر الفعلي الأول (2026-10-04/05) — اقرأها قبل أي نشر ثانٍ

هذه تصحيحات خرجت من أول تشغيل حقيقي على الـVPS، وتتقدّم على أي نص أعلاه يخالفها:

1. **لا تثق بـ`.env` القديم على الخادم.** إن كانت خدمات تعمل أصلاً، فكلمات مرور PG/Redis ومفتاح الـwebhook وقيم MinIO تُؤخذ من الحاويات العاملة (`docker inspect`/`env`) لا من ملف قديم؛ وإلا حصل `password authentication failed` و`WRONGPASS`. أعِد تسمية الملف القديم (`.env.stale-<تاريخ>`).
2. **المتغيّر `SHARWA_AI_GATEWAY_WEBHOOK_SECRET` مطلوب** لتفسير compose؛ غيابه يوقف `compose config`.
3. **المنفذ الافتراضي للـAPI على الاستضافة 8100** (`API_HOST_PORT`) لأن 8000 محجوز لـDjango لاحقاً؛ الوصول عبر نفق SSH.
4. **JWT:** issuer=`sharwa-sso` وaudience=`sharwa-api` (قيم Django). المفتاح العام سطر واحد بـ`\n`. التوكن يُولَّد على جهازك فقط (`scripts/mint_admin_token.py`)، والمفتاح الخاص لا يغادر جهازك.
5. **`MARKETING_FOOTER_AR` يجب أن يكون بين علامتي اقتباس** في `.env.prod`. و**لا تُنفِّذ `source .env.prod` أبداً**: يصدّر متغيّرات قديمة تتفوّق على env-file في compose. استخدم `export COMPOSE_ENV_FILES=.env.prod` في جلسة نظيفة.
6. **الدمج على الـVPS:** `git fetch && git merge` وليس `git pull --ff-only` (يفشل مع commit دمج محلي). نفّذ `cd ~/sharwa_ai` أولاً، وقبل أي دمج: `git checkout -- <ملف عُدّل يدوياً>`.
7. **Prometheus:** ملف `ops/prometheus/secrets/metrics_token` يجب `chown 65534` وصلاحية 600 وإلا لا يقرؤه. تحقّق دائماً بـ`promtool check config` قبل إعادة التشغيل (تعليق حلقة إعادة تشغيل سببها علامات اقتباس مهرّبة في alerts.yml).
8. **العامل:** `docker compose --profile engine up -d --no-deps worker-realtime` كي لا تُعاد إنشاء بقية الخدمات. تحذير `shard_stale:0` عند الإقلاع الأول عابر.
9. **ويندوز:** أوامر المفاتيح تُنفَّذ في CMD أو PowerShell على جهازك لا في bash على الـVPS؛ لا تلصق أحدهما في الآخر.
10. **قبل الترحيل دائماً:** `pg_dump` احتياطي. الترحيلات للأمام فقط عبر `scripts/migrate.sh` ولا تعمل تلقائياً (H62).

## 12. الدومين وTLS — `ai.sharwaah.com` (P4 Task 17؛ النطاق الإنتاجي منذ 2026-10-09 — F-P4-12)

**الحالة:** الملفات مكتوبة ومُختبَرة محلياً (Caddy 2.10.2 وnginx 1.24 أمام خادم بديل، 20/20 فحصاً لكلٍّ منهما)؛ **لم يُنفَّذ شيء على الـVPS** — التنفيذ هو Task 18 بيدك، وكل أمر هنا UNVERIFIED على الخادم حتى تلصق مخرجاته.

**القرار:** المحرك على نطاق فرعي مخصّص **`ai.sharwaah.com`**. السبب: `sharwaah.com` و`*.sharwaah.com` لمتاجر شروه (Django)، ولوحة المحرك تُفتح من صفحة إطلاق على نطاق المتجر. الـAPI يبقى مربوطاً على `127.0.0.1` فقط؛ البروكسي على المضيف هو الباب الوحيد من الإنترنت.

| المسار العام | يصل إلى الـAPI؟ |
|---|---|
| `/console/` (و`/console` ⇐ 308 إلى `/console/`) | نعم — اللوحة |
| `/v1/*` ومنها `/v1/ws` (WebSocket) | نعم |
| `/webhooks/platform/catalog`، `/webhooks/platform/cart` | نعم — سقف الجسم 512 KB في البروكسي (والـAPI يفرض 256 KB + HMAC) |
| `/healthz` | نعم |
| كل ما عداه، ومنه `/metrics` `/readyz` `/docs` `/redoc` `/openapi.json` | **لا — 404 من البروكسي** |

الملفات: `ops/proxy/Caddyfile` (الموصى به: شهادة تلقائية وتجديد تلقائي)، `ops/proxy/nginx-ai.sharwaah.com.conf` (بديل فقط إن كان nginx يملك 80/443 أصلاً)، `ops/proxy/check_proxy.sh` (فحص بعد التشغيل، قراءة فقط).

### 12.0 من يملك المنفذين 80/443 على الـVPS؟

```bash
sudo ss -ltnp '( sport = :80 or sport = :443 )'
```
| النتيجة | الطريق |
|---|---|
| لا شيء | **12.3-أ Caddy** |
| `nginx` | **12.3-ب nginx** (لا تثبّت Caddy: لا يملك المنفذين برنامجان) |
| `caddy` (لموقع آخر) | أضف كتلة `ai.sharwaah.com {...}` من `ops/proxy/Caddyfile` إلى ملفه الحالي بدل استبداله |

### 12.1 DNS (عند مسجّل النطاق — بيدك)

- سجل **A**: الاسم `ai` (في نطاق `sharwaah.com`) ⇐ عنوان IPv4 للـVPS. **AAAA** فقط إن كان للخادم IPv6 يعمل فعلاً (سجل AAAA خاطئ يُفشل إصدار الشهادة).
- إن وُجد سجل `*.sharwaah.com` (wildcard) فالسجل الصريح `ai` يتقدّم عليه — لكن **يجب ألّا يُسمح لمتجر بالاسمين `ai` و`api`** (قرار المالك، أسماء البنية التحتية محجوزة) في شروه (OQ-P4-22).
- إن كان على `sharwaah.com` سجلات CAA فيجب أن تسمح بـ`letsencrypt.org`.

```bash
dig +short ai.sharwaah.com A        # يجب أن يطابق عنوان الخادم
curl -4 -s https://ifconfig.me ; echo   # (على الـVPS) عنوان الخادم للمقارنة
```

### 12.2 الجدار الناري

```bash
sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw status | grep -E '80|443|8000|8100'   # 8000/8100 يجب ألّا يظهرا مفتوحين
```
وافتح 80/443 أيضاً في جدار مزوّد الاستضافة إن وُجد. **لا تفتح 8000/8100 أبداً.**

### 12.3-أ Caddy (الموصى به)

```bash
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy
caddy version                                   # اختُبر الملف على 2.10.2

cd ~/sharwa_ai
PORT=$(grep '^API_HOST_PORT=' .env.prod | cut -d= -f2); echo "API port: ${PORT:-8000}"
sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak.$(date +%s) 2>/dev/null
sudo install -m 644 ops/proxy/Caddyfile /etc/caddy/Caddyfile
sudo sed -i "s/127.0.0.1:8100/127.0.0.1:${PORT:-8000}/g" /etc/caddy/Caddyfile
sudo install -d -o caddy -g caddy /var/log/caddy
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile   # يجب: Valid configuration
sudo systemctl reload caddy || sudo systemctl restart caddy
sudo journalctl -u caddy --since "5 min ago" --no-pager | grep -iE 'certificate obtained|error' | tail -5
```
الشهادة تُصدَر في أول دقيقة وتُجدَّد تلقائياً. إن ظهر خطأ ACME: راجع DNS (12.1) والجدار (12.2).

### 12.3-ب nginx (بديل، فقط إن كان nginx موجوداً)

الكتلة 443 تشير إلى شهادة غير موجودة بعد، لذا على مرحلتين:
```bash
cd ~/sharwa_ai
PORT=$(grep '^API_HOST_PORT=' .env.prod | cut -d= -f2)
sudo apt-get install -y certbot
sudo mkdir -p /var/www/certbot
# (1) المنفذ 80 فقط لإصدار الشهادة
sed '/^# --- port 443/,$d' ops/proxy/nginx-ai.sharwaah.com.conf | sed "s/127.0.0.1:8100/127.0.0.1:${PORT:-8000}/" | sudo tee /etc/nginx/conf.d/sharwa-ai.conf >/dev/null
sudo nginx -t && sudo systemctl reload nginx
sudo certbot certonly --webroot -w /var/www/certbot -d ai.sharwaah.com --deploy-hook "systemctl reload nginx"
# (2) الملف كاملاً
sed "s/127.0.0.1:8100/127.0.0.1:${PORT:-8000}/" ops/proxy/nginx-ai.sharwaah.com.conf | sudo tee /etc/nginx/conf.d/sharwa-ai.conf >/dev/null
sudo nginx -t && sudo systemctl reload nginx
systemctl list-timers | grep -i certbot        # التجديد التلقائي
```
ملاحظات: nginx ≥ 1.25.1 يطبع تحذير `listen ... http2 is deprecated` — غير ضار. إن لم يكن للخادم IPv6 فاحذف سطري `listen [::]` (وإلا يفشل `nginx -t`). nginx يرسل `Server: nginx` بلا رقم إصدار.

### 12.4 متغيّرات `.env.prod` الخاصة بالنطاق

يتطلب هذا آخر نسخة من المستودع (compose صار يمرّر المتغيّرين — F-P4-11):
```bash
cd ~/sharwa_ai && export COMPOSE_ENV_FILES=.env.prod && umask 077
sed -i '/^CONSOLE_ALLOWED_ORIGINS=/d' .env.prod
printf 'CONSOLE_ALLOWED_ORIGINS=https://ai.sharwaah.com\n' >> .env.prod
# فقط حين تجهز صفحة الإطلاق في شروه (Task 6ب) — وإلا اتركه فارغاً:
# sed -i '/^CONSOLE_SSO_PLATFORM_ORIGINS=/d' .env.prod && printf 'CONSOLE_SSO_PLATFORM_ORIGINS=https://*.sharwaah.com\n' >> .env.prod
chmod 600 .env.prod
docker compose config --quiet && docker compose up -d --no-deps api
docker compose exec api env | grep -E '^CONSOLE_(ALLOWED|SSO)'
```
بدون `CONSOLE_ALLOWED_ORIGINS` يرفض الـAPI مصافحة `/v1/ws` من المتصفح عبر النطاق (Origin غير مسموح) ويخسر صندوق الوارد التحديث الحيّ.

### 12.5 التحقّق

```bash
cd ~/sharwa_ai && bash ops/proxy/check_proxy.sh
```
المتوقّع: `N passed, 0 failed` (مع الـAPI الحقيقي: `/v1/me` ⇐ 401 و`GET` على الـwebhook ⇐ 405؛ كلاهما يعني «وصل الـAPI»). ثم افتح `https://ai.sharwaah.com/console/` من متصفحك.

### 12.6 ما يأتي في Task 18 (بيدك)

- روابط الـwebhook في شروه: `https://ai.sharwaah.com/webhooks/platform/catalog` و`https://ai.sharwaah.com/webhooks/platform/cart` (السر نفسه `PLATFORM_WEBHOOK_SECRET`).
- إغلاق نفق SSH المؤقت بعد نجاح 12.5.

### 12.7 الرجوع

`sudo systemctl stop caddy` (أو حذف `/etc/nginx/conf.d/sharwa-ai.conf` ثم `reload`) يعيدك إلى الوصول بالنفق وحده؛ الـAPI لا يتغيّر (ما زال على 127.0.0.1).

### 12.8 قرارات مقصودة

- **HSTS** سنة واحدة **بلا** `includeSubDomains`/`preload`: بقية `*.sharwaah.com` قرار شروه لا المحرك.
- **سجل الوصول بلا query string** (Caddy يحذف `ticket`؛ nginx يسجّل `$uri`): تذكرة الـWebSocket لا تصل ملف سجل. Caddy لا يسجّل قيم `Authorization`.
- **عنوان العميل الحقيقي:** الـAPI يرى عنوان جسر Docker لا عنوان الزائر، ولا يثق بـ`X-Forwarded-For` (`FORWARDED_ALLOW_IPS=127.0.0.1`). لا ميزة اليوم تعتمد على IP (حدود المعدّل لكل مستأجر/موظف).
