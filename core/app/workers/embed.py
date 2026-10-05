"""core/app/workers/embed.py - batch product embedding + query embedding (V2/V5).

Two jobs share this module (both are the ONLY places outside app/llm/** allowed to
touch the embedding provider - S9 rule 2):

  1. embed_once(): the batch worker. Every EMBED_INTERVAL_S it embeds active
     products whose catalog_embeddings row is missing or whose content_hash no
     longer matches `title + description` (content_hash is the work identity, V3:
     no new job table). H40: read the batch => close => call => open => write.

  2. query_vector(): the H42 fail-open query embedder for vector search. Any
     failure to obtain a vector (breaker open, budget degraded, timeout, error,
     wrong dim) returns None - search then runs on two lexical lists and still
     answers (the vector half optimizes, never gates).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from redis.exceptions import RedisError

from app import db as core_db
from app.db import repos_catalog
from app.db import repos_llm
from app.obs import logging as obs_logging
from app.obs import metrics
from app.text.arabic import normalize
from app.workers.config import WorkerSettings

_log = obs_logging.get_logger("embed")


@dataclass(frozen=True)
class EmbedHandle:
    """provider + breaker built from settings (only embed.py builds this)."""

    provider: Any
    breaker: Any
    provider_name: str
    model_name: str


def build_embed(settings: WorkerSettings) -> EmbedHandle:
    from app.llm import registry
    from app.llm.breaker import CircuitBreaker

    provider = registry.build_embedding_provider(settings.embedding_provider, settings)
    breaker = CircuitBreaker(
        settings.llm_breaker_fail_threshold, settings.llm_breaker_reset_s,
    )
    return EmbedHandle(
        provider=provider, breaker=breaker,
        provider_name=settings.embedding_provider,
        model_name=f"{settings.embedding_provider}-embedding",
    )


# --- query embedding (V5): pure helpers + the H42 fail-open caller ------------


def query_cache_key(query: str) -> str:
    """Key = fingerprint of the NORMALIZED query (never the raw text, H20)."""
    return "emb:q:" + hashlib.sha256(normalize(query).encode("utf-8")).hexdigest()


def encode_vector(vec: list[float]) -> str:
    return json.dumps(vec)


def decode_vector(raw: str) -> list[float] | None:
    """None on any malformation (a corrupt cache value is treated as a miss)."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, list):
        return None
    try:
        return [float(x) for x in data]
    except (TypeError, ValueError):
        return None


def _skip(reason: str) -> None:
    metrics.search_vector_skipped_total.labels(reason).inc()


def query_vector(
    *, settings: WorkerSettings, handle: EmbedHandle, query: str,
    tenant_id: uuid.UUID, cache_client: Any,
) -> list[float] | None:
    """Obtain a query vector, or return None (search falls back to two lists).

    H42: never raises to the search caller. Cache-first (governance: no paid call
    for a query already in memory), then budget-check (short tx), breaker,
    provider (OUTSIDE any tx), dim-check (H44), cache-store, budget-account.
    """
    from app.llm import budget
    from app.llm.port import EmbeddingProviderError

    key = query_cache_key(query)

    # 1. cache hit -> free, no paid call, no budget (V5: cache loss = new call).
    raw: Any = None
    try:
        raw = cache_client.get(key)
    except RedisError:
        metrics.embed_query_cache_total.labels("miss").inc()
    if raw is not None:
        vec = decode_vector(raw)
        if vec is not None and len(vec) == settings.embedding_dim:
            metrics.embed_query_cache_total.labels("hit").inc()
            return vec
        metrics.embed_query_cache_total.labels("miss").inc()

    # 2. budget check (short tx; creates the month row so the later account works).
    try:
        with core_db.tenant_tx(tenant_id) as conn:
            state = budget.ensure_and_check(
                conn, tenant_id=tenant_id, month=budget.current_month(),
                limit_micro_usd=settings.tenant_monthly_budget_micro_usd,
            )
    except Exception as exc:  # noqa: BLE001 - H42: a budget failure must not break search
        obs_logging.log_event(_log, event="embed.budget_check_failed", component="embed",
                              level=logging.WARNING, error=str(exc))
        _skip("error")
        return None
    if state == "degraded":
        _skip("budget_degraded")
        return None

    # 3. breaker.
    if not handle.breaker.allow():
        _skip("breaker_open")
        return None

    # 4. provider call (OUTSIDE any transaction, H40).
    try:
        result = handle.provider.embed(
            texts=[query], timeout_s=settings.embed_timeout_s,
        )
    except EmbeddingProviderError:
        handle.breaker.record_failure()
        _skip("error")
        return None
    except Exception as exc:  # noqa: BLE001 - H42: never break search
        handle.breaker.record_failure()
        obs_logging.log_event(_log, event="embed.query_provider_error", component="embed",
                              level=logging.WARNING, error=str(exc))
        _skip("error")
        return None
    handle.breaker.record_success()

    # 5. dim check (H44).
    vec = result.vectors[0] if result.vectors else []
    if len(vec) != settings.embedding_dim:
        metrics.embed_dim_mismatch_total.inc()
        _skip("dim_mismatch")
        return None

    # 6. cache store (best-effort).
    try:
        cache_client.setex(key, settings.embed_query_cache_ttl_s, encode_vector(vec))
        metrics.embed_query_cache_total.labels("set").inc()
    except RedisError:
        metrics.embed_query_cache_total.labels("miss").inc()

    # 7. budget account (short tx), success.
    try:
        with core_db.tenant_tx(tenant_id) as conn:
            budget.account(
                conn, tenant_id=tenant_id, month=budget.current_month(),
                conversation_id=None, purpose="embedding",
                provider=result.usage.provider, model=result.usage.model,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                cost_micro_usd=budget.compute_cost_micro_usd(
                    result.usage.input_tokens, result.usage.output_tokens,
                    settings.llm_price_table, result.usage.provider, result.usage.model,
                ),
                latency_ms=result.usage.latency_ms, status="ok",
            )
        metrics.llm_calls_total.labels("embedding", result.usage.provider, "ok").inc()
    except Exception as exc:  # noqa: BLE001 - accounting failure must not break search
        obs_logging.log_event(_log, event="embed.account_failed", component="embed",
                              level=logging.WARNING, error=str(exc))

    return vec


# --- batch product embedding (V2/V3) ------------------------------------------


def _record_embedding_calls(
    settings: WorkerSettings, items: list[tuple[uuid.UUID, uuid.UUID, str, str]],
    provider: str, model: str, latency_ms: int,
) -> None:
    """One llm_calls row per tenant per batch (purpose='embedding', cost 0 for the
    fake). Product embedding is cross-tenant infrastructure, so it is recorded for
    governance (V8/V13) but NOT gated by the per-tenant monthly budget - the
    budget governs QUERY embedding (V5) where attribution is unambiguous."""
    per_tenant: dict[uuid.UUID, int] = {}
    for tenant_id, _pid, text, _ch in items:
        per_tenant[tenant_id] = per_tenant.get(tenant_id, 0) + len(text)
    for tenant_id, chars in per_tenant.items():
        try:
            with core_db.tenant_tx(tenant_id) as conn:
                repos_llm.record_llm_call(
                    conn, tenant_id=tenant_id, conversation_id=None, purpose="embedding",
                    provider=provider, model=model, input_tokens=chars // 4,
                    output_tokens=0, cost_micro_usd=0, latency_ms=latency_ms, status="ok",
                )
            metrics.llm_calls_total.labels("embedding", provider, "ok").inc()
        except Exception as exc:  # noqa: BLE001 - accounting is best-effort
            obs_logging.log_event(_log, event="embed.account_failed", component="embed",
                                  level=logging.WARNING, error=str(exc))


def embed_once(*, settings: WorkerSettings, handle: EmbedHandle) -> str:
    """One bounded cycle. Returns 'ok' | 'error'. H40 is preserved: read => close
    => provider call => open => write, and the provider call is never inside a tx."""
    from app.llm.port import EmbeddingProviderError

    try:
        with core_db.system_tx() as conn:
            candidates = repos_catalog.list_products_needing_embedding(
                conn, limit=settings.embed_max_products_per_cycle,
            )
    except Exception as exc:  # noqa: BLE001 - a transient DB error must not crash the thread
        obs_logging.log_event(_log, event="embed.list_failed", component="embed",
                              level=logging.ERROR, error=str(exc))
        metrics.embed_products_total.labels("error").inc()
        return "error"

    metrics.embed_queue_depth.set(len(candidates))
    if not candidates:
        return "ok"

    embedded = 0
    for i in range(0, len(candidates), settings.embed_batch):
        batch = candidates[i:i + settings.embed_batch]
        # Phase A: read texts (per-tenant short txs), compute the content_hash.
        items: list[tuple[uuid.UUID, uuid.UUID, str, str]] = []
        for tenant_id, product_id in batch:
            try:
                with core_db.tenant_tx(tenant_id) as conn:
                    title, description = repos_catalog.read_product_embed_text(
                        conn, tenant_id=tenant_id, product_id=product_id,
                    )
            except Exception as exc:  # noqa: BLE001 - one product's read must not stop the batch
                obs_logging.log_event(_log, event="embed.read_failed", component="embed",
                                      level=logging.WARNING, error=str(exc))
                continue
            if title is None:
                continue
            text = (title + " " + (description or "")).strip()
            if len(text) > settings.embed_max_text_chars:
                text = text[:settings.embed_max_text_chars]
            items.append((tenant_id, product_id, text, repos_catalog.content_hash(title, description)))
        if not items:
            continue

        # Phase B: provider call OUTSIDE any transaction (H40), breaker-guarded.
        if not handle.breaker.allow():
            metrics.embed_products_total.labels("breaker_open").inc()
            obs_logging.log_event(_log, event="embed.breaker_open", component="embed",
                                  level=logging.WARNING)
            break
        batch_started = time.monotonic()
        try:
            result = handle.provider.embed(
                texts=[it[2] for it in items], timeout_s=settings.embed_timeout_s,
            )
        except EmbeddingProviderError as exc:
            handle.breaker.record_failure()
            metrics.embed_products_total.labels("error").inc()
            obs_logging.log_event(_log, event="embed.provider_error", component="embed",
                                  level=logging.WARNING, error=str(exc))
            break
        except Exception as exc:  # noqa: BLE001 - a provider bug must not crash the thread
            handle.breaker.record_failure()
            metrics.embed_products_total.labels("error").inc()
            obs_logging.log_event(_log, event="embed.provider_error", component="embed",
                                  level=logging.ERROR, error=str(exc))
            break
        handle.breaker.record_success()
        metrics.embed_batch_duration_seconds.observe(time.monotonic() - batch_started)

        # Phase C: write per tenant (H44 checked per vector before any write).
        for (tenant_id, product_id, _text, ch), vec in zip(items, result.vectors):
            if len(vec) != settings.embedding_dim:
                metrics.embed_dim_mismatch_total.inc()
                metrics.embed_products_total.labels("dim_mismatch").inc()
                obs_logging.log_event(_log, event="embed.dim_mismatch", component="embed",
                                      level=logging.ERROR, product_id=str(product_id), dim=len(vec))
                continue
            try:
                with core_db.tenant_tx(tenant_id) as conn:
                    repos_catalog.upsert_catalog_embedding(
                        conn, tenant_id=tenant_id, product_id=product_id,
                        content_hash=ch, model=result.usage.model, embedding=vec,
                    )
                embedded += 1
            except Exception as exc:  # noqa: BLE001 - one write failure must not stop the batch
                obs_logging.log_event(_log, event="embed.write_failed", component="embed",
                                      level=logging.ERROR, error=str(exc))

        _record_embedding_calls(
            settings, items, result.usage.provider, result.usage.model, result.usage.latency_ms,
        )

    metrics.embed_products_total.labels("ok").inc(embedded)
    metrics.embed_queue_depth.set(max(0, len(candidates) - embedded))
    return "ok"



