"""P4 Task 18b-1: the size-reply template contract (OQ-P4-15 / OQ-P4-16) and the
advice -> template mapping of compose_size_reply. Pure: real SizeAdvisor outputs,
the real Verifier (check_text with a SizeContext), no DB, no model."""
from __future__ import annotations

import re
from decimal import Decimal

import pytest

from app.fit.size_advisor import SizeAdvice, SizeChart, SizeRow, advise
from app.text import arabic
from app.workers import templates, verify_rules
from app.workers.compose import compose_size_reply

D = Decimal
SIZE_TEMPLATES = (
    "size_recommend", "size_recommend_alt", "size_nearest_out_of_range",
    "size_need_inputs", "size_no_chart", "size_no_fit",
)
WITH_SIZE = ("size_recommend", "size_recommend_alt", "size_nearest_out_of_range")
LABELS = (
    "XS", "S", "M", "L", "XL", "XXL", "2XL", "3XL", "4XL", "5XL",
    "38", "40", "42", "44", "46", "48", "سمول", "ميديوم", "لارج",
)


def _rules() -> verify_rules.BlocklistSet:
    return verify_rules.build_rules(
        profanity=("كلب",), competitor=("salla",), disclosure=("بوت", "deepseek"),
    )


def _verdict(text: str, size: str | None, alt: str | None) -> verify_rules.RuleVerdict:
    return verify_rules.check_text(
        text, rules=_rules(), max_chars=4000,
        size_context=verify_rules.SizeContext(size, alt, LABELS),
    )


def _render(template_id: str, size: str | None, alt: str | None = None) -> str:
    out_of_range = template_id in ("size_nearest_out_of_range", "size_no_fit")
    reasons = {
        "size_need_inputs": ("missing_height_weight",),
        "size_no_chart": ("no_chart",),
        "size_no_fit": ("heavier_than_chart",),
    }.get(template_id, ("height_weight_match",))
    tid, text = compose_size_reply(SizeAdvice(size, alt, "medium", reasons, out_of_range))
    assert tid == template_id
    return text


# ---- the written contract on the raw templates --------------------------------

def test_every_size_template_is_registered_and_one_line():
    for tid in SIZE_TEMPLATES:
        assert "\n" not in templates.TEMPLATES[tid]


def test_every_label_placeholder_follows_the_word_maqas():
    # OQ-P4-15: «size» / «alt» are always written right after «مقاس » so the
    # Verifier's two-token numeric window always captures numeric labels.
    for tid in SIZE_TEMPLATES:
        raw = templates.TEMPLATES[tid]
        for ph in re.findall(r"«(?:size|alt)»", raw):
            assert raw.count(f"مقاس {ph}") == raw.count(ph), (tid, ph)
    for tid in WITH_SIZE:
        assert "«size»" in templates.TEMPLATES[tid]


def test_no_size_ish_words_outside_the_labels():
    # OQ-P4-16: words the size rule does not know must never carry a size.
    banned = {arabic.normalize(w) for w in ("صغير", "وسط", "كبير", "small", "medium", "large")}
    for tid in SIZE_TEMPLATES:
        tokens = {t.strip("،.:!؟") for t in arabic.normalize(templates.TEMPLATES[tid]).split()}
        assert not tokens & banned, tid


# ---- rendered templates against the real Verifier ------------------------------

@pytest.mark.parametrize("label", LABELS)
def test_rendered_advice_passes_the_verifier_for_every_label(label):
    assert _verdict(_render("size_recommend", label), label, None).ok
    assert _verdict(_render("size_nearest_out_of_range", label), label, None).ok
    alt = "L" if label != "L" else "M"
    assert _verdict(_render("size_recommend_alt", label, alt), label, alt).ok


@pytest.mark.parametrize(("shown", "advised"), [
    ("M", "L"), ("42", "44"), ("XL", "XXL"), ("S", "لارج"), ("2XL", "XL"),
])
def test_a_label_other_than_the_advice_is_caught(shown, advised):
    text = _render("size_recommend", shown)
    verdict = _verdict(text, advised, None)
    assert (verdict.ok, verdict.rule_id) == (False, "size_mismatch")


def test_the_advised_size_is_the_primary_one_in_the_text():
    # The Verifier accepts size AND alt_size, so it cannot tell them apart: the
    # exact rendering is pinned here (the advised size first, alt as the option).
    assert _render("size_recommend_alt", "M", "L") == (
        "المقاس المناسب لك: مقاس M، ويمكنك أيضاً تجربة مقاس L. ✅"
    )
    assert _render("size_recommend", "42") == "المقاس المناسب لك: مقاس 42 ✅"
    assert _render("size_nearest_out_of_range", "S").startswith("أقرب مقاس لك في جدول هذا المنتج هو مقاس S،")


def test_size_free_templates_carry_no_size_token():
    for tid in ("size_need_inputs", "size_no_chart", "size_no_fit"):
        assert _verdict(templates.TEMPLATES[tid], None, None).ok, tid


# ---- compose_size_reply mapping on REAL advisor outputs ------------------------

def _r(lo: str, hi: str) -> tuple[Decimal, Decimal]:
    return (D(lo), D(hi))


CHART = SizeChart(rows=(
    SizeRow("S", 1, height_cm=_r("150", "165"), weight_kg=_r("45", "60"), chest_cm=_r("86", "91")),
    SizeRow("M", 2, height_cm=_r("165", "178"), weight_kg=_r("60", "75"), chest_cm=_r("91", "97")),
    SizeRow("L", 3, height_cm=_r("172", "185"), weight_kg=_r("73", "88"), chest_cm=_r("97", "103")),
))


@pytest.mark.parametrize(("kwargs", "chart", "expected"), [
    ({"height_cm": D("170"), "weight_kg": D("65")}, CHART, "size_recommend"),
    ({"height_cm": D("175"), "weight_kg": D("74")}, CHART, "size_recommend_alt"),
    ({"height_cm": D("140"), "weight_kg": D("50")}, CHART, "size_nearest_out_of_range"),
    ({"height_cm": D("180"), "weight_kg": D("120")}, CHART, "size_no_fit"),
    ({"body_cm": {"chest": D("120")}}, CHART, "size_no_fit"),
    ({"height_cm": D("170"), "weight_kg": D("65")}, None, "size_no_chart"),
    ({"height_cm": D("170"), "weight_kg": D("65")}, SizeChart(rows=CHART.rows, fit_type="weird"),
     "size_no_chart"),
    ({"height_cm": D("170")}, CHART, "size_need_inputs"),
    ({"height_cm": D("20"), "weight_kg": D("65")}, CHART, "size_need_inputs"),
])
def test_compose_maps_every_advisor_outcome(kwargs, chart, expected):
    advice = advise(chart, **kwargs)
    tid, text = compose_size_reply(advice)
    assert tid == expected
    assert "«" not in text
    assert _verdict(text, advice.size, advice.alt_size).ok


def test_unknown_advice_shape_hands_off():
    tid, text = compose_size_reply(SizeAdvice(None, None, "low", ("something_new",), False))
    assert tid == "handoff_notice"
    assert text == templates.TEMPLATES["handoff_notice"]
