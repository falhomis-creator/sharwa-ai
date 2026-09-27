"""core/app/text/redact.py - shared phone masking (H41).

`mask_phones` moved here from `app/llm/router.py` (P1.6 C6, the same pattern as
P1.4 C6) so the output verifier can mask a draft excerpt before writing it to
`verifier_blocks` WITHOUT importing `app.llm` (H45: the verifier layer may not
import `app.llm`). `router.py` re-exports it so every existing call site and
test stays green untouched (H21).
"""
from __future__ import annotations

import re

# H41: any run of 7+ digits becomes ***<last-3> - only the last 3 digits survive.
_PHONE_RE = re.compile(r"\d{7,}")


def mask_phones(text: str) -> str:
    """H41: every run of 7+ digits becomes ***<last-3> - only the last 3 digits
    survive. Called on every message before it reaches the provider, and on any
    draft excerpt before it is written to verifier_blocks."""
    return _PHONE_RE.sub(lambda m: "***" + m.group(0)[-3:], text)
