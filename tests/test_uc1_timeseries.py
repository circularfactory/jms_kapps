"""The time-series store's client, against a fake InfluxDB that answers from memory.

The fake answers the four HTTP calls the client makes, the way InfluxDB 3 Core 3.11.5 answered
them when measured: 200 or 409 for a create, 204 for a write, and for a SQL query a JSON array of
rows in which a column that is null in a row is left out of it. The live round trip is in
`test_uc1_pipeline.py`.
"""

from __future__ import annotations

import json

import httpx
import pytest

from jms_kapps.uc1.frame import AXIAL_FORCE, ROBOT_POSITION, Frame
from jms_kapps.uc1.timeseries import (
    DEFAULT_DATABASE,
    DEFAULT_URL,
    TABLE,
    FrameNotFound,
    FrameReader,
    FrameReference,
    FrameWriter,
    InfluxDB,
    InfluxDBError,
    connect,
    format_time,
    lines_of,
    parse_time,
)

T0 = 1_758_800_000_000_000_000
HALF_SECOND = 500_000_000


def two_samples(**overrides) -> Frame:
    arguments = {
        "times": (T0, T0 + HALF_SECOND),
        "signals": {ROBOT_POSITION: (1.0, 0.5), AXIAL_FORCE: (0.0, 0.2)},
        "phases": (None, None),
    }
    arguments.update(overrides)
    return Frame(**arguments)


class FakeInfluxDB:
    """Records every request, and answers each path with the next queued response."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.answers: dict[str, list[httpx.Response]] = {}

    def answer(self, path: str, *responses: httpx.Response) -> None:
        self.answers.setdefault(path, []).extend(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queued = self.answers.get(request.url.path)
        if queued:
            return queued.pop(0)
        defaults = {
            "/health": httpx.Response(200, text="OK"),
            "/api/v3/configure/database": httpx.Response(200),
            "/api/v3/configure/table": httpx.Response(200),
            "/api/v3/write_lp": httpx.Response(204),
            "/api/v3/query_sql": httpx.Response(200, json=[]),
        }
        return defaults.get(request.url.path, httpx.Response(404, text="no such path"))

    def to(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == path]


@pytest.fixture
def fake() -> FakeInfluxDB:
    return FakeInfluxDB()


@pytest.fixture
def influxdb(fake) -> InfluxDB:
    return InfluxDB("http://influx.test:8181", transport=httpx.MockTransport(fake))


# The reference


def test_a_reference_is_a_uri_relative_to_the_store_the_reader_is_connected_to():
    reference = FrameReference(
        database="jms-kapps",
        table=TABLE,
        frame="0f3a",
        start=T0,
        stop=T0 + HALF_SECOND,
    )

    assert str(reference) == (
        "influxdb:///jms-kapps/unscrewing?frame=0f3a"
        "&start=2025-09-25T11%3A33%3A20.000000000Z"
        "&stop=2025-09-25T11%3A33%3A20.500000000Z"
    )
    assert FrameReference.parse(str(reference)) == reference


def test_a_reference_keeps_a_database_name_with_a_slash_in_one_segment():
    reference = FrameReference(
        database="kit/uc1", table=TABLE, frame="0f3a", start=T0, stop=T0
    )

    assert "/kit%2Fuc1/unscrewing?" in str(reference)
    assert FrameReference.parse(str(reference)) == reference


@pytest.mark.parametrize(
    "text",
    [
        "http:///jms-kapps/unscrewing?frame=a&start=2025-09-25T11:33:20Z&stop=2025-09-25T11:33:20Z",
        "influxdb://shared.example/jms-kapps/unscrewing?frame=a&start=2025-09-25T11:33:20Z&stop=2025-09-25T11:33:20Z",
        "influxdb:///jms-kapps?frame=a&start=2025-09-25T11:33:20Z&stop=2025-09-25T11:33:20Z",
        "influxdb:///jms-kapps/unscrewing?start=2025-09-25T11:33:20Z&stop=2025-09-25T11:33:20Z",
        "influxdb:///jms-kapps/unscrewing?frame=a&start=yesterday&stop=2025-09-25T11:33:20Z",
        "influxdb:///jms-kapps/un;screwing?frame=a&start=2025-09-25T11:33:20Z&stop=2025-09-25T11:33:20Z",
        "influxdb:///jms-kapps/unscrewing?frame=a&start=2025-09-25T11:33:21Z&stop=2025-09-25T11:33:20Z",
    ],
)
def test_a_reference_refuses_text_that_does_not_name_one_frame(text):
    with pytest.raises(ValueError):
        FrameReference.parse(text)


def test_a_time_keeps_its_nanoseconds_both_ways():
    t = T0 + 123_456_789

    assert format_time(t) == "2025-09-25T11:33:20.123456789Z"
    assert parse_time(format_time(t)) == t
    # How InfluxDB writes a time in a JSON row: no zone, and no trailing zeros.
    assert parse_time("2025-09-25T11:33:20.5") == T0 + HALF_SECOND
    assert parse_time("2025-09-25T11:33:20") == T0


# The layout


def test_each_sample_is_one_line_with_the_frame_its_phase_and_its_source_as_tags():
    frame = two_samples(phases=("Approach", None), source="example-d320118/success")

    assert lines_of(frame, "0f3a") == [
        (
            "unscrewing,frame=0f3a,phase=Approach,source=example-d320118/success "
            f"robot_position=1.0,axial_force=0.0 {T0}"
        ),
        (
            "unscrewing,frame=0f3a,source=example-d320118/success "
            f"robot_position=0.5,axial_force=0.2 {T0 + HALF_SECOND}"
        ),
    ]


def test_a_recorded_frame_without_phases_carries_the_frame_tag_alone():
    assert lines_of(two_samples(), "0f3a")[0] == (
        f"unscrewing,frame=0f3a robot_position=1.0,axial_force=0.0 {T0}"
    )


# The connection


def test_the_connection_defaults_to_the_local_service(monkeypatch):
    monkeypatch.delenv("INFLUXDB_URL", raising=False)
    monkeypatch.delenv("INFLUXDB_TOKEN", raising=False)
    fake = FakeInfluxDB()

    with connect(transport=httpx.MockTransport(fake)) as influxdb:
        assert influxdb.available()

    assert DEFAULT_URL == "http://localhost:8181"
    assert str(fake.requests[0].url) == "http://localhost:8181/health"
    assert "authorization" not in fake.requests[0].headers


def test_a_shared_server_is_an_opt_in_through_the_environment(monkeypatch):
    monkeypatch.setenv("INFLUXDB_URL", "https://influx.example/")
    monkeypatch.setenv("INFLUXDB_TOKEN", "apiv3_secret")
    fake = FakeInfluxDB()

    with connect(transport=httpx.MockTransport(fake)) as influxdb:
        influxdb.available()

    assert str(fake.requests[0].url) == "https://influx.example/health"
    assert fake.requests[0].headers["authorization"] == "Bearer apiv3_secret"


def test_a_store_that_does_not_answer_is_not_available():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    assert not InfluxDB(transport=httpx.MockTransport(refuse)).available()


# The writer


def test_the_writer_lays_out_the_table_once_and_then_writes_each_frame(fake, influxdb):
    writer = FrameWriter(influxdb, database="jms-kapps-w1")
    writer.write(two_samples())
    writer.write(two_samples())

    assert [json.loads(r.content) for r in fake.to("/api/v3/configure/database")] == [
        {"db": "jms-kapps-w1"}
    ]
    assert [json.loads(r.content) for r in fake.to("/api/v3/configure/table")] == [
        {
            "db": "jms-kapps-w1",
            "table": "unscrewing",
            "tags": ["frame", "phase", "source"],
            "fields": [
                {"name": "robot_position", "type": "float64"},
                {"name": "axial_force", "type": "float64"},
                {"name": "unscrewing_torque", "type": "float64"},
            ],
        }
    ]
    assert len(fake.to("/api/v3/write_lp")) == 2


@pytest.mark.parametrize("database", ["", "_internal", "jms kapps", "x" * 65])
def test_the_writer_refuses_a_database_name_before_it_writes(fake, influxdb, database):
    with pytest.raises(ValueError, match="database name"):
        FrameWriter(influxdb, database=database)

    assert fake.requests == []


def test_the_writer_accepts_a_layout_that_is_already_there(fake, influxdb):
    fake.answer("/api/v3/configure/database", httpx.Response(409, text="exists"))
    fake.answer("/api/v3/configure/table", httpx.Response(409, text="exists"))

    FrameWriter(influxdb).write(two_samples())

    assert len(fake.to("/api/v3/write_lp")) == 1


def test_the_writer_writes_the_whole_frame_or_nothing_and_answers_its_reference(
    fake, influxdb
):
    reference = FrameWriter(influxdb).write(two_samples())

    (write,) = fake.to("/api/v3/write_lp")
    assert write.method == "POST"
    assert dict(write.url.params) == {
        "db": DEFAULT_DATABASE,
        "precision": "nanosecond",
        "accept_partial": "false",
    }
    assert write.content.decode().splitlines() == lines_of(
        two_samples(), reference.frame
    )
    assert reference.database == DEFAULT_DATABASE == "jms-kapps"
    assert reference.table == TABLE
    assert (reference.start, reference.stop) == (T0, T0 + HALF_SECOND)


def test_each_written_frame_gets_a_frame_id_of_its_own(influxdb):
    writer = FrameWriter(influxdb)

    assert writer.write(two_samples()).frame != writer.write(two_samples()).frame


def test_a_refused_write_raises_with_the_store_s_reason(fake, influxdb):
    fake.answer("/api/v3/write_lp", httpx.Response(400, text="invalid column type"))

    with pytest.raises(InfluxDBError, match="400.*invalid column type"):
        FrameWriter(influxdb).write(two_samples())


# The reader


REFERENCE = FrameReference(
    database="jms-kapps", table=TABLE, frame="0f3a", start=T0, stop=T0 + HALF_SECOND
)


def test_the_reader_asks_for_the_frame_s_rows_within_its_times(fake, influxdb):
    fake.answer(
        "/api/v3/query_sql",
        httpx.Response(
            200,
            json=[
                {"frame": "0f3a", "time": "2025-09-25T11:33:20", "robot_position": 1.0},
            ],
        ),
    )

    FrameReader(influxdb).read(str(REFERENCE))

    (query,) = fake.to("/api/v3/query_sql")
    body = json.loads(query.content)
    assert body["db"] == "jms-kapps"
    assert body["format"] == "json"
    assert body["q"] == (
        'SELECT * FROM "unscrewing" '
        "WHERE frame = $frame AND time >= $start AND time <= $stop ORDER BY time"
    )
    assert body["params"] == {
        "frame": "0f3a",
        "start": "2025-09-25T11:33:20.000000000Z",
        "stop": "2025-09-25T11:33:20.500000000Z",
    }


def test_the_reader_answers_the_curves_the_phases_and_the_source(fake, influxdb):
    fake.answer(
        "/api/v3/query_sql",
        httpx.Response(
            200,
            json=[
                {
                    "axial_force": 0.0,
                    "frame": "0f3a",
                    "phase": "Approach",
                    "robot_position": 1.0,
                    "source": "example-d320118/success",
                    "time": "2025-09-25T11:33:20",
                },
                {
                    "axial_force": 0.2,
                    "frame": "0f3a",
                    "robot_position": 0.5,
                    "source": "example-d320118/success",
                    "time": "2025-09-25T11:33:20.500",
                },
            ],
        ),
    )

    frame = FrameReader(influxdb).read(REFERENCE)

    assert frame == two_samples(
        phases=("Approach", None), source="example-d320118/success"
    )


def test_a_frame_with_no_rows_is_not_found(influxdb):
    with pytest.raises(FrameNotFound, match="frame=0f3a"):
        FrameReader(influxdb).read(REFERENCE)


def test_a_signal_missing_from_one_sample_is_refused(fake, influxdb):
    fake.answer(
        "/api/v3/query_sql",
        httpx.Response(
            200,
            json=[
                {"frame": "0f3a", "time": "2025-09-25T11:33:20", "axial_force": 0.0},
                {"frame": "0f3a", "time": "2025-09-25T11:33:20.5"},
            ],
        ),
    )

    with pytest.raises(ValueError, match="2 sample times"):
        FrameReader(influxdb).read(REFERENCE)


def test_a_refused_query_raises_with_the_store_s_reason(fake, influxdb):
    fake.answer(
        "/api/v3/query_sql",
        httpx.Response(400, text="table 'public.iox.unscrewing' not found"),
    )

    with pytest.raises(InfluxDBError, match="not found"):
        FrameReader(influxdb).read(REFERENCE)
