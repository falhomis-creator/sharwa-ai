#!/usr/bin/env python3
"""scripts/mint_admin_token.py - mint a SHORT-LIVED platform_admin RS256 JWT (P4.1).

Run it on the OWNER'S OWN MACHINE, never on the VPS: the private key must not
leave the owner's machine (the VPS only holds the PUBLIC key, JWT_PUBLIC_KEY_PEM).
`app.cli issue-dev-token` is refused when ENV=production by design, so this is
the sanctioned production path until the main Sharwa platform issues tokens.

    pip install pyjwt cryptography
    python scripts/mint_admin_token.py --key sharwa_ops_private.pem \
        --issuer <JWT_ISSUER> --audience <JWT_AUDIENCE> [--tenant ops] [--ttl-s 3600]

The token is printed to stdout only. `tenant` must be the platform_ref of an
EXISTING tenants row (the playbook creates `ops` with `app.cli create-tenant`).
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid

MAX_TTL_S = 8 * 3600  # hard cap: an admin token never outlives a working day


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", required=True, help="path to the RSA PRIVATE key (PEM) - stays on this machine")
    ap.add_argument("--issuer", required=True)
    ap.add_argument("--audience", required=True)
    ap.add_argument("--tenant", default="ops", help="platform_ref of an existing tenant (default: ops)")
    ap.add_argument("--role", default="platform_admin", choices=("platform_admin", "merchant_admin", "staff"))
    ap.add_argument("--sub", default=None)
    ap.add_argument("--kid", default=None)
    ap.add_argument("--ttl-s", type=int, default=3600)
    a = ap.parse_args()

    if not 60 <= a.ttl_s <= MAX_TTL_S:
        print(f"--ttl-s must be between 60 and {MAX_TTL_S}", file=sys.stderr)
        return 2
    try:
        import jwt  # PyJWT
    except ImportError:
        print("missing dependency: pip install pyjwt cryptography", file=sys.stderr)
        return 2
    with open(a.key, encoding="ascii") as f:
        private_key = f.read()
    if "PRIVATE KEY" not in private_key:
        print("--key does not look like a PEM private key", file=sys.stderr)
        return 2

    now = int(time.time())
    claims = {
        "iss": a.issuer, "aud": a.audience, "sub": a.sub or str(uuid.uuid4()),
        "tenant": a.tenant, "role": a.role, "iat": now, "exp": now + a.ttl_s,
    }
    headers = {"kid": a.kid} if a.kid else None
    token = jwt.encode(claims, private_key, algorithm="RS256", headers=headers)
    print(token if isinstance(token, str) else token.decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
