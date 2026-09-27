"""Pure P1.5b rolling-summary ceiling tests (PROMPT §6: V9/V10/H43) - no DB/network.

Covers the six written ceilings (trigger, input-messages, input-char cap, output
token cap via settings default, stored-char cap, min-messages-between, daily cap)
and the H43 double-masking.
"""
from __future__ import annotations

from app.llm.router import mask_phones
from app.workers.summary import build_summary_input, cap_summary_output, summary_decision


# --- V9: the skip ceilings (trigger / min-between / daily cap) -----------------


def test_summary_decision_below_trigger():
    kwargs = dict(last_inbound_seq=12, summary_seq=None, trigger_messages=12,
                  min_between=10, max_per_day=6, day_count=0)
    assert summary_decision(message_count=12, **kwargs) == "below_trigger"
    assert summary_decision(message_count=13, **kwargs) is None


def test_summary_decision_min_between_nine_vs_ten():
    # Nine new inbound messages since the last summary => skip; ten => one summary.
    assert summary_decision(
        message_count=30, last_inbound_seq=20, summary_seq=11,
        trigger_messages=12, min_between=10, max_per_day=6, day_count=0,
    ) == "min_between"
    assert summary_decision(
        message_count=30, last_inbound_seq=20, summary_seq=10,
        trigger_messages=12, min_between=10, max_per_day=6, day_count=0,
    ) is None


def test_summary_decision_first_summary_has_no_min_between():
    # No prior summary (summary_seq=None) => the trigger alone gates it.
    assert summary_decision(
        message_count=13, last_inbound_seq=13, summary_seq=None,
        trigger_messages=12, min_between=10, max_per_day=6, day_count=0,
    ) is None


def test_summary_decision_daily_cap():
    assert summary_decision(
        message_count=30, last_inbound_seq=30, summary_seq=None,
        trigger_messages=12, min_between=10, max_per_day=6, day_count=6,
    ) == "daily_cap"
    assert summary_decision(
        message_count=30, last_inbound_seq=30, summary_seq=None,
        trigger_messages=12, min_between=10, max_per_day=6, day_count=5,
    ) is None


# --- V9: input ceilings (input-messages + input-char cap) + H43 input masking ---


def test_build_summary_input_masks_input():
    msgs = [(1, "in", "رقمي 967123456789"), (2, "out", "أهلاً")]
    out = build_summary_input(msgs, existing_summary="ملخص برقم 967555555555",
                              max_messages=20, max_chars=1000)
    assert "967123456789" not in out
    assert "***789" in out
    assert "967555555555" not in out


def test_build_summary_input_keeps_only_last_n_messages():
    msgs = [(i, "in", f"msg{i}") for i in range(30)]
    out = build_summary_input(msgs, existing_summary=None, max_messages=20, max_chars=100_000)
    assert "msg5" not in out   # oldest dropped
    assert "msg29" in out


def test_build_summary_input_char_cap():
    msgs = [(1, "in", "x" * 5000)]
    out = build_summary_input(msgs, existing_summary=None, max_messages=20, max_chars=1000)
    assert len(out) <= 1000


# --- V9: stored-output ceiling + H43 output masking ----------------------------


def test_cap_summary_output_truncates():
    assert cap_summary_output("x" * 5000, 1600) == "x" * 1600


def test_summary_output_is_masked_again_before_storage():
    # The model may repeat a phone it heard; mask again before storing (H43).
    text = cap_summary_output(mask_phones("رقم العميل 967123456789"), 1600)
    assert "967123456789" not in text
    assert "***789" in text


def test_summary_max_output_tokens_default_is_400():
    # The output-token ceiling is a written default of <= 400 (01 §2.4); the
    # worker passes it verbatim from settings to the provider.
    from dataclasses import fields as dataclass_fields
    from app.workers.config import WorkerSettings
    field = next(f for f in dataclass_fields(WorkerSettings) if f.name == "summary_max_output_tokens")
    assert field.default == 400
