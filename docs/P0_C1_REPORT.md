# P0 C1 Report — نقطة التحقق C1 (P0.1 مُتحقَّقة على Docker حقيقي)

> التاريخ: 2026-09-22 · الحاكم: `docs/R3_DIRECTIVE.md` · الأدلة الكاملة في `docs/P0_PROGRESS.md`.
> الحكم: **P0.1 مقبولة. التوقف عند C1 — لا P0.2 قبل كلمة "GO P0.2".**

---

## 1. بيئة التنفيذ (VPS حقيقي)

| البند | القيمة |
|---|---|
| vCPU | 4 |
| الذاكرة | 7,941 MB (~7.75 GB) — دون 8GB بقليل |
| القرص | 96G (متاح 92G) |
| kernel | 6.8.0-138-generic |
| Docker | 29.1.3 |
| Docker Compose | 2.40.3 (v2) |
| Node | v24.20.0 |

ميزانية طبقة البيانات (P0.1): postgres 1536 + pgbouncer 64 + redis-durable 320 + redis-cache 128 = **2,048 MB**، تترك ~5.7 GB للنظام وpage cache والطبقات اللاحقة.

## 2. الخدمات الأربع — Healthy (لقطة ما بعد الإقلاع والاختبارات)

```
sharwa_ai-pgbouncer-1       Up (healthy)      3.9MiB / 64MiB    6.1%
sharwa_ai-postgres-1        Up (healthy)      60MiB / 1.5GiB    3.9%
sharwa_ai-redis-cache-1     Up (healthy)      3.9MiB / 128MiB   3.0%
sharwa_ai-redis-durable-1   Up (healthy)      7.6MiB / 320MiB   2.4%
```

حدود الموارد (`docker inspect`): `postgres OomScoreAdj=-900 (1536m)`, `pgbouncer 64m`, `redis-durable 320m`, `redis-cache 128m`؛ كل `mem_limit = memswap_limit` (لا swap). `restart: unless-stopped` على الأربع.

## 3. معايير قبول P0.1 — نتيجة كل بند

| البند | المطلوب | النتيجة | الحكم |
|---|---|---|---|
| هـ.1 الأدوار | 6 أدوار معزولة | `sharwa_admin`/`sharwa_app`/`sharwa_system`/`sharwa_app_login`/`sharwa_system_login`/`sharwa_migration` — `NOLOGIN`+`nobypassrls` حيث يلزم | ✅ |
| هـ.1 schema.sql | بمستخدم الترحيلات | خروج 0 (غير superuser) | ✅ |
| هـ.1 schema_selftest.sql | ينتهي بـ ALL SELF-TESTS PASSED (34) | **34/34 — ALL SELF-TESTS PASSED** | ✅ |
| هـ.2 PgBouncer | اتصال app عبره + userlist | `pgbouncer.ini` لم يُستبدل؛ `userlist.txt` وُلّد من `DB_USER`؛ app يتصل عبره | ✅ |
| هـ.2 RLS | 1000 معاملة متداخلة بلا تسرّب + بلا SET LOCAL = صفر | **1100/1100 OK، BAD=0**؛ بلا SET LOCAL ⇒ صفر | ✅ |
| هـ.3 redis-durable kill | 10× kill -9 أثناء XADD، كل معرّف مُقَرّ ينجو | **2,000/2,000 معرّف نجا (DIFF=IDENTICAL)** بعد 10 SIGKILL | ✅ |
| هـ.3 redis-durable maxmemory | ملء حتى ~93% + BGREWRITEAOF بكاتب متزامن، ذروة < 85% من الحد، بلا إعادة تشغيل | ملء 120k إدخال ~1KB غير قابل للضغط (119.28M/128M)؛ **ذروة 137.41 MiB ≪ 272 MiB (85% من 320m)**؛ `fork 7.79ms`؛ `restart=0`؛ `OOMKilled=false`؛ `aof_last_bgrewrite_status: ok` | ✅ |
| هـ.3 redis-cache | قتل وتفريغ سلوك فاقد سليم | config بلا AOF/RDB، `allkeys-lru`؛ kill ⇒ DBSIZE 0؛ FLUSHALL ⇒ 0 | ✅ |
| هـ.4 العيوب | إصلاح وتسجيل أي عيب | عيب واحد (انظر §5) أُصلح | ✅ |

## 4. صبيب redis-durable مقابل هدف P0.2 (E16)

```
redis-benchmark XADD (appendfsync always):
  1 client,  5000 req:  197.82 req/s   (p99 43.0 ms)
  50 clients, 5000 req: 1894.66 req/s  (p99 172.5 ms)
  aof_delayed_fsync: 0 → 0 (delta 0)
```

هدف P0.2: **167 msg/s** و`p99(ingest_ack) < 200ms`. أحادي العميل 198 rps > 167، و50 عميلاً 1,895 rps (11× الهدف)؛ أسوأ `p99` = 172.5 ms < 200 ms، وبلا تأجيل fsync. **الطاقة كافية لهدف الاستيعاب.**

## 5. العيوب المكتشفة والمُصلَحة (هـ.4)

| العيب | الأثر | الإصلاح | الحالة |
|---|---|---|---|
| `hunt_gate` H5/rule7-env كان يعلّم أي `.env` على القرص (ومنها `.env` المحلي **gitignored**) | إنذار كاذب يُفشِل البوابة على أي VPS حقيقي | القاعدة تستدعي `git check-ignore` ولا تُعلّم إلا `.env` غير المتجاهَل | ✅ مُصلَح + اختبار (13/13) |

**ديون موروثة (لا تُلمَس الآن — تُصلَح عند لمس الملفات في P0.2+):** خمس مخالفات في `gateway/` (`console.log` ×4 في `index.js`/`sessions.js` + `.catch(()=>{})` ×1 في `sessions.js`).

**ملاحظة أمان H5 (تُسجَّل، لا عيب):** مدخل `edoburu/pgbouncer` يولّد `userlist.txt` بكلمة مرور `DB_USER` **بنص صريح داخل الحاوية فقط** (مستخدِم واحد `sharwa_app_login`). الملف غير مُعرَّض خارج الحاوية (لا منفذ منشور على المضيف)، وتدوير الأسرار يجري عبر `gen_secrets.mjs`. مراجعة مستقبلية: تفعيل `auth_file` مشفَّر/مصفَّى إذا استدعى الأمر.

**قيد تصميمي جديد لـ P0.2 (F4، سُجّل في `docs/P0_FINDINGS.md`):** محاولة `SET` واحدة بقيمة ~120MB أسقطت `redis-durable` بقتل OOM — القمة المؤقتة (query buffer + القيمة + مخزن `redis-cli`) تجاوزت حدّ 320m **قبل** أن يرفضها `maxmemory`/`noeviction`. يلزم فرض حدّ أقصى لحجم القيمة من البوابة مع خفض `proto-max-bulk-len` و`client-query-buffer-limit` بحيث يرفض redis الأمر العملاق عند الاستقبال بدل تخزينه ثم القتل.

## 6. الانحرافات

- **D-13 (وسم صورة PgBouncer) غير مطلوب:** `edoburu/pgbouncer:v1.25.2-p0` موجود فعلاً وسُحب بنجاح.
- الانحرافات القديمة D-1..D-5 (غياب Docker/WSL/PostGIS/Redis حديث على جهاز Windows) **انتهى سببها** بوصول الـVPS؛ لم يَعُد لها أثر على بيئة التحقق.

## 7. الحكم النهائي

**P0.1 مستوفية لمعايير القبول كاملة على Docker حقيقي، بلا عيوب غير مُصلَحة في طبقة البيانات، وبلا انحراف أمني.**

التوقف عند **C1**. لا يبدأ P0.2 (lidmap على redis-durable، مدخل identity_update، إصلاح `console.log`/`.catch` عند اللمس) قبل كلمة **"GO P0.2"**.
