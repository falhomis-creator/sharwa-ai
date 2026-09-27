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
    # 'كلب' (blocked) is a prefix of 'كلاب' (dogs) - equality must NOT match.
    rules = _rules(profanity=("كلب",))
    assert _check("اشتريت كلاب", rules=rules).ok


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
