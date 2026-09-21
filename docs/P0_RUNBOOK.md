# P0 Runbook — VPS first-run sequence (audit P0_AUDIT_02 §3)

> تُنفَّذ بالترتيب على جهاز/خادم Linux فيه Docker، بعد جولة R2.

## 0. متطلبات مسبقة

- Docker + Compose v2 مثبّتان، و`node` ≥ 22 متاح لتشغيل سكربتات الفحص.

## 1. إعداد النظام (قبل أي حاوية)

```bash
# Redis AOF rewrite يعتمد على fork؛ بدون هذا قد يفشل fork عند ضيق الذاكرة (B3).
sudo sysctl vm.overcommit_memory=1

# للإبقاء بعد إعادة التشغيل:
echo 'vm.overcommit_memory=1' | sudo tee /etc/sysctl.d/99-sharwa-ai.conf
```

## 2. سحب وبناء الصور

```bash
docker compose pull
docker compose build
```

## 3. فحص الأسرار (إجباري قبل الإقلاع)

```bash
node scripts/check_env.mjs          # يجب أن يطبع OK؛ أي فشل يوقف العملية
# إن لم يوجد .env بعد:
node scripts/gen_secrets.mjs && node scripts/check_env.mjs
```

## 4. الإقلاع والتحقق من الصحة

```bash
docker compose up -d
docker compose ps                  # يجب أن تكون postgres/pgbouncer/redis-durable/redis-cache كلها healthy
```

## 5. اختبار سكربت التهيئة + schema_selftest

```bash
# حاوية Postgres جديدة ⇒ الأدوار موجودة ⇒ schema.sql بمستخدم الترحيلات ⇒ schema_selftest.sql
# (يُثبت B2 وB5: ينتهي بـ ALL SELF-TESTS PASSED — 34 بنداً)
```

## 6. اختبار PgBouncer / RLS

```bash
# ألف معاملة متداخلة متزامنة لمتجرين عبر PgBouncer: صفر تسرّب بين المتجرين،
# ومعاملة بلا SET LOCAL تعيد صفراً (معيار قبول P0.1).
```

## 7. اختبارات Redis

```bash
# redis-durable: املأ حتى maxmemory تحت حمل كتابة ثم BGREWRITEAOF وراقب
# docker stats — الذروة < 85% من الحد وبلا إعادة تشغيل للحاوية (B3).
# ثم kill -9 + إعادة تشغيل: كل المعرّفات المُقرّة موجودة (10 مرات).
# redis-cache: تفريغها بعد القتل سلوك سليم (موثّق).
```

## 8. بعد نجاح كل ما سبق

تُستأنف P0.2 وما بعدها حسب `PROMPT_P0_foundation_for_deepseek.md`.
