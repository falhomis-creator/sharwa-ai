# MARKETING_RUNBOOK — تشغيل التسويق تحت ضبط (P3.4 · H100–H103)

> **الحالة الافتراضية: مُطفأ لكل المستأجرين.** القالب `cart_reminder` مسجَّل في الكتالوج، لكن التسجيل ليس تفعيلاً: لا رسالة تسويقية تخرج لمستأجر ما لم يُفعَّل بأمر مشغّل صريح (H100). هذا الدليل هو الطريق الوحيد.

## 0. من يفعل ماذا
المنفّذ/الكود لا يُفعِّل أحداً أبداً. **التفعيل فعل بشري** يؤدّيه المالك أو من يفوّضه، من جهاز تشغيل لديه `CORE_MIGRATION_DATABASE_URL`، بالأمر `python -m app.cli marketing enable`. لا API ولا لوحة ولا متغيّر بيئة يُفعِّل.

## 1. قبل أي تفعيل — قائمة المالك
1. **اعتماد النص حرفياً (H103):** نصّ `cart_reminder` والتذييل `MARKETING_FOOTER_AR` ما زالا «مقترحين» حتى يعتمدهما المالك. غيّرهما إن أردت (النصّ في `core/app/workers/config.py`، التذييل بمتغيّر البيئة)، ثم اضبط `MARKETING_FOOTER_AR` **صراحةً** في بيئة العمّال وبيئة الأمر (`enable` يرفض إن لم يكن مضبوطاً).
2. **مراجعة قانونية/سياسة واتساب (مسؤولية المالك):** الإرسال التسويقي على رقم Baileys غير الرسمي يحمل خطر الحظر. لا يقرّر هذا الدليل نيابةً عنك.
3. **اختر مستأجر الكاناري** (يُفضَّل مستأجر ودود، رقمه مُحمّى منذ أيام، وله مشتركون حقيقيون صريحو الموافقة) وسقفاً يومياً صغيراً (الافتراضي 5).
4. **المشتركون:** يُعدّ فقط من اشترك بنفسه برسالة نصية صريحة (`customer_message_optin`). `checkout_optin` و`import` محجوبان عن التسويق (OQ-P3-14).

## 2. الأوامر (كلها: عدّاد وحالات فقط — لا هاتف ولا نصّ عميل في أي مخرج)
```
python -m app.cli marketing status  --tenant-ref <ref>
python -m app.cli marketing preview --tenant-ref <ref>      # جفاف: أعداد + عرض ببيانات اصطناعية، لا يكتب شيئاً
python -m app.cli marketing enable  --tenant-ref <ref> --cap 5 --actor <name> --reason "<…>" \
                                    --confirm "ENABLE-MARKETING <ref>"
python -m app.cli marketing set-cap --tenant-ref <ref> --cap N --actor <name> --reason "<…>"
python -m app.cli marketing disable --tenant-ref <ref> --actor <name> --reason "<…>"
```
`enable` يرفض بسببٍ مكتوب (مفردات مغلقة) إن فشل أحد: `template_not_registered` · `footer_not_explicit` · `no_connected_channel` · `number_not_healthy` · `warmup_not_started` · `warmup_too_young` (أقل من `MARKETING_MIN_WARMUP_DAYS`، الافتراضي 3 أيام) · `global_switch_off` · `no_eligible_subscribers`. كل تفعيل وإيقاف وتغيير سقف يُسجَّل في `marketing_activation_log` (إلحاقي: لا تعديل ولا حذف).

## 3. الكاناري — الخطوات
1. `status` ثم `preview` وتأكّد من الأعداد (كم سلّة مؤهَّلة، كم ستُسقَط ولماذا).
2. `enable` بسقف صغير. السقف **يؤجّل ولا يُسقط** (`canary_cap_reached`): ما زاد عن السقف ينتظر نافذة الـ24 ساعة المتحرّكة ضمن عمر الرسالة.
3. **راقب الساعة الأولى:** `marketing_sent_total` · `policy_verdicts_total{class="marketing"}` (أسباب الإسقاط/التأجيل) · `consent_events_total{action="revoked"}` · حالة الرقم `app.cli policy status --channel …`.
4. ارفع السقف تدريجياً بـ`set-cap` فقط بعد يوم نظيف.

## 4. التراجع بثلاثة مستويات (الأسرع أولاً)
| المستوى | الأمر | الأثر |
|---|---|---|
| ١ — فوري لكل المستأجرين | ضبط المفتاح العام `kill_switches` للقدرة `marketing` على `off` | كل صفّ تسويقي يُسقَط بـ`kill_switch_off` عند بوّابة الإرسال (يُفحص قبل التفعيل) |
| ٢ — مستأجر واحد | `marketing disable --tenant-ref <ref> …` | يُطفئ ويُسقط طابور التسويق المعلّق ويُعيد الفتحات المحجوزة (H85) ويُلغي مهام `cart_reminder` المعلّقة (`marketing_disabled`، قابلة للإحياء عند إعادة التفعيل). **لا يمسّ** utility/service |
| ٣ — القطع النهائي | حذف `cart_reminder` من `PROACTIVE_TEMPLATES` ونشر | لا قالب ⇒ `unknown_template` |

## 5. متى تتوقّف فوراً (مستوى ١ أو ٢)
أي حظر/تقييد/انقطاع مفاجئ من واتساب للرقم · أي شكوى من عميل · التنبيه `MarketingOptoutRatioHigh` (أكثر من 5% من المستلمين أوقفوا خلال 24 ساعة، بحدّ أدنى 20 إرسالاً) · حالة الرقم `paused`/`throttled`. **التنبيه يُنبّه ولا يُطفئ آلياً** — الإطفاء قرارك (H102).

## 6. ما لا يتغيّر بالتفعيل
STOP يغلب كل شيء (H97) · موافقة صريحة فقط (H96) · سقف الزبون الواحد 1/24س و2/7 أيام · الساعات الهادئة (22:00–09:00 بتوقيت المستأجر) · التقطير والإحماء وحالة الرقم (H79/H81) · التذييل إلزامي · المدقّق النهائي (H83).
