# P0 — تدقيق رقم 2 (على جولة R1) — 2026-09-21

**الحكم:** البند (1) **مقبول**. البندان (2) و(3) **مرفوضان جزئياً**: فيهما عيوب حقيقية أثبتُّها بتشغيلها بنفسي (لا بالقراءة فقط). `PHASE_GATE.md` يبقى `P0: BLOCKED`. المسودات تبقى `[UNVERIFIED]`.

## 1. ما شغّلتُه بنفسي (على نسخة الملفات كما وصلتني)

| الفحص | النتيجة |
|---|---|
| `node --test scripts/__tests__/hunt_gate.test.mjs` | **11/11 ناجح** ✔ (يطابق تقريرك) |
| `node scripts/hunt_gate.mjs` | 5 مخالفات كما في تقريرك ✔ |
| `node --test gateway/src/__tests__/identity.test.js` | 7/7 ناجح ✔ (لكن انظر E1: الاختبارات لا تغطي الشكل الحقيقي) |
| `docker compose config` على `docker-compose.yml` بقيم `.env.example` | **نجح** (الصياغة والقيم والحدود سليمة) ✔ |
| تشغيل `10_roles.sh` على PostgreSQL 16 حقيقي | **فشل** ✘ (B1 وB2 أدناه) |
| تطبيق `schema.sql` بمستخدم ترحيلات **غير superuser** ثم `schema_selftest.sql` | **ALL SELF-TESTS PASSED (34)** ✔ — يثبت أن تصميم الأدوار (مالك غير superuser + RLS بلا FORCE) سليم |
| تحليل `postgres.conf` و`redis-durable.conf` بنهايات أسطر CRLF | يُقبلان (لكن راجع B1) |

لم أستطع التحقق من وجود وسوم الصور على Docker Hub (الشبكة محجوبة عندي): أول أمر على الـVPS `docker compose pull && docker compose build`.

## 2. العيوب (مرقّمة؛ أصلحها كلها في الجولة التالية)

### حرجة (أثبتُّها بتشغيلها)

**B1 — نهايات أسطر CRLF في كل ملفات البنية ولا يوجد `.gitattributes`.**
الدليل: `10_roles.sh: line 10: set: pipefail: invalid option name` (المخرج الحقيقي). كل الملفات فيها `\r` (68 سطراً في السكربت وحده). سكربت `init` سيفشل في الحاوية فتفشل تهيئة قاعدة البيانات كاملة.
الإصلاح: `.gitattributes` (`* text=auto eol=lf` و`*.sh text eol=lf` و`Dockerfile text eol=lf`)، و`.editorconfig` (`end_of_line = lf`)، ثم `git add --renormalize .`، ثم إثبات `grep -c $'\r'` = 0 لكل ملف نصي. **أضف قاعدة إلى `hunt_gate.mjs` (مع اختبار)** تفشل عند وجود `\r` في `*.sh`/`Dockerfile`/`*.yml`/`*.conf`/`*.ini`/`*.sql`.

**B2 — `10_roles.sh`: متغيرات psql `:'app_user'` لا تُستبدل داخل كتل `DO $$ … $$`.**
الدليل (بعد إزالة CR): `ERROR: syntax error at or near ":"` ثم `exit=3`. الإصلاح **المُتحقَّق منه** (جرّبتُه: ينشئ الأدوار، والمنح تعمل، و`schema.sql` يُطبَّق بمستخدم الترحيلات، والاختبار الذاتي 34/34):

```sql
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L NOBYPASSRLS', :'app_user', :'app_password')
  WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec
-- كرّر للـ system_user و migration_user
GRANT sharwa_app TO :"app_user";
GRANT sharwa_system TO :"system_user";
GRANT sharwa_app TO :"migration_user";
GRANT CREATE ON DATABASE :"dbname" TO :"migration_user";
GRANT CREATE ON SCHEMA public TO :"migration_user";
```
(كتلة إنشاء الأدوار الأساسية `sharwa_app/sharwa_system` بلا متغيرات فتصح كما هي داخل `DO`.) استعمل هذا النمط، وأضف اختبار تكامل حقيقي لسكربت التهيئة (على الـVPS: حاوية Postgres جديدة ⇒ الأدوار موجودة ⇒ `schema.sql` بمستخدم الترحيلات ⇒ `schema_selftest.sql`).

**E1 — `identity.js` يتجاهل الشكل الحقيقي لـ `sender_pn` (منطق خاطئ، واختباراتك تخفيه).**
الدليل من F1 نفسه: `ev.emit('chats.phoneNumberShare', { lid, jid: node.attrs.sender_pn })` — أي أن `sender_pn` **JID** (`…@s.whatsapp.net`) لا أرقام مجرّدة. شغّلتُ:
```
resolvePhoneE164({jid:'123…@lid', senderPn:'201111112222@s.whatsapp.net'})  -> null   (المتوقع: الرقم)
normalizeIdentity({remoteJid:'201234567890:12@s.whatsapp.net'})  -> phone_e164:null, wa_id:'…:12@s.whatsapp.net'
lidMap.get() يعيد JID  -> null
```
اختباراتك السبعة تمرّر `senderPn: '201111112222'` (أرقام مجرّدة) فلا تكشف ذلك. الإصلاح:
1. دالة تفكيك على غرار `jidDecode`: تأخذ الجزء قبل `@`، وتقطع عند `:` (الجهاز) و`_` (الوكيل)، وتقبل الخادمين `s.whatsapp.net` و`c.us` فقط لاستخراج الرقم؛ و`@lid` لا يُستخرج منه رقم أبداً.
2. `senderPn` وقيم `lidMap.get()` تُقبَل كأرقام مجرّدة **أو** كـJID.
3. `wa_id` للعنوان الرقمي = الأرقام بلا لاحقة الجهاز.
4. **قرار تنسيق:** `phone_e164` يُصدَر بصيغة E.164 الحقيقية **`+` ثم الأرقام** (اسم العمود في المخطط `phone_e164`)، و`wa_id` أرقام بلا `+`. سجّل القرار في `P0_DEVIATIONS.md` كـ«قرار تنسيق مُعتمد من المدقق».
5. اختبارات جديدة بأشكال F1 الحقيقية: `sender_pn` كـJID، وJID بلاحقة `:12`، و`@c.us`، وقيمة lidMap كـJID، وخاصية: أي ناتج غير null يطابق `^\+\d{5,15}$` ولا يُشتق أبداً من الجزء الرقمي لـ`@lid`.

### عالية

**B3 — `redis-durable`: `maxmemory 160mb` داخل `mem_limit 192m` مع AOF `always`.**
إعادة كتابة AOF (fork + copy-on-write + مخزن الكتابة) قد تتجاوز حد الحاوية فتُقتَل (OOM) — أي أن مخزن الـWAL نفسه يسقط (كوارث 16 و17). كما أن Redis ذاته حذّر في تشغيلي من `vm.overcommit_memory` (يُفشل الـfork عند ضيق الذاكرة).
الإصلاح: ارفع `mem_limit` إلى `320m` واجعل `maxmemory 128mb` (أو أبقِ الحد 192m وخفّض `maxmemory` إلى 80mb — وفّق بالقياس)، واضبط `auto-aof-rewrite-min-size`. أضف إلى معايير P0.1 اختباراً حقيقياً: املأ حتى `maxmemory` تحت حمل كتابة ثم `BGREWRITEAOF` وراقب `docker stats`: **الذروة < 85% من الحد وبلا إعادة تشغيل للحاوية**. وأضف `vm.overcommit_memory=1` إلى `docs/P0_RUNBOOK.md` كخطوة إعداد للـVPS (`sysctl`). مجموع ذاكرة طبقة البيانات يبقى ≪ ميزانية 5,360MB.

**B4 — `pgbouncer.ini`: `default_pool_size` يُطبَّق لكل زوج (قاعدة، مستخدم).**
بوجود مستخدمَي `app` و`system` قد يصل عدد اتصالات الخادم إلى ~50+ فتنكسر ميزانية 60 اتصالاً. أضف `max_db_connections = 30` (25 + 5 احتياطي). كذلك: قائمة `userlist` تُولَّد لمستخدم واحد (`DB_USER`) فلن يستطيع `sharwa_system` الدخول عبر PgBouncer؛ قرّر صراحةً (إدخال ثانٍ مُولَّد من `.env`، أو `auth_query`) وسجّله. وتأكد على الـVPS من سلوك مدخل الصورة مع الملف المركّب (ادّعاؤك «لا يستبدله» افتراض غير مُتحقَّق). احذف `server_reset_query = DISCARD ALL` (لا يُستعمل في transaction pooling وفيه تضليل)، وأضف `query_wait_timeout` و`server_idle_timeout` صريحين.

**B5 — منح `spatial_ref_sys`.** أثناء تطبيق `schema.sql` بمستخدم غير مالك ظهر `WARNING: no privileges were granted for "spatial_ref_sys"`. أضف في سكربت التهيئة (superuser) `GRANT SELECT ON spatial_ref_sys TO sharwa_app;` وأثبت باختبار: بدور `sharwa_app` ينجح `SELECT ST_Transform(ST_SetSRID(ST_MakePoint(44,15),4326),3857)`.

### متوسطة

**B6 — `.env.example` بقيمة `change-me` تمرّ من `${VAR:?}`.** من ينسخه كما هو يشغّل النظام بكلمات مرور معروفة (H5). أضف `scripts/check_env.mjs` يرفض: القيمة `change-me`، والأقل من 24 حرفاً، وتكرار كلمة المرور بين دورين؛ واجعل التشغيل الموثّق يمرّ به قبل `docker compose up` (مع اختبار له)، ومولّد أسرار اختياري `node scripts/gen_secrets.mjs`.

**B7 — كلمتا مرور Redis على سطر أوامر الحاوية** (`--requirepass ${…}`) تظهران في `docker inspect`. غيّر إلى إدخال عبر متغير بيئة داخل الحاوية (`sh -c 'exec redis-server … --requirepass "$$REDIS_PASSWORD"'`) وسجّل في `P0_DEVIATIONS.md` أن متغيرات البيئة ما زالت مرئية لمن يملك صلاحية Docker (مقبول في P0، وتُبحث `secrets` لاحقاً).

### ملاحظات (لا إصلاح الآن)
- الشبكة `data` `internal: true` ممتازة للإنتاج؛ لكن `docker-compose.test.yml` لاحقاً يحتاج شبكة غير داخلية لنشر المنافذ، والبوابة/`api` تحتاجان الشبكتين.
- ملاحظة إيجابية: اختيارك `postgis/postgis:16-3.4` + `postgresql-16-pgvector` صحيح، وتوثيق D-6/D-7/D-8 مقبول.

## 3. توجيه الجولة التالية (R2) — لديب سيك

مسموح فقط ما يلي (ما زال لا Docker):
1. إصلاح B1 (مع قاعدة hunt_gate واختبارها) وB2 وB3 وB4 وB5 وB6 وB7 في ملفات المسودة. تُوسَم `[UNVERIFIED]` ما لم يُشغَّل فعلياً؛ **`check_env.mjs` وقاعدة CR في hunt_gate وإصلاح identity تُشغَّل فعلاً هنا** وتُلصق مخرجاتها.
2. إصلاح E1 كاملاً (النقاط 1–5) مع اختبارات الأشكال الحقيقية، وأعد `node --test` للمجموعة كاملة (21 + الجديد) وألصق المخرج.
3. أعد تشغيل `node scripts/hunt_gate.mjs` وألصق المخرج (المتوقع: المخالفات الموروثة الخمس فقط حتى تُلمس `sessions.js`/`index.js`).
4. لا شيء آخر. لا SUBMITTED. عند الانتهاء: ملخص بالمخرجات وتوقف.

**عند وصول الـVPS** (بعد R2): أول ما يُنفَّذ بالترتيب: `sysctl vm.overcommit_memory=1`، ثم `docker compose pull && docker compose build`، ثم `node scripts/check_env.mjs`، ثم `docker compose up -d` وإثبات `healthy` للأربع، ثم اختبار سكربت التهيئة الحقيقي + `schema_selftest.sql` عبر اتصال مباشر، ثم اختبار PgBouncer/RLS، ثم اختبارات Redis (قتل + rewrite). بعدها يُستأنف P0.2 وما بعده حسب `PROMPT_P0`.
