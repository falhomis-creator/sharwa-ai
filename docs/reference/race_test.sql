BEGIN;
SET LOCAL ROLE sharwa_app;
SET LOCAL app.tenant_id = '11111111-1111-1111-1111-111111111111';
SELECT count(*) FROM app.allocate_stock_holds('VAR-RACE', 1, interval '30 minutes');
COMMIT;
