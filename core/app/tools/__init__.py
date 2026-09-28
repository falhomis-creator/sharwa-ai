"""core/app/tools - the closed tool layer (P1.7, H50).

A tool is a pure function: it receives a frozen ToolContext (values read in
advance + an injected CommercePort) and returns a frozen result. NO module under
app/tools/ may import app.db/psycopg/httpx/redis/app.llm/app.channels, nor call
.execute() (enforced structurally by S11-a/b). The coordinator
(app/workers/orders.py) is the ONLY reader/writer.
"""
