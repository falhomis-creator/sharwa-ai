"""core/app/db/repos_catalog.py - the P1.4 catalog read model + hybrid search.

Every SQL string the catalog needs (ingestion, search, sync cursor) lives here,
and ONLY here. Callers in app/api/routes_catalog.py and app/workers/catalog.py
pass an already-open connection from tenant_tx()/system_tx() and never see a
SQL string or a psycopg import themselves (H2). RLS is the real tenant boundary
on every query; the explicit `tenant_id = %s` predicates are the statement of
intent (H29).

H35 - the catalog is advisory, money stays with the platform: the search card
is an explicit whitelist {product_id, platform_product_id, title, category,
options_summary}. price_hint_minor / stock_hint / currency are ranking hints
only and can never leave this module in a search result.

H36 - no forbidden platform field is ever stored: each event type has an
allowlist, and anything outside it is dropped at the gate (never stored, never
logged by value). Fields matching the S7 vocabulary are counted separately and
reported by NAME only (a platform-side leak worth flagging). The vocabulary
itself lives in app.config.CATALOG_FORBIDDEN_FIELDS - deliberately outside this
module so the catalog path never names a forbidden field literally.

H37 - older events are ignored silently-but-measured: every entity carries a
monotonic source_version, and a write with a version <= the stored one is a
no-op (counted "stale", never an error). The INSERT ... ON CONFLICT ... DO
UPDATE ... WHERE EXCLUDED.source_version > table.source_version guard is the
final authority, not arrival order.

search_products()/kb_search() are the C4/C5 SERVICES (not LLM tools). They run
inside a tenant_tx() and merge two lexical sources - PostgreSQL `arabic` FTS and
pg_trgm similarity - with Reciprocal Rank Fusion (see rrf_merge). The vector
source is P1.5 and is deliberately absent (H6/H16).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.config import CATALOG_FORBIDDEN_FIELDS
from app.obs import logging as obs_logging
from app.obs import metrics

# --- search ceilings (written; H4: no unbounded query, no OFFSET) ------------
SEARCH_CANDIDATE_K = 50   # per-source candidate list cap before fusion
SEARCH_RRF_K = 60         # RRF rank damping constant (Cormack et al., SIGIR 2009)
SEARCH_TRGM_THRESHOLD = 0.2
SEARCH_RESULT_MAX = 8     # "a whole catalog is never sent" (01 §2.4)
KB_RESULT_MAX = 3

# Bounded shape of the options summary (H4): never an unbounded aggregation.
_OPTIONS_MAX_KEYS = 8
_OPTIONS_MAX_VALUES = 16

# --- event allowlist (H36) ---------------------------------------------------
# `version` is the inbound field that maps to the source_version column.
EVENT_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "product.upserted": frozenset({
        "platform_product_id", "title", "description", "category",
        "attributes", "active", "version",
    }),
    "product.deleted": frozenset({"platform_product_id", "version"}),
    "variant.upserted": frozenset({
        "platform_variant_id", "platform_product_id", "sku", "options",
        "price_hint_minor", "currency", "version",
    }),
    "variant.stock": frozenset({"platform_variant_id", "qty", "version"}),
    "kb.upserted": frozenset({"source", "content", "version"}),
}


def is_forbidden_field(name: str) -> bool:
    """True when a field NAME is in the S7 vocabulary (case-insensitive)."""
    return name.casefold() in CATALOG_FORBIDDEN_FIELDS


@dataclass(frozen=True)
class PartitionedFields:
    kept: dict[str, Any]
    dropped_count: int
    forbidden_fields: tuple[str, ...]


def partition_event_fields(event_type: str, fields: dict[str, Any]) -> PartitionedFields:
    """Split one event's fields into kept (in the allowlist) vs dropped.

    Dropped fields are counted only (never logged by value). Fields whose NAME
    matches the S7 vocabulary are additionally collected (by name only) so the
    caller can flag the platform-side leak. A forbidden field is always also a
    dropped field (it is never in an allowlist), so both counters advance (K5).
    """
    allowed = EVENT_ALLOWED_FIELDS[event_type]
    kept: dict[str, Any] = {}
    dropped = 0
    forbidden: list[str] = []
    for key, value in fields.items():
        if key in allowed:
            kept[key] = value
            continue
        dropped += 1
        if is_forbidden_field(key):
            forbidden.append(key)
    return PartitionedFields(kept=kept, dropped_count=dropped, forbidden_fields=tuple(forbidden))


@dataclass(frozen=True)
class CatalogEventOutcome:
    event_type: str
    outcome: str  # applied | stale | dropped | unknown_type
    dropped_fields: int
    forbidden_fields: tuple[str, ...]


def rrf_merge(lists: list[list[str]], k: int = SEARCH_RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion over one or more ranked document-id lists.

        score(d) = SUM over lists L of 1 / (k + rank_L(d))

    where rank_L(d) is 1-based and k = 60. k=60 is the standard constant
    (Cormack, Clarke & Buettcher, SIGIR 2009): it damps the rank-1 advantage so
    no single source dominates the merge, and it is insensitive to the number of
    lists - adding the P1.5 vector list later needs NO change to this function.

    Returns [(doc_id, score), ...] sorted by score DESC, ties broken by doc_id
    ASC for determinism. Pure: no SQL, no IO.
    """
    scores: dict[str, float] = {}
    for lst in lists:
        for rank, doc_id in enumerate(lst, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


# --- embeddings + vector search (P1.5b V2/V3/V4) ------------------------------


def content_hash(title: str, description: str | None) -> str:
    """The identity of a product's embeddable content (V3). MD5 over the UTF-8
    bytes of `title + "\\n" + (description or "")`. This MUST stay byte-identical
    to the SQL expression in 0009_p1_vector.sql
    (`md5(title || E'\\n' || coalesce(description, ''))`) so the cross-tenant
    candidate lister and this Python writer agree on "needs re-embedding" and
    never loop. MD5 (not sha256) because it is built into PostgreSQL; for a
    content fingerprint a collision only causes one redundant re-embed - never a
    correctness bug (H42-safe, idempotent upsert)."""
    canonical = title + "\n" + (description or "")
    return hashlib.md5(canonical.encode("utf-8")).hexdigest()


def _vector_literal(embedding: list[float]) -> str:
    """pgvector's canonical `[x,y,...]` text form, for `%s::vector` server casts."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


# F-P4-15: the vector source's relevance floor (cosine distance, 0 = same
# direction, 1 = unrelated, 2 = opposite). Without it the vector list always
# returns up to SEARCH_CANDIDATE_K products, so a query for something the store
# does not sell ("a pencil") came back with 8 unrelated cards. 0.99 drops only
# vectors with essentially no overlap; a semantic model is calibrated per model
# through SEARCH_VECTOR_MAX_DISTANCE (tests/test_search_golden_db.py prints the
# distances to choose it).
VECTOR_MAX_DISTANCE_DEFAULT = 0.99


@dataclass(frozen=True)
class QueryVector:
    """A query embedding together with the model that produced it (F-P4-13):
    the vector source only compares it with catalog vectors of the SAME model -
    vectors from two embedding models live in unrelated spaces - and only keeps
    products closer than max_distance (F-P4-15)."""

    model: str
    values: list[float]
    max_distance: float = VECTOR_MAX_DISTANCE_DEFAULT


def list_products_needing_embedding(
    conn: psycopg.Connection, *, limit: int, model: str,
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Cross-tenant candidates via the SECURITY DEFINER
    app.list_products_needing_embedding(limit, model) (0020; 0009 + F-P4-13:
    a vector written by another model also needs re-embedding) - pointers only
    (tenant_id, product_id), never any tenant content. Called on a system_tx()."""
    rows = conn.execute(
        "SELECT * FROM app.list_products_needing_embedding(%s, %s)", (limit, model),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def read_product_embed_text(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, product_id: uuid.UUID,
) -> tuple[str | None, str | None]:
    """(title, description) for one active product inside its tenant_tx; (None,
    None) when the product no longer exists or was deactivated."""
    row = conn.execute(
        "SELECT title, description FROM catalog_products "
        "WHERE tenant_id = %s AND id = %s AND active = true",
        (tenant_id, product_id),
    ).fetchone()
    if row is None:
        return None, None
    return row[0], row[1]


def upsert_catalog_embedding(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, product_id: uuid.UUID,
    content_hash: str, model: str, embedding: list[float],
) -> None:
    """Write one vector (the caller has already checked H44: len == 1024).
    ON CONFLICT DO UPDATE by content_hash/model/updated_at - idempotent, and the
    content_hash alone decides re-embedding (V3: no new job table)."""
    conn.execute(
        "INSERT INTO catalog_embeddings "
        "(tenant_id, product_id, content_hash, model, embedding) "
        "VALUES (%s, %s, %s, %s, %s::vector) "
        "ON CONFLICT (tenant_id, product_id) DO UPDATE SET "
        "  content_hash = EXCLUDED.content_hash, model = EXCLUDED.model, "
        "  embedding = EXCLUDED.embedding, updated_at = now()",
        (tenant_id, product_id, content_hash, model, _vector_literal(embedding)),
    )


@dataclass(frozen=True)
class GiftCandidateRow:
    """One product the gift curator may pick (P4 Task 14): its cheapest in-budget
    variant's price HINT, used only inside the solver - never shown (H35)."""

    product_id: str
    platform_product_id: str
    title: str
    category: str
    platform_variant_id: str
    price_minor: int
    currency: str


def list_priced_currencies(conn: psycopg.Connection, *, tenant_id: uuid.UUID) -> set[str]:
    """The currencies the store's active, priced variants are in (P4 Task 14)."""
    rows = conn.execute(
        "SELECT DISTINCT cv.currency FROM catalog_variants cv "
        "JOIN catalog_products cp ON cp.tenant_id = cv.tenant_id AND cp.id = cv.product_id "
        "WHERE cv.tenant_id = %s AND cp.active = true AND cv.price_hint_minor > 0 "
        "AND cv.currency IS NOT NULL",
        (tenant_id,),
    ).fetchall()
    return {str(r[0]) for r in rows}


def list_gift_candidates(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, currency: str, max_price_minor: int,
    limit: int = 500,
) -> list[GiftCandidateRow]:
    """Active products with a priced, not-known-out-of-stock variant at or under
    the budget (stock_hint NULL = unknown => allowed; the platform checks live at
    checkout). One row per product: its cheapest such variant."""
    rows = conn.execute(
        "SELECT DISTINCT ON (cp.id) cp.id, cp.platform_product_id, cp.title, "
        "  coalesce(cp.category, ''), cv.platform_variant_id, cv.price_hint_minor, cv.currency "
        "FROM catalog_products cp "
        "JOIN catalog_variants cv ON cv.tenant_id = cp.tenant_id AND cv.product_id = cp.id "
        "WHERE cp.tenant_id = %s AND cp.active = true "
        "AND cv.price_hint_minor > 0 AND cv.price_hint_minor <= %s AND cv.currency = %s "
        "AND (cv.stock_hint IS NULL OR cv.stock_hint > 0) "
        "ORDER BY cp.id, cv.price_hint_minor ASC, cv.platform_variant_id ASC LIMIT %s",
        (tenant_id, max_price_minor, currency, limit),
    ).fetchall()
    return [
        GiftCandidateRow(str(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4]), int(r[5]), str(r[6]))
        for r in rows
    ]


# H35: the ONLY keys a search card may carry. price_hint_minor / stock_hint /
# currency are structurally impossible to leak because they are not copied here.
PRODUCT_CARD_KEYS = ("product_id", "platform_product_id", "title", "category", "options_summary")


def build_product_card(
    *, product_id: uuid.UUID, platform_product_id: str, title: str,
    category: str | None, options_summary: dict[str, list[str]],
) -> dict[str, Any]:
    """The explicit card whitelist (H35) - nothing else can ever be returned."""
    return {
        "product_id": str(product_id),
        "platform_product_id": platform_product_id,
        "title": title,
        "category": category,
        "options_summary": options_summary,
    }



# --- ingestion helpers -------------------------------------------------------


def _require_str(fields: dict[str, Any], key: str) -> str | None:
    value = fields.get(key)
    if isinstance(value, str) and value:
        return value
    return None


def _valid_source_version(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _existing_version(conn: psycopg.Connection, *, sql: str, params: tuple[Any, ...]) -> int | None:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    # N1 (defense-in-depth): a NULL source_version (pre-backfill row) counts as
    # "-1" so the stale check never treats a NULL as "no row to compare" - and
    # never crashes on int(None).
    return -1 if row[0] is None else int(row[0])


def _upsert_product(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, fields: dict[str, Any], source_version: int,
) -> str:
    platform_product_id = _require_str(fields, "platform_product_id")
    title = _require_str(fields, "title")
    if platform_product_id is None or title is None:
        return "dropped"
    current = _existing_version(
        conn,
        sql="SELECT source_version FROM catalog_products WHERE tenant_id = %s AND platform_product_id = %s",
        params=(tenant_id, platform_product_id),
    )
    if current is not None and source_version <= current:
        return "stale"
    active = fields.get("active", True)
    if not isinstance(active, bool):
        active = bool(active)
    conn.execute(
        "INSERT INTO catalog_products "
        "(tenant_id, platform_product_id, title, description, category, attributes, active, source_version) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (tenant_id, platform_product_id) DO UPDATE SET "
        "  title = EXCLUDED.title, description = EXCLUDED.description, "
        "  category = EXCLUDED.category, attributes = EXCLUDED.attributes, "
        "  active = EXCLUDED.active, source_version = EXCLUDED.source_version, "
        "  updated_at = now() "
        "WHERE EXCLUDED.source_version > COALESCE(catalog_products.source_version, -1)",
        (
            tenant_id, platform_product_id, title, fields.get("description"),
            fields.get("category"), Jsonb(fields.get("attributes") or {}),
            active, source_version,
        ),
    )
    return "applied"


def _delete_product(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, fields: dict[str, Any], source_version: int,
) -> str:
    platform_product_id = _require_str(fields, "platform_product_id")
    if platform_product_id is None:
        return "dropped"
    current = _existing_version(
        conn,
        sql="SELECT source_version FROM catalog_products WHERE tenant_id = %s AND platform_product_id = %s",
        params=(tenant_id, platform_product_id),
    )
    if current is None:
        # Nothing to deactivate; the read model already has no active row.
        return "applied"
    if source_version <= current:
        return "stale"
    conn.execute(
        "UPDATE catalog_products SET active = false, source_version = %s, updated_at = now() "
        "WHERE tenant_id = %s AND platform_product_id = %s",
        (source_version, tenant_id, platform_product_id),
    )
    return "applied"



def _upsert_variant(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, fields: dict[str, Any], source_version: int,
) -> str:
    platform_variant_id = _require_str(fields, "platform_variant_id")
    platform_product_id = _require_str(fields, "platform_product_id")
    if platform_variant_id is None or platform_product_id is None:
        return "dropped"
    prow = conn.execute(
        "SELECT id FROM catalog_products WHERE tenant_id = %s AND platform_product_id = %s",
        (tenant_id, platform_product_id),
    ).fetchone()
    if prow is None:
        # The variant references a product we have not ingested yet.
        return "dropped"
    current = _existing_version(
        conn,
        sql="SELECT source_version FROM catalog_variants WHERE tenant_id = %s AND platform_variant_id = %s",
        params=(tenant_id, platform_variant_id),
    )
    if current is not None and source_version <= current:
        return "stale"
    conn.execute(
        "INSERT INTO catalog_variants "
        "(tenant_id, product_id, platform_variant_id, sku, options, price_hint_minor, currency, source_version) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (tenant_id, platform_variant_id) DO UPDATE SET "
        "  product_id = EXCLUDED.product_id, sku = EXCLUDED.sku, options = EXCLUDED.options, "
        "  price_hint_minor = EXCLUDED.price_hint_minor, currency = EXCLUDED.currency, "
        "  source_version = EXCLUDED.source_version "
        "WHERE EXCLUDED.source_version > COALESCE(catalog_variants.source_version, -1)",
        (
            tenant_id, prow[0], platform_variant_id, fields.get("sku"),
            Jsonb(fields.get("options") or {}), fields.get("price_hint_minor"),
            fields.get("currency"), source_version,
        ),
    )
    return "applied"


def _apply_stock(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, fields: dict[str, Any], source_version: int,
) -> str:
    platform_variant_id = _require_str(fields, "platform_variant_id")
    if platform_variant_id is None:
        return "dropped"
    qty = fields.get("qty")
    if not isinstance(qty, int) or isinstance(qty, bool) or qty < 0:
        return "dropped"
    current = _existing_version(
        conn,
        sql="SELECT source_version FROM stock_levels WHERE tenant_id = %s AND platform_variant_id = %s",
        params=(tenant_id, platform_variant_id),
    )
    if current is not None and source_version <= current:
        return "stale"
    conn.execute(
        "INSERT INTO stock_levels (tenant_id, platform_variant_id, observed_available, source_version) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (tenant_id, platform_variant_id) DO UPDATE SET "
        "  observed_available = EXCLUDED.observed_available, source_version = EXCLUDED.source_version, "
        "  observed_at = now() "
        "WHERE EXCLUDED.source_version > COALESCE(stock_levels.source_version, -1)",
        (tenant_id, platform_variant_id, qty, source_version),
    )
    # stock_hint is a ranking hint on the variant, never shown to a customer.
    conn.execute(
        "UPDATE catalog_variants SET stock_hint = %s "
        "WHERE tenant_id = %s AND platform_variant_id = %s",
        (qty, tenant_id, platform_variant_id),
    )
    return "applied"


def _upsert_kb(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, fields: dict[str, Any], source_version: int,
) -> str:
    source = _require_str(fields, "source")
    content = fields.get("content")
    if source is None or not isinstance(content, str) or not content:
        return "dropped"
    current = _existing_version(
        conn,
        sql="SELECT source_version FROM kb_chunks WHERE tenant_id = %s AND source = %s",
        params=(tenant_id, source),
    )
    if current is not None and source_version <= current:
        return "stale"
    conn.execute(
        "INSERT INTO kb_chunks (tenant_id, source, content, source_version) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (tenant_id, source) DO UPDATE SET "
        "  content = EXCLUDED.content, source_version = EXCLUDED.source_version "
        "WHERE EXCLUDED.source_version > COALESCE(kb_chunks.source_version, -1)",
        (tenant_id, source, content, source_version),
    )
    return "applied"



def ingest_event(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, event: dict[str, Any],
) -> CatalogEventOutcome:
    """Apply ONE platform catalog event inside an already-open tenant_tx().

    Pure SQL + pure dispatch: no metric increments and no logging here (the
    caller owns those, so the same logic serves both the webhook and the
    reconcile worker). Returns the outcome plus the dropped/forbidden field
    counts the caller needs to count and flag.
    """
    event_type = event.get("type")
    if not isinstance(event_type, str) or event_type not in EVENT_ALLOWED_FIELDS:
        return CatalogEventOutcome(
            event_type=str(event_type), outcome="unknown_type",
            dropped_fields=0, forbidden_fields=(),
        )
    fields = {k: v for k, v in event.items() if k != "type"}
    partitioned = partition_event_fields(event_type, fields)
    version = partitioned.kept.get("version")
    if not _valid_source_version(version):
        return CatalogEventOutcome(
            event_type=event_type, outcome="dropped",
            dropped_fields=partitioned.dropped_count,
            forbidden_fields=partitioned.forbidden_fields,
        )
    outcome = _apply_known_event(
        conn, tenant_id=tenant_id, event_type=event_type,
        fields=partitioned.kept, source_version=int(version),
    )
    return CatalogEventOutcome(
        event_type=event_type, outcome=outcome,
        dropped_fields=partitioned.dropped_count,
        forbidden_fields=partitioned.forbidden_fields,
    )


def _apply_known_event(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, event_type: str,
    fields: dict[str, Any], source_version: int,
) -> str:
    if event_type == "product.upserted":
        return _upsert_product(conn, tenant_id=tenant_id, fields=fields, source_version=source_version)
    if event_type == "product.deleted":
        return _delete_product(conn, tenant_id=tenant_id, fields=fields, source_version=source_version)
    if event_type == "variant.upserted":
        return _upsert_variant(conn, tenant_id=tenant_id, fields=fields, source_version=source_version)
    if event_type == "variant.stock":
        return _apply_stock(conn, tenant_id=tenant_id, fields=fields, source_version=source_version)
    if event_type == "kb.upserted":
        return _upsert_kb(conn, tenant_id=tenant_id, fields=fields, source_version=source_version)
    raise RuntimeError(f"unhandled catalog event type {event_type!r} (unreachable)")



# --- search (C4/C5) ----------------------------------------------------------


def _fts_product_ids(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, query: str,
    category: str | None, limit: int,
) -> list[str]:
    if category:
        rows = conn.execute(
            "SELECT id FROM catalog_products "
            "WHERE tenant_id = %s AND active = true "
            "AND category = %s AND search_tsv @@ websearch_to_tsquery('arabic', %s) "
            "ORDER BY ts_rank_cd(search_tsv, websearch_to_tsquery('arabic', %s)) DESC LIMIT %s",
            (tenant_id, category, query, query, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM catalog_products "
            "WHERE tenant_id = %s AND active = true "
            "AND search_tsv @@ websearch_to_tsquery('arabic', %s) "
            "ORDER BY ts_rank_cd(search_tsv, websearch_to_tsquery('arabic', %s)) DESC LIMIT %s",
            (tenant_id, query, query, limit),
        ).fetchall()
    return [str(r[0]) for r in rows]


def _trgm_product_ids(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, query: str,
    category: str | None, limit: int,
) -> list[str]:
    if category:
        rows = conn.execute(
            "SELECT id FROM catalog_products "
            "WHERE tenant_id = %s AND active = true "
            "AND category = %s AND similarity(title, %s) > %s "
            "ORDER BY similarity(title, %s) DESC LIMIT %s",
            (tenant_id, category, query, SEARCH_TRGM_THRESHOLD, query, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM catalog_products "
            "WHERE tenant_id = %s AND active = true "
            "AND similarity(title, %s) > %s "
            "ORDER BY similarity(title, %s) DESC LIMIT %s",
            (tenant_id, query, SEARCH_TRGM_THRESHOLD, query, limit),
        ).fetchall()
    return [str(r[0]) for r in rows]


def _vector_product_ids(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, query_vector: QueryVector,
    category: str | None, limit: int,
) -> list[str]:
    """P1.5b V4: the third source. An exact scan inside the tenant's hash
    partition (RLS + `<=>` L2 distance, NO ANN index - the architecture defers
    HNSW until real data can size it). Joins catalog_embeddings so a product with
    no vector yet simply does not appear here (the other two sources still cover
    it - H42). F-P4-13: only vectors of the query's own model are compared, so a
    provider switch never mixes two embedding spaces while re-embedding runs."""
    literal = _vector_literal(query_vector.values)
    if category:
        rows = conn.execute(
            "SELECT cp.id FROM catalog_products cp "
            "JOIN catalog_embeddings ce ON ce.tenant_id = cp.tenant_id AND ce.product_id = cp.id "
            "WHERE cp.tenant_id = %s AND cp.active = true AND cp.category = %s "
            "AND ce.model = %s AND (ce.embedding <=> %s::vector) < %s "
            "ORDER BY ce.embedding <=> %s::vector LIMIT %s",
            (tenant_id, category, query_vector.model, literal, query_vector.max_distance, literal, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT cp.id FROM catalog_products cp "
            "JOIN catalog_embeddings ce ON ce.tenant_id = cp.tenant_id AND ce.product_id = cp.id "
            "WHERE cp.tenant_id = %s AND cp.active = true "
            "AND ce.model = %s AND (ce.embedding <=> %s::vector) < %s "
            "ORDER BY ce.embedding <=> %s::vector LIMIT %s",
            (tenant_id, query_vector.model, literal, query_vector.max_distance, literal, limit),
        ).fetchall()
    return [str(r[0]) for r in rows]


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _options_summary(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, product_ids: list[uuid.UUID],
) -> dict[str, dict[str, list[str]]]:
    if not product_ids:
        return {}
    rows = conn.execute(
        "SELECT product_id, options FROM catalog_variants "
        "WHERE tenant_id = %s AND product_id = ANY(%s::uuid[])",
        (tenant_id, list(product_ids)),
    ).fetchall()
    collected: dict[str, dict[str, set[str]]] = {}
    for product_id, options in rows:
        if not isinstance(options, dict):
            continue
        bucket = collected.setdefault(str(product_id), {})
        for opt_key, opt_val in options.items():
            bucket.setdefault(str(opt_key), set()).add(_stringify(opt_val))
    summary: dict[str, dict[str, list[str]]] = {}
    for pid, opts in collected.items():
        summarized: dict[str, list[str]] = {}
        for opt_key in sorted(opts)[:_OPTIONS_MAX_KEYS]:
            summarized[opt_key] = sorted(opts[opt_key])[:_OPTIONS_MAX_VALUES]
        summary[pid] = summarized
    return summary


def _product_cards(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID,
    merged: list[tuple[str, float]],
) -> list[dict[str, Any]]:
    ids = [uuid.UUID(doc_id) for doc_id, _score in merged]
    rows = conn.execute(
        "SELECT id, platform_product_id, title, category FROM catalog_products "
        "WHERE tenant_id = %s AND active = true AND id = ANY(%s::uuid[])",
        (tenant_id, list(ids)),
    ).fetchall()
    by_id = {str(r[0]): r for r in rows}
    summaries = _options_summary(conn, tenant_id=tenant_id, product_ids=ids)
    cards: list[dict[str, Any]] = []
    for doc_id, _score in merged:
        row = by_id.get(doc_id)
        if row is None:
            continue
        cards.append(build_product_card(
            product_id=row[0], platform_product_id=row[1], title=row[2],
            category=row[3], options_summary=summaries.get(doc_id, {}),
        ))
    return cards



def search_products(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, query: str,
    filters: dict[str, Any] | None = None, limit: int = SEARCH_RESULT_MAX,
    query_vector: QueryVector | None = None,
) -> list[dict[str, Any]]:
    """Hybrid search: FTS (arabic) + pg_trgm + (optionally) vector, merged by RRF
    (C4). H42: the vector list is an OPTIONAL third source - when query_vector is
    None the search still runs on the two lexical lists and returns results. The
    caller computes the vector with a fail-open path (breaker/budget/timeout/dim)
    and counts the skip itself; this function never raises because the vector is
    missing.

    Returns <= limit (<= 8) whitelisted cards. `tenant_id` is never taken as a
    free parameter from a caller - the transaction is already tenant-scoped.
    """
    q = query.strip()
    if not q:
        return []
    limit = max(1, min(limit, SEARCH_RESULT_MAX))
    category = None
    if filters and isinstance(filters.get("category"), str) and filters["category"]:
        category = filters["category"]
    started = time.monotonic()
    try:
        fts_ids = _fts_product_ids(conn, tenant_id=tenant_id, query=q, category=category, limit=SEARCH_CANDIDATE_K)
        metrics.search_queries_total.labels("fts").inc()
        trgm_ids = _trgm_product_ids(conn, tenant_id=tenant_id, query=q, category=category, limit=SEARCH_CANDIDATE_K)
        metrics.search_queries_total.labels("trgm").inc()
        lists: list[list[str]] = [fts_ids, trgm_ids]
        if query_vector is not None:
            vector_ids = _vector_product_ids(
                conn, tenant_id=tenant_id, query_vector=query_vector,
                category=category, limit=SEARCH_CANDIDATE_K,
            )
            metrics.search_queries_total.labels("vector").inc()
            lists.append(vector_ids)
        merged = rrf_merge(lists, SEARCH_RRF_K)[:limit]
        metrics.search_sources_used_total.labels(str(len(lists))).inc()
        if not merged:
            metrics.search_empty_results_total.inc()
            return []
        return _product_cards(conn, tenant_id=tenant_id, merged=merged)
    finally:
        metrics.search_duration_seconds.observe(time.monotonic() - started)


def kb_search(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, question: str,
    limit: int = KB_RESULT_MAX,
) -> list[dict[str, str]]:
    """Search store policies in kb_chunks (C5): FTS only, <= 3 {source, content}."""
    q = question.strip()
    if not q:
        return []
    limit = max(1, min(limit, KB_RESULT_MAX))
    started = time.monotonic()
    try:
        metrics.search_queries_total.labels("fts").inc()
        rows = conn.execute(
            "SELECT source, content FROM kb_chunks "
            "WHERE tenant_id = %s AND tsv @@ websearch_to_tsquery('arabic', %s) "
            "ORDER BY ts_rank_cd(tsv, websearch_to_tsquery('arabic', %s)) DESC LIMIT %s",
            (tenant_id, q, q, limit),
        ).fetchall()
        result = [{"source": r[0], "content": r[1]} for r in rows]
        if not result:
            metrics.search_empty_results_total.inc()
        return result
    finally:
        metrics.search_duration_seconds.observe(time.monotonic() - started)


# --- sync cursor (C3) --------------------------------------------------------


def get_catalog_sync_cursor(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID,
) -> tuple[str | None, datetime.datetime | None]:
    """(cursor, last_reconcile_ok) for one tenant, or (None, None) if no row."""
    row = conn.execute(
        "SELECT cursor, last_reconcile_ok FROM catalog_sync_cursor WHERE tenant_id = %s",
        (tenant_id,),
    ).fetchone()
    if row is None:
        return None, None
    return row[0], row[1]


def set_catalog_sync_cursor(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, cursor: str,
    last_reconcile_ok: datetime.datetime,
) -> None:
    """Record a successful reconcile: advance the cursor and the staleness bound."""
    conn.execute(
        "INSERT INTO catalog_sync_cursor (tenant_id, cursor, last_event_at, last_reconcile_ok) "
        "VALUES (%s, %s, now(), %s) "
        "ON CONFLICT (tenant_id) DO UPDATE SET "
        "  cursor = EXCLUDED.cursor, last_event_at = now(), last_reconcile_ok = EXCLUDED.last_reconcile_ok",
        (tenant_id, cursor, last_reconcile_ok),
    )


def list_catalog_sync_state(
    conn: psycopg.Connection,
) -> list[tuple[uuid.UUID, str, str | None, datetime.datetime | None]]:
    """All tenants and their sync state, via the SECURITY DEFINER function
    app.list_catalog_sync_state() (0007). Called on a system_tx() connection."""
    rows = conn.execute(
        "SELECT tenant_id, platform_ref, cursor, last_reconcile_ok FROM app.list_catalog_sync_state()"
    ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]



# --- shared ingestion orchestration (webhook + reconcile worker) -------------


def apply_catalog_events(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, events: list[dict[str, Any]],
    logger: logging.Logger, seen_unknown_types: set[str],
) -> dict[str, int]:
    """Apply a batch of events with the shared metric/logging side effects.

    Both the webhook receiver and the reconcile worker use this so the counters
    and the forbidden-field warnings stay identical. `seen_unknown_types` is
    caller-owned process state: an unknown event type is logged once per new
    type (never once per event), and is otherwise ignored + counted.
    """
    counts: dict[str, int] = {"applied": 0, "stale": 0, "dropped": 0, "unknown_type": 0}
    for event in events:
        oc = ingest_event(conn, tenant_id=tenant_id, event=event)
        counts[oc.outcome] += 1
        metrics.catalog_events_total.labels(oc.event_type, oc.outcome).inc()
        if oc.dropped_fields:
            metrics.catalog_dropped_fields_total.inc(oc.dropped_fields)
        for name in oc.forbidden_fields:
            metrics.catalog_cost_field_seen_total.inc()
            # NAME only, never the value (H36): a forbidden field arriving means
            # a platform-side leak worth flagging, but its contents are dropped.
            obs_logging.log_event(
                logger, event="catalog.forbidden_field", component="catalog",
                level=logging.WARNING, field=name,
            )
        if oc.outcome == "unknown_type" and oc.event_type not in seen_unknown_types:
            seen_unknown_types.add(oc.event_type)
            obs_logging.log_event(
                logger, event="catalog.unknown_type", component="catalog",
                level=logging.WARNING, type_=oc.event_type,
            )
    return counts

