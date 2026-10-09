"""core/app/workers/gift.py - the gift coordinator (P4 Task 14).

Glues the pure parts together inside the turn's write transaction (no network,
no model - like the size coordinator):

  gift_extract (customer's words) -> repos_catalog.list_gift_candidates (catalog
  read model, price HINTS) -> app.gift.curator.curate (pure solver) ->
  repos_gift.insert_gift_cart (one row per proposed basket) -> links.

Design rules kept from docs/01_FIVE_TASKS_DESIGN.md §5.2 and the P4
constitution §3:
  * sharwa_ai never converts currency: the customer's named currency must be
    the catalog's; a bare "ريال" matches YER or SAR only when the store prices
    in exactly one of them; mixed or other currencies => hand off.
  * no number from the solver reaches the customer: the reply lists titles and
    a link per basket; the platform prices the basket at checkout.
  * the solver is bounded by its own deterministic step budget (MAX_STEPS,
    Task 12) - it runs inline; its wall time is logged per turn.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.db import repos, repos_catalog, repos_gift
from app.gift import curator
from app.obs import logging as obs_logging
from app.tools.gift_extract import GiftRequest, extract_gift_request

_log = obs_logging.get_logger("gift")

# Minor-unit digits per ISO 4217 for the currencies a store can price in here.
MINOR_DIGITS: dict[str, int] = {"YER": 2, "SAR": 2, "USD": 2, "AED": 2}
_RIYALS = frozenset({"YER", "SAR"})
CANDIDATE_POOL = 500
RELEVANT_SCORE_TOP = 100
RELEVANT_SCORE_STEP = 10
BASE_RELEVANCE = 10


@dataclass(frozen=True)
class GiftOffer:
    cart_id: uuid.UUID
    titles: tuple[str, ...]


@dataclass(frozen=True)
class GiftTurn:
    # baskets | need_budget | currency_mismatch | no_basket | no_link
    status: str
    offers: tuple[GiftOffer, ...] = ()
    link_base: str = ""


# Owner decision (2026-10-09): the link is store-scoped -
# https://<store>.sharwaah.com/checkout/gift/<cart_id> - so the platform knows
# the store from the address and the basket lands in that store's session.
# GIFT_CHECKOUT_BASE_URL carries the store as the {tenant_ref} placeholder.
TENANT_REF_PLACEHOLDER = "{tenant_ref}"
_STORE_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def checkout_base_url(template: str, tenant_ref: str | None) -> str | None:
    """The link base for this store, or None when the template needs a store
    label and the tenant's platform_ref is not a valid DNS label."""
    if TENANT_REF_PLACEHOLDER not in template:
        return template
    if not tenant_ref or not _STORE_LABEL_RE.fullmatch(tenant_ref):
        return None
    return template.replace(TENANT_REF_PLACEHOLDER, tenant_ref)


def is_gift_request(bodies: tuple[str, ...]) -> bool:
    return extract_gift_request(bodies).is_gift


def _pick_currency(present: set[str], hint: str | None) -> str | None:
    """The single currency this basket is priced in, or None (=> hand off)."""
    if hint == "RIYAL":
        riyals = present & _RIYALS
        return next(iter(riyals)) if len(riyals) == 1 else None
    if hint is not None:
        return hint if hint in present else None
    return next(iter(present)) if len(present) == 1 else None


def _relevance(conn: Any, tenant_id: uuid.UUID, query: str) -> dict[str, int]:
    """product_id -> relevance from the store's own search ranking."""
    if not query:
        return {}
    cards = repos_catalog.search_products(conn, tenant_id=tenant_id, query=query)
    return {
        str(c["product_id"]): RELEVANT_SCORE_TOP - RELEVANT_SCORE_STEP * i
        for i, c in enumerate(cards)
    }


def curate_for_turn(
    conn: Any, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID, bodies: tuple[str, ...],
    link_template: str,
) -> GiftTurn:
    req: GiftRequest = extract_gift_request(bodies)
    if req.budget_major is None:
        return GiftTurn("need_budget")
    # Resolve the store's link base BEFORE any basket is stored: a basket the
    # customer could never open is not proposed (=> hand off).
    link_base = checkout_base_url(link_template, repos.platform_ref_for_tenant(conn, tenant_id))
    if link_base is None:
        return GiftTurn("no_link")

    present = repos_catalog.list_priced_currencies(conn, tenant_id=tenant_id)
    if not present:
        return GiftTurn("no_basket")
    currency = _pick_currency(present, req.currency_hint)
    if currency is None:
        return GiftTurn("currency_mismatch")
    budget_minor = int(req.budget_major * (Decimal(10) ** MINOR_DIGITS.get(currency, 2)))
    rows = repos_catalog.list_gift_candidates(
        conn, tenant_id=tenant_id, currency=currency, max_price_minor=budget_minor,
        limit=CANDIDATE_POOL,
    )

    rel = _relevance(conn, tenant_id, req.query)
    rows.sort(key=lambda r: (-rel.get(r.product_id, BASE_RELEVANCE), -r.price_minor, r.product_id))
    pool = rows[:curator.K_MAX]
    by_id = {r.product_id: r for r in pool}
    started = time.monotonic()
    result = curator.curate(
        tuple(curator.GiftCandidate(r.product_id, r.price_minor, rel.get(r.product_id, BASE_RELEVANCE),
                                    r.category) for r in pool),
        budget_minor,
    )
    obs_logging.log_event(
        _log, event="gift.curated", component="gift", level=logging.INFO,
        candidates=len(pool), baskets=len(result.baskets), reasons=",".join(result.reasons),
        solver_ms=int((time.monotonic() - started) * 1000),
    )
    if not result.baskets:
        return GiftTurn("no_basket")

    offers = []
    for basket in result.baskets:
        items = [
            {"platform_product_id": by_id[pid].platform_product_id,
             "platform_variant_id": by_id[pid].platform_variant_id, "qty": 1}
            for pid in basket.product_ids
        ]
        cart_id = repos_gift.insert_gift_cart(
            conn, tenant_id=tenant_id, conversation_id=conversation_id, items=items,
            currency=currency, budget_minor=budget_minor,
        )
        offers.append(GiftOffer(cart_id, tuple(by_id[pid].title for pid in basket.product_ids)))
    return GiftTurn("baskets", tuple(offers), link_base)
