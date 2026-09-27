"""Pure, table-driven opt-out detection tests (P1.1.5, H16: deterministic).

Runs with no database/Redis - the only thing under test is the normalization +
word-list matching in app/workers/optout.py, with fixed input -> fixed output.
"""
from __future__ import annotations

from app.workers import optout

_AR = (
    "إيقاف",
    "ايقاف",
    "توقف",
    "الغاء الاشتراك",
    "إلغاء الاشتراك",
    "لا اريد",
    "لا أريد",
    "لا ترسل",
    "اوقف الرسائل",
)
_EN = ("stop", "unsubscribe", "opt out", "optout")


def _detect(msg: str) -> tuple[str, ...]:
    return optout.detect(msg, phrases_ar=_AR, phrases_en=_EN)


def test_normalize_unifies_alef_yeh_taa_marbuta_and_diacritics():
    assert optout.normalize("إِيقَاف") == "ايقاف"
    assert optout.normalize("أيقاف") == "ايقاف"
    assert optout.normalize("آيقاف") == "ايقاف"
    assert optout.normalize("الغاء الاشتراك") == "الغاء الاشتراك"


def test_normalize_arabic_indic_digits_to_latin():
    assert optout.normalize("إيقاف ٠١٢٣") == "ايقاف 0123"


def test_normalize_strips_control_and_zero_width_and_collapses_whitespace():
    assert optout.normalize("  توقف\u200b\n\r  ") == "توقف"


def test_detect_whole_message_ar():
    assert _detect("إيقاف") == ("ar",)
    assert _detect("ايقاف") == ("ar",)


def test_detect_beginning_of_message():
    assert _detect("إيقاف من فضلك") == ("ar",)
    assert _detect("stop please") == ("en",)


def test_detect_casefolded_english():
    assert _detect("STOP") == ("en",)
    assert _detect("Unsubscribe") == ("en",)


def test_detect_begins_ar_does_not_midmatch_en():
    # "إيقاف" is the beginning -> ar; "stop" is mid-text -> NOT an en opt-out
    # (whole-or-beginning match only, never partial mid-text).
    assert _detect("إيقاف stop") == ("ar",)


def test_no_partial_mid_text_match():
    # A customer talking about cancelling must NOT opt out (spec, literal):
    # only the whole message or its beginning matches, never a phrase buried
    # mid-text.
    assert _detect("لا توقف طلبي") == ()
    assert _detect("رجاء لا ترسلوني للتوقف") == ()
    assert _detect("أريد إيقاف هذا الطلب من فضلك") == ()


def test_cancel_alone_is_not_optout():
    # Owner decision (P1_DEVIATIONS.md): cancel/الغاء alone are NOT opt-out.
    assert _detect("cancel") == ()
    assert _detect("الغاء") == ()
    assert _detect("الغاء الطلب") == ()


def test_empty_and_whitespace_not_optout():
    assert _detect("") == ()
    assert _detect("   ") == ()
