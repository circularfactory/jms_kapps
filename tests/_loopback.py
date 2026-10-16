"""The endpoint a stand-in store keeps, so that the middleware's wipe guard lets its wipes through.

The middleware refuses to clear a dataset, or to reload its shared ontologies, on a store whose
host is neither loopback nor a bare container alias. It reads that host from the endpoint the
interface's own store keeps (`_endpoint.url`), because the store contract declares no URL. A
stand-in without one is refused, so each stand-in that a seed or a reset wipes keeps this one.
"""

from __future__ import annotations

from types import SimpleNamespace

LOOPBACK_ENDPOINT = SimpleNamespace(url="http://localhost:7200")
