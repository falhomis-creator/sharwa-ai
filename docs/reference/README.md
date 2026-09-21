# reference/ — المرجع التنفيذي

| الملف | الغرض |
|---|---|
| `schema.sql` | مخطط PostgreSQL 16 (+ pgvector + PostGIS + pg_trgm) بضمانات مفروضة في القاعدة: RLS، UNIQUE للـ idempotency، آلة حالات التسليم، التخصيص الذرّي للمخزون، claim للطوابير الدائمة، مفاتيح الطوارئ. |
| `schema_selftest.sql` | 34 بنداً تحقق تُشغَّل بأدوار `sharwa_app`/`sharwa_system` الفعلية. |
| `race_test.sql` | سكربت pgbench لسباق تخصيص المخزون (كارثة 7). |
| `docker-compose.reference.yml` | حدود ذاكرة/CPU/PIDs لكل حاوية، `oom_score_adj` لـ PostgreSQL، شبكة بيانات داخلية، سقوف سجلات (كوارث 2، 3، 16، 17، 18، 21). |
| `gift_solver_bench.py` | قياس الحلّال المحدود لمنسق الهدايا (كارثة 15). |

## التشغيل

```bash
# قاعدة بترميز UTF8 إلزامياً (البحث العربي يعتمد عليه)
createdb sharwa --encoding UTF8 --template template0 --locale C.UTF-8
psql -d sharwa -v ON_ERROR_STOP=1 -f schema.sql
psql -d sharwa -v ON_ERROR_STOP=1 -f schema_selftest.sql        # يجب أن ينتهي بـ: ALL SELF-TESTS PASSED

# سباق المخزون: يجب أن يبقى حجز واحد فقط
psql -d sharwa -c "INSERT INTO ... /* 20 منتظراً على VAR-RACE */"
pgbench -n -f race_test.sql -c 32 -j 4 -t 25 sharwa
```

ملاحظات: الحزم الإضافية المطلوبة `postgresql-16-pgvector` و`postgresql-16-postgis-3`. أدوار `LOGIN` تُنشأ عند النشر وترث `sharwa_app` أو `sharwa_system`. `statement_timeout` يُضبط على أدوار التطبيق لا على مستوى الخادم حتى لا يعطّل الترحيلات الطويلة.
