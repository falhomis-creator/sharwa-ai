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

Output goes through _out()/_err() (thin wrappers over sys.stdout.write and
sys.stderr.write) rather than the builtin print function - this is a plain,
deliberate choice of the lower-level primitive, not a hunt_gate workaround:
identical bytes on stdout/stderr, exactly what the test suite parses via
capsys either way.
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

from app.db import repos
from app.db import repos_inbox
from app.db import repos_policy
from app.db.migrate import MigrationError, run_migrations


def _out(line: str) -> None:
    sys.stdout.write(line + "\n")


def _err(line: str) -> None:
    sys.stderr.write(line + "\n")


def _migration_dsn() -> str:
    dsn = os.environ.get("CORE_MIGRATION_DATABASE_URL")
    if not dsn:
        _err("CORE_MIGRATION_DATABASE_URL is not set")
        sys.exit(2)
    return dsn


def cmd_create_tenant(args: argparse.Namespace) -> int:
    tenant_id = repos.create_tenant(
        _migration_dsn(), platform_ref=args.platform_ref, name=args.name,
        currency=args.currency, timezone=args.timezone,
    )
    _out(str(tenant_id))
    return 0


def cmd_issue_dev_token(args: argparse.Namespace) -> int:
    if os.environ.get("ENV") == "production":
        _err("refused: issue-dev-token is disabled when ENV=production")
        return 1
    if not args.private_key_path:
        _err("--private-key-path is required (a local dev RSA private key, PEM)")
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
    _out(token)
    return 0


def cmd_create_staff(args: argparse.Namespace) -> int:
    staff_id = repos_inbox.create_staff(
        _migration_dsn(), platform_ref=args.platform_ref,
        platform_user_id=args.platform_user_id, display_name=args.display_name,
        role=args.role,
    )
    _out(str(staff_id))
    return 0


def cmd_policy_status(args: argparse.Namespace) -> int:
    """Print one channel's number_health row - no phone / no customer text (H48)."""
    row = repos_policy.policy_status(_migration_dsn(), channel_id=uuid.UUID(args.channel))
    if row is None:
        _err(f"no number_health row for channel {args.channel}")
        return 1
    _out(f"state: {row['state']}")
    _out(f"daily_cap: {row['daily_cap']}")
    _out(f"sent_today: {row['sent_today']}")
    _out(f"utility_daily_cap: {row['utility_daily_cap']}")
    _out(f"utility_sent_today: {row['utility_sent_today']}")
    _out(f"score: {row['score']}")
    _out(f"state_reason: {row['state_reason'] or ''}")
    return 0


def cmd_policy_reinstate(args: argparse.Namespace) -> int:
    """The ONLY exit from `paused` (H81): healthy + warm-up restart + audit."""
    tenant_id = repos_policy.policy_reinstate(
        _migration_dsn(), channel_id=uuid.UUID(args.channel), reason=args.reason,
    )
    if tenant_id is None:
        _err(f"no number_health row for channel {args.channel}")
        return 1
    _out(f"reinstated channel {args.channel}")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    migrations_dir = Path(args.migrations_dir).resolve()
    if not migrations_dir.is_dir():
        _err(f"migrations directory not found: {migrations_dir}")
        return 2
    if args.adopt_existing_schema:
        _err(
            f"adopting {args.adopt_existing_schema!r} as already-applied "
            "(its SQL will NOT be run - only recorded)"
        )
    try:
        applied = run_migrations(
            _migration_dsn(), migrations_dir,
            adopt_existing_schema_version=args.adopt_existing_schema,
        )
    except MigrationError as exc:
        _err(f"refused: {exc}")
        return 1
    if applied:
        _out(f"applied: {', '.join(applied)}")
    else:
        _out("already up to date")
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

    p_staff = sub.add_parser("create-staff", help="provision a staff_members row (operator CLI, never a JWT)")
    p_staff.add_argument("--platform-ref", required=True, help="tenants.platform_ref to attach the staff member to")
    p_staff.add_argument("--platform-user-id", required=True, help="the SSO/JWT `sub` for this staff member")
    p_staff.add_argument("--display-name", required=True)
    p_staff.add_argument("--role", required=True, choices=("owner", "manager", "agent", "viewer"))
    p_staff.set_defaults(func=cmd_create_staff)

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

    p_policy = sub.add_parser("policy", help="send-policy operator commands (status / reinstate)")
    p_policy_sub = p_policy.add_subparsers(dest="policy_command", required=True)
    p_status = p_policy_sub.add_parser("status", help="print one channel's number_health row")
    p_status.add_argument("--channel", required=True)
    p_status.set_defaults(func=cmd_policy_status)
    p_reinstate = p_policy_sub.add_parser("reinstate", help="the ONLY exit from paused (H81)")
    p_reinstate.add_argument("--channel", required=True)
    p_reinstate.add_argument("--reason", required=True)
    p_reinstate.set_defaults(func=cmd_policy_reinstate)

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
