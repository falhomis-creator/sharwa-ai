"""core/tests/test_proactive.py - pure tests for the proactive writer's catalog
rendering (H77/H83): the class is derived from the catalog, merge keys are
whitelisted, and unknown templates fail closed.
"""
from __future__ import annotations

import pytest

from app.workers import proactive


def test_render_stock_available_fills_title():
    text, meta = proactive.render_text("stock_available", {"title": "قميص قطني"})
    assert meta.message_class == "utility"
    assert meta.consent_scope == "back_in_stock"
    assert "قميص قطني" in text
    assert "«title»" not in text


def test_render_unknown_template_raises():
    with pytest.raises(ValueError):
        proactive.render_text("no_such_template", {})


def test_render_rejects_unknown_merge_key():
    with pytest.raises(ValueError):
        proactive.render_text("stock_available", {"price": "10"})


def test_scope_to_template_ids_is_derived():
    assert "stock_available" in proactive.template_ids_for_scope("back_in_stock")
    assert proactive.template_ids_for_scope("marketing") == ("cart_reminder",)
