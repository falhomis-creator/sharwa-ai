"""core/app/api/ws_frames.py - the single WebSocket frame filter (H34, S6).

Every outgoing WebSocket frame is built here and ONLY here: app/api/ws.py must
never assemble a frame payload itself. to_frame() enforces the same whitelist
that governs inbox_events rows (INBOX_EVENT_ALLOWED_KEYS) so no message text,
phone number, or internal note can ever leave over a WebSocket (H28/H34) - those
are fetched by the client over REST on its own authorized path.

The only keys allowed beyond the inbox_events whitelist are the two integers of
the hello control frame (latest_seq / retention_days) - server-position metadata,
never content. static_gate S6 makes this the only legal frame builder.
"""
from __future__ import annotations

from typing import Any

from app.db.repos_inbox import INBOX_EVENT_ALLOWED_KEYS

_FRAME_CONTROL_KEYS = frozenset({"latest_seq", "retention_days"})
_FRAME_ALLOWED_KEYS = INBOX_EVENT_ALLOWED_KEYS | _FRAME_CONTROL_KEYS


def to_frame(frame_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one outgoing WS frame. Raises ValueError if any payload key is not
    whitelisted (H34): a frame that would leak text/phone/notes is refused loudly
    at the send site rather than silently shipped."""
    frame: dict[str, Any] = {"type": frame_type}
    if payload:
        disallowed = set(payload) - _FRAME_ALLOWED_KEYS
        if disallowed:
            raise ValueError(f"ws frame payload has non-whitelisted keys: {sorted(disallowed)}")
        frame.update(payload)
    return frame
