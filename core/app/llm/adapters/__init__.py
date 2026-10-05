"""core/app/llm/adapters/ - REAL provider adapters (P4.2).

This is the ONLY package (with the two config files, S8 rule 3) allowed to
name an LLM vendor. Each adapter module exposes exactly one entry point,
``build(settings) -> LlmProvider | EmbeddingProvider``, selected by name from
app/llm/registry.py so the registry itself never names a provider.
"""