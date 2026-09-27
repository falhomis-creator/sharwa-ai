"""core/app/llm - the single LLM abstraction + governance layer (P1.5).

The model CLASSIFIES; the code WRITES (H38). This package is the ONLY place the
model is ever called, and the ONLY thing the model returns is a strict JSON
{intent, query, confidence} that the router maps to a deterministic action.
Nothing a model emits ever reaches a customer.

Submodules:
  port.py     - LlmProvider, the single provider abstraction (L1).
  registry.py - provider selection from config; fake-only this batch (L2).
  breaker.py  - per-provider circuit breaker (L5).
  budget.py   - budget state/cost math + orchestration (L4); SQL in app/db.
  router.py   - masking (H41), strict schema validation, and routing (L6).
"""
