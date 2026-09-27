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
"""
from __future__ import annotations

import ast
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "core" / "app"
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
    """The four P1.4 catalog globs, as module dotted names."""
    if mod == "app.db.repos_catalog":
        return True
    if mod.startswith("app.workers.catalog"):
        return True
    if mod.startswith("app.api.routes_catalog"):
        return True
    if mod == "app.commerce" or mod.startswith("app.commerce."):
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
    "abc", "argparse", "asyncio", "base64", "collections", "contextlib", "copy",
    "dataclasses", "datetime", "enum", "functools", "hashlib", "hmac",
    "importlib", "inspect", "itertools", "json", "logging", "math", "os",
    "pathlib", "random", "re", "secrets", "shutil", "signal", "socket",
    "string", "sys", "tempfile", "threading", "time", "traceback", "types",
    "typing", "unicodedata", "uuid", "warnings", "weakref",
    # external packages
    "fastapi", "starlette", "pydantic", "pydantic_core", "psycopg",
    "psycopg_pool", "prometheus_client", "jwt", "redis", "httpx", "httpcore",
    "anyio", "uvicorn", "multipart", "h11", "idna", "certifi", "cryptography",
    "annotated_types", "typing_extensions", "yaml",
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


def main() -> int:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"[static_gate {ts}]\n")
    mods = _module_files()
    mod_names = {m: _top_level_names(ast.parse(p.read_text(encoding="utf-8"))) for m, p in mods.items()}

    # ---- S1: name resolution (every mod.attr read/call/assign - S1-a) ------
    for mod, path in mods.items():
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
            if target not in mod_names:
                # S1-b: an unresolved module target is a violation, never a silent skip.
                if _is_allowlisted(target):
                    continue
                # `from app.config import Settings` aliases a NAME (a class), not a
                # module - `Settings.load` is class/instance access, not module access.
                # Distinguish it from a genuinely unresolved module by resolving the
                # last segment against its parent module's real top-level names.
                if "." in target:
                    parent, _, last = target.rpartition(".")
                    if parent in mod_names:
                        parent_defined, parent_imported = mod_names[parent]
                        if last in parent_defined or last in parent_imported:
                            continue
                _err("S1", rel, getattr(node, "lineno", 0),
                     f"'{alias}.{node.attr}' - unresolved module target '{target}'")
                continue
            defined, imported = mod_names[target]
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
    #    (the enforcement point), or a module whose every call is origin="human".
    for mod, path in mods.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = _insert_outbox_calls(tree)
        if not calls:
            continue
        if mod in _S10_INSERT_OUTBOX_UNCONDITIONAL:
            continue
        if all(_call_origin_human(c) for c in calls):
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
        elif not any(_calls_name(f, "check_text") for f in fns):
            _err("S10", str(v_path.relative_to(ROOT)), 0,
                 "insert_verified_outbox body must call check_text (H45)")
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

