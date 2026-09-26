"""core/app/cli.py - operator CLI: `python -m app.cli <command>`.

create-tenant   Inserts a real row into tenants (via the migration role -
                the same role that owns schema objects; sharwa_app itself has
                no INSERT grant on tenants at all, by design - see schema.sql
                REVOKE). This is deliberately NOT an HTTP endpoint: tenant
                onboarding is an operator/platform action, never something a
                merchant's own JWT can trigger.

issue-dev-token Mints a short-lived RS256 JWT signed with a LOCAL dev keypair
                for manual/local testing against a real tenant. Refuses
                outright when ENV=production (H-safety: a dev-signing key
                must never exist anywhere near a production deployment).

migrate         Runs app.db.migrate.run_migrations() against
                CORE_MIGRATION_DATABASE_URL - the one sanctioned way to apply
                core/migrations/*.sql outside the test suite (H14: forward-only,
                checksum-tracked, advisory-locked).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import jwt as pyjwt
import psycopg

from app.db.migrate import MigrationError, run_migrations


def _migration_dsn() -> str:
    dsn = os.environ.get("CORE_MIGRATION_DATABASE_URL")
    if not dsn:
        print("CORE_MIGRATION_DATABASE_URL is not set", file=sys.stderr)
        sys.exit(2)
    return dsn


def cmd_create_tenant(args: argparse.Namespace) -> int:
    with psycopg.connect(_migration_dsn()) as conn:
        row = conn.execute(
            "INSERT INTO tenants (platform_ref, name, default_currency, timezone) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (args.platform_ref, args.name, args.currency, args.timezone),
        ).fetchone()
        conn.commit()
    if row is None:
        # Unreachable in practice - a successful INSERT ... RETURNING always
        # yields exactly one row - but mypy --strict needs this to narrow
        # fetchone()'s `tuple | None` before row[0] below.
        raise RuntimeError("INSERT ... RETURNING id produced no row")
    print(row[0])
    return 0


def cmd_issue_dev_token(args: argparse.Namespace) -> int:
    if os.environ.get("ENV") == "production":
        print("refused: issue-dev-token is disabled when ENV=production", file=sys.stderr)
        return 1
    if not args.private_key_path:
        print("--private-key-path is required (a local dev RSA private key, PEM)", file=sys.stderr)
        return 2
    with open(args.private_key_path) as f:
        private_key = f.read()

    now = int(time.time())
    claims = {
        "iss": args.issuer,
        "aud": args.audience,
        "sub": args.sub or str(uuid.uuid4()),
        "tenant": args.tenant,
        "role": args.role,
        "iat": now,
        "exp": now + args.ttl_s,
    }
    headers = {"kid": args.kid} if args.kid else None
    token = pyjwt.encode(claims, private_key, algorithm="RS256", headers=headers)
    print(token)
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    migrations_dir = Path(args.migrations_dir).resolve()
    if not migrations_dir.is_dir():
        print(f"migrations directory not found: {migrations_dir}", file=sys.stderr)
        return 2
    if args.adopt_existing_schema:
        print(
            f"adopting {args.adopt_existing_schema!r} as already-applied "
            "(its SQL will NOT be run - only recorded)",
            file=sys.stderr,
        )
    try:
        applied = run_migrations(
            _migration_dsn(), migrations_dir,
            adopt_existing_schema_version=args.adopt_existing_schema,
        )
    except MigrationError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    if applied:
        print(f"applied: {', '.join(applied)}")
    else:
        print("already up to date")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_tenant = sub.add_parser("create-tenant", help="insert a new tenant row")
    p_tenant.add_argument("--platform-ref", required=True)
    p_tenant.add_argument("--name", required=True)
    p_tenant.add_argument("--currency", default="SAR")
    p_tenant.add_argument("--timezone", default="Asia/Riyadh")
    p_tenant.set_defaults(func=cmd_create_tenant)

    p_token = sub.add_parser("issue-dev-token", help="mint a local dev RS256 JWT (refused in production)")
    p_token.add_argument("--private-key-path", required=True)
    p_token.add_argument("--issuer", required=True)
    p_token.add_argument("--audience", required=True)
    p_token.add_argument("--tenant", required=True, help="platform_ref claim value")
    p_token.add_argument("--role", required=True, choices=("merchant_admin", "staff", "platform_admin"))
    p_token.add_argument("--sub", default=None)
    p_token.add_argument("--kid", default=None)
    p_token.add_argument("--ttl-s", type=int, default=3600)
    p_token.set_defaults(func=cmd_issue_dev_token)

    p_migrate = sub.add_parser("migrate", help="apply core/migrations/*.sql (forward-only, idempotent)")
    p_migrate.add_argument("--migrations-dir", default="migrations")
    p_migrate.add_argument(
        "--adopt-existing-schema", default=None, metavar="VERSION",
        help=(
            "one-time adoption: record VERSION (e.g. 0001_baseline) as already "
            "applied WITHOUT running its SQL, because the database already has "
            "that schema from before this migration runner was used on it. "
            "Only has any effect when schema_migrations is still empty."
        ),
    )
    p_migrate.set_defaults(func=cmd_migrate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func: Callable[[argparse.Namespace], int] = args.func
    return func(args)


if __name__ == "__main__":
    raise SystemExit(main())
