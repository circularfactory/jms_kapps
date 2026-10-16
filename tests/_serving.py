"""Serve an app on a real port of this machine for the length of a test, as a run serves it.

A stream passes through the Launcher only where both ends are real servers, and a browser opens a
page only at an address that answers, so these tests serve with uvicorn rather than a test client.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn
from _wait import eventually
from starlette.types import ASGIApp

from jms_kapps.common.process import bind_socket, server_config


@contextmanager
def serving(app: ASGIApp) -> Iterator[str]:
    """Serve `app` on a free port of 127.0.0.1 until the block ends, and give its address."""
    sock = bind_socket("127.0.0.1")
    server = uvicorn.Server(server_config(app))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    eventually(lambda: server.started)
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
