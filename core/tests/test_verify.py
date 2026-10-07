"""Pure P1.6 output-verifier enforcement tests (recorded doubles, no DB).

The repo functions, ws_publish and metrics are monkeypatched with recording
doubles; no live Postgres/Redis. These pin H46 (single write point), H47 (fail
closed), H48 (no text/phone in logs/metrics) and the §5.3 violation order.
"""
from __future__ import annotations

import uuid

import pytest

from app.config import ConfigError
from app.db import repos_inbox
from app.db import repos_outbox
from app.workers import verify
from app.workers import verify_rules
from app.workers.stream import TransientError


class Settings:
    verify_enabled = True
    verify_max_chars = 4000
    verify_excerpt_max_chars = 200
    verify_safe_template_id = "handoff_notice"
    verify_join_window_max = 6
    verify_profanity_ar = ("كلب",)
    verify_profanity_en = ()
    verify_competitors = ()
    verify_disclosure = ()
    marketing_footer_ar = "لإيقاف الرسائل الترويجية أرسل: إيقاف"


def _rules() -> verify_rules.BlocklistSet:
    return verify_rules.build_rules(profanity=("كلب",), competitor=(), disclosure=())


class Log:
    def __init__(self):
        self.events: list[tuple] = []

    def add(self, *parts):
        self.events.append(parts)


class FakeMetric:
    def __init__(self, log, name):
        self.log = log
        self.name = name

    def inc(self, *a):
        self.log.add("metric", self.name, "inc", a)

    def labels(self, *a):
        self.log.add("metric", self.name, "labels", a)
        return self

    def observe(self, *a):
        self.log.add("metric", self.name, "observe", a)

    def set(self, *a):
        self.log.add("metric", self.name, "set", a)


def _install(monkeypatch, log, *, status="active", epoch=1, version=1, set_result=2):
    monkeypatch.setattr(repos_outbox, "insert_outbox", lambda conn, **kw: (log.add("insert_outbox", kw) or uuid.uuid4()))
    monkeypatch.setattr(repos_outbox, "insert_verifier_block",
                        lambda conn, **kw: (log.add("insert_verifier_block", kw) or 1))
    monkeypatch.setattr(repos_outbox, "set_bot_status",
                        lambda conn, **kw: (log.add("set_bot_status", kw) or set_result))
    monkeypatch.setattr(repos_outbox, "read_conversation_epoch_status", lambda conn, cid: (epoch, status))
    monkeypatch.setattr(repos_outbox, "read_conversation_version", lambda conn, cid: version)
    monkeypatch.setattr(repos_inbox, "write_inbox_event", lambda conn, **kw: (log.add("write_inbox_event", kw) or 1))
    monkeypatch.setattr(verify.ws_publish, "queue_publish", lambda **kw: log.add("queue_publish", kw))
    for name in ("verify_checks_total", "verify_violations_total", "verify_errors_total",
                 "verify_duration_seconds", "verify_safe_template_sent_total",
                 "verify_enabled", "verify_blocklist_phrases"):
        monkeypatch.setattr(verify.metrics, name, FakeMetric(log, name))


def _named(log, name):
    return [e for e in log.events if e[0] == name]


def _call(monkeypatch, text, **kw):
    log = Log()
    settings = kw.pop("settings", None) or Settings()
    expected_epoch = kw.pop("expected_epoch", 1)
    size_context = kw.pop("size_context", None)
    _install(monkeypatch, log, **kw)
    outcome = verify.insert_verified_outbox(
        None, settings=settings, rules=_rules(),
        tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(),
        channel_account_id=uuid.uuid4(), idempotency_key="k",
        message_class="service", expected_epoch=expected_epoch, to_wa_id="wa",
        template_id="handoff_notice", text=text, size_context=size_context,
    )
    return outcome, log


def test_clean_text_writes_original_once(monkeypatch):
    outcome, log = _call(monkeypatch, "منتج رائع")
    assert outcome.ok
    outs = _named(log, "insert_outbox")
    assert len(outs) == 1
    assert outs[0][1]["text"] == "منتج رائع"
    assert not _named(log, "set_bot_status")
    assert not _named(log, "insert_verifier_block")


def test_violation_active_pauses_and_sends_safe(monkeypatch):
    outcome, log = _call(monkeypatch, "كلب", status="active", epoch=1, version=3, set_result=7)
    assert not outcome.ok
    assert outcome.rule_id == "profanity"
    outs = _named(log, "insert_outbox")
    assert len(outs) == 1
    assert all(o[1]["text"] != "كلب" for o in outs)  # original never written
    assert outs[0][1]["template_id"] == "handoff_notice"
    assert outs[0][1]["idempotency_key"] == "k:v1"
    assert outs[0][1]["expected_epoch"] == 7
    assert outs[0][1]["message_class"] == "service"
    ss = _named(log, "set_bot_status")
    assert len(ss) == 1
    assert ss[0][1]["new_status"] == "paused_human"
    assert ss[0][1]["reason"] == "verifier_profanity"
    assert ss[0][1]["expected_version"] == 3
    assert len(_named(log, "insert_verifier_block")) == 1
    assert len(_named(log, "write_inbox_event")) == 2
    assert len(_named(log, "queue_publish")) == 2


def test_violation_already_paused_does_not_repause(monkeypatch):
    outcome, log = _call(monkeypatch, "كلب", status="paused_human", epoch=5, version=4)
    assert not _named(log, "set_bot_status")
    outs = _named(log, "insert_outbox")
    assert len(outs) == 1
    assert outs[0][1]["expected_epoch"] == 5


def test_no_conversation_updated_when_already_paused(monkeypatch):
    # PROMPT_P1_07 §0.4: when the bot was already paused (paused=False), the
    # verifier emits handoff.requested alone - never a conversation.updated that
    # would announce a handoff_reason the DB column does not hold.
    _, log = _call(monkeypatch, "كلب", status="paused_human", epoch=5, version=4)
    events = _named(log, "write_inbox_event")
    assert [e[1]["event_type"] for e in events] == ["handoff.requested"]


def test_stale_version_raises_transient(monkeypatch):
    log = Log()
    _install(monkeypatch, log, status="active", set_result=None)
    with pytest.raises(TransientError):
        verify.insert_verified_outbox(
            None, settings=Settings(), rules=_rules(),
            tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(),
            channel_account_id=uuid.uuid4(), idempotency_key="k",
            message_class="service", expected_epoch=1, to_wa_id="wa",
            template_id="handoff_notice", text="كلب",
        )
    assert not _named(log, "insert_outbox")


def test_check_text_raises_fails_closed(monkeypatch):
    log = Log()
    _install(monkeypatch, log, status="active")

    def boom(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(verify.verify_rules, "check_text", boom)
    outcome = verify.insert_verified_outbox(
        None, settings=Settings(), rules=_rules(),
        tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(),
        channel_account_id=uuid.uuid4(), idempotency_key="k",
        message_class="service", expected_epoch=1, to_wa_id="wa",
        template_id="handoff_notice", text="أي نص",
    )
    assert not outcome.ok
    assert outcome.rule_id == "verifier_error"
    assert any(e[1] == "verify_errors_total" and e[2] == "inc" for e in log.events if e[0] == "metric")


def test_safe_not_written_before_pause(monkeypatch):
    _, log = _call(monkeypatch, "كلب", status="active")
    names = [e[0] for e in log.events]
    assert names.index("set_bot_status") < names.index("insert_outbox")


def test_excerpt_masked_and_capped(monkeypatch):
    text = "كلب 967123456789 " + "x" * 500
    _, log = _call(monkeypatch, text, status="active")
    block = _named(log, "insert_verifier_block")[0][1]
    excerpt = block["draft_excerpt"]
    assert len(excerpt) <= 200
    assert "967123456789" not in excerpt
    assert "***789" in excerpt


def test_no_text_or_phone_in_logs_or_metrics(monkeypatch):
    log = Log()
    _install(monkeypatch, log, status="active")
    logged: list = []
    monkeypatch.setattr(verify.obs_logging, "log_event", lambda *a, **kw: logged.append((a, kw)))
    verify.insert_verified_outbox(
        None, settings=Settings(), rules=_rules(),
        tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(),
        channel_account_id=uuid.uuid4(), idempotency_key="k",
        message_class="service", expected_epoch=1, to_wa_id="wa",
        template_id="handoff_notice", text="كلب 967123456789",
    )
    for e in log.events:
        assert "967123456789" not in repr(e)
    for l in logged:
        assert "967123456789" not in repr(l)


def test_disabled_writes_as_is(monkeypatch):
    s = Settings()
    s.verify_enabled = False
    outcome, log = _call(monkeypatch, "نص خام", settings=s)
    assert outcome.ok
    outs = _named(log, "insert_outbox")
    assert len(outs) == 1
    assert outs[0][1]["text"] == "نص خام"
    assert any(e[1] == "verify_enabled" and e[2] == "set" and e[3] == (0,) for e in log.events if e[0] == "metric")


def test_invalid_safe_template_id_rejected():
    from app.workers.config import validate_safe_template_id
    with pytest.raises(ConfigError):
        validate_safe_template_id("bogus")
    validate_safe_template_id("handoff_notice")


def test_polluted_template_fails_boot(monkeypatch):
    monkeypatch.setattr(verify.metrics, "verify_blocklist_phrases", FakeMetric(Log(), "verify_blocklist_phrases"))
    monkeypatch.setattr(verify.metrics, "verify_enabled", FakeMetric(Log(), "verify_enabled"))
    monkeypatch.setattr(verify.templates, "TEMPLATES", {"polluted": "هذا كلب في القالب"})
    with pytest.raises(ConfigError):
        verify.build_rules(Settings())


# --- Task 11: size_context pass-through ----------------------------------------


def test_size_context_reaches_the_size_rule(monkeypatch):
    outcome, log = _call(
        monkeypatch, "خذ مقاس XL",
        size_context=verify_rules.SizeContext("M", None, ("S", "M", "XL")),
    )
    assert not outcome.ok
    assert outcome.rule_id == "size_mismatch"
    block = _named(log, "insert_verifier_block")[0][1]
    assert block["reason"] == "verifier_size_mismatch"


def test_size_context_advice_passes_through(monkeypatch):
    outcome, log = _call(
        monkeypatch, "خذ مقاس M",
        size_context=verify_rules.SizeContext("M", None, ("S", "M", "XL")),
    )
    assert outcome.ok
    assert not _named(log, "insert_verifier_block")


def test_disabled_path_ignores_size_context(monkeypatch):
    s = Settings()
    s.verify_enabled = False
    outcome, log = _call(
        monkeypatch, "خذ مقاس XL", settings=s,
        size_context=verify_rules.SizeContext("M", None, ("S", "M", "XL")),
    )
    assert outcome.ok
    outs = _named(log, "insert_outbox")
    assert outs[0][1]["text"] == "خذ مقاس XL"


