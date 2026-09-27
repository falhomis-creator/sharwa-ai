# P1 Dependencies — الاعتماديات

> H11: كل مكتبة جديدة باسمها وإصدارها وسببها وترخيصها + مخرج `pip-audit`. لا مكتبة جديدة أُضيفت في هذه الدفعة.

---

## مكتبات جديدة: **لا شيء**

كل الكود الجديد يستخدم حصراً:
- **stdlib**: `logging`, `http.server`, `hmac`, `json`, `re`, `unicodedata`, `threading`, `signal`, `dataclasses`, `collections`.
- **اعتماديات قائمة مسبقاً في `core/requirements.txt`**: `pydantic` (لـ`WalEntry`), `prometheus_client` (للعائلات), `psycopg`/`psycopg-pool` (عبر `app/db`), `redis` (لـ`stream.py`).

لذلك لا اسم جديد ولا إصدار جديد ولا سبب تبرير ولا مخرج `pip-audit` جديد مطلوب لهذه الدفعة (لا شيء جديد ليتدقّق).

## سبب عدم كفاية المكتبة القياسية في المواضع الثلاثة

| الموضع | لماذا ليست stdlib كافية | المكتبة (قائمة مسبقاً) |
|---|---|---|
| `WalEntry` | تحقق صريح من المخطط مع `extra='ignore'` مُقاس | `pydantic` |
| مقاييس Prometheus | بروتوكول exposition + histograms/gauges | `prometheus_client` |
| أوامر Redis Streams | `XREADGROUP`/`XAUTOCLAIM`/`XACK`/`XADD` | `redis` |

## ملاحظة `pip-audit`

`python -m pip_audit --version` → **غير مثبَّت** في هذه البيئة (وانتهت محاولة `pip install` بـtimeout). لا اعتماديات جديدة أُضيفت، لذا لا سطح هجوم جديد. يُعاد التشغيل على السيرفر.
