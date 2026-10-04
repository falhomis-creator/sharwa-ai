#!/usr/bin/env python3
"""scripts/dev_console.py - P3.5: run the dashboard LOCALLY with demo data.

DEV ONLY. It refuses a non-local database. What it does (all against the DB named by
CORE_MIGRATION_DATABASE_URL, which must already be migrated - `python -m app.cli migrate`):

  1. generates a throwaway RSA keypair under .dev-console/ (gitignored) and mints two
     RS256 tokens signed with it: a platform_admin token and a merchant_admin token;
  2. (re)creates four clearly named DEMO tenants (refs demo-*) with synthetic channels,
     subscribers, carts, sent-message history and opt-outs, so every screen has data.
     All demo tenants start with marketing DISABLED (H100) - you enable them in the UI;
  3. writes .dev-console/env.sh and env.ps1 (the JWT_* variables the API needs) and
     prints how to start it.

No phone number is real (all 9677000... synthetic) and nothing is ever sent: no gateway
is involved. Run from the repo root:  python scripts/dev_console.py
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

import jwt  # noqa: E402
import psycopg  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from app.db import testsupport as ts  # noqa: E402

OUT = ROOT / ".dev-console"
ISSUER, AUDIENCE = "sharwa-dev-console", "sharwa-ai-dev"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _keys() -> tuple[str, str]:
    OUT.mkdir(exist_ok=True)
    priv_path, pub_path = OUT / "private.pem", OUT / "public.pem"
    if not priv_path.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        priv_path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        pub_path.write_bytes(key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return priv_path.read_text(), pub_path.read_text()


def _token(priv: str, *, sub: str, tenant: str, role: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": sub, "tenant": tenant, "role": role,
         "iat": now, "exp": now + 8 * 3600},
        priv, algorithm="RS256", headers={"kid": "dev"},
    )


def _tenant(dsn: str, ref: str, name: str) -> uuid.UUID:
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT id FROM tenants WHERE platform_ref = %s", [ref]).fetchone()
    if row:
        ts.delete_tenant_full(dsn, row[0])
    return ts.insert_tenant_returning_id(dsn, platform_ref=ref, name=name)


def _store(dsn: str, ref: str, name: str, *, channel: bool, warmup_days: int | None, subs: int,
           history: bool, seq: int) -> uuid.UUID:
    tid = _tenant(dsn, ref, name)
    chid = None
    if channel:
        chid = ts.insert_channel_account(
            dsn, tenant_id=tid, type_="whatsapp_baileys", session_id=f"demo-{uuid.uuid4()}",
            status="connected", engine="ai_core")
        started = None if warmup_days is None else (
            datetime.now(timezone.utc) - timedelta(days=warmup_days)).isoformat()
        ts.seed_number_health(dsn, tenant_id=tid, channel_id=chid, warmup_started_at=started)
    customers = []
    for i in range(subs):
        wa = f"9677000{seq}{i:04d}"          # synthetic, never a real number
        cid = ts.insert_customer(dsn, tenant_id=tid, wa_id=wa)
        ts.insert_consent(dsn, tenant_id=tid, customer_id=cid, scope="marketing")
        customers.append(cid)
    if history and chid and customers:
        conv = ts.seed_conversation(dsn, tenant_id=tid, channel_id=chid, customer_id=customers[0],
                                    bot_status="active", epoch=0)
        with psycopg.connect(dsn, autocommit=True) as conn:
            # 14 days of synthetic sends: a few marketing, more utility/service
            for day in range(14):
                for cls, n in (("marketing", (day * 7 + seq) % 4), ("utility", 3 + day % 3), ("service", 5 + day % 4)):
                    for _ in range(n):
                        oid = ts.seed_outbox_row(dsn, tenant_id=tid, channel_id=chid, conversation_id=conv,
                                                 origin="automation" if cls != "service" else "bot",
                                                 message_class=cls, to_wa_id="97770000000",
                                                 expected_epoch=0 if cls == "service" else None, status="sent")
                        conn.execute("UPDATE outbox SET sent_at = clock_timestamp() - make_interval(days => %s, hours => %s) "
                                     "WHERE id = %s", [day, 12 - (n % 12), oid])
            # opt-outs / opt-ins spread over the window
            for k, cid in enumerate(customers[: max(2, subs // 3)]):
                ts.insert_consent(dsn, tenant_id=tid, customer_id=cid, scope="marketing", granted=False,
                                  source="customer_message_optout")
                conn.execute("UPDATE consents SET created_at = clock_timestamp() - make_interval(days => %s) "
                             "WHERE tenant_id = %s AND customer_id = %s AND granted = false", [k * 2 + 1, tid, cid])
    for i, status in enumerate(["open", "open", "open", "recovered", "recovered", "reminded", "expired", "expired", "cleared"]):
        if customers:
            ts.insert_cart(dsn, tenant_id=tid, customer_id=customers[i % len(customers)],
                           platform_cart_id=f"demo-{seq}-{i}", status=status,
                           snapshot={"item_count": 2, "currency": "SAR"})
    return tid


def main() -> int:
    dsn = os.environ.get("CORE_MIGRATION_DATABASE_URL", "")
    if not dsn:
        print("CORE_MIGRATION_DATABASE_URL is not set (and the DB must be migrated).", file=sys.stderr)
        return 2
    host = urlparse(dsn).hostname or ""
    if host not in LOCAL_HOSTS:
        print(f"refusing: database host {host!r} is not local. This script deletes and recreates demo-* tenants.",
              file=sys.stderr)
        return 2
    priv, pub = _keys()
    _tenant(dsn, "demo-platform", "DEMO - المنصّة (حساب المدير)")
    _store(dsn, "demo-store-a", "DEMO - متجر الأناقة (جاهز)", channel=True, warmup_days=10, subs=9, history=True, seq=1)
    _store(dsn, "demo-store-b", "DEMO - متجر بلا قناة", channel=False, warmup_days=None, subs=3, history=False, seq=2)
    _store(dsn, "demo-store-c", "DEMO - متجر إحماء حديث", channel=True, warmup_days=1, subs=5, history=True, seq=3)

    (OUT / "admin.token").write_text(_token(priv, sub="demo-owner", tenant="demo-platform", role="platform_admin"))
    (OUT / "store_a.token").write_text(_token(priv, sub="demo-merchant-a", tenant="demo-store-a", role="merchant_admin"))
    footer = "لإيقاف الرسائل الترويجية أرسل: إيقاف"
    env = {"JWT_ISSUER": ISSUER, "JWT_AUDIENCE": AUDIENCE, "JWT_PUBLIC_KEY_PEM": pub.strip(),
           "MARKETING_FOOTER_AR": footer, "CONSOLE_STATIC_DIR": str(ROOT / "frontend")}
    (OUT / "env.sh").write_text("".join(f"export {k}='{v}'\n" for k, v in env.items()), encoding="utf-8")
    (OUT / "env.ps1").write_text("".join(
        f"$env:{k} = @'\n{v}\n'@\n".replace("\n'@\n", "\n'@\n") for k, v in env.items()), encoding="utf-8")
    print("demo data ready (all four tenants start with marketing DISABLED).")
    print(f"  admin token : {OUT / 'admin.token'}")
    print(f"  store token : {OUT / 'store_a.token'}")
    print(f"  env         : {OUT / 'env.ps1'} (PowerShell)  |  {OUT / 'env.sh'} (bash)")
    print("next: load the env file in the shell that starts the API, then open http://127.0.0.1:8000/console/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
