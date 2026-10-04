"""core/tests/test_repo_params_ast.py - §5.13 (PURE, no DB): no public function
in repos_scheduler.py / repos_carts.py declares a keyword parameter it never
uses in its body. This is the F-P3-17 trap (cancel_pending_proactive accepted
customer_id and ignored it, cancelling EVERYONE's queue) caught BEFORE it is
born, as a structural rule.

Detection: a parameter "counts" as used when its NAME appears as a whole word
in the function body's unparsed source (covers Name loads, attribute bases
like `settings.cart_item_title_max`, and dict/format references). Only PUBLIC
functions are checked (a leading underscore marks deliberate white-box
helpers); `conn` is exempt - it is the passed-through transaction handle.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_FILES = (
    Path(__file__).resolve().parent.parent / "app" / "db" / "repos_scheduler.py",
    Path(__file__).resolve().parent.parent / "app" / "db" / "repos_carts.py",
    Path(__file__).resolve().parent.parent / "app" / "db" / "repos_consent.py",
)


def _public_functions(tree: ast.Module):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            yield node


def _unused_keyword_params(fn: ast.FunctionDef) -> list[str]:
    body_src = "\n".join(ast.unparse(stmt) for stmt in fn.body)
    unused = []
    for arg in fn.args.kwonlyargs:
        if arg.arg == "conn":
            continue  # the transaction handle is passed through by design
        if not re.search(r"\b" + re.escape(arg.arg) + r"\b", body_src):
            unused.append(arg.arg)
    return unused


@pytest.mark.parametrize("path", REPO_FILES, ids=lambda p: p.name)
def test_no_unused_keyword_parameters(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for fn in _public_functions(tree):
        for unused in _unused_keyword_params(fn):
            violations.append(f"{fn.name}({unused})")
    assert not violations, (
        f"{path.name}: keyword parameters never used in their function body "
        f"(F-P3-17 trap): {violations}"
    )


def test_detection_actually_has_teeth():
    """H7: prove the check fails on a planted violation (the exact F-P3-17
    shape - a customer_id parameter that never appears in the body)."""
    planted = ast.parse(
        "def cancel_pending(conn, *, tenant_id, customer_id, template_ids):\n"
        "    return conn.execute('UPDATE outbox SET status = 1', (tenant_id,))\n"
    ).body[0]
    assert isinstance(planted, ast.FunctionDef)
    unused = _unused_keyword_params(planted)
    assert "customer_id" in unused and "template_ids" in unused


# --- P3.3 §4.12 (PURE/AST): H98 - the consent writer never takes text/phone ---

_BANNED_PARAM_NAMES = ("text", "body", "phone")


def test_repos_consent_has_no_text_body_phone_parameter():
    """H98: evidence is an identifier (message/entry UUID) - the consent
    writer structurally CANNOT receive customer text or a phone number, so it
    can never store one."""
    path = Path(__file__).resolve().parent.parent / "app" / "db" / "repos_consent.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for fn in _public_functions(tree):
        args = list(fn.args.posonlyargs) + list(fn.args.args) + list(fn.args.kwonlyargs)
        for arg in args:
            if arg.arg in _BANNED_PARAM_NAMES:
                violations.append(f"{fn.name}({arg.arg})")
    assert not violations, (
        f"repos_consent.py declares a text/body/phone parameter (H98 trap): {violations}"
    )
