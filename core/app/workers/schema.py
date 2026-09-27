"""core/app/workers/schema.py - WAL entry validation + session resolution (P1.1.2).

WalEntry is pydantic with explicit types for every field in the §6.1 WAL
contract, and `extra='ignore'` (NOT `forbid`): the gateway's own contract says
every future extension is additive/optional, so rejecting a new field would
turn a future gateway addition into an instant ingest outage. Tolerance here is
measured, not silent - parse_entry() returns the set of unknown top-level
fields so the caller can bump ingest_core_unknown_fields_total and log once per
new field.

session_id is opaque: the ONLY bridge to a tenant is `app.resolve_session()`
(SECURITY DEFINER, GRANTed to sharwa_system), run under system_tx() - never
inferred from any entry field or any text (H2). A small, bounded LRU cache
(<=30s TTL, documented max size) avoids re-hitting Postgres per message; the
engine field lives in that cache no longer than the TTL, so a merchant's
Engine-v1 migration propagates within seconds.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from app.db import repos_ingest
from app.db.context import system_tx

# Bounded session-resolution cache (H4: written caps, not unbounded growth).
SESSION_CACHE_MAX = 4096
SESSION_CACHE_TTL_S = 30.0

MESSAGE_TYPES = frozenset(
    {"text", "image", "audio", "video", "document", "location", "contact",
     "sticker", "reaction", "unsupported"}
)
SPECIAL_TYPES = frozenset({"identity_update", "human_takeover_signal"})


class WalIdentity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    jid_raw: str | None = None
    addressing: str | None = None  # 'pn' | 'lid'
    wa_id: str | None = None
    phone_e164: str | None = None


class WalMedia(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str | None = None
    mimetype: str | None = None
    fileLength: int | None = None  # camelCase - gateway normalize.js's real key
    fileName: str | None = None
    object_key: str | None = None
    status: str | None = None


class WalLocation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lat: float | None = None
    lng: float | None = None
    name: str | None = None
    address: str | None = None
    is_live: bool | None = None


class WalContact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    numbers: list[str] | None = None


class WalReaction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str | None = None
    target_message_id: str | None = None


class WalEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    v: int = 1
    session_id: str
    provider_message_id: str | None = None
    type: str
    ts: int | None = None
    identity: WalIdentity | None = None
    text: str | None = None
    media: WalMedia | None = None
    location: WalLocation | None = None
    contact: WalContact | None = None
    reaction: WalReaction | None = None
    engine: str | None = None
    direction: str | None = None
    lid: str | None = None
    phone_e164: str | None = None


_KNOWN_TOP_LEVEL = frozenset(WalEntry.model_fields)


def parse_entry(raw: dict[str, object]) -> tuple[WalEntry, frozenset[str]]:
    """Validate `raw` into a WalEntry, returning it plus the set of unknown
    top-level fields (which were ignored). Raises pydantic.ValidationError when
    a REQUIRED/known field has the wrong type - the caller treats that as a
    permanent schema mismatch (DLQ), never a retry loop."""
    unknown = frozenset(k for k in raw if k not in _KNOWN_TOP_LEVEL)
    return WalEntry.model_validate(raw), unknown


@dataclass(frozen=True)
class SessionResolution:
    channel_account_id: uuid.UUID
    tenant_id: uuid.UUID
    engine: str


class _SessionCache:
    """Tiny bounded LRU with a TTL; stores the full resolution (including
    engine) for at most SESSION_CACHE_TTL_S - so an Engine-v1 -> ai_core
    migration propagates within one TTL window (spec, literal)."""

    def __init__(self, *, max_size: int, ttl_s: float) -> None:
        self._max_size = max_size
        self._ttl_s = ttl_s
        self._items: OrderedDict[str, tuple[float, SessionResolution]] = OrderedDict()
        # The cache is a module-level singleton shared by every shard thread;
        # OrderedDict is not thread-safe, so all mutations happen under a lock
        # (D4 fix: no concurrent get/put/move_to_end/popitem on the same dict).
        self._lock = threading.Lock()

    def get(self, session_id: str) -> SessionResolution | None:
        with self._lock:
            item = self._items.get(session_id)
            if item is None:
                return None
            inserted_at, resolution = item
            if time.monotonic() - inserted_at > self._ttl_s:
                del self._items[session_id]
                return None
            self._items.move_to_end(session_id)
            return resolution

    def put(self, session_id: str, resolution: SessionResolution) -> None:
        with self._lock:
            self._items[session_id] = (time.monotonic(), resolution)
            self._items.move_to_end(session_id)
            while len(self._items) > self._max_size:
                self._items.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


_session_cache = _SessionCache(max_size=SESSION_CACHE_MAX, ttl_s=SESSION_CACHE_TTL_S)


def resolve_session(session_id: str) -> SessionResolution | None:
    """Resolve a WAL session_id to (channel_account_id, tenant_id, engine) via
    the SECURITY DEFINER app.resolve_session(), under system_tx() (no tenant
    context). Returns None for an unknown session (the caller ACKs + skips)."""
    cached = _session_cache.get(session_id)
    if cached is not None:
        return cached
    with system_tx() as conn:
        row = repos_ingest.resolve_session(conn, session_id)
    if row is None:
        return None
    resolution = SessionResolution(
        channel_account_id=row[0], tenant_id=row[1], engine=row[2],
    )
    _session_cache.put(session_id, resolution)
    return resolution


def message_type_for(entry_type: str) -> str:
    """Map an entry type to a storable messages.type. A wholly unknown type is
    stored as 'unsupported' (never silently dropped - same gateway principle)."""
    if entry_type in MESSAGE_TYPES:
        return entry_type
    return "unsupported"
