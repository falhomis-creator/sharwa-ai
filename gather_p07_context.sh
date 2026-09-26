#!/usr/bin/env bash
# ops/gather_p07_context.sh
#
# قراءة فقط — لا كتابة، لا تعديل، لا git add/commit، لا docker compose up/down.
# الهدف الوحيد: جمع كل العقود/الملفات الحقيقية التي يحتاجها العمل على P0.7
# الفعلي (core/ الباك إند + console/ الفرونت + ترحيل 0002_p0_api.sql) في طلب
# واحد مجمّع، بدل تخمين أي محتوى أو مسار. هذا يطابق نفس المنهجية المتّبعة في
# بداية كل مرحلة سابقة من هذا المشروع (P0.1 و P0.6 وغيرهما).
#
# كما يتحقق هذا السكربت أيضاً من الحالة الحقيقية الراهنة لـ git/الحاويات بعد
# ما وُصف بأنه "بناء وcommit يدوي" لدفعة P0.6c — لم يصلني حتى الآن أي مخرج
# حقيقي يثبت ذلك، وP0.7 سيُبنى فوق نفس docker-compose.yml/قاعدة البيانات، لذا
# من الضروري معرفة الحالة الفعلية قبل أي سطر كود جديد.
#
# الاستخدام: bash ops/gather_p07_context.sh   (من جذر المستودع، مثال: /home/sharwa/sharwa_ai)
# ثم الصق المخرج الكامل كما هو (بلا تلخيص أو حذف أجزاء).

set -uo pipefail

if [ ! -d .git ]; then
  echo "REFUSED: هذا ليس جذر مستودع git ($(pwd))." >&2
  echo "شغّل من جذر المستودع (مثال: /home/sharwa/sharwa_ai)." >&2
  exit 1
fi

sec() { echo; echo "========================================================================"; echo "== $1"; echo "========================================================================"; }

sec "[0] الموقع والفرع"
pwd
git branch --show-current 2>&1
git status --porcelain=v1 --branch 2>&1

sec "[1] آخر 20 commit (للتحقق الحقيقي من ادّعاء P0.6c: هل تم commit فعلاً؟)"
git log --oneline -20 2>&1

sec "[1b] آخر commit بالتفصيل (رسالة + diff --stat)"
git show --stat -1 2>&1

sec "[1c] هل هناك تغييرات غير مُلتزَمة الآن (staged أو غير staged)؟"
git status 2>&1
echo "--- git diff --stat (unstaged) ---"
git diff --stat 2>&1
echo "--- git diff --stat --cached (staged) ---"
git diff --stat --cached 2>&1

sec "[2] حالة الحاويات الحقيقية الآن"
docker compose ps -a 2>&1

sec "[3] docker-compose.yml الحالي (كامل، كما هو على القرص الآن)"
cat docker-compose.yml 2>&1

sec "[4] هل مجلد core/ موجود أصلاً؟ (هل بدأ عمل P0.7 من قبل؟)"
if [ -d core ]; then
  echo "core/ موجود. المحتويات:"
  find core -type f | sort
else
  echo "core/ غير موجود بعد."
fi

sec "[5] هل مجلد console/ موجود أصلاً؟"
if [ -d console ]; then
  echo "console/ موجود. المحتويات:"
  find console -type f | sort
else
  echo "console/ غير موجود بعد."
fi

sec "[6] كل ملفات SQL في المستودع (لتحديد المسار الحقيقي لـ schema.sql وأي ترحيلات قائمة)"
find . -name "*.sql" -not -path "./node_modules/*" -not -path "*/node_modules/*" 2>&1 | sort

sec "[7] محتوى schema.sql الحقيقي الكامل (المسار كما ظهر في [6] أعلاه — عدّل المسار التالي إن اختلف)"
SCHEMA_FILE="$(find . -name 'schema.sql' -not -path '*/node_modules/*' 2>/dev/null | head -1)"
if [ -n "$SCHEMA_FILE" ]; then
  echo "المسار: $SCHEMA_FILE"
  cat "$SCHEMA_FILE" 2>&1
else
  echo "لم يُعثر على schema.sql تلقائياً — الصق المسار الصحيح يدوياً إن كان اسمه مختلفاً."
fi

sec "[8] هل يوجد core/migrations/0001_baseline.sql (نسخة بايت-ببايت من schema.sql حسب الوثيقة)؟"
find . -path "*migrations*" -name "*.sql" -not -path "*/node_modules/*" 2>&1 | sort

sec "[9] محتوى ops/ الكامل (قوائم + الملفات النصية الصغيرة)"
find ops -type f 2>&1 | sort

sec "[10] docs/reference/ (إن وُجد) — القائمة فقط"
if [ -d docs/reference ]; then
  find docs/reference -type f | sort
else
  echo "docs/reference/ غير موجود."
fi

sec "[11] .env.example كاملاً (بلا أسرار حقيقية بحكم كونه مثالاً)"
cat .env.example 2>&1

sec "[12] أسماء المتغيرات المعرّفة فعلياً في .env (بدون طباعة القيم أبداً — لحماية الأسرار)"
grep -oE '^[A-Z_][A-Z0-9_]*=' .env 2>&1 | sed 's/=$//' | sort

sec "[13] هل توجد أي إشارة سابقة لـ JWT/JWKS/SSO في المستودع (بحث عن الأسماء فقط، لا القيم)؟"
grep -rlE 'JWKS_URL|JWT_PUBLIC_KEY_PEM|SSO_LOGIN_URL' --include='*.example' --include='*.md' . 2>/dev/null | grep -v node_modules | sort

sec "[14] بيئة الأدوات على هذا الـVPS"
echo "-- node --"
node --version 2>&1
echo "-- npm --"
npm --version 2>&1
echo "-- python3 --"
python3 --version 2>&1
echo "-- python3.12 (إن وُجد كأمر منفصل) --"
command -v python3.12 >/dev/null 2>&1 && python3.12 --version 2>&1 || echo "python3.12 غير موجود كأمر منفصل"
echo "-- pip3 --"
pip3 --version 2>&1
echo "-- docker --"
docker --version 2>&1
echo "-- docker compose --"
docker compose version 2>&1

sec "[15] آخر 40 سطر من docs/P0_PROGRESS.md (خط الأساس المُسجَّل لكل مرحلة)"
tail -n 40 docs/P0_PROGRESS.md 2>&1

sec "[16] محتوى docs/PHASE_GATE.md كاملاً (الحَكَم الوحيد لاكتمال المرحلة)"
cat docs/PHASE_GATE.md 2>&1

sec "[17] آخر 30 سطر من docs/P0_FINDINGS.md و docs/P0_DEVIATIONS.md (هل ظهرت فيها F18/D-28 فعلاً بعد دفعة P0.6c؟)"
echo "--- P0_FINDINGS.md (tail) ---"
tail -n 30 docs/P0_FINDINGS.md 2>&1
echo "--- P0_DEVIATIONS.md (tail) ---"
tail -n 30 docs/P0_DEVIATIONS.md 2>&1

sec "[18] نتيجة hunt_gate الحقيقية الآن على الكود كما هو على القرص"
node scripts/hunt_gate.mjs 2>&1

sec "[19] هل توجد إشارة إلى api أو core في docker-compose.yml الحالي (سيؤثر على كيفية إضافة خدمة api الجديدة)؟"
grep -n -A3 '^\s*api:' docker-compose.yml 2>&1 || echo "لا توجد خدمة باسم api في docker-compose.yml بعد."

sec "انتهى. الصق كل المخرج أعلاه كما هو بلا حذف أو تلخيص."
