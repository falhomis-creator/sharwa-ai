# P1_03_REPORT — تقرير تسليم الدفعة الثالثة (P1.3): «صندوق المحادثات وردّ الموظف»

> **الحالة:** `P1.3: SUBMITTED (awaiting audit).` الـWebSocket مفصول إلى P1.3b (لم يُلمَس).
> **البيئة (قرار المالك):** الفحص الثابت الصارم وحده، بلا حاويات Docker، حتى إشعار آخر.

---

## 0. الخطوة صفر — العيبان الحاجزان + تصليب البوابة (منفَّذة أولاً)

### 0.1 F-P1-05 و F-P1-06

| العيب | الإصلاح | الدليل |
|---|---|---|
| F-P1-05 `optout.detect_handoff` غير معرَّفة | `detect_handoff(message, *, phrases_ar, phrases_en)` في `optout.py` (نفس `normalize()`/`_match`) + `CORE_HANDOFF_PHRASES_AR/_EN` في `WorkerSettings` | البوابة لا ترفع مخالفة على `turn.py`/`optout.py` |
| F-P1-06 `templates.POLICY_EXEMPT_TEMPLATES` غير معرَّفة | `POLICY_EXEMPT_TEMPLATES = frozenset({"safe_ack"})` (قائمة مغلقة) + تحديث `test_workers_turn.py` | البوابة لا ترفع مخالفة على `dispatch.py`/`templates.py` |

### 0.2 تصليب `static_gate.py` (S1-a/b/c/d + S5)

- **S1-a** فحص كل `mod.attr` (قراءة/نداء/إسناد) بدل `ast.Call` وحده.
- **S1-b** هدف لا يُحلّ = مخالفة `[S1] unresolved module target` (لا تخطٍّ صامت) مع قائمة سماح مكتوبة `_EXTERNAL_ALLOWLIST`.
- **S1-c** `_top_level_names` يعيد `(defined, imported)`؛ المطابقة على اسم مستورَد تُبلَّغ «re-export definition».
- **S1-d** أُعيد تشغيل البوابة كآخر أمر قبل التقرير، بمخرج بطابع زمني، مع إفشال متعمَّد جديد.
- **S5** أي ملف في الإغلاق الاستيرادي لـ`app.workers.dispatch`/`app.channels` يذكر `internal_notes` = `[S5]`.

**إثبات الإفشال المتعمَّد (حقيقي):**

| الاختبار | المخرج المُلتقَط | الحالة |
|---|---|---|
| S1-a: `templates.NO_SUCH_CONST` | `_s1d_probe.py:5: [S1] 'templates.NO_SUCH_CONST' - 'NO_SUCH_CONST' not defined in app.workers.templates` | ✔ كُشف ثم حُذف |
| S5: `internal_notes` في `repos_outbox.py` | `repos_outbox.py:0: [S5] send-path module 'app.db.repos_outbox' references 'internal_notes'` | ✔ كُشف ثم حُذف |

**مخرج البوابة الأخير:** 13 مخالفة **كلها سابقة** في `routes_channels.py` (F-P1-04 — دوال P0.7 مفقودة، خارج نطاق P1.3). **ملفات P1.3 الجديدة: صفر مخالفة.**

---

## 1. الملخص

بُني صندوق المحادثات REST: قوائم بترقيم مؤشِّري (cursor) بلا `OFFSET`، تفاصيل المحادثة + بطاقة العميل المحلية، قائمة الرسائل، **ردّ الموظف عبر الزناد**، انتقالات الحالة، التوزيع بقفل تفاؤلي، الملاحظات الداخلية بفصل بنيوي (S5)، كتابة وقراءة `inbox_events`، RBAC بمصفوفة صريحة، رموز أخطاء، مقاييس وتنبيهات، وترحيل `0005`.

**القيمة المعمارية:** رسالة الموظف تُكتب بـ`INSERT INTO messages … sent_by='staff'` فيوقفها الزناد `app.trg_messages_seq()` ذرّياً في نفس المعاملة — **لا نداء يدوي لـ`set_bot_status`** — ويكتب `outbox` بـ`origin='human'` و`expected_epoch=NULL` (لا تُسقَط أبداً، H27). والملاحظات الداخلية في جدول منفصل لا يقرؤه أي مسار إرسال (H28، S5).

| الخطوة | النتيجة |
|---|---|
| الخطوة صفر | ✔ F-P1-05/06 + S1-a/b/c/d + S5 + إفشالان متعمَّدان |
| I1 قوائم + ترقيم مؤشِّري | ✔ `routes_inbox.py` + `repos_inbox` |
| I2 ردّ الموظف | ✔ عبر الزناد + `outbox` (origin=human) + `inbox_events` |
| I3 انتقالات الحالة | ✔ `handoff`/`resume`/`close` عبر `app.set_bot_status` وحدها |
| I4 التوزيع | ✔ `claim`/`assign`/`transfer` بقفل تفاؤلي |
| I5 الملاحظات الداخلية | ✔ قراءة/إضافة + فصل S5 |
| I6 RBAC | ✔ `permissions.py` + مصفوفة 6×4 |
| I7 `inbox_events` | ✔ كاتب من العمال والـAPI + قارئ REST |
| I8 إعادة تزامن REST | ✔ `GET /v1/inbox/events?since=` |
| I9 `create-staff` | ✔ `python -m app.cli create-staff` |
| I10 رموز/مقاييس/تنبيهات | ✔ 6 رموز + 6 مقاييس + قاعدتا تنبيه |
| I11 ترحيل `0005` | ✔ فهرسان فقط (لا جدول موجود) |
| I12 اختبارات نقية + تصليب | ✔ 35 اختبار نقي + البوابة |

---

## 2. الملفات

### مضاف
```
core/app/api/routes_inbox.py          مسارات الصندوق (12 مساراً)
core/app/db/repos_inbox.py            كل SQL الصندوق + بياض حمولة inbox_events + create_staff
core/app/security/permissions.py      RBAC (StaffContext + require_permission + مصفوفة 6×4)
core/migrations/0005_p1_inbox.sql     فهرسا قائمة المحادثات والملاحظات
core/tests/test_inbox.py              6 اختبارات نقية (G2/G9/G10)
```

### معدَّل
```
core/app/api/errors.py                6 رموز أخطاء جديدة
core/app/main.py                      include inbox_router + middleware مقاييس الطلبات
core/app/cli.py                       create-staff
core/app/obs/metrics.py               6 عائلات مقاييس P1.3
core/app/workers/turn.py              handoff.requested + conversation.updated (I7)
core/app/workers/evt.py               message.status (I7)
core/app/workers/realtime.py          message.new الوارد (I7)
core/app/db/repos_ingest.py           insert_message يعيد (id, seq)
core/app/workers/templates.py         F-P1-06: POLICY_EXEMPT_TEMPLATES = {safe_ack}
core/tests/test_workers_turn.py       تحديث اختبار POLICY_EXEMPT_TEMPLATES
scripts/static_gate.py                S1-a/b/c/d + S5 + طابع زمني + re-export notes
ops/prometheus/alerts.yml             قاعدتا تنبيه P1.3
docs/PHASE_GATE.md                    سطر P1.3 → SUBMITTED (فقط)
docs/P1_FINDINGS.md / P1_DEVIATIONS.md / P1_OPEN_QUESTIONS.md
```

---

## 3. الأدلة (حقيقي — Rule 1)

| الأمر | المخرج الفعلي |
|---|---|
| `python scripts/static_gate.py` | `[static_gate 2026-09-27T11:27:06Z]` + `STATIC GATE FAILED — 13 violation(s)` (كلها F-P1-04 في `routes_channels.py`) |
| `python -m py_compile …` | `EXIT: 0` |
| استيراد الوحدات الجديدة | `IMPORTS OK` |
| `pip install httpx` | `Successfully installed … httpx-0.28.1` |
| فحوص نقية (سكربت مستقل) | `test_inbox: 6, turn: 7, optout: 10, schema: 7, stream: 5` ⇒ **TOTAL 35 PASS** |

> **لماذا سكربت مستقل لا `pytest`:** `conftest.py` يفتح تجمّع DB autouse فيفشل على `password authentication failed` (أدوار `p07_*` غير مكوَّنة محلياً). شُغِّلت الاختبارات النقية كدوال عادية — متسق مع تقرير P1.2.

---

## 4. جدول الدَيْن التقني (V3) — بلا خلية فارغة

| المعيار | الحالة |
|---|---|
| A0 (A1–A20 من P1.1) | **مؤجَّل — يحتاج حاويات** |
| A1–A16 (P1.2 turn/dispatch/evt) | **مؤجَّل — يحتاج حاويات** |
| A17–A22 (P1.2) | **مؤجَّل — يحتاج حاويات** |
| G3 ردّ الموظف (ترتيب الزناد + outbox) | **مُثبَت بقراءة الكود**؛ التشغيل **مؤجَّل — يحتاج PG** |
| G4 idempotency فعلي (صف واحد) | **مُثبَت بقراءة الكود**؛ **مؤجَّل — يحتاج PG** |
| G5 عزل H29 فعلي (NOT_FOUND عبر RLS) | **مُثبَت بقراءة الكود**؛ **مؤجَّل — يحتاج PG** |
| G8 ترقيم مؤشِّري (لا OFFSET) | **مُثبَت بقراءة الكود**؛ **مؤجَّل — يحتاج PG** |
| RLS حقيقي / التزامن / الأداء | **مؤجَّل — يحتاج حاويات** |
| احتفاظ `inbox_events` (OQ-P1-08) | **دين معلَن** — لا حذف؛ السياسة في P1.3b مع sweeper |
| بطاقة العميل C9 (OQ-P1-09) | **دين معلَن** — محلية فقط |
| مؤقتات SLA (OQ-P1-10) | **دين معلَن** — مع `scheduled_jobs`/sweeper |
| `audit_log` لانتقالات الحالة (D-P1-21) | **دين معلَن** — مع P1.8 console |

---

## 5. مصفوفة القبول G0–G14 (بلا خلية فارغة)

| # | الحالة |
|---|---|
| G0 الخطوة صفر | ✔ F-P1-05/06 + S1-a..d + S5، بمخرج بوابة + إفشالين متعمَّدين |
| G1 بوابة صفر على الجديد | ✔ **ملفات P1.3 الجديدة صفر مخالفة**؛ المتبقي 13 سابقة F-P1-04 |
| G2 مصفوفة الصلاحيات 24 حالة | ✔ `test_permission_matrix_24_cases` (6×4) — PASS |
| G3 ردّ الموظف بالزناد | ✔ قراءة كود: `insert_staff_message` ثم `insert_outbox(origin=human, expected_epoch=NULL)` في `tenant_tx` واحدة؛ **لا `set_bot_status`** |
| G4 idempotency | ✔ قراءة كود: `fetch_idempotency` ⇒ replay/`IDEMPOTENCY_KEY_REUSED` |
| G5 عزل H29 | ✔ `WHERE tenant_id` + RLS ⇒ `NOT_FOUND` (قراءة كود) |
| G6 انتقالات الحالة | ✔ `set_bot_status` وحدها؛ `CONFLICT_VERSION` عند NULL؛ `resume` لا يضبط `needs_turn`؛ كل انتقال يكتب `conversation.updated` |
| G7 الملاحظات الداخلية | ✔ S5 خضراء + `INBOX_EVENT_ALLOWED_KEYS` يستبعد نص/هاتف |
| G8 ترقيم مؤشِّري | ✔ `_decode_cursor` تالف ⇒ `VALIDATION_FAILED`؛ `limit` يُقصّ؛ **لا OFFSET** |
| G9 `inbox_events` | ✔ قائمة بيضاء + `write_inbox_event` يرفض أي مفتاح خارجها |
| G10 رموز الأخطاء | ✔ 6 رموز + `ApiError` يرفض غير المُعلَن |
| G11 مقاييس وتنبيهات | ✔ 6 عائلات موصولة + قاعدتا تنبيه (S2/S3/S4 تمر) |
| G12 استيراد | ✔ `pip install httpx` نجح؛ استيراد كل وحدة `IMPORTS OK`؛ `TestClient` **مؤجَّل — PG** |
| G13 لا انحدار | ✔ 35 اختبار نقي PASS |
| G14 دفتر الدَيْن V3 | ✔ موسَّع بلا خلية فارغة |

## 6. الإقرار الذاتي (H1–H30)

| المادة | الحالة |
|---|---|
| H1–H26 | ملتزم (كما P1.1/P1.2) |
| H27 رسالة الموظف مقدَّسة | ملتزم: `origin='human'` لا يمر ببوابة `ai_reply`؛ سقف تقني معلن فقط |
| H28 الملاحظة لا تخرج | ملتزم: جدول منفصل + S5 تمنع قراءته من أي مسار إرسال |
| H29 tenant من الرمز + NOT_FOUND | ملتزم: `WHERE tenant_id` + RLS ⇒ `NOT_FOUND` لا `FORBIDDEN` |
| H30 كل POST idempotent | ملتزم: `Idempotency-Key` إلزامي + `api_idempotency` في ردّ الموظف |

## 7. قيود معروفة بصدق

1. **لا Docker/Redis/PG حقيقي**: معايير التشغيل (A0/A1–A22، G3/G4/G5 التشغيلية، RLS الحقيقي، التزامن، الأداء) مؤجَّلة بالكامل (قرار المالك).
2. `pytest` الكامل لم يُشغَّل لأن `conftest.py` يفتح تجمّع DB عند الجلسة؛ شُغِّلت الاختبارات النقية كدوال عادية (35 PASS).
3. `mypy`/`ruff`/`importlinter`/`pip-audit` لم تُثبَّت (حاولت `pip install -r requirements.txt` وانتهى بـtimeout؛ لكن `httpx`/`fastapi`/`starlette`/`PyJWT`/`pytest` ثُبِّتت).
4. `CONFLICT_VERSION` بلا «الحالة الحالية» في جسم الخطأ (D-P1-19).
5. لا `audit_log` لانتقالات الحالة (D-P1-21) — دين معلَن.

---

## 8. الختام

`P1.3 COMPLETE — STOPPING. AWAITING AUDIT. NO P1.3b WORK STARTED.`

