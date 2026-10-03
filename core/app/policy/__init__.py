"""app.policy - the pure, deterministic send-policy decision layer (P3.1, H84).

Closed import set (S21): __future__, dataclasses, typing, enum, datetime,
zoneinfo, and app.policy.* only. No clock reads, no IO, no DB, no network.
"""
