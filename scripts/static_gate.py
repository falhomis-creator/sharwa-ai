#!/usr/bin/env python3
"""scripts/static_gate.py - REAL static verification (no Docker, no network).

The architect's rejection: `py_compile` resolves NO name. This script resolves
names, metrics, labels and alert references - stdlib only. Exit 0 = clean;
exit 1 = violations as `file:line: [phase] detail`.

  S1 - resolve every `module.attr` ACCESS (read/call/assign) in core/app/
       against the REAL definitions of that module (following import aliases).
       An unresolved module target is a violation, never a silent skip (S1-b);
       imported names are reported as re-exports, not definitions (S1-c).
  S2 - every `metrics.X` used in core/app/ must be defined in app/obs/metrics.py,
       AND every family defined there must have >=1 use in production code.
  S3 - every `.labels(...)` call must pass exactly the family's labelnames count.
  S4 - every metric name in ops/prometheus/alerts.yml must be defined.
  S5 - `internal_notes` must never be referenced by a send path: any module in
       the import closure of app.workers.dispatch / app.channels that mentions
       `internal_notes` (in SQL or as a name) is a structural violation (H28).
  S12 - P1.8 (H57/H58): a) `internal_notes` only named in repos_inbox + note
        routes; b) no serialization whitelist names a note key; c) every WS event
        key is a REST field or a documented envelope key (one truth per field).
  S13 - P1.8 (H56): the frozen console API contract (docs/console_api.lock.json)
        must equal the actual @router routes + permissions in app/api/**.
  S14 - P2.1 (F-P1-11 guard): a) a function declaring a non-None return type
        whose every return is bare/None; b) a statement after a terminal
        (return/raise/continue/break) in the same block.
  S15 - P2.1 (H60): every _required("X") key is provided by docker-compose.yml,
        and any key compose reads from .env via ${X:?...} is in .env.example.
  S24 - P3.2 (H87): (a) raw scheduled_jobs write SQL exists ONLY in
        app/db/repos_scheduler.py (app/db/testsupport.py is the declared
        test-seeding exception, like its other raw test SQL); (b) the scheduler
        WRITER functions are called only from the engine, its handler modules,
        the cart-event worker (the webhook's same-tx cancel, H92) - and tests.
  S25 - P3.2 (H90): the JOB_KINDS registry is a key<->handler bijection, every
        handler resolves to a real def under app/workers/, and a scheduler
        handler module never imports GatewayClient nor calls
        repos_outbox.insert_outbox (proactive.py only).
  S26 - P3.2 (H91): in routes_carts, verify_platform_signature runs BEFORE any
        json.loads, and no logging call passes payload/events/items.
  S28 - P3.3 (F-P3-25/H86): in app/db/repos_*.py no SQL execute() parameter is
         a module-level tuple/set/frozenset constant or a literal tuple -
         psycopg adapts a tuple as a RECORD ('malformed array literal' at
         runtime); sequences are passed as an explicit list(...).

  S27 - P3.3 (H95): (a) raw consents INSERT / suppressions INSERT+DELETE
         exists ONLY in app/db/repos_consent.py (testsupport.py is the
         declared seed exception); (b) the consent WRITER functions are called
         only from app/workers/realtime.py, app/workers/stock.py - and tests;
         (c) CONSENT_SOURCES in code == the 0016 CHECK list verbatim.

  S29 - P3.4 (H100): (a) raw marketing_activation(_log) write SQL exists ONLY
         in app/db/repos_marketing.py (testsupport.py the declared seed
         exception); (b) the activation writers are called only from
         app/cli.py + app/workers/marketing.py (rollback) + tests, and
         set_enabled(enabled=True) ONLY from app/cli.py + tests.
"""
from __future__ import annotations

import ast
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "core" / "app"
TESTS = ROOT / "core" / "tests"
METRICS_FILE = APP / "obs" / "metrics.py"
ALERTS_FILE = ROOT / "ops" / "prometheus" / "alerts.yml"

_ALERT_ALLOWED_EXTRA = {
    "up", "process_open_fds", "process_max_fds", "nodejs_heap_size_used_bytes",
    "session_state", "ingest_spool_depth", "dlq_length",
    # P0.6 gateway metrics / redis_exporter metrics referenced by the P0 alerts
    # (not defined in core/app/obs/metrics.py - they live in gateway/src/ or a
    # future redis_exporter).
    "redis_memory_used_bytes", "redis_memory_max_bytes",
    "stream_pending_oldest_seconds",
}

# --- S7 (P1.4): forbidden-field tokens the catalog path must never name ------
# H36: the read model has no cost/wholesale/margin column, and the catalog code
# must not name one. Matching is case-insensitive and TOKEN-scoped (the name
# must stand alone as an identifier, not be a substring of a larger identifier)
# so the mandated metric identifier `catalog_cost_field_seen_total` - which
# COUNTS a forbidden field being seen, per §7 - is not a false positive.
_CATALOG_S7_ASCII_TOKENS = ("cost", "cost_price", "wholesale", "margin", "profit", "purchase_price")
_CATALOG_S7_ARABIC_TOKENS = ("تكلفة", "هامش", "جملة")


def _is_catalog_path(mod: str) -> bool:
    """The four P1.4 catalog globs, plus the P1.7 order-tracking path (S7 §6)."""
    if mod == "app.db.repos_catalog":
        return True
    if mod.startswith("app.workers.catalog"):
        return True
    if mod.startswith("app.api.routes_catalog"):
        return True
    if mod == "app.commerce" or mod.startswith("app.commerce."):
        return True
    if mod.startswith("app.tools"):
        return True
    if mod == "app.workers.orders":
        return True
    if mod == "app.workers.compose":
        return True
    return False


def _catalog_s7_violations(text: str) -> list[str]:
    """Forbidden-field tokens found as standalone identifiers (or Arabic substrings)."""
    found: list[str] = []
    for token in _CATALOG_S7_ASCII_TOKENS:
        pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(token) + r"(?![A-Za-z0-9_])", re.IGNORECASE)
        if pattern.search(text):
            found.append(token)
    for token in _CATALOG_S7_ARABIC_TOKENS:
        if token in text:
            found.append(token)
    return found


# --- S8 (P1.5): H38/H39 - the model only classifies; one consumer; no leak -----
_LLM_MODULE_PREFIX = "app.llm"
# P1.5b: app.llm now has three consumers - the router (turn.py), the batch/query
# embedder (embed.py) and the rolling summarizer (summary.py). All three are
# app/workers/** modules that orchestrate a provider call outside a transaction.
_LLM_CONSUMERS = frozenset({"app.workers.turn", "app.workers.embed", "app.workers.summary"})
_S8_PROVIDER_TOKENS = ("openai", "anthropic", "deepseek", "claude", "gpt")
_S8_CONFIG_FILES = {"app.config", "app.workers.config"}


def _imports_app_llm(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == _LLM_MODULE_PREFIX or a.name.startswith(_LLM_MODULE_PREFIX + "."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == _LLM_MODULE_PREFIX or node.module.startswith(_LLM_MODULE_PREFIX + ".")):
                return True
    return False


def _is_llm_module(mod: str) -> bool:
    return mod in _LLM_CONSUMERS or mod == _LLM_MODULE_PREFIX or mod.startswith(_LLM_MODULE_PREFIX + ".")


def _s8_provider_violations(text: str) -> list[str]:
    found: list[str] = []
    for token in _S8_PROVIDER_TOKENS:
        pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(token) + r"(?![A-Za-z0-9_])", re.IGNORECASE)
        if pattern.search(text):
            found.append(token)
    return found


def _s8_outbox_text_violations(tree: ast.Module) -> int:
    """insert_outbox(... text=<literal> ...) is a raw-text leak (H38)."""
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
        if name != "insert_outbox":
            continue
        for kw in node.keywords:
            if kw.arg == "text" and isinstance(kw.value, (ast.Constant, ast.JoinedStr)):
                count += 1
    return count


# --- S9 (P1.5b): H42/H43/H44 - vector is optimization; summary is context ------
_S9_CATALOG_EMBEDDINGS_ALLOWED = frozenset({"app.db.repos_catalog", "app.workers.embed"})
_S9_EMBED_PROVIDER_NAMES = frozenset({
    "build_embedding_provider", "EmbeddingProvider", "FakeEmbeddingProvider",
    "EmbeddingResult", "EmbeddingProviderError", "EmbeddingTimeoutError",
})
_SUMMARY_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])summary(?![A-Za-z0-9_])")


def _is_embedding_provider_allowed(mod: str) -> bool:
    return mod == "app.workers.embed" or mod == _LLM_MODULE_PREFIX or mod.startswith(_LLM_MODULE_PREFIX + ".")


def _module_mentions_insert_outbox(tree: ast.Module) -> bool:
    """True when a module DEFINES insert_outbox or CALLS it (the send path)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "insert_outbox":
            return True
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
            if name == "insert_outbox":
                return True
    return False


def _s9_embedding_provider_imports(tree: ast.Module) -> list[str]:
    found: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name in _S9_EMBED_PROVIDER_NAMES:
                    found.append(a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name in _S9_EMBED_PROVIDER_NAMES:
                    found.append(a.name)
    return found


# --- S10 (P1.6): H45/H46 - single outbox write point; deterministic verifier ---
_S10_INSERT_OUTBOX_UNCONDITIONAL = frozenset({"app.db.repos_outbox", "app.workers.verify"})
# P3.1 (H46 amended): proactive.py is the third writer, origin='automation' literally.
_S10_INSERT_OUTBOX_AUTOMATION = frozenset({"app.workers.proactive"})
_VERIFY_RULES_ALLOWED_IMPORTS = frozenset({
    "re", "dataclasses", "typing", "enum", "__future__", "app.text.arabic",
})


def _insert_outbox_calls(tree: ast.Module) -> list[ast.Call]:
    """Every insert_outbox CALL (name or attribute), via ast.walk (not re)."""
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
        if name == "insert_outbox":
            calls.append(node)
    return calls


def _call_origin_human(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "origin" and isinstance(kw.value, ast.Constant) and kw.value.value == "human":
            return True
    return False


def _call_origin_automation(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "origin" and isinstance(kw.value, ast.Constant) and kw.value.value == "automation":
            return True
    return False


def _resolved_import_targets(tree: ast.Module, mods: dict[str, Path]) -> list[str]:
    """Resolve each import to a module name: `from app.text import arabic` ->
    `app.text.arabic` (because that submodule exists), `from dataclasses import
    dataclass` -> `dataclasses`, `from __future__ import x` -> `__future__`."""
    targets: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                targets.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            for a in node.names:
                if not base:
                    continue
                joined = f"{base}.{a.name}"
                targets.append(joined if joined in mods else base)
    return targets


def _function_calls(tree: ast.Module, fn_name: str) -> list[ast.FunctionDef]:
    return [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == fn_name]


def _calls_name(node: ast.AST, callee: str) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
            if name == callee:
                return True
    return False


def _call_text_is_verified(call: ast.Call) -> bool:
    """text= must be a .value attribute (approved.value) OR templates.template_text(...)."""
    for kw in call.keywords:
        if kw.arg != "text":
            continue
        v = kw.value
        if isinstance(v, ast.Attribute) and v.attr == "value":
            return True
        if isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr == "template_text":
            return True
    return False


# --- S11 (P1.7): H50 - the tools layer has no direct DB/network reach ----------
_TOOLS_FORBIDDEN_ROOTS = (
    "app.db", "psycopg", "psycopg_pool", "httpx", "redis", "app.llm",
    "app.channels", "app.ws_publish",
)
_SQL_CALL_ATTRS = frozenset({"execute", "executemany", "fetchone", "fetchall", "cursor"})
_S11_REGISTRY_KEYS = frozenset({"track_order"})


def _import_module_names(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                names.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
    return names


def _forbidden_tool_imports(tree: ast.Module) -> list[str]:
    bad: list[str] = []
    for mod in _import_module_names(tree):
        for root in _TOOLS_FORBIDDEN_ROOTS:
            if mod == root or mod.startswith(root + "."):
                bad.append(mod)
                break
    return bad


def _s11_sql_calls(tree: ast.Module) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _SQL_CALL_ATTRS:
            found.append(node.attr)
    return found


def _s11_registry_violations(tree: ast.Module) -> list[str]:
    bad: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "TOOLS":
                if not isinstance(node.value, ast.Dict):
                    bad.append("TOOLS is not a literal dict")
                    continue
                keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
                if len(keys) != len(node.value.keys) or set(keys) != _S11_REGISTRY_KEYS:
                    bad.append(f"TOOLS keys {sorted(keys)} != {sorted(_S11_REGISTRY_KEYS)}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
            if name in ("register", "update", "setdefault"):
                bad.append(f"call to {name}")
        if isinstance(node, ast.Name) and node.id in ("globals", "importlib", "getattr"):
            bad.append(f"use of {node.id}")
    return bad


def _s11d_third_party_callers(mods: dict[str, Path]) -> list[str]:
    bad: list[str] = []
    for mod, path in mods.items():
        if mod in ("app.db.repos_outbox", "app.workers.orders"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
                if name in ("order_lookup_blocked", "insert_order_lookup_attempt"):
                    bad.append(mod)
                    break
    return bad


# --- S12 (P1.8) vocabulary + helpers -----------------------------------------

_S12_NOTE_KEYS = frozenset({"note", "notes", "internal_note", "internal_notes"})
_S12_ALLOWED_INTERNAL_NOTES = frozenset({"app.db.repos_inbox", "app.api.routes_inbox"})
# H58/S12-c: the event-envelope keys that legitimately differ from REST naming -
# an event references a conversation/message by *_id and carries a transition
# `reason`, while REST returns `id` and `handoff_reason`. Documented in the
# contract §4. A WS key outside REST and outside this envelope is two truths.
_S12_ENVELOPE_KEYS = frozenset({"conversation_id", "message_id", "reason"})


def _named_frozenset_strings(tree: ast.Module, name: str) -> set[str]:
    """String elements of the module-level `name = frozenset({...})` assignment."""
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name):
            val = node.value
            if isinstance(val, ast.Call) and val.args and isinstance(val.args[0], ast.Set):
                return {e.value for e in val.args[0].elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            if isinstance(val, ast.Set):
                return {e.value for e in val.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    return set()


def _field_whitelist_strings(tree: ast.Module, skip_assign: str) -> set[str]:
    """String elements of every tuple/list/set literal, skipping the named
    assignment's subtree (so the event whitelist is not double-counted as REST)."""
    found: set[str] = set()

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.Assign):
            if (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == skip_assign):
                return
        for child in ast.iter_child_nodes(node):
            visit(child)
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            for elt in node.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    found.add(elt.value)

    visit(tree)
    return found


def _router_decorator(dec: ast.expr) -> tuple[str | None, str | None]:
    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
        f = dec.func
        if isinstance(f.value, ast.Name) and f.value.id == "router":
            if f.attr in ("get", "post", "put", "delete", "patch") and dec.args:
                first = dec.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    return f.attr.upper(), first.value
    return None, None


def _route_permission(node: ast.FunctionDef) -> str | None:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        f = child.func
        name = f.id if isinstance(f, ast.Name) else None
        if name == "require_permission":
            if child.args and isinstance(child.args[0], ast.Constant) and isinstance(child.args[0].value, str):
                return child.args[0].value
        if name == "rate_limited":
            if len(child.args) >= 2 and isinstance(child.args[1], ast.Constant) and isinstance(child.args[1].value, str):
                return child.args[1].value
    return None


def _console_routes(mods: dict[str, Path]) -> set[tuple[str, str, str]]:
    """(method, path, permission) of every @router.<method> route in app/api/** that
    carries a require_permission(...)/rate_limited(..., ...) dependency."""
    routes: set[tuple[str, str, str]] = set()
    for mod, path in mods.items():
        if not mod.startswith("app.api."):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                method, route_path = _router_decorator(dec)
                if method is None:
                    continue
                perm = _route_permission(node)
                if perm is None:
                    continue
                routes.add((method, route_path, perm))
    return routes


def _defines_router(path: Path) -> bool:
    """True when a module has a top-level `router = ...` assignment."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "router":
                    return True
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "router":
            return True
    return False


def _served_console_paths() -> set[tuple[str, str]] | None:
    """(method, path) actually served by the BUILT app (S13 fix). Builds
    create_app() and reads app.openapi()["paths"], so a router that is declared
    in app/api/** but never mounted disappears from this set - the exact defect
    S13 used to miss. Returns None (skip) when the app cannot be built for want
    of env vars; the skip is recorded explicitly, never a silent pass."""
    try:
        import os as _os
        # create_app() only loads Settings and assembles routers; it never
        # connects. Dummy env values satisfy Settings.load()'s required vars.
        for k, v in {
            "ENV": "test",
            "CORE_DATABASE_URL": "postgresql://gate",
            "CORE_SYSTEM_DATABASE_URL": "postgresql://gate",
            "JWT_ISSUER": "gate", "JWT_AUDIENCE": "gate", "JWT_PUBLIC_KEY_PEM": "gate",
            "REDIS_CACHE_HOST": "gate", "REDIS_CACHE_PORT": "6379", "REDIS_CACHE_PASSWORD": "gate",
            "GATEWAY_BASE_URL": "http://gate", "GATEWAY_API_KEY": "gate",
            "METRICS_TOKEN": "gate", "SSO_LOGIN_URL": "http://gate",
            "PLATFORM_WEBHOOK_SECRET": "gate",
        }.items():
            _os.environ.setdefault(k, v)
        sys.path.insert(0, str(ROOT / "core"))
        from app.main import create_app  # noqa: PLC0415 - deferred: needs env + core on path
        app = create_app()
        served: set[tuple[str, str]] = set()
        for path, methods in (app.openapi().get("paths") or {}).items():
            for m in methods:
                if m.lower() in ("get", "post", "put", "delete", "patch"):
                    served.add((m.upper(), path))
        return served
    except Exception as exc:  # noqa: BLE001 - the skip is recorded below
        _note(f"S13 skipped: could not build app to read OpenAPI ({type(exc).__name__}: {exc})")
        return None


# --- S19 (P2.3): no out-of-transaction lock in the stock path -----------------
_S19_STOCK_MODULES = ("app.workers.stock", "app.db.repos_stock", "app.tools.join_waitlist")
_S19_LOCK_RE = re.compile(r"\block\b")


def _imports_module(tree: ast.Module, name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == name or a.name.startswith(name + "."):
                    return True
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module == name or node.module.startswith(name + "."):
                return True
    return False


def _s20_violations(mods: dict[str, Path]) -> list[tuple[str, int, str]]:
    """S20 (H74): `DELETE FROM tenants` in tests, testsupport or scripts must live
    inside the single cleanup authority (delete_tenant_full). F-P2-10: the match
    is case/whitespace-insensitive and the scope includes scripts/*.py."""
    _s20_re = re.compile(r"(?i)delete\s+from\s+tenants")
    bad: list[tuple[str, int, str]] = []
    for name, path in mods.items():
        # F-P3-14: as_posix() so the path prefix match is OS-independent
        # (Windows' `\\` separator must not silently change the gate's scope).
        rel = path.relative_to(ROOT).as_posix()
        # F-P3-14: this file documents + defines the S20 regex itself, so it must
        # never be scanned (it was reporting its own docstring as a violation).
        if rel == "scripts/static_gate.py":
            continue
        is_target = name == "app.db.testsupport" or rel.startswith("core/tests/") or rel.startswith("scripts/")
        if not is_target:
            continue
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        allowed: list[tuple[int, int]] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "delete_tenant_full":
                allowed.append((node.lineno, node.end_lineno or node.lineno))
        for i, line in enumerate(text.splitlines(), start=1):
            if _s20_re.search(line) and not any(lo <= i <= hi for lo, hi in allowed):
                bad.append((rel, i, "'DELETE FROM tenants' outside delete_tenant_full (H74)"))
    return bad


_S21_ALLOWED_ROOTS = frozenset({"__future__", "dataclasses", "typing", "enum", "datetime", "zoneinfo"})
_S21_FORBIDDEN_TOKENS = (
    ".now()", ".utcnow()", ".today()", ".time()", "import time", "import random",
    "app.db", "psycopg", "httpx", "redis", "app.llm", "open(", "os.environ",
)


def _s21_violations(mods: dict[str, Path]) -> list[tuple[str, int, str]]:
    """S21 (H84): app/policy/** is pure and deterministic - a closed import set,
    no clock reads, no IO, no DB, no network."""
    bad: list[tuple[str, int, str]] = []
    for name, path in mods.items():
        if name != "app.policy" and not name.startswith("app.policy."):
            continue
        rel = str(path.relative_to(ROOT))
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] not in _S21_ALLOWED_ROOTS and not a.name.startswith("app.policy"):
                        bad.append((rel, node.lineno, f"imports {a.name!r} outside the closed set"))
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                if node.module.split(".")[0] not in _S21_ALLOWED_ROOTS and not node.module.startswith("app.policy"):
                    bad.append((rel, node.lineno, f"imports {node.module!r} outside the closed set"))
        for token in _S21_FORBIDDEN_TOKENS:
            if token in text:
                bad.append((rel, 0, f"forbidden token {token!r} (H84: pure, deterministic)"))
    return bad


def _s22_violations(mods: dict[str, Path]) -> list[tuple[str, int, str]]:
    """S22 (P3.1, H46/S10-a amended): the automation writer is single and closed.
    (a) insert_outbox(origin='automation') only from proactive.py; (b) proactive.py
    never declares a `message_class` parameter (the class is derived from the
    catalog); (c) is S10-a (already amended); (d) a `.send(` call only in dispatch.py
    (the single network exit for GatewayClient.send)."""
    bad: list[tuple[str, int, str]] = []
    for mod, path in mods.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # (a)
        for call in _insert_outbox_calls(tree):
            if _call_origin_automation(call) and mod != "app.workers.proactive":
                bad.append((str(path.relative_to(ROOT)), getattr(call, "lineno", 0),
                            "insert_outbox origin='automation' outside proactive.py (S22-a)"))
        # (d)
        if mod != "app.workers.dispatch" and mod != "app.channels.gateway_client":
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "send":
                    bad.append((str(path.relative_to(ROOT)), getattr(node, "lineno", 0),
                                "GatewayClient.send called outside dispatch.py (S22-d)"))
    # (b)
    p = mods.get("app.workers.proactive")
    if p is not None:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg in node.args.args + node.args.kwonlyargs:
                    if arg.arg == "message_class":
                        bad.append((str(p.relative_to(ROOT)), node.lineno,
                                    "proactive.py declares a message_class parameter (S22-b)"))
    return bad


def _s23_violations(mods: dict[str, Path]) -> list[tuple[str, int, str]]:
    """S23 (P3.1): the dispatcher's automation branch must pass through policy_gate
    before any automation send. Declared ceiling: lexical presence - dispatch.py
    references policy_gate.gate, and _send_automation lives inside _dispatch_automation
    which itself calls policy_gate.gate before _send_automation."""
    bad: list[tuple[str, int, str]] = []
    p = mods.get("app.workers.dispatch")
    if p is None:
        return bad
    rel = str(p.relative_to(ROOT))
    text = p.read_text(encoding="utf-8")
    tree = ast.parse(text)
    if "policy_gate.gate" not in text:
        bad.append((rel, 0, "dispatch.py never references policy_gate.gate (S23)"))
        return bad
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_dispatch_automation":
            seg = ast.get_source_segment(text, node) or ""
            if "policy_gate.gate" not in seg:
                bad.append((rel, node.lineno, "_dispatch_automation never calls policy_gate.gate (S23)"))
            if "_send_automation" in seg and "policy_gate.gate" in seg \
                    and seg.index("_send_automation") < seg.index("policy_gate.gate"):
                bad.append((rel, node.lineno, "_send_automation runs before policy_gate.gate (S23)"))
            break
    return bad


# --- S24/S25/S26 (P3.2): scheduler writer, JOB_KINDS registry, cart webhook ---

_SCHEDULER_WRITERS = frozenset({
    "schedule", "reschedule", "cancel", "cancel_by_id", "complete",
    "fail_attempt", "defer", "finish_failed",
})
# H87: the ONLY non-test modules that may CALL the scheduled_jobs writers: the
# engine itself, the handler modules it registers, and the cart-event worker
# (the webhook's same-transaction cancel, H92).
_S24_WRITER_CALLER_ALLOWLIST = frozenset({
    "app.workers.scheduler", "app.workers.cart_reminder", "app.workers.carts",
})
_S24_WRITE_SQL_RE = re.compile(r"(?i)(insert\s+into|update|delete\s+from)\s+scheduled_jobs")


def _s24_violations(mods: dict[str, Path], test_mods: dict[str, Path]) -> list[tuple[str, int, str]]:
    """S24 (H87): one SQL writer, one caller surface."""
    bad: list[tuple[str, int, str]] = []
    everywhere = dict(mods)
    for k, v in test_mods.items():
        everywhere.setdefault(k, v)
    for mod, path in everywhere.items():
        text = path.read_text(encoding="utf-8")
        # (a) raw write SQL only in the single writer module. app.db.testsupport
        # is the DECLARED test-seeding exception (its whole charter is raw test
        # SQL - fast-forwarding/forcing job state - never reachable from
        # production code), exactly like its existing outbox/number_health SQL.
        if (_S24_WRITE_SQL_RE.search(text)
                and mod != "app.db.repos_scheduler"
                and mod != "app.db.testsupport"):
            bad.append((str(path.relative_to(ROOT)), 0,
                        "raw scheduled_jobs write SQL outside app/db/repos_scheduler.py (S24-a/H87)"))
        # (b) writer calls only from the engine / handlers / cart worker (+ tests).
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _SCHEDULER_WRITERS
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "repos_scheduler"):
                allowed = (mod in _S24_WRITER_CALLER_ALLOWLIST
                           or mod == "app.db.repos_scheduler"
                           or mod.startswith("tests."))
                if not allowed:
                    bad.append((str(path.relative_to(ROOT)), getattr(node, "lineno", 0),
                                f"repos_scheduler.{node.func.attr} called outside the engine surface (S24-b/H87)"))
    return bad


# --- S28 (P3.3, F-P3-25/H86): sequences passed to SQL must be list(...) --------


def _s28_sequence_constant_names(tree: ast.Module) -> set[str]:
    """Top-level constants whose value is a tuple/set literal or frozenset(...)."""
    names: set[str] = set()
    for node in tree.body:
        target: ast.Name | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            target, value = node.target, node.value
        if target is None or value is None:
            continue
        if isinstance(value, (ast.Tuple, ast.Set)):
            names.add(target.id)
        elif (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
              and value.func.id == "frozenset"):
            names.add(target.id)
    return names


def _s28_violations(mods: dict[str, Path]) -> list[tuple[str, int, str]]:
    """S28 (F-P3-25): psycopg adapts a Python tuple as a RECORD, never an
    array - `= ANY(%s)` with a tuple dies at runtime with 'malformed array
    literal' (the P3.3 step-zero regression). In app/db/repos_*.py, any
    execute() parameter that IS a module-level tuple/set/frozenset constant
    (by name or attribute) or a literal tuple is a violation; the sequence
    must be passed as an explicit list(...)."""
    bad: list[tuple[str, int, str]] = []
    for mod, path in mods.items():
        if not mod.startswith("app.db.repos_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        seq_consts = _s28_sequence_constant_names(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "execute"):
                continue
            params = node.args[1] if len(node.args) >= 2 else None
            if not isinstance(params, ast.Tuple):
                continue  # a non-literal parameter list is not statically decidable
            for el in params.elts:
                detail = None
                if isinstance(el, ast.Name) and el.id in seq_consts:
                    detail = f"sequence SQL parameter '{el.id}' is a tuple/set constant - pass list(...) (psycopg adapts a tuple as a record)"
                elif isinstance(el, ast.Attribute) and el.attr in seq_consts:
                    detail = f"sequence SQL parameter '{el.attr}' is a tuple/set constant - pass list(...) (psycopg adapts a tuple as a record)"
                elif isinstance(el, ast.Tuple):
                    detail = "literal tuple inside SQL execute parameters - pass list(...) (psycopg adapts a tuple as a record)"
                if detail is not None:
                    bad.append((str(path.relative_to(ROOT)), getattr(el, "lineno", 0), detail))
    return bad


# --- S27 (P3.3, H95): single consent/suppression writer -----------------------

_S27_CONSENT_INSERT_RE = re.compile(r"(?i)insert\s+into\s+consents\b")
_S27_SUPPRESSION_WRITE_RE = re.compile(r"(?i)(insert\s+into|delete\s+from)\s+suppressions\b")
# H95: the repos_consent WRITERS (read_history is a reader - the CLI uses it).
_S27_WRITER_FUNCS = frozenset({
    "record_optin", "record_optout", "record_waitlist_join",
    "write_consent", "insert_suppressions",
})
# H95: the ONLY non-test modules that may CALL the consent writers: the
# message-ingest capture (H99) and the waitlist join (H50/OQ-P3-12).
_S27_WRITER_CALLER_ALLOWLIST = frozenset({
    "app.workers.realtime",
    "app.workers.stock",
})
_S27_MIGRATION_FILE = ROOT / "core" / "migrations" / "0016_p3_consent.sql"
_S27_CHECK_NAME = "consents_source_closed_list"


def _s27_sources_in_code(mods: dict[str, Path]) -> list[str] | None:
    """The CONSENT_SOURCES string tuple in app/db/repos_consent.py (None when
    the module or the constant is missing/not a string tuple)."""
    p = mods.get("app.db.repos_consent")
    if p is None:
        return None
    tree = ast.parse(p.read_text(encoding="utf-8"))
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.target is not None:
            targets = [node.target]
        if not any(isinstance(t, ast.Name) and t.id == "CONSENT_SOURCES" for t in targets):
            continue
        if isinstance(node.value, ast.Tuple):
            return [e.value for e in node.value.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        return None
    return None


def _s27_sources_in_migration() -> list[str] | None:
    """The closed list inside the 0016 CHECK constraint (None when the
    migration or the constraint text is missing)."""
    text = _S27_MIGRATION_FILE.read_text(encoding="utf-8")
    m = re.search(re.escape(_S27_CHECK_NAME) + r"[^\n]*CHECK\s*\(source\s+IN\s*\(([^)]*)\)", text)
    if m is None:
        return None
    return re.findall(r"'([^']+)'", m.group(1))


def _s27_violations(mods: dict[str, Path], test_mods: dict[str, Path]) -> list[tuple[str, int, str]]:
    """S27 (H95): one consent/suppression writer, one caller surface, and the
    code's closed source list identical to the 0016 constraint."""
    bad: list[tuple[str, int, str]] = []
    everywhere = dict(mods)
    everywhere.update(test_mods)
    for mod, path in everywhere.items():
        # app/db/testsupport.py is the declared test-seeding exception (S24-a
        # style): its charter is raw fixture SQL, exactly like its existing
        # outbox/number_health/scheduled_jobs seeds.
        if mod in ("app.db.repos_consent", "app.db.testsupport"):
            continue
        text = path.read_text(encoding="utf-8")
        if _S27_CONSENT_INSERT_RE.search(text):
            bad.append((str(path.relative_to(ROOT)), 0,
                        "raw consents INSERT outside app/db/repos_consent.py (S27-a/H95)"))
        if _S27_SUPPRESSION_WRITE_RE.search(text):
            bad.append((str(path.relative_to(ROOT)), 0,
                        "raw suppressions INSERT/DELETE outside app/db/repos_consent.py (S27-a/H95)"))
        tree = ast.parse(text)
        aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _S27_WRITER_FUNCS
                    and isinstance(node.func.value, ast.Name)
                    and (node.func.value.id == "repos_consent"
                         or aliases.get(node.func.value.id) == "app.db.repos_consent")):
                allowed = (mod in _S27_WRITER_CALLER_ALLOWLIST
                           or mod == "app.db.repos_consent"
                           or mod.startswith("tests."))
                if not allowed:
                    bad.append((str(path.relative_to(ROOT)), getattr(node, "lineno", 0),
                                f"repos_consent.{node.func.attr} called outside the consent capture surface (realtime/stock + tests) (S27-b/H95)"))
    # (c) the closed source list in code == the 0016 CHECK list, verbatim.
    code_sources = _s27_sources_in_code(mods)
    sql_sources = _s27_sources_in_migration()
    if code_sources is None:
        bad.append(("core/app/db/repos_consent.py", 0,
                    "CONSENT_SOURCES string tuple not found (S27-c/H95)"))
    elif sql_sources is None:
        bad.append((str(_S27_MIGRATION_FILE.relative_to(ROOT)), 0,
                    f"{_S27_CHECK_NAME} CHECK (source IN (...)) not found in 0016 (S27-c/H95)"))
    elif code_sources != sql_sources:
        bad.append(("core/app/db/repos_consent.py", 0,
                    f"CONSENT_SOURCES != the 0016 constraint list (code={code_sources}, sql={sql_sources}) (S27-c/H95)"))
    return bad


# --- S29 (P3.4, H100): single marketing_activation writer ----------------------

_S29_WRITE_SQL_RE = re.compile(
    r"(?i)(insert\s+into|update|delete\s+from)\s+marketing_activation(_log)?\b"
)
# H100: the only non-test callers of the activation writers. set_enabled(True)
# is even narrower - app/cli.py ONLY (the deliberate human act); disable and
# set_cap are also called by the rollback coordinator app/workers/marketing.py
# (H101 level 2: the same-transaction queue cancel).
_S29_WRITER_FUNCS = frozenset({"set_enabled", "set_cap"})
_S29_ANY_CALLER_ALLOWLIST = frozenset({
    "app.cli",
    "app.workers.marketing",
})
_S29_ENABLE_TRUE_ALLOWLIST = frozenset({"app.cli"})


def _s29_enabled_literal_true(node: ast.Call) -> bool:
    """True when the call passes `enabled=True` (keyword) or a positional
    literal True in the `enabled` slot (3rd positional: conn, tenant_id,
    enabled - keyword-only in real code, both covered)."""
    for kw in node.keywords:
        if kw.arg == "enabled":
            return isinstance(kw.value, ast.Constant) and kw.value.value is True
    if len(node.args) >= 3:
        third = node.args[2]
        return isinstance(third, ast.Constant) and third.value is True
    return False


def _s29_violations(mods: dict[str, Path], test_mods: dict[str, Path]) -> list[tuple[str, int, str]]:
    """S29 (H100): one marketing_activation writer, and enabling is a CLI-only
    human act. (a) raw write SQL on marketing_activation(_log) only in
    app/db/repos_marketing.py (testsupport.py the declared seed exception);
    (b) set_enabled(..., enabled=True, ...) called only from app/cli.py and
    tests - never from a worker, a route, or a migration-facing module."""
    bad: list[tuple[str, int, str]] = []
    everywhere = dict(mods)
    everywhere.update(test_mods)
    for mod, path in everywhere.items():
        if mod in ("app.db.repos_marketing", "app.db.testsupport"):
            continue
        text = path.read_text(encoding="utf-8")
        if _S29_WRITE_SQL_RE.search(text):
            bad.append((str(path.relative_to(ROOT)), 0,
                        "raw marketing_activation(_log) write outside app/db/repos_marketing.py (S29-a/H100)"))
        tree = ast.parse(text)
        aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _S29_WRITER_FUNCS
                    and isinstance(node.func.value, ast.Name)
                    and (node.func.value.id == "repos_marketing"
                         or aliases.get(node.func.value.id) == "app.db.repos_marketing")):
                continue
            if mod.startswith("tests.") or mod == "app.db.repos_marketing":
                continue
            if mod in _S29_ANY_CALLER_ALLOWLIST:
                if _s29_enabled_literal_true(node) and mod not in _S29_ENABLE_TRUE_ALLOWLIST:
                    bad.append((str(path.relative_to(ROOT)), getattr(node, "lineno", 0),
                                "repos_marketing.set_enabled(enabled=True) outside app/cli.py - enabling is a CLI-only human act (S29-b/H100)"))
                continue
            bad.append((str(path.relative_to(ROOT)), getattr(node, "lineno", 0),
                        f"repos_marketing.{node.func.attr} called outside the activation surface (cli/marketing worker + tests) (S29-b/H100)"))
    return bad


def _s25_violations(mods: dict[str, Path]) -> list[tuple[str, int, str]]:
    """S25 (H90): JOB_KINDS key<->handler bijection over REAL defs under
    app/workers/, and handler modules never touch the gateway or the outbox
    writer directly (proactive.py is the only enqueue path)."""
    bad: list[tuple[str, int, str]] = []
    p = mods.get("app.workers.scheduler")
    if p is None:
        return bad
    rel = str(p.relative_to(ROOT))
    text = p.read_text(encoding="utf-8")
    tree = ast.parse(text)
    aliases = _import_aliases(tree)

    keys: list[str] = []
    handlers: list[str] = []
    jobkinds_node = None
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.target is not None:
            targets = [node.target]  # JOB_KINDS: dict[str, JobKind] = {...}
        if any(isinstance(t, ast.Name) and t.id == "JOB_KINDS" for t in targets):
            jobkinds_node = node
            break
    value = getattr(jobkinds_node, "value", None) if jobkinds_node is not None else None
    if value is None or not isinstance(value, ast.Dict):
        bad.append((rel, 0, "scheduler.py must define the closed JOB_KINDS dict (H90)"))
        return bad
    for k, v in zip(value.keys, value.values):
        if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
            bad.append((rel, getattr(k, "lineno", 0), "JOB_KINDS keys must be string literals (H90)"))
            continue
        keys.append(k.value)
        handler_expr = None
        if isinstance(v, ast.Call):
            for kw in v.keywords:
                if kw.arg == "handler":
                    handler_expr = ast.unparse(kw.value)
        elif isinstance(v, ast.Name):
            handler_expr = v.id
        if handler_expr is None:
            bad.append((rel, getattr(v, "lineno", 0),
                        f"JOB_KINDS[{k.value!r}] has no handler= entry (S25-a/H90)"))
            continue
        handlers.append(handler_expr)

    # (a) bijection: every key maps to exactly one handler, none shared/missing.
    if len(set(handlers)) != len(handlers) or len(keys) != len(handlers):
        bad.append((rel, jobkinds_node.lineno,
                    "JOB_KINDS is not a key<->handler bijection (S25-a/H90)"))

    # (b) every handler resolves to a real def under app/workers/, and handler
    # modules never import GatewayClient nor call insert_outbox.
    for handler_expr in sorted(set(handlers)):
        head = handler_expr.split(".")[0]
        if head not in aliases or not aliases[head].startswith("app.workers."):
            bad.append((rel, jobkinds_node.lineno,
                        f"handler {handler_expr!r} does not resolve to an app/workers/ module (S25-b/H90)"))
            continue
        module = aliases[head]
        fn_name = handler_expr.split(".")[-1]
        hpath = mods.get(module)
        if hpath is None:
            bad.append((rel, jobkinds_node.lineno, f"handler module {module!r} not found (S25-b)"))
            continue
        htree = ast.parse(hpath.read_text(encoding="utf-8"))
        defined = {n.name for n in htree.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if fn_name not in defined:
            bad.append((str(hpath.relative_to(ROOT)), 0,
                        f"{module} does not define {fn_name} (S25-b/H90)"))
        htext = hpath.read_text(encoding="utf-8")
        if "GatewayClient" in htext:
            bad.append((str(hpath.relative_to(ROOT)), 0,
                        f"{module} references GatewayClient (S25-b: handlers never send directly)"))
        for n in ast.walk(htree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "insert_outbox"):
                bad.append((str(hpath.relative_to(ROOT)), getattr(n, "lineno", 0),
                            f"{module} calls insert_outbox directly (S25-b: proactive.py only)"))
    return bad


_S26_FORBIDDEN_LOG_KWARGS = frozenset({"payload", "events", "items"})


def _s26_violations(mods: dict[str, Path]) -> list[tuple[str, int, str]]:
    """S26 (H91): signature BEFORE parsing in the cart webhook, and no payload
    ever reaches a log call."""
    bad: list[tuple[str, int, str]] = []
    p = mods.get("app.api.routes_carts")
    if p is None:
        return bad
    rel = str(p.relative_to(ROOT))
    tree = ast.parse(p.read_text(encoding="utf-8"))
    for node in tree.body:
        if not (isinstance(node, ast.AsyncFunctionDef) and node.name == "platform_cart_webhook"):
            continue
        sig_line: int | None = None
        loads_line: int | None = None
        for n in ast.walk(node):
            if not isinstance(n, ast.Call):
                continue
            fn = ast.unparse(n.func)
            if fn.endswith("verify_platform_signature") and sig_line is None:
                sig_line = n.lineno
            if fn == "json.loads" and loads_line is None:
                loads_line = n.lineno
        if sig_line is None:
            bad.append((rel, node.lineno,
                        "platform_cart_webhook never calls verify_platform_signature (S26-a)"))
        elif loads_line is not None and loads_line < sig_line:
            bad.append((rel, node.lineno,
                        "json.loads runs BEFORE verify_platform_signature (S26-a/H91)"))
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        fn = ast.unparse(n.func)
        short = fn.split(".")[-1]
        if fn.endswith("log_event") or short in ("info", "warning", "error", "debug", "exception"):
            for kw in n.keywords:
                if kw.arg in _S26_FORBIDDEN_LOG_KWARGS:
                    bad.append((rel, getattr(n, "lineno", 0),
                                f"logging call passes {kw.arg!r} (S26-b/H91: no payloads in logs)"))
    return bad


def _s19_tx_network_violations(tree: ast.Module) -> list[str]:
    """A commerce/port network call lexically inside a tenant_tx/system_tx block
    in app/workers/stock.py (H40 enforced structurally on the stock path)."""
    bad: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        in_tx = False
        for item in node.items:
            for n in ast.walk(item.context_expr):
                if isinstance(n, ast.Attribute) and n.attr in ("tenant_tx", "system_tx"):
                    in_tx = True
                    break
            if in_tx:
                break
        if not in_tx:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                v = sub.func.value
                if isinstance(v, ast.Name) and v.id in ("commerce", "port"):
                    bad.append("commerce call inside a transaction block (H40)")
                elif isinstance(v, ast.Attribute) and v.attr == "commerce_port":
                    bad.append("commerce call inside a transaction block (H40)")
    return bad


_violations: list[str] = []
_notes: list[str] = []  # informational (S1-c re-exports) - never cause a failure


def _err(phase: str, path: str, line: int, detail: str) -> None:
    _violations.append(f"{path}:{line}: [{phase}] {detail}")


def _note(detail: str) -> None:
    _notes.append(detail)


def _top_level_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Return (defined, imported) top-level names.

    `defined` are the names actually DEFINED here (functions, classes,
    assignments). `imported` are names brought in by imports - they are NOT
    definitions, but they may be legitimate re-exports via __init__.py. A
    resolution that only matches an imported name is reported as a re-export
    (S1-c) rather than silently counted as a definition (which is exactly how
    a masked `from x import missing` error passes).
    """
    defined: set[str] = set()
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defined.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
        elif isinstance(node, (ast.ImportFrom, ast.Import)):
            for a in node.names:
                imported.add(a.asname or a.name.split(".")[0])
    return defined, imported


def _module_files() -> dict[str, Path]:
    """module dotted name (relative to core/) -> path.

    A package's `__init__.py` maps to the package name itself (`app/db/__init__.py`
    -> `app.db`, `app/__init__.py` -> `app`) so import aliases like `from app
    import db` (which resolve to `app.db`) match the same key - otherwise every
    package reference would be an unresolved target (S1-b false positive).
    """
    out: dict[str, Path] = {}
    for p in sorted(APP.rglob("*.py")):
        rel = p.relative_to(APP.parent)  # core/ -> app/db/repos_ingest.py
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        out[".".join(parts)] = p
    return out


def _test_module_files() -> dict[str, Path]:
    """core/tests/** -> tests.* (S16: the S1 name resolver scans tests too)."""
    out: dict[str, Path] = {}
    for p in sorted(TESTS.rglob("*.py")):
        rel = p.relative_to(TESTS.parent)  # core/ -> tests/test_inbox.py
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        out[".".join(parts)] = p
    return out


def _script_module_files() -> dict[str, Path]:
    """scripts/*.py as TOP-LEVEL modules (e.g. `import static_gate`), so the S1
    resolver can check test_console_contract.py's calls into the gate script
    (S16)."""
    out: dict[str, Path] = {}
    for p in sorted((ROOT / "scripts").glob("*.py")):
        out[p.stem] = p
    return out


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    """alias -> module dotted name."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split(".")[0]] = a.name
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            for a in node.names:
                out[a.asname or a.name] = f"{node.module}.{a.name}"
    return out


# S1-b: a written allowlist of stdlib modules + external packages. A module
# target whose root is NOT here and NOT a core/app module is an unresolved
# target violation (never a silent skip). Adding an entry here is a deliberate,
# reviewed declaration that a module is outside core/app's name surface.
_EXTERNAL_ALLOWLIST = frozenset({
    # stdlib
    "abc", "argparse", "ast", "asyncio", "base64", "collections", "contextlib", "copy",
    "dataclasses", "datetime", "enum", "functools", "hashlib", "hmac",
    "importlib", "inspect", "itertools", "json", "logging", "math", "os",
    "pathlib", "random", "re", "secrets", "shutil", "signal", "socket",
    "string", "subprocess", "sys", "tempfile", "threading", "time", "traceback", "types",
    "typing", "unicodedata", "uuid", "warnings", "weakref",
    # external packages
    "fastapi", "starlette", "pydantic", "pydantic_core", "psycopg",
    "psycopg_pool", "prometheus_client", "jwt", "redis", "httpx", "httpcore",
    "anyio", "uvicorn", "multipart", "h11", "idna", "certifi", "cryptography",
    "annotated_types", "typing_extensions", "yaml",
    # test-only packages (S16: core/tests/** is now scanned by S1)
    "pytest", "unittest",
})


def _is_allowlisted(target: str) -> bool:
    return target.split(".")[0] in _EXTERNAL_ALLOWLIST


def _import_targets(tree: ast.Module) -> set[str]:
    """Dotted module names a module imports (both `import a.b` and `from a.b
    import c`). Used only for the S5 import-closure walk."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            out.add(node.module)
            for a in node.names:
                out.add(f"{node.module}.{a.name}")
    return out


S5_ROOTS = ("app.workers.dispatch", "app.channels")


def _send_path_closure(mods: dict[str, Path]) -> set[str]:
    """Transitive import closure (restricted to core/app modules) of the send
    paths (dispatch + channels). A file in this closure is a SEND path for the
    purposes of S5 - internal_notes must never be referenced there."""
    imports: dict[str, set[str]] = {}
    for mod, path in mods.items():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            imports[mod] = set()
            continue
        imports[mod] = {t for t in _import_targets(tree) if t in mods}

    closure: set[str] = set()
    stack = [r for r in S5_ROOTS if r in mods]
    while stack:
        mod = stack.pop()
        if mod in closure:
            continue
        closure.add(mod)
        for dep in imports.get(mod, ()):
            if dep not in closure:
                stack.append(dep)
    return closure


def _labelcount(node: ast.AST) -> int:
    if not isinstance(node, ast.Call):
        return 0
    for kw in node.keywords:
        if kw.arg == "labelnames" and isinstance(kw.value, (ast.List, ast.Tuple)):
            return len(kw.value.elts)
    return 0


# --- S14 (P2.1): a function must be able to return what its signature promises ----
# F-P1-11 lived five batches because no gate asked "can this function actually
# return its declared type?". Two AST checks, near-zero false positives:
#   a) a FunctionDef with a non-None/non-NoReturn return annotation, no `yield`,
#      >=1 explicit return, and EVERY return bare or `return None` => the
#      signature promises a value the body can never produce.
#   b) any statement after a terminal (return/raise/continue/break) in the SAME
#      block => unreachable code (the displaced `return StaffRow(...)`).


def _scoped_nodes(func: ast.AST, kinds: tuple[type, ...]) -> list[ast.AST]:
    """Nodes of `kinds` inside `func`, without descending into nested scopes."""
    out: list[ast.AST] = []

    def walk(node: ast.AST) -> None:
        if node is not func and isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
        ):
            return
        if isinstance(node, kinds):
            out.append(node)
            return
        for child in ast.iter_child_nodes(node):
            walk(child)

    walk(func)
    return out


def _promises_non_none(ann: ast.expr | None) -> bool:
    if ann is None:
        return False
    if isinstance(ann, ast.Constant) and ann.value is None:
        return False
    if isinstance(ann, ast.Name) and ann.id == "NoReturn":
        return False
    return True


def _is_none_return(node: ast.Return) -> bool:
    if node.value is None:
        return True
    if isinstance(node.value, ast.Constant) and node.value.value is None:
        return True
    if isinstance(node.value, ast.Name) and node.value.id == "None":
        return True
    return False


_S14_TERMINALS = (ast.Return, ast.Raise, ast.Continue, ast.Break)


def _s14a_violations(tree: ast.Module) -> list[ast.FunctionDef]:
    bad: list[ast.FunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _promises_non_none(node.returns):
            continue
        if _scoped_nodes(node, (ast.Yield, ast.YieldFrom)):
            continue
        returns = [r for r in _scoped_nodes(node, (ast.Return,)) if isinstance(r, ast.Return)]
        if not returns:
            continue
        if all(_is_none_return(r) for r in returns):
            bad.append(node)
    return bad


def _s14b_violations(tree: ast.Module) -> list[ast.stmt]:
    bad: list[ast.stmt] = []

    def scan(stmts: list[ast.stmt]) -> None:
        terminal = False
        for stmt in stmts:
            if terminal:
                bad.append(stmt)
            if isinstance(stmt, _S14_TERMINALS):
                terminal = True

    for node in ast.walk(tree):
        for attr in ("body", "orelse", "finalbody"):
            seq = getattr(node, attr, None)
            if isinstance(seq, list):
                scan(seq)
        for handler in getattr(node, "handlers", None) or ():
            scan(handler.body)
    return bad


# --- S15 (P2.1, H60): no boot with incomplete config --------------------------


def _config_required_keys(mods: dict[str, Path]) -> set[str]:
    keys: set[str] = set()
    for mod in ("app.config", "app.workers.config"):
        path = mods.get(mod)
        if path is None:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_required"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                keys.add(node.args[0].value)
    return keys


# --- S17 (P2.2): H64/H66/H68 - coordinates never come from the model ----------

_S17_COORD_RE = re.compile(r"\b(?:lat|lng|latitude|longitude|coordinates)\b|ST_MakePoint")
_S17_RESOLVE_ALLOWED_IMPORTS = frozenset({"dataclasses", "typing", "enum", "math", "__future__"})
_S17_DECISIONS = frozenset({"accepted", "confirm_with_customer", "disambiguate", "ask_for_pin", "rejected"})


def _s17b_bad_imports(tree: ast.Module) -> list[str]:
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in _S17_RESOLVE_ALLOWED_IMPORTS:
                    bad.append(a.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module.split(".")[0] not in _S17_RESOLVE_ALLOWED_IMPORTS:
                bad.append(node.module)
    return bad


def _s17c_violations(tree: ast.Module) -> list[str]:
    bad: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fname = None
        if isinstance(node.func, ast.Name):
            fname = node.func.id
        elif isinstance(node.func, ast.Attribute):
            fname = node.func.attr
        if fname not in ("insert_address_resolution", "persist_decision"):
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
        decision = kwargs.get("decision")
        loc = kwargs.get("location")
        loc_is_none = isinstance(loc, ast.Constant) and loc.value is None
        if isinstance(decision, ast.Constant) and isinstance(decision.value, str):
            if decision.value not in _S17_DECISIONS:
                bad.append(f"decision={decision.value!r} outside the closed 5-set")
            if decision.value == "accepted":
                if loc is None or loc_is_none:
                    bad.append("decision='accepted' passed location=None (H64)")
        else:
            # computed decision: a literal None location is never allowed on this
            # write path (an accepted row must always carry a point - F-P2-04).
            if loc_is_none:
                bad.append("location=None passed literally (H64 - accepted must carry a point)")
    return bad


def main() -> int:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"[static_gate {ts}]\n")
    mods = _module_files()
    mod_names = {m: _top_level_names(ast.parse(p.read_text(encoding="utf-8"))) for m, p in mods.items()}
    # S16 (P2.2): the S1 name resolver also scans core/tests/** and the
    # scripts/*.py the tests import (e.g. `import static_gate`), so a test
    # calling a missing name is caught. Other stages keep the app-only `mods`.
    s1_mods = {**mods, **_test_module_files(), **_script_module_files()}
    s1_names = {m: _top_level_names(ast.parse(p.read_text(encoding="utf-8"))) for m, p in s1_mods.items()}

    # ---- S1: name resolution (every mod.attr read/call/assign - S1-a) ------
    for mod, path in s1_mods.items():
        rel = str(path.relative_to(ROOT))
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            _err("S1", rel, e.lineno or 1, f"syntax error: {e.msg}")
            continue
        aliases = _import_aliases(tree)
        seen: set[tuple[str, str]] = set()  # (target, attr) - dedupe re-export notes
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
                continue
            alias = node.value.id
            if alias not in aliases:
                continue
            target = aliases[alias]
            if node.attr.startswith("__") and node.attr.endswith("__"):
                continue  # module dunders (__file__, __name__, ...) are always present
            if target not in s1_names:
                # S1-b: an unresolved module target is a violation, never a silent skip.
                if _is_allowlisted(target):
                    continue
                # `from app.config import Settings` aliases a NAME (a class), not a
                # module - `Settings.load` is class/instance access, not module access.
                # Distinguish it from a genuinely unresolved module by resolving the
                # last segment against its parent module's real top-level names.
                if "." in target:
                    parent, _, last = target.rpartition(".")
                    if parent in s1_names:
                        parent_defined, parent_imported = s1_names[parent]
                        if last in parent_defined or last in parent_imported:
                            continue
                _err("S1", rel, getattr(node, "lineno", 0),
                     f"'{alias}.{node.attr}' - unresolved module target '{target}'")
                continue
            defined, imported = s1_names[target]
            if node.attr in defined:
                continue
            if node.attr in imported:
                # S1-c: a resolution that only matches an imported name is a re-export,
                # not a definition - report it so a masked `from x import missing`
                # never passes silently.
                key = (target, node.attr)
                if key not in seen:
                    seen.add(key)
                    _note(f"re-export definition: '{target}.{node.attr}' is an import, "
                          f"not a real definition (referenced as '{alias}.{node.attr}' in {rel})")
                continue
            _err("S1", rel, getattr(node, "lineno", 0),
                 f"'{alias}.{node.attr}' - '{node.attr}' not defined in {target}")

    # ---- S2: metrics defined <-> used --------------------------------------
    try:
        mtree = ast.parse(METRICS_FILE.read_text(encoding="utf-8"))
    except SyntaxError as e:
        _err("S2", "core/app/obs/metrics.py", e.lineno or 1, f"syntax: {e.msg}")
        mtree = ast.Module(body=[])
    metric_defs: dict[str, int] = {}
    for node in mtree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    metric_defs[t.id] = _labelcount(node.value)

    used: set[str] = set()
    for mod, path in mods.items():
        if path == METRICS_FILE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id == "metrics":
                    if node.attr not in metric_defs:
                        _err("S2", str(path.relative_to(ROOT)), getattr(node, "lineno", 0),
                             f"metrics.{node.attr} used but not defined in app/obs/metrics.py")
                    else:
                        used.add(node.attr)

    for name in metric_defs:
        if name not in used and name != "registry":
            _err("S2", "core/app/obs/metrics.py", 0,
                 f"metric '{name}' defined but never used in production code")

    # ---- S3: labels count ---------------------------------------------------
    for mod, path in mods.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "labels" or not isinstance(node.func.value, ast.Attribute):
                continue
            mv = node.func.value
            if not isinstance(mv.value, ast.Name) or mv.value.id != "metrics":
                continue
            name = mv.attr
            if name in metric_defs:
                want = metric_defs[name]
                got = len(node.args) + len(node.keywords)
                if want != got:
                    _err("S3", str(path.relative_to(ROOT)), node.lineno,
                         f"metrics.{name}.labels(...) takes {want} label(s), got {got}")

    # ---- S4: alerts reference existing metrics ------------------------------
    if ALERTS_FILE.exists():
        text = ALERTS_FILE.read_text(encoding="utf-8")
        # metric-looking names only (suffixes), on `expr:` lines only, with
        # quoted label values stripped - so comments/descriptions/label values
        # are never mistaken for metric names.
        _SUFFIX = (
            r"(?:up|[a-z][a-z0-9_]*(?:_total|_seconds|_depth|_size|_lag|_length|"
            r"_bytes|_fds|_used_bytes|_max_bytes|_up))"
        )
        for line in text.splitlines():
            if "expr:" not in line:
                continue
            cleaned = re.sub(r'"[^"]*"', '""', line)
            for mm in re.finditer(_SUFFIX, cleaned):
                name = mm.group(0)
                if name in metric_defs or name in _ALERT_ALLOWED_EXTRA:
                    continue
                _err("S4", "ops/prometheus/alerts.yml", 0,
                     f"alert expr references metric '{name}' not defined anywhere")

    # ---- S5: internal_notes must never be referenced by a send path ---------
    closure = _send_path_closure(mods)
    for mod in sorted(closure):
        path = mods[mod]
        if "internal_notes" in path.read_text(encoding="utf-8"):
            _err("S5", str(path.relative_to(ROOT)), 0,
                 f"send-path module '{mod}' references 'internal_notes' (H28: structural separation)")

    # ---- S6: every WS send_json/send_text payload must pass through to_frame -
    # H34: no message text/phone/note may leave over a WebSocket. The single
    # filter app/api/ws_frames.py::to_frame enforces the whitelist, so any
    # send_json/send_text whose payload is NOT a to_frame(...) call is a violation.
    for mod, path in mods.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in ("send_json", "send_text"):
                continue
            if not node.args:
                _err("S6", str(path.relative_to(ROOT)), node.lineno,
                     "send_json/send_text without a payload argument")
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id == "to_frame":
                continue
            _err("S6", str(path.relative_to(ROOT)), node.lineno,
                 "send_json/send_text payload must be built by to_frame() (H34 single filter)")

    # ---- S7: the catalog path must never name a forbidden field --------------
    # H36 (PROMPT_P1_04 §4.1): a standalone cost/wholesale/margin token in the
    # catalog read model or code is a structural violation - the read model must
    # not even NAME such a field, let alone store it.
    for mod, path in mods.items():
        if not _is_catalog_path(mod):
            continue
        text = path.read_text(encoding="utf-8")
        for token in _catalog_s7_violations(text):
            _err("S7", str(path.relative_to(ROOT)), 0,
                 f"catalog path '{mod}' names the forbidden field '{token}' (H36)")

    # ---- S8: H38/H39 - the model only classifies; no leak ---------------------
    # rule 1: app.llm is imported ONLY by turn.py / embed.py / summary.py and
    #         app.llm/** (the three orchestration points).
    # rule 2: insert_outbox(... text=<literal> ...) is a raw-text leak (H38).
    # rule 3: a provider name outside app/llm/adapters/ and the config files is
    #         a leaked abstraction.
    for mod, path in mods.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not _is_llm_module(mod) and _imports_app_llm(tree):
            _err("S8", str(path.relative_to(ROOT)), 0,
                 f"'{mod}' imports app.llm (consumers are {sorted(_LLM_CONSUMERS)})")
        if _s8_outbox_text_violations(tree):
            _err("S8", str(path.relative_to(ROOT)), 0,
                 f"'{mod}' calls insert_outbox with a literal text (H38: compose-only)")
        if mod not in _S8_CONFIG_FILES and not mod.startswith("app.llm.adapters"):
            for token in _s8_provider_violations(path.read_text(encoding="utf-8")):
                _err("S8", str(path.relative_to(ROOT)), 0,
                     f"'{mod}' names provider '{token}' outside app/llm/adapters/ and config")

    # ---- S9 (P1.5b): H42/H43/H44 ---------------------------------------------
    # rule 1: `summary` must never be read/written/passed on the send path
    #         (compose.py + any module that defines or calls insert_outbox).
    # rule 2: the embedding provider is importable only from app/llm/** and
    #         app/workers/embed.py.
    # rule 3: catalog_embeddings may be referenced only in repos_catalog.py and
    #         embed.py.
    summary_path_mods: set[str] = set()
    for mod, path in mods.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if mod == "app.workers.compose" or _module_mentions_insert_outbox(tree):
            summary_path_mods.add(mod)
    for mod, path in mods.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        text = path.read_text(encoding="utf-8")
        if mod in summary_path_mods and _SUMMARY_TOKEN_RE.search(text):
            _err("S9", str(path.relative_to(ROOT)), 0,
                 f"'{mod}' mentions 'summary' on the send path (H43: summary is context, not content)")
        if not _is_embedding_provider_allowed(mod):
            for name in _s9_embedding_provider_imports(tree):
                _err("S9", str(path.relative_to(ROOT)), 0,
                     f"'{mod}' imports the embedding provider name '{name}' outside app/llm/** and app/workers/embed.py")
        if mod not in _S9_CATALOG_EMBEDDINGS_ALLOWED and "catalog_embeddings" in text:
            _err("S9", str(path.relative_to(ROOT)), 0,
                 f"'{mod}' references 'catalog_embeddings' outside repos_catalog.py and embed.py")

    # ---- S10 (P1.6): H45/H46 - single outbox write point; deterministic layer ---
    # a: insert_outbox may only be called by repos_outbox (definition), verify.py
    #    (the enforcement point), a module whose every call is origin="human", or
    #    proactive.py whose every call is origin="automation" (P3.1, H46 amended).
    for mod, path in mods.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = _insert_outbox_calls(tree)
        if not calls:
            continue
        if mod in _S10_INSERT_OUTBOX_UNCONDITIONAL:
            continue
        if all(_call_origin_human(c) for c in calls):
            continue
        if mod in _S10_INSERT_OUTBOX_AUTOMATION and all(_call_origin_automation(c) for c in calls):
            continue
        _err("S10", str(path.relative_to(ROOT)), 0,
             f"'{mod}' calls insert_outbox without origin='human' literal "
             "(H46: only verify.py + the human reply path may write outbox)")

    # b: verify_rules imports are closed (H45 - no LLM/IO/DB/random/time).
    vr_path = mods.get("app.workers.verify_rules")
    if vr_path is not None:
        vr_tree = ast.parse(vr_path.read_text(encoding="utf-8"))
        for target in _resolved_import_targets(vr_tree, mods):
            if target not in _VERIFY_RULES_ALLOWED_IMPORTS:
                _err("S10", str(vr_path.relative_to(ROOT)), 0,
                     f"app.workers.verify_rules imports '{target}' outside the closed set "
                     "(H45: no LLM/IO/DB/random/time)")

    # c: verify.insert_verified_outbox exists, calls check_text, <=2 insert_outbox.
    v_path = mods.get("app.workers.verify")
    if v_path is not None:
        v_tree = ast.parse(v_path.read_text(encoding="utf-8"))
        fns = _function_calls(v_tree, "insert_verified_outbox")
        if not fns:
            _err("S10", str(v_path.relative_to(ROOT)), 0,
                 "app.workers.verify must define insert_verified_outbox (H46)")
        elif not any(_calls_name(f, "approve") for f in fns):
            _err("S10", str(v_path.relative_to(ROOT)), 0,
                 "insert_verified_outbox body must call approve (H45 - the single check+approve entry point)")
        n_calls = len(_insert_outbox_calls(v_tree))
        if n_calls > 2:
            _err("S10", str(v_path.relative_to(ROOT)), 0,
                 f"app.workers.verify has {n_calls} insert_outbox calls (max 2: accept + safe template)")

    # d: turn.py must not call insert_outbox (it delegates to verify).
    t_path = mods.get("app.workers.turn")
    if t_path is not None:
        t_tree = ast.parse(t_path.read_text(encoding="utf-8"))
        if _insert_outbox_calls(t_tree):
            _err("S10", str(t_path.relative_to(ROOT)), 0,
                 "app.workers.turn must not call insert_outbox (delegate to verify.insert_verified_outbox)")

    # e: verified-text discipline - every insert_outbox text= in verify.py must be
    #    approved.value or templates.template_text(...) (owner order).
    if v_path is not None:
        v_tree_e = ast.parse(v_path.read_text(encoding="utf-8"))
        for c in _insert_outbox_calls(v_tree_e):
            if not _call_text_is_verified(c):
                _err("S10", str(v_path.relative_to(ROOT)), getattr(c, "lineno", 0),
                     "insert_outbox text= must be approved.value or templates.template_text(...)")

    # ---- S11 (P1.7): H50 - tools layer has no direct DB/network reach ---------
    # a: closed import set for app.tools.*
    for mod, path in mods.items():
        if not (mod == "app.tools" or mod.startswith("app.tools.")):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for target in _forbidden_tool_imports(tree):
            _err("S11", str(path.relative_to(ROOT)), 0,
                 f"'{mod}' imports '{target}' (H50: tools must not import app.db/psycopg/httpx/redis/app.llm/app.channels/app.ws_publish)")

    # b: no SQL in the tools layer
    for mod, path in mods.items():
        if not (mod == "app.tools" or mod.startswith("app.tools.")):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for attr in _s11_sql_calls(tree):
            _err("S11", str(path.relative_to(ROOT)), 0,
                 f"'{mod}' calls .{attr} (H50: no SQL in the tools layer)")

    # c: the registry is closed and literal
    reg_path = mods.get("app.tools.registry")
    if reg_path is not None:
        reg_tree = ast.parse(reg_path.read_text(encoding="utf-8"))
        for detail in _s11_registry_violations(reg_tree):
            _err("S11", str(reg_path.relative_to(ROOT)), 0,
                 f"app.tools.registry: {detail} (closed literal registry)")

    # d: order_lookup_blocked / insert_order_lookup_attempt have one consumer
    for mod in _s11d_third_party_callers(mods):
        _err("S11", str(mods[mod].relative_to(ROOT)), 0,
             f"'{mod}' calls order_lookup_blocked/insert_order_lookup_attempt (only app.workers.orders)")

    # ---- S12 (P1.8, H57/H58): internal-notes isolation on the READ path ---------
    # a: `internal_notes` may only be named in repos_inbox (definition) + note routes.
    for mod, path in mods.items():
        if mod in _S12_ALLOWED_INTERNAL_NOTES:
            continue
        if "internal_notes" in path.read_text(encoding="utf-8"):
            _err("S12", str(path.relative_to(ROOT)), 0,
                 f"'{mod}' references 'internal_notes' (H57: read-path isolation - only "
                 "app.db.repos_inbox and the note routes may name it)")

    # b: no message/conversation serialization whitelist may name a note key.
    for mod in ("app.db.repos_inbox", "app.api.routes_inbox", "app.api.ws_frames"):
        path = mods.get(mod)
        if path is None:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                for elt in node.elts:
                    if (isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                            and elt.value in _S12_NOTE_KEYS):
                        _err("S12", str(path.relative_to(ROOT)), getattr(node, "lineno", 0),
                             f"serialization whitelist in '{mod}' names note key '{elt.value}' (H57)")

    # c (H58): every WS event key must be a REST field or an envelope key.
    inbox_path = mods.get("app.db.repos_inbox")
    if inbox_path is not None:
        inbox_tree = ast.parse(inbox_path.read_text(encoding="utf-8"))
        event_keys = _named_frozenset_strings(inbox_tree, "INBOX_EVENT_ALLOWED_KEYS")
        rest_keys = _field_whitelist_strings(inbox_tree, "INBOX_EVENT_ALLOWED_KEYS")
        routes_path = mods.get("app.api.routes_inbox")
        if routes_path is not None:
            rest_keys |= _field_whitelist_strings(
                ast.parse(routes_path.read_text(encoding="utf-8")), "INBOX_EVENT_ALLOWED_KEYS",
            )
        for key in sorted(event_keys - rest_keys - _S12_ENVELOPE_KEYS):
            _err("S12", str(inbox_path.relative_to(ROOT)), 0,
                 f"WS event key '{key}' is not a REST field nor an envelope key (H58: one truth per field)")

    # ---- S13 (P1.8, H56): the frozen contract is a rule, not an intention -----
    # P2.3 fix: compare the lock against the OpenAPI doc of the BUILT app
    # (create_app().openapi()["paths"]) instead of the @router decorators in
    # source - so an unmounted router becomes a violation of the same tool.
    lock_path = ROOT / "docs" / "console_api.lock.json"
    if not lock_path.exists():
        _err("S13", "docs/console_api.lock.json", 0,
             "missing contract lock - generate it with scripts/generate_console_lock.py")
    else:
        import json as _json
        locked_routes = {
            (r["method"], r["path"], r["permission"])
            for r in _json.loads(lock_path.read_text(encoding="utf-8"))["routes"]
        }
        served = _served_console_paths()
        if served is not None:
            for method, path, perm in sorted(locked_routes):
                if (method, path) not in served:
                    _err("S13", "docs/console_api.lock.json", 0,
                         f"{method} {path} ({perm}) is in the contract but not served by the built app")
            for method, path, perm in sorted(_console_routes(mods)):
                if (method, path) in served and (method, path, perm) not in locked_routes:
                    _err("S13", "docs/console_api.lock.json", 0,
                         f"{method} {path} ({perm}) is served but not frozen in the contract - update the contract")

    # ---- S18 (P2.3): every app/api/** router must be mounted in app.main ------
    # F-P2-03 guard: a module that defines `router` but is never imported AND
    # passed to include_router in main.py is an unmounted router (=> 404s).
    main_path = mods.get("app.main")
    if main_path is not None:
        main_tree = ast.parse(main_path.read_text(encoding="utf-8"))
        aliases = _import_aliases(main_tree)
        mounted = set()
        for node in ast.walk(main_tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "include_router":
                for arg in node.args:
                    if isinstance(arg, ast.Name):
                        mounted.add(arg.id)
        for mod in sorted(m for m in mods if m.startswith("app.api.") and m != "app.api" and _defines_router(mods[m])):
            target = f"{mod}.router"
            if not any(a in mounted for a, t in aliases.items() if t == target):
                _err("S18", str(mods[mod].relative_to(ROOT)), 0,
                     f"'{mod}' defines `router` but it is not mounted in app.main (F-P2-03)")

    # ---- S19 (P2.3, H69/H40): no out-of-transaction lock in the stock path ----
    for mod in _S19_STOCK_MODULES:
        path = mods.get(mod)
        if path is None:
            continue
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        if _imports_module(tree, "redis"):
            _err("S19", str(path.relative_to(ROOT)), 0,
                 f"'{mod}' imports redis (H69: no Redis lock in the stock path)")
        if "setnx" in text or "nx=True" in text:
            _err("S19", str(path.relative_to(ROOT)), 0,
                 f"'{mod}' references setnx/nx=True (H69: no Redis lock in the stock path)")
        if _S19_LOCK_RE.search(text):
            _err("S19", str(path.relative_to(ROOT)), 0,
                 f"'{mod}' references 'lock' (H69: the data row itself is the serialization point)")
        if mod == "app.workers.stock":
            for detail in _s19_tx_network_violations(tree):
                _err("S19", str(path.relative_to(ROOT)), 0, detail)

    # ---- S20 (P2.4, H74): single tenant-cleanup authority --------------------
    s20_mods = {**mods, **_test_module_files(), **_script_module_files()}
    for rel, lineno, detail in _s20_violations(s20_mods):
        _err("S20", rel, lineno, detail)

    # ---- S21 (P3.1, H84): app/policy/** is pure and deterministic -------------
    for rel, lineno, detail in _s21_violations(mods):
        _err("S21", rel, lineno, detail)

    # ---- S22 (P3.1, H46 amended): single automation writer --------------------
    for rel, lineno, detail in _s22_violations(mods):
        _err("S22", rel, lineno, detail)

    # ---- S23 (P3.1): dispatcher <-> policy_gate integration -------------------
    for rel, lineno, detail in _s23_violations(mods):
        _err("S23", rel, lineno, detail)

    # ---- S24 (P3.2, H87): single scheduled_jobs writer + closed caller surface -
    for rel, lineno, detail in _s24_violations(mods, _test_module_files()):
        _err("S24", rel, lineno, detail)

    # ---- S25 (P3.2, H90): JOB_KINDS bijection + clean handler modules ---------
    for rel, lineno, detail in _s25_violations(mods):
        _err("S25", rel, lineno, detail)

    # ---- S26 (P3.2, H91): signature-before-parse + no payloads in logs --------
    for rel, lineno, detail in _s26_violations(mods):
        _err("S26", rel, lineno, detail)

    # ---- S27 (P3.3, H95): single consent/suppression writer -------------------
    for rel, lineno, detail in _s27_violations(mods, _test_module_files()):
        _err("S27", rel, lineno, detail)

    # ---- S29 (P3.4, H100): single marketing_activation writer -----------------
    for rel, lineno, detail in _s29_violations(mods, _test_module_files()):
        _err("S29", rel, lineno, detail)

    # ---- S28 (P3.3, F-P3-25/H86): sequence SQL params must be list(...) -------
    for rel, lineno, detail in _s28_violations(mods):
        _err("S28", rel, lineno, detail)

    # ---- S14 (P2.1): a function must be able to return its promised type -----
    for mod, path in mods.items():
        rel = str(path.relative_to(ROOT))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in _s14a_violations(tree):
            _err("S14", rel, getattr(fn, "lineno", 0),
                 f"{fn.name}() declares a non-None return type but every return is bare/None (F-P1-11 guard)")
        for stmt in _s14b_violations(tree):
            _err("S14", rel, getattr(stmt, "lineno", 0),
                 "unreachable statement after return/raise/continue/break in the same block")

    # ---- S15 (P2.1, H60): every _required key is provided + documented ------
    compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    env_example_text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in sorted(_config_required_keys(mods)):
        if not re.search(r"\b" + re.escape(key) + r"\b", compose_text):
            _err("S15", "docker-compose.yml", 0,
                 f"required env key '{key}' is not provided by docker-compose.yml (H60)")
        elif (re.search(r"\$\{" + re.escape(key) + r":\?", compose_text)
              and not re.search(r"\b" + re.escape(key) + r"\b", env_example_text)):
            _err("S15", ".env.example", 0,
                 f"required env key '{key}' is read from .env via ${{{key}:?...}} but missing from .env.example (H60)")

    # ---- S17 (P2.2): H64/H66/H68 - no coordinates from the model ------------
    # a: app/llm/** must never name a coordinate token or build ST_MakePoint.
    for mod, path in mods.items():
        if not (mod == "app.llm" or mod.startswith("app.llm.")):
            continue
        text = path.read_text(encoding="utf-8")
        for m in _S17_COORD_RE.finditer(text):
            _err("S17", str(path.relative_to(ROOT)), 0,
                 f"'{mod}' names coordinate token '{m.group(0)}' (H64: coordinates never from the model)")
            break
    # b: app.geo.resolve's import set is closed (dataclasses/typing/enum/math/__future__).
    rp = mods.get("app.geo.resolve")
    if rp is not None:
        rtree = ast.parse(rp.read_text(encoding="utf-8"))
        for bad in _s17b_bad_imports(rtree):
            _err("S17", "core/app/geo/resolve.py", 0,
                 f"app.geo.resolve imports '{bad}' (S17-b: closed import set)")
    # c: an accepted address_resolutions write must carry a non-None location.
    for mod, path in mods.items():
        if not mod.startswith("app."):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for detail in _s17c_violations(tree):
            _err("S17", str(path.relative_to(ROOT)), 0,
                 f"'{mod}' writes address_resolutions with {detail}")
    # d: geo_gazetteer writes (tenant_id NULL shared rows) belong in seed only.
    # app.db.testsupport is the test-only sibling (not a runtime path) and seeds
    # test fixtures; it is exempt from the "runtime never writes shared rows" rule.
    for mod, path in mods.items():
        if mod == "app.db.testsupport":
            continue
        if "INSERT INTO geo_gazetteer" in path.read_text(encoding="utf-8"):
            _err("S17", str(path.relative_to(ROOT)), 0,
                 f"'{mod}' writes geo_gazetteer (H68: shared rows are seeded by scripts/seed_gazetteer.py only)")

    # ---- output -------------------------------------------------------------
    if _notes:
        sys.stdout.write("--- detailed notes (informational) ---\n")
        for n in sorted(set(_notes)):
            sys.stdout.write(n + "\n")
    if _violations:
        for v in _violations:
            sys.stderr.write(v + "\n")
        sys.stderr.write(f"STATIC GATE FAILED — {len(_violations)} violation(s).\n")
        return 1
    sys.stdout.write("STATIC GATE PASSED — 0 violations.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

