"""core/app/text - shared, dependency-light text-normalization utilities.

The single Arabic normalizer lives in app/text/arabic.py (P1.4 C6): both the
opt-out detector (app.workers.optout) and the catalog search layer reuse it, so
there is exactly one normalization implementation in the project instead of two
that drift apart.
"""
