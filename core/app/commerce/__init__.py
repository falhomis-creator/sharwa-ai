"""core/app/commerce - the platform (sharwa_saas) integration boundary (C7).

CommercePort (port.py) is the ONE abstract interface the catalog code talks to;
SharwaCommerceAdapter (adapter.py) is its single HTTP implementation, backed by
app.channels.commerce_client (the only module allowed to reach the network,
H24). A FakeCommerce in tests/ implements the same protocol from a JSON file.
"""
