"""Pure deterministic output-verifier rule tests (P1.6 §9, H45).

No DB/network: only check_text / squeeze / build_rules and the approved templates
are under test. The substring trap (a short blocked phrase that is a prefix of a
clean word must PASS) is the row that distinguishes equality matching from
substring scanning - do not delete it.
"""
from __future__ import annotations

from app.workers import templates
from app.workers import verify_rules
from app.workers.compose import PRODUCT_LIST_TEMPLATE


def _rules(**kw) -> verify_rules.BlocklistSet:
    return verify_rules.build_rules(
        profanity=kw.get("profanity", ("كلب", "كلمة بذيئة")),
        competitor=kw.get("competitor", ("salla",)),
        disclosure=kw.get("disclosure", ("بوت", "deepseek", "chatbot")),
    )


def _check(text: str, rules: verify_rules.BlocklistSet | None = None, max_chars: int = 4000) -> verify_rules.RuleVerdict:
    return verify_rules.check_text(text, rules=rules or _rules(), max_chars=max_chars)


# --- structure ---------------------------------------------------------------


def test_empty_and_whitespace():
    assert _check("").rule_id == "empty"
    assert _check("   ").rule_id == "empty"


def test_oversize_boundary():
    assert _check("x" * 4000, max_chars=4000).ok
    assert _check("x" * 4001, max_chars=4000).rule_id == "oversize"


def test_smuggled_zero_width_char():
    assert _check("نص\u200bمقبول").rule_id == "control_chars"


def test_unsubstituted_placeholder():
    assert _check("منتج {items} متوفر").rule_id == "placeholder"


# --- profanity ---------------------------------------------------------------


def test_profanity_direct():
    assert _check("كلب").rule_id == "profanity"


def test_profanity_with_diacritics():
    assert _check("كَلْب").rule_id == "profanity"


def test_profanity_arabic_indic_digits():
    rules = _rules(profanity=("كلمة٢",))
    assert _check("كلمة٢", rules=rules).rule_id == "profanity"


def test_profanity_separator_evasion():
    assert _check("ك.ل.ب").rule_id == "profanity"
    assert _check("كلللب").rule_id == "profanity"


def test_profanity_multiword():
    assert _check("هذه كلمة بذيئة جدا").rule_id == "profanity"


def test_substring_trap_passes():
    # 'كلب' (blocked) is a prefix/substring of 'كلبي'/'كلبين' - equality must NOT
    # match them (a naive `in` matcher WOULD). 'كلاب' is NOT a substring (ك-ل-ا-ب),
    # and 'زبون'/'زبدة' are the classic Arabic trap for a hypothetical short blocked
    # word (PROMPT_P1_07 §0.3).
    rules = _rules(profanity=("كلب",))
    assert _check("اشتريت كلبي", rules=rules).ok
    assert _check("اشتريت كلبين", rules=rules).ok
    assert _check("اشتريت كلاب", rules=rules).ok
    assert _check("الزبون سعيد", rules=rules).ok
    assert _check("زبدة الفول السوداني", rules=rules).ok


def test_space_evasion_profanity_blocked():
    # PROMPT_P1_07 §0.2: 'ك ل ب' smuggles 'كلب' through the token splitter.
    rules = _rules(profanity=("كلب",))
    assert _check("ك ل ب", rules=rules).rule_id == "profanity"


def test_space_evasion_competitor_blocked():
    # 's a l l a' smuggles 'salla'.
    rules = _rules(competitor=("salla",))
    assert _check("s a l l a", rules=rules).rule_id == "competitor"


def test_space_join_does_not_false_positive():
    # 'كل بلد' joined = 'كلبلد' != 'كلب' (equality, not substring) - must pass.
    rules = _rules(profanity=("كلب",))
    assert _check("كل بلد", rules=rules).ok


# --- competitors --------------------------------------------------------------


def test_competitor_standalone():
    assert _check("salla").rule_id == "competitor"


def test_competitor_in_sentence():
    assert _check("استخدم منصة salla").rule_id == "competitor"


def test_competitor_uppercase():
    assert _check("SALLA").rule_id == "competitor"


# --- disclosure ---------------------------------------------------------------


def test_disclosure_arabic():
    assert _check("أنا بوت آلي").rule_id == "disclosure"


def test_disclosure_english():
    assert _check("I am a chatbot").rule_id == "disclosure"


def test_disclosure_provider_from_settings():
    assert _check("مدعوم من deepseek").rule_id == "disclosure"


# --- safety / purity / lists ---------------------------------------------------


def test_all_approved_templates_pass():
    rules = _rules()
    for text in templates.TEMPLATES.values():
        assert _check(text, rules=rules).ok


def test_product_list_template_passes_after_substitution():
    rules = _rules()
    text = PRODUCT_LIST_TEMPLATE.format(items="منتج متوفر")
    assert _check(text, rules=rules).ok


def test_long_policy_text_passes():
    rules = _rules()
    text = "سياسة الاستبدال تتيح للعميل إرجاع المنتج خلال أربعة عشر يوماً بشرط سلامة الغلاف." * 20
    assert _check(text, rules=rules).ok


def test_purity_same_verdict_ten_times():
    rules = _rules()
    text = "منتج رائع للتسوق"
    first = verify_rules.check_text(text, rules=rules, max_chars=4000)
    for _ in range(10):
        assert verify_rules.check_text(text, rules=rules, max_chars=4000) == first


def test_empty_normalized_phrase_ignored():
    rules = _rules(profanity=("...",))
    assert rules.ignored_empty >= 1
    assert _check("أي نص عادي", rules=rules).ok


def test_squeeze_canonical_forms():
    assert verify_rules.squeeze("ك.ل.ب") == "كلب"
    assert verify_rules.squeeze("كلللب") == "كلب"
    assert verify_rules.squeeze("كـلـب") == "كلب"


# --- size_mismatch (Task 11) ---------------------------------------------------

LABELS = ("S", "M", "L", "XL")


def _size_check(text: str, sc: verify_rules.SizeContext | None) -> verify_rules.RuleVerdict:
    return verify_rules.check_text(text, rules=_rules(), max_chars=4000, size_context=sc)


def test_size_mismatch_is_in_the_closed_rule_list():
    # The closed list gains EXACTLY one member in Task 11 (H20/H4).
    assert frozenset({
        "empty", "oversize", "control_chars", "placeholder",
        "profanity", "competitor", "disclosure", "verifier_error",
        "size_mismatch",
    }) == verify_rules.CLOSED_RULE_IDS


def test_matching_size_passes():
    assert _size_check("مقاسك هو M", verify_rules.SizeContext("M", "L", LABELS)).ok
    assert _size_check("your size is m", verify_rules.SizeContext("M", None, LABELS)).ok


def test_alt_size_passes():
    assert _size_check("وإن أردت أوسع فجرّب L", verify_rules.SizeContext("M", "L", LABELS)).ok


def test_wrong_size_violates():
    verdict = _size_check("خذ مقاس XL", verify_rules.SizeContext("M", "L", LABELS))
    assert (verdict.ok, verdict.rule_id, verdict.category) == (False, "size_mismatch", "size")


def test_arabic_size_words_compare_canonically():
    assert _size_check("خذ المقاس لارج", verify_rules.SizeContext("L", None, LABELS)).ok
    assert _size_check("خذ المقاس اكسترا لارج", verify_rules.SizeContext("XL", None, LABELS)).ok
    assert _size_check("خذ المقاس سمول", verify_rules.SizeContext("L", None, LABELS)) \
        .rule_id == "size_mismatch"


def test_case_insensitive_latin():
    assert _size_check("Size XL fits you", verify_rules.SizeContext("XL", None, LABELS)).ok
    assert _size_check("size XXL fits you", verify_rules.SizeContext("XL", None, LABELS)) \
        .rule_id == "size_mismatch"


def test_any_size_violates_when_size_is_none():
    assert _size_check("مقاسك M", verify_rules.SizeContext(None, None, LABELS)).rule_id == "size_mismatch"
    # alt_size cannot rescue a None advice
    assert _size_check("مقاسك L", verify_rules.SizeContext(None, "L", LABELS)).rule_id == "size_mismatch"


def test_no_size_token_passes_even_with_none():
    assert _size_check("لا توجد توصية مقاس الآن", verify_rules.SizeContext(None, None, LABELS)).ok


def test_numeric_label_needs_the_size_word():
    labels = ("40", "42", "44")
    assert _size_check("مقاس 42", verify_rules.SizeContext("40", None, labels)).rule_id == "size_mismatch"
    # the same numeric token far from any size word never binds
    assert _size_check("الصدر 42", verify_rules.SizeContext("40", None, labels)).ok
    assert _size_check("الطول 175 سم", verify_rules.SizeContext("40", None, labels)).ok


def test_numeric_label_across_size_word_clitics():
    # R4: prefixes (و ف ب ل ال وال بال لل) and possessive suffixes are stripped
    # before the size-word test, else a wrong numeric size slips through (H49).
    labels = ("40", "42", "44")
    sc = verify_rules.SizeContext("42", None, labels)
    for text in ("بمقاس 44", "المقاس 44", "مقاسي 44", "ولمقاس 44", "مقاساتك 44"):
        assert _size_check(text, sc).rule_id == "size_mismatch", text
    assert _size_check("بمقاس 42", sc).ok
    assert _size_check("175 سم", sc).ok


def test_size_writing_variants():
    sc_xl = verify_rules.SizeContext("XL", None, LABELS)
    # R5: 2XL/3XL ARE XXL/XXXL, not XL - an XL advice must block them (H49).
    assert _size_check("خذ مقاس 2XL", sc_xl).rule_id == "size_mismatch"
    assert _size_check("خذ مقاس 3XL", sc_xl).rule_id == "size_mismatch"
    assert _size_check("خذ مقاس XXL", sc_xl).rule_id == "size_mismatch"
    # ...while the two names of the SAME size pass in both directions
    assert _size_check("خذ مقاس 2XL", verify_rules.SizeContext("XXL", None, LABELS)).ok
    assert _size_check("خذ المقاس XXL", verify_rules.SizeContext("2XL", None, LABELS)).ok
    assert _size_check("خذ مقاس 3XL", verify_rules.SizeContext("XXXL", None, LABELS)).ok
    # 4XL/5XL are standalone labels: captured anywhere, compared to themselves
    assert _size_check("خذ مقاس 4XL", verify_rules.SizeContext("4XL", None, LABELS)).ok
    assert _size_check("خذ مقاس 4XL", sc_xl).rule_id == "size_mismatch"
    assert _size_check("خذ مقاس 5XL", verify_rules.SizeContext("5XL", None, LABELS)).ok
    assert _size_check("خذ مقاس xl", sc_xl).ok
    assert _size_check("خذ مقاس (L)", verify_rules.SizeContext("L", None, LABELS)).ok
    assert _size_check("خذ المقاس اكس سمول", verify_rules.SizeContext("XS", None, LABELS)).ok
    # the compound consumes its head word: "اكسترا لارج" is xl, NOT also l
    assert _size_check("خذ المقاس اكسترا لارج", sc_xl).ok
    # ...while a standalone لارج is still the letter l
    assert _size_check("خذ المقاس لارج", verify_rules.SizeContext("L", None, LABELS)).ok
    assert _size_check("خذ المقاس لارج", sc_xl).rule_id == "size_mismatch"


def test_letter_inside_word_is_not_captured():
    assert _size_check("متجرنا المميز", verify_rules.SizeContext("M", None, LABELS)).ok
    assert _size_check("welcome to our mall", verify_rules.SizeContext("M", None, LABELS)).ok


def test_size_context_absent_keeps_legacy_behavior():
    # the default: no size rule runs at all - a size-laden text stays legal
    assert _check("خذ مقاس XL واللارج كذلك").ok


def test_malformed_context_is_a_violation():
    assert _size_check("نص عادي جدا", "M").rule_id == "size_mismatch"  # type: ignore[arg-type]
    assert _size_check("نص عادي جدا", verify_rules.SizeContext(5, None, LABELS)).rule_id == "size_mismatch"  # type: ignore[arg-type]
    assert _size_check("نص عادي جدا", verify_rules.SizeContext("M", None, ("M", 5))) \
        .rule_id == "size_mismatch"  # type: ignore[list-item]


def test_internal_failure_inside_the_rule_is_a_violation_h47():
    class Poison(str):
        def strip(self, *args, **kwargs):
            raise RuntimeError("boom")

    verdict = _size_check("مقاسك M", verify_rules.SizeContext(Poison("M"), None, LABELS))
    assert (verdict.ok, verdict.rule_id) == (False, "size_mismatch")
