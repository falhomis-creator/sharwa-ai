P0: IN PROGRESS — P0.0 done. P0.1 verified on real Docker at C1. P0.2 DONE (acceptance a/b/c/d on real Docker + real redis-durable; a real WhatsApp phone connection is explicitly out of P0.2's acceptance scope, deferred as a separate operational smoke test). P0.3 DONE (real MinIO E2E, commits 4d96e0e/cf756ef). P0.4 DONE (durable outbound queue, 97c8dc5). P0.5 DONE (session lifecycle/lease/fencing, ac86d7a). P0.6 PARTIAL — Batch A DONE (readyz/metrics/graceful shutdown/H12, be5debe); Batch B (gateway-side kill-switch) implemented and verified against real redis-cache; monitoring (Prometheus + alert rules + the remaining metric families + a forwarder /metrics endpoint) is the one remaining piece of P0.6. P0.7 (core/ + console/) and P0.8 (E2E/chaos/report) NOT STARTED.
P1: LOCKED
P2: LOCKED
P3: LOCKED
P4: LOCKED
P5: LOCKED
