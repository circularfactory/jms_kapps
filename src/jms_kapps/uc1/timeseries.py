"""The time-series store of use case 1: InfluxDB 3, through its HTTP API.

The raw curves of an unscrewing attempt are too many and too fast for the knowledge graph. So
the graph holds the attempt's operation and one reference to its frame, and the curves are here.
The screwing resource writes each frame with a `FrameWriter`, and stores the reference that the
writer gives back on the operation. The detector and the learner read a frame back with a
`FrameReader`, from the reference that they find on an operation.

**The layout.** Each frame is in one table, `unscrewing`, as one row for each sample:

- `time`: the sample time.
- One float column for each signal: `robot_position`, `axial_force`, `unscrewing_torque`. A
  signal that a later frame adds becomes a new column. The earlier frames have no value in it.
- The tag `frame`: an id that the writer gives the frame. It selects the frame's rows.
- The tag `phase`: the sample's phase. A sample without a known phase has no `phase` tag, because
  InfluxDB has no empty tag value. So a frame recorded without phases has the same layout as one
  recorded with them, and a query by phase does not find it. One frame can hold both kinds of
  sample: a legacy mock frame has no phase after the disassembly.
- The tag `source`: only on a replayed frame, and it names the recording.

The writer creates the database and the table with these tags, in this order, before its first
write. So the first frame that a store gets does not decide the layout.

**The reference** is a URI relative to the store that the reader is connected to:
`influxdb:///<database>/<table>?frame=<id>&start=<time>&stop=<time>`. It names no server, as the
graph names no triple store. The times are the frame's first and last sample, in RFC 3339 with
nanoseconds. The reader reads only between them, so a read touches the store's files for those
seconds only, however long the history is.

**The connection** comes from the environment: the server that `INFLUXDB_URL` names, with
`INFLUXDB_TOKEN` if the server asks for a token. Only without `INFLUXDB_URL` does the client
connect to the local service that `compose.yaml` starts, which asks for no token: the local stack
is only the fallback. The database is never read from the environment. The caller names it, and
it defaults to `jms-kapps`, the name of the dataset of these use cases.

Changed on 2026-10-05, after Etienne Hoffmann decided that every demo runs on the configured
store and that the local one is only the fallback; the ticket of that decision, on jms_kapps'
demos on the configured live store (kapps_semantic_middleware 422), asks that no text call the
local stack the default. Which server the client connects to did not change. Until then
the paragraph began: "**The connection** comes from the environment. `INFLUXDB_URL` defaults to
the local service that `compose.yaml` starts, which asks for no token. A shared server is the
opt-in: set `INFLUXDB_URL`, and `INFLUXDB_TOKEN` if the server asks for a token."
"""

from __future__ import annotations

import builtins
import calendar
import os
import re
import time
import uuid
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

import httpx

from jms_kapps.uc1.frame import SIGNALS, Frame

DEFAULT_URL = "http://localhost:8181"
DEFAULT_DATABASE = "jms-kapps"

TABLE = "unscrewing"
FRAME_TAG = "frame"
PHASE_TAG = "phase"
SOURCE_TAG = "source"
# In the order they are most often queried by, which is the order InfluxDB sorts a table's rows by.
TAGS = (FRAME_TAG, PHASE_TAG, SOURCE_TAG)

SCHEME = "influxdb"

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_DATABASE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_/-]{0,63}")
_TIME = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?Z?")


class InfluxDBError(RuntimeError):
    """InfluxDB refused a request. The message holds its status and its reason."""


class FrameNotFound(LookupError):
    """The store holds no row of the frame that a reference names."""


def format_time(nanoseconds: int) -> str:
    """A time in nanoseconds since the Unix epoch, as RFC 3339 in UTC with nine decimals."""
    seconds, fraction = divmod(nanoseconds, 1_000_000_000)
    return (
        time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds)) + f".{fraction:09d}Z"
    )


def parse_time(text: str) -> int:
    """A time in nanoseconds since the Unix epoch, from RFC 3339 in UTC.

    Accepts the times that `format_time` writes, and the times that InfluxDB writes into a JSON
    row: no zone, and a fraction of up to nine digits without its trailing zeros.

    Raises:
        ValueError: If `text` is not such a time.
    """
    match = _TIME.fullmatch(text)
    if match is None:
        raise ValueError(f"{text!r} is not an RFC 3339 time in UTC")
    whole = calendar.timegm(time.strptime(match[1], "%Y-%m-%dT%H:%M:%S"))
    return whole * 1_000_000_000 + int((match[2] or "").ljust(9, "0"))


@dataclass(frozen=True)
class FrameReference:
    """Where one frame is, in the store that the reader is connected to.

    `str()` gives the URI that the graph holds, and `parse()` reads it back.
    """

    database: str
    table: str
    frame: str
    start: int
    stop: int

    def __post_init__(self) -> None:
        if not _DATABASE.fullmatch(self.database):
            raise ValueError(f"{self.database!r} is not an InfluxDB database name")
        if not _NAME.fullmatch(self.table):
            raise ValueError(f"{self.table!r} is not an InfluxDB table name")
        if not self.frame:
            raise ValueError("a reference needs a frame id")
        if self.stop < self.start:
            raise ValueError("a frame cannot stop before it starts")

    def __str__(self) -> str:
        query = urlencode(
            {
                FRAME_TAG: self.frame,
                "start": format_time(self.start),
                "stop": format_time(self.stop),
            }
        )
        return f"{SCHEME}:///{quote(self.database, safe='')}/{self.table}?{query}"

    @classmethod
    def parse(cls, text: str) -> FrameReference:
        """Read a reference back from the URI that `str()` gave.

        Raises:
            ValueError: If `text` does not name one frame in this layout.
        """
        parts = urlsplit(text)
        if parts.scheme != SCHEME or parts.netloc:
            raise ValueError(
                f"{text!r} is not a reference: it must start with `{SCHEME}:///`, and name no "
                "server"
            )
        segments = parts.path.split("/")
        if len(segments) != 3 or segments[0]:
            raise ValueError(
                f"{text!r} is not a reference: its path must be /<database>/<table>"
            )
        query = parse_qs(parts.query, strict_parsing=True)
        try:
            (frame,) = query[FRAME_TAG]
            (start,) = query["start"]
            (stop,) = query["stop"]
        except (KeyError, ValueError):
            raise ValueError(
                f"{text!r} is not a reference: it needs one frame, one start and one stop"
            ) from None
        return cls(
            database=unquote(segments[1]),
            table=segments[2],
            frame=frame,
            start=parse_time(start),
            stop=parse_time(stop),
        )


class InfluxDB:
    """A client of one InfluxDB 3 server, through its HTTP API (`/api/v3/`).

    Args:
        url: The server. Defaults to the local service.
        token: Sent as a bearer token when given. The local service asks for none.
        timeout: Seconds to wait for each request.
        transport: Replaces the network, for tests.
    """

    def __init__(
        self,
        url: str = DEFAULT_URL,
        token: str | None = None,
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.Client(
            base_url=url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    def available(self) -> bool:
        """Whether the server answers its health check."""
        try:
            return self._http.get("/health").status_code == 200
        except httpx.TransportError:
            return False

    def create_database(self, database: str) -> None:
        """Create `database`, unless it exists."""
        self._send(
            "POST", "/api/v3/configure/database", json={"db": database}, exists_ok=True
        )

    def create_table(
        self, database: str, table: str, *, tags: list[str], fields: list[str]
    ) -> None:
        """Create `table` with `tags` in this order and float `fields`, unless it exists."""
        self._send(
            "POST",
            "/api/v3/configure/table",
            json={
                "db": database,
                "table": table,
                "tags": tags,
                "fields": [{"name": name, "type": "float64"} for name in fields],
            },
            exists_ok=True,
        )

    def write_lines(self, database: str, lines: list[str]) -> None:
        """Write line protocol with nanosecond times. One bad line refuses the whole write."""
        self._send(
            "POST",
            "/api/v3/write_lp",
            params={
                "db": database,
                "precision": "nanosecond",
                "accept_partial": "false",
            },
            content="\n".join(lines).encode("utf-8"),
        )

    def query(
        self,
        database: str,
        sql: str,
        params: dict[str, str | float | int | bool | None],
    ) -> list[dict[str, Any]]:
        """The rows that a SQL query answers. A column that is null in a row is not in it."""
        response = self._send(
            "POST",
            "/api/v3/query_sql",
            json={"db": database, "q": sql, "params": params, "format": "json"},
        )
        rows = response.json()
        if not isinstance(rows, list):
            raise InfluxDBError(f"InfluxDB answered a query with {rows!r}, not rows")
        return rows

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: builtins.type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _send(
        self, method: str, path: str, *, exists_ok: bool = False, **kwargs: Any
    ) -> httpx.Response:
        response = self._http.request(method, path, **kwargs)
        if response.is_success or (exists_ok and response.status_code == 409):
            return response
        raise InfluxDBError(
            f"{method} {path} answered {response.status_code}: {response.text.strip()}"
        )


def connect(*, transport: httpx.BaseTransport | None = None) -> InfluxDB:
    """A client of the server that `INFLUXDB_URL` names, or of the local service without it."""
    return InfluxDB(
        os.environ.get("INFLUXDB_URL") or DEFAULT_URL,
        os.environ.get("INFLUXDB_TOKEN") or None,
        transport=transport,
    )


def lines_of(frame: Frame, frame_id: str) -> list[str]:
    """The frame's samples in line protocol: one line for each sample, in time order."""
    lines = []
    for index, moment in enumerate(frame.times):
        tags = [f"{FRAME_TAG}={frame_id}"]
        if frame.phases[index] is not None:
            tags.append(f"{PHASE_TAG}={frame.phases[index]}")
        if frame.source is not None:
            tags.append(f"{SOURCE_TAG}={frame.source}")
        fields = ",".join(
            f"{signal}={values[index]!r}" for signal, values in frame.signals.items()
        )
        lines.append(f"{TABLE},{','.join(tags)} {fields} {moment}")
    return lines


class FrameWriter:
    """Writes frames into one database, for the screwing resource."""

    def __init__(self, influxdb: InfluxDB, database: str = DEFAULT_DATABASE) -> None:
        if not _DATABASE.fullmatch(database):
            raise ValueError(f"{database!r} is not an InfluxDB database name")
        self._influxdb = influxdb
        self._database = database
        self._laid_out = False

    def write(self, frame: Frame) -> FrameReference:
        """Write `frame` under an id of its own, and give back the reference to store.

        Raises:
            InfluxDBError: If the store refuses the layout or the frame. It keeps none of a
                refused frame.
        """
        if not self._laid_out:
            self._influxdb.create_database(self._database)
            self._influxdb.create_table(
                self._database, TABLE, tags=list(TAGS), fields=list(SIGNALS)
            )
            self._laid_out = True
        frame_id = uuid.uuid4().hex
        self._influxdb.write_lines(self._database, lines_of(frame, frame_id))
        return FrameReference(
            database=self._database,
            table=TABLE,
            frame=frame_id,
            start=frame.start,
            stop=frame.stop,
        )


class FrameReader:
    """Reads frames back through their references, for the detector and the learner.

    **One read is one attempt.** A reference names one frame and its time window, which is one
    unscrewing attempt, and the reader reads only within that window. The learner reads each new
    operation's curves through that operation's own reference, never all of history in one query,
    so no query spans more than one attempt. InfluxDB 3 Core limits how many files one query may
    read, about 72 hours of data by default; a read of one attempt stays far below it.
    """

    _COLUMNS = frozenset({"time", *TAGS})

    def __init__(self, influxdb: InfluxDB) -> None:
        self._influxdb = influxdb

    def read(self, reference: FrameReference | str) -> Frame:
        """The frame that `reference` names, as it was written.

        Raises:
            FrameNotFound: If the store holds no row of that frame.
            InfluxDBError: If the store refuses the query, for example for a table it lacks.
            ValueError: If the rows do not make one frame.
        """
        if isinstance(reference, str):
            reference = FrameReference.parse(reference)
        rows = self._influxdb.query(
            reference.database,
            f'SELECT * FROM "{reference.table}" '
            "WHERE frame = $frame AND time >= $start AND time <= $stop ORDER BY time",
            {
                "frame": reference.frame,
                "start": format_time(reference.start),
                "stop": format_time(reference.stop),
            },
        )
        if not rows:
            raise FrameNotFound(f"the store holds no row of {reference}")
        signals: dict[str, list[float]] = {}
        for row in rows:
            for column, value in row.items():
                if column not in self._COLUMNS:
                    signals.setdefault(column, []).append(value)
        sources = {row.get(SOURCE_TAG) for row in rows}
        if len(sources) != 1:
            raise ValueError(
                f"the rows of {reference} name more than one source: {sources}"
            )
        return Frame(
            times=[parse_time(row["time"]) for row in rows],
            signals=signals,
            phases=[row.get(PHASE_TAG) for row in rows],
            source=sources.pop(),
        )
