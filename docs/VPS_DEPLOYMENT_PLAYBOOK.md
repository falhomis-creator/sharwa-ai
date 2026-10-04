# VPS_DEPLOYMENT_PLAYBOOK.md — دليل نشر المحرّك على الـVPS (P4.1)

**الحالة:** مكتوب ومُراجَع محلياً؛ **لم يُنفَّذ على الـVPS** (لا وصول لي إليه). كل أمر هنا **UNVERIFIED على الخادم** حتى تلصق مخرجاته. الهدف: فتح لوحة التحكّم على `/console/` والدخول كمدير منصّة، **بلا تفعيل أي تسويق**.

## 0. ثلاث حقائق قبل أن تبدأ

1. **العامل `worker-realtime` لا يُشغَّل في هذا النشر.** هو يرفض الإقلاع تحت `ENV=production` ما دام مزوّد الـLLM/الـEmbedding الوحيد المنفَّذ هو `fake` (حارس H-safety: مزوّد وهمي لا يصل لعميل حقيقي). وُضع في profile باسم `engine`. لوحة التحكّم والـAPI لا يحتاجانه. تشغيله قرار منفصل بعد اختيار مزوّد حقيقي (سؤال مفتوح للمالك).
2. **لا تُولِّد كلمات سر قاعدة البيانات من جديد.** مجلد `pgdata` موجود، وأدوار PostgreSQL أُنشئت بكلمات سر `.env` الحالية؛ تغييرها في الملف يكسر الاتصال. لذلك `.env.prod` يُبنى **من نسخة `.env` الحالي** ثم تُضاف إليه المتغيّرات الناقصة فقط.
3. **المنفذ 8000 قد يكون محجوزاً لـDjango على نفس الخادم.** الخطوة 2 تفحصه وتختار 8000 أو 8100 تلقائياً. الـAPI مربوط على **127.0.0.1 فقط** (لا يُكشف للإنترنت)، وتصل إليه بنفق SSH (الخطوة 7).

افتراضات: المستودع في `~/sharwa-ai`، Docker Compose ≥ 2.24، المستخدم له صلاحية docker.

## 1. جلب آخر نسخة

```bash
cd ~/sharwa-ai
git status --short | head        # يجب أن يكون نظيفاً؛ إن ظهر تعديل محلي فاحفظه قبل المتابعة
git pull --ff-only
git log --oneline -3             # تأكد أن الأحدث يتضمن P3.5 وP4.1
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

**اختياري — نطاق + TLS تلقائي (Caddy على المضيف):** يلزم نطاق يشير A-record إلى الـVPS ومنفذا 80/443 مفتوحان.
```bash
sudo apt-get install -y caddy
sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
console.<دومينك> {
    reverse_proxy 127.0.0.1:8000
}
EOF
sudo systemctl reload caddy
sudo ufw allow 80,443/tcp
```
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

## 9. ما يبقى مقفلاً (لا تغيّره في هذا النشر)

- **لا مستأجر مُفعَّل للتسويق** وكل شيء الافتراضي OFF. التفعيل لاحقاً من اللوحة (عبارة تأكيد حرفية) أو CLI، وبقرارك.
- لا إرسال حيّ: العامل مُوقَف، والمحرّك لا يرسل شيئاً.
- لا `issue-dev-token` في الإنتاج (محظور بتصميمه).

## 10. أعطال شائعة

| العَرَض | السبب | الحل |
|---|---|---|
| `bind: address already in use` على 8000 | Django يحجزه | أعد الخطوة 2 (تختار 8100) ثم `docker compose up -d api` |
| `api` يعيد التشغيل: `JWT ... required` | لا JWKS ولا مفتاح | أضف `JWT_PUBLIC_KEY_PEM` (الخطوة 3) |
| 401 `UNAUTHENTICATED` | issuer/audience/المفتاح لا يطابق | أعد توليد التوكن بالقيم الصحيحة |
| 403 `FORBIDDEN_ROLE` | التوكن ليس platform_admin | أعد التوليد بدون `--role` مخصص |
| `up -d` يعلّق على `gateway` | api يعتمد على gateway healthy | `docker compose logs gateway --tail 30` (من P0) |
| اللوحة بيضاء | CSP/مسار | تأكد أن `./frontend` موجود على الخادم (`ls frontend/index.html`) |
