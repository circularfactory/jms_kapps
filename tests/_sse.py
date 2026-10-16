"""Hold an activity stream open the way a browser's `EventSource` does.

A middleware instance's `/activity/stream` ends after a short lifetime, so that it never holds a
stopping instance open. A browser reconnects at once and sends the id of the last event it read as
`Last-Event-ID`. The middleware's own tests follow a stream the same way.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable

import httpx


def hold(
    url: str,
    *,
    until: Callable[[], bool],
    opened: threading.Event,
    shown: list[dict] | None = None,
) -> threading.Thread:
    """Keep the stream at `url` open from a daemon thread, as an open activity page does.

    The thread reads the stream, and follows it across its ends, until `until()` is true; it
    checks after every line and before every reconnect. Each record it reads goes into `shown`, if
    given, as the page shows it; otherwise the stream is discarded. A refused or broken connection
    is retried, as a browser retries it. An answer that is no stream, such as the Launcher's 503
    for a process that has stopped, ends the thread, as it makes a browser give up. There is no
    read timeout, as a browser has none, so the thread lets go of a stream only when the stream
    ends or breaks. `opened` is set once a response is in flight.
    """

    def follow() -> None:
        last_event_id = ""
        with httpx.Client(timeout=httpx.Timeout(None, connect=10.0)) as client:
            while not until():
                headers = {"Last-Event-ID": last_event_id} if last_event_id else {}
                try:
                    with client.stream("GET", url, headers=headers) as response:
                        response.raise_for_status()
                        opened.set()
                        for line in response.iter_lines():
                            if line.startswith("id: "):
                                last_event_id = line[len("id: ") :]
                            elif line.startswith("data: ") and shown is not None:
                                shown.append(json.loads(line[len("data: ") :]))
                            if until():
                                return
                except httpx.TransportError:
                    time.sleep(0.05)
                except httpx.HTTPStatusError:
                    return

    thread = threading.Thread(target=follow, daemon=True)
    thread.start()
    return thread
