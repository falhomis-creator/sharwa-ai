"""Pure P1.4 catalog tests (PROMPT §8: K3/K5/K7/K8 + C6/C7) - no DB/Redis.

The DB-transaction guarantees (K4 H37 ordering, K6 soft-delete, K9 isolation,
K10 reconciliation) need real Postgres and are recorded as debt (K15). Everything
here is pure: RRF, the H36 allowlist, the H35 card whitelist, the webhook
signature/timestamp checks, the shared normalizer, and the FakeCommerce protocol.
"""
from __future__ import annotations

import hashlib
import hmac
import uuid

from app.api.errors import ERROR_CODES, _STATUS_BY_CODE
from app.api.routes_catalog import verify_platform_signature, verify_webhook_timestamp
from app.db.repos_catalog import (
    EVENT_ALLOWED_FIELDS,
    PRODUCT_CARD_KEYS,
    build_product_card,
    is_forbidden_field,
    partition_event_fields,
    rrf_merge,
)
from app.text.arabic import normalize
from app.workers import optout
from tests.fake_commerce import FakeCommerce


# --- K7: RRF is pure and hand-checked ---------------------------------------


def test_rrf_merge_hand_computed_ranking():
    result = rrf_merge([["a", "b"], ["c", "a"]], k=60)
    expected = {
        "a": 1 / 61 + 1 / 62,  # rank 1 in L1, rank 2 in L2
        "b": 1 / 62,            # rank 2 in L1
        "c": 1 / 61,            # rank 1 in L2
    }
    assert [doc for doc, _ in result] == ["a", "c", "b"]
    for doc, score in result:
        assert abs(score - expected[doc]) < 1e-12


def test_rrf_merge_ties_broken_by_doc_id():
    # Two lists with no overlap: every doc keeps its own rank score.
    result = rrf_merge([["x"], ["y"]], k=60)
    assert [doc for doc, _ in result] == ["x", "y"]  # tie on score -> doc_id asc
    assert result[0][1] == result[1][1]


# --- K5 / H36: the allowlist drops forbidden fields --------------------------


def test_partition_event_fields_drops_forbidden_fields():
    fields = {
        "platform_product_id": "P1", "title": "Shirt", "version": 3,
        "cost_price": 100, "margin": 5, "unrelated": "x",
    }
    part = partition_event_fields("product.upserted", fields)
    assert part.kept == {"platform_product_id": "P1", "title": "Shirt", "version": 3}
    # Both counters advance: cost_price + margin + unrelated are all dropped.
    assert part.dropped_count == 3
    assert part.forbidden_fields == ("cost_price", "margin")
    assert "cost_price" not in part.kept
    assert "margin" not in part.kept


def test_partition_event_fields_keeps_variant_price_hints():
    # price_hint_minor / currency are ranking hints (H35), so they ARE in the
    # variant.upserted allowlist - but stock_hint is NOT (it arrives via stock).
    fields = {
        "platform_variant_id": "V1", "platform_product_id": "P1",
        "price_hint_minor": 99, "currency": "SAR", "version": 1, "stock_hint": 5,
    }
    part = partition_event_fields("variant.upserted", fields)
    assert "price_hint_minor" in part.kept
    assert "currency" in part.kept
    assert "stock_hint" not in part.kept
    assert part.dropped_count == 1


def test_no_forbidden_field_is_ever_allowed():
    for allowed in EVENT_ALLOWED_FIELDS.values():
        for field in allowed:
            assert not is_forbidden_field(field), field


def test_is_forbidden_field_case_insensitive():
    assert is_forbidden_field("cost_price")
    assert is_forbidden_field("MARGIN")
    assert is_forbidden_field("margin")
    assert not is_forbidden_field("title")


def test_event_allowlist_is_the_five_contract_types():
    assert set(EVENT_ALLOWED_FIELDS) == {
        "product.upserted", "product.deleted", "variant.upserted",
        "variant.stock", "kb.upserted",
    }


# --- K8 / H35: the card is an explicit whitelist -----------------------------


def test_build_product_card_is_an_explicit_whitelist():
    card = build_product_card(
        product_id=uuid.uuid4(), platform_product_id="P1", title="Shirt",
        category="clothing", options_summary={"size": ["M", "L"]},
    )
    assert set(card.keys()) == set(PRODUCT_CARD_KEYS)
    assert card["platform_product_id"] == "P1"
    assert card["title"] == "Shirt"
    assert "price_hint_minor" not in card
    assert "stock_hint" not in card
    assert "currency" not in card


# --- K3: webhook signature + timestamp (pure) --------------------------------


def test_verify_platform_signature():
    secret = "s3cret"
    timestamp = "1700000000"
    raw_body = b'{"tenant_ref":"t","events":[]}'
    message = f"{timestamp}.".encode("utf-8") + raw_body
    sig = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()

    assert verify_platform_signature(secret, timestamp, raw_body, sig)
    assert not verify_platform_signature(secret, timestamp, raw_body, "deadbeef")
    assert not verify_platform_signature("", timestamp, raw_body, sig)  # empty secret (H5)
    assert not verify_platform_signature(secret, timestamp, raw_body, "")  # empty signature
    assert not verify_platform_signature(secret, "1700000001", raw_body, sig)  # wrong timestamp


def test_verify_webhook_timestamp_replay_window():
    assert verify_webhook_timestamp("1000", 1300.0, 300)          # exactly at the edge
    assert not verify_webhook_timestamp("1000", 1301.0, 300)      # older than skew
    assert not verify_webhook_timestamp("abc", 1300.0, 300)       # unparseable
    assert not verify_webhook_timestamp("0", 1300.0, 300)         # non-positive
    assert not verify_webhook_timestamp("-5", 1300.0, 300)


# --- K3: the three new error codes are declared ------------------------------


def test_new_platform_error_codes_are_declared():
    assert "PLATFORM_SIGNATURE_INVALID" in ERROR_CODES
    assert "PLATFORM_TIMESTAMP_SKEW" in ERROR_CODES
    assert "PLATFORM_PAYLOAD_TOO_LARGE" in ERROR_CODES
    assert _STATUS_BY_CODE["PLATFORM_SIGNATURE_INVALID"] == 401
    assert _STATUS_BY_CODE["PLATFORM_TIMESTAMP_SKEW"] == 401
    assert _STATUS_BY_CODE["PLATFORM_PAYLOAD_TOO_LARGE"] == 413


# --- C6: the single shared normalizer ---------------------------------------


def test_shared_normalizer_and_optout_reexport():
    assert normalize("إِيقَاف") == "ايقاف"
    assert normalize("أيقاف") == "ايقاف"
    # optout re-exports the SAME function (existing tests stay green untouched).
    assert optout.normalize("إيقاف ٠١٢٣") == "ايقاف 0123"


# --- C7: FakeCommerce implements the CommercePort protocol -------------------


def test_fake_commerce_changes_and_snapshot():
    fake = FakeCommerce({
        "changes": {
            "t1": {
                "pages": [
                    {"cursor": "c1", "events": [{"type": "product.upserted"}]},
                    {"cursor": "c2", "events": [{"type": "kb.upserted"}]},
                ],
            },
        },
        "snapshot": {"t1": [{"type": "variant.upserted"}]},
    })
    events, cursor = fake.get_changes("t1", None)
    assert [e["type"] for e in events] == ["product.upserted"]
    assert cursor == "c1"
    events, cursor = fake.get_changes("t1", "c1")
    assert [e["type"] for e in events] == ["kb.upserted"]
    assert cursor == "c2"
    events, cursor = fake.get_changes("t1", "c2")
    assert events == []
    snapshot, next_page = fake.get_snapshot("t1", None)
    assert [e["type"] for e in snapshot] == ["variant.upserted"]
    assert next_page is None

