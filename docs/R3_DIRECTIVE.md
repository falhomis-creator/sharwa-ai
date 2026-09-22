R3 — الـVPS وصل. اقرأ docs/P0_AUDIT_03.md (قسم "يوم الـVPS") ثم نفّذ حرفياً بالترتيب:

أ) على الـVPS (Linux، Docker حقيقي): سجّل في docs/P0_PROGRESS.md مخرجات: nproc, free -m, df -h, docker --version, docker compose version, uname -r.
ب) git ls-files --eol (لا w/crlf)، ثم node scripts/hunt_gate.mjs، ثم node --test لكل الحزم.
ج) node scripts/gen_secrets.mjs && node scripts/check_env.mjs، ثم docker compose pull && docker compose build.
   إن فشل سحب صورة PgBouncer بسبب الوسم: استبدلها بوسم موجود فعلاً وسجّل الانحراف D-13.
د) docker compose up -d، وأثبت healthy للأربع، وأرفق docker inspect (OomScoreAdj, Memory لكل حاوية) و docker stats --no-stream.
هـ) معايير قبول P0.1 كاملة بمخرجات حقيقية (PROMPT_P0 §6 P0.1):
   - سكربت التهيئة داخل الحاوية: الأدوار موجودة؛ schema.sql بمستخدم الترحيلات؛ schema_selftest.sql ينتهي بـ ALL SELF-TESTS PASSED (34).
   - PgBouncer: اتصال بمستخدم التطبيق عبره؛ تحقق فعلي من سلوك userlist مع الملف المركّب؛ اختبار RLS: 1000 معاملة متداخلة لمتجرين بلا أي تسرّب، ومعاملة بلا SET LOCAL تعيد صفراً.
   - Redis durable: قتل kill -9 عشر مرات أثناء XADD متواصل، وكل معرّف مُقَرّ موجود بعد الإقلاع؛ ثم ملء حتى maxmemory مع BGREWRITEAOF وذروة docker stats أقل من 85% من الحد وبلا إعادة تشغيل للحاوية. redis-cache: قتل وتفريغ سلوك سليم موثّق.
   - أي عيب يظهر في المسودة تصلحه وتسجّله.
و) نقطة تحقق C1 (مرة واحدة): بعد نجاح هـ) توقّف وأنشئ docs/P0_C1_REPORT.md بالمخرجات الحقيقية. لا تبدأ P0.2 قبل كلمة "GO P0.2" مني. السبب: أول تشغيل حقيقي كشف عيوباً في كل جولة سابقة.

بعد GO: تكمل P0.2 إلى P0.8 حسب PROMPT_P0 وتدقيقات 01-03 (بما فيها lidmap على redis-durable ومدخل identity_update، وإصلاح console.log و .catch في sessions.js و index.js عند لمسهما)، وgit محلي فقط.
كل قواعد PROMPT_P0 (H1-H15، U1-U9، بروتوكول الإيقاف) سارية. لا SUBMITTED إلا عند اكتمال P0.8، ثم العبارة الحرفية والتوقف.
