P0: CLOSED — أغلقها المالك رسمياً بنسبة 100% (قرار إداري 2026-09-26). P0.0–P0.6 منجزة ومتحقَّق منها على Docker حقيقي؛ P0.7 الباك إند (core/) منجز (240/240 اختباراً، mypy --strict/ruff/import-linter نظيفة)؛ P0.7 الفرونت إند (console/) مؤجَّل بقرار المالك إلى دفعة لاحقة من P1 مع صندوق المحادثات؛ P0.8 (E2E/chaos/report) مُسقطة بقرار المالك ولا تُطالَب.
P1: OPEN — الدفعة الأولى قيد التنفيذ بـ PROMPT_P1_01_ingest_for_deepseek.md، وتغطي P1.0 (دور worker-realtime في core/: الرصد، عميل redis-durable، compose، بوابات الفحص) و P1.1 (مستهلك الاستيعاب: in:{shard} ← معاملة PostgreSQL واحدة، opt-out حتمي، إشارة التسليم البشري، identity_update، DLQ) حصراً. لا LLM ولا إرسال ولا فرونت إند في هذه الدفعة.
P1.2..P1.8: LOCKED — يفتحها المدقق بعد قبول P1.0+P1.1 (P1.2 outbox/dispatcher، P1.3 Inbox API/WebSocket، P1.4 الكتالوج والبحث الهجين، P1.5 طبقة LLM والميزانيات، P1.6 الوكيل وOutput Verifier، P1.7 تتبع الطلبات ورابط الدفع، P1.8 console/ وقبول P1).
P2: LOCKED
P3: LOCKED
P4: LOCKED
P5: LOCKED
