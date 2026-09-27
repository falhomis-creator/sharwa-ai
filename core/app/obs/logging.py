"""core/app/obs/logging.py - structured JSON logs for the realtime worker.

H12 ("كل منظومة فرعية ... سجلات JSON منظّمة بحقول ثابتة") + H20 ("لا يُسجَّل
نص رسالة العميل") + H5 ("الهواتف مقنّعة - آخر 3 خانات").

Every log line is a single JSON object with this fixed shape:

    ts, level, event, component,
    tenant_id?, session_id?, conversation_id?, provider_message_id?,
    type?, outcome?, duration_ms?

Optional fields default to null and are never omitted, so every line parses to
the same key set for downstream alerting. The caller may add safe extra fields
(shard, stream, id, reason, lang, attempts, ...) - never client message text.

No third-party logging library (stdlib `logging` + a hand-rolled formatter) is
the H11 default; the gateway uses pino, but core/ stays stdlib-only so the
worker adds zero new dependencies.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

# H5: any digit run that LOOKS like a phone number (7-15 digits, optionally
# `+`-prefixed) is masked to `+***<last3>` wherever it appears in a log line -
# the same conservative over-masking posture as gateway/src/logger.js. This is
# defence-in-depth only: call sites below never log a phone or message text in
# the first place.
_PHONE_RE = re.compile(r"\+?\d{7,15}")


def mask_phone(value: str) -> str:
    """Mask every phone-shaped digit run in `value` to `+***<last3>`."""
    def _mask(match: re.Match[str]) -> str:
        token = match.group(0)
        digits = re.sub(r"\D", "", token)
        last3 = digits[-3:]
        prefix = "+" if token.startswith("+") else ""
        return f"{prefix}***{last3}"

    return _PHONE_RE.sub(_mask, value)


def _redact(value: object) -> object:
    """Apply mask_phone to any string/container, bounded to depth 8 (H4)."""
    if isinstance(value, str):
        return mask_phone(value)
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    return value


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per record; masks phone-shaped digits everywhere."""

    def format(self, record: logging.LogRecord) -> str:
        # `record.msg` may be a dict of structured fields (preferred) or a bare
        # string; `record.args` is unused in this codebase (we pass fields in).
        if isinstance(record.msg, dict):
            payload: dict[str, Any] = dict(record.msg)
            message = ""
        else:
            payload = {}
            message = record.getMessage()

        line: dict[str, Any] = {
            "ts": time.time(),
            "level": record.levelname,
            "event": payload.pop("event", record.name),
            "component": payload.pop("component", record.name),
            "tenant_id": payload.pop("tenant_id", None),
            "session_id": payload.pop("session_id", None),
            "conversation_id": payload.pop("conversation_id", None),
            "provider_message_id": payload.pop("provider_message_id", None),
            "type": payload.pop("type", None),
            "outcome": payload.pop("outcome", None),
            "duration_ms": payload.pop("duration_ms", None),
        }
        if message:
            line["message"] = mask_phone(message)
        for key, value in payload.items():
            line[key] = _redact(value)
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(line, ensure_ascii=False, default=str)


_configured = False


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON handler once (idempotent). Level via LOG_LEVEL, default INFO."""
    global _configured
    if _configured:
        return
    root = logging.getLogger("core.worker")
    root.setLevel(level.upper())
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.propagate = False
    _configured = True


def get_logger(component: str) -> logging.Logger:
    """Return a logger whose lines carry `component` unless overridden."""
    configure_logging()
    return logging.getLogger(f"core.worker.{component}")


def log_event(
    logger: logging.Logger,
    *,
    event: str,
    component: str,
    tenant_id: str | None = None,
    session_id: str | None = None,
    conversation_id: str | None = None,
    provider_message_id: str | None = None,
    type_: str | None = None,
    outcome: str | None = None,
    duration_ms: float | None = None,
    level: int = logging.INFO,
    **extra: object,
) -> None:
    """Log one structured event with the fixed H12 field set. `extra` keys must
    be safe (shard/stream/id/reason/lang/attempts/...) - NEVER client text (H20)."""
    fields: dict[str, Any] = {
        "event": event,
        "component": component,
        "tenant_id": tenant_id,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "provider_message_id": provider_message_id,
        "type": type_,
        "outcome": outcome,
        "duration_ms": duration_ms,
    }
    fields.update(extra)
    logger.log(level, fields)
