"""The completion matcher of the measurement harness: from the load's start to the
drain's end it reads the run's ended transports from the graph at the configuration's interval,
and records each read, with its time and how long it took, and the end it saw of each transport.

A clock that moves only when the matcher waits, and a graph whose transports end at given times:
nothing reaches a store.
"""

from __future__ import annotations

import pytest
from _evaluation_fakes import Events, FakeClock
from test_evaluation_bench import CROSS, Bench

from jms_kapps.evaluation.bench import TransportEnd
from jms_kapps.evaluation.configuration import Configuration
from jms_kapps.evaluation.matcher import CompletionMatcher


class EndingGraph:
    """The run's transports, each ended from its time on the clock; a read takes `read_seconds`
    of the clock."""

    def __init__(
        self, clock: FakeClock, ended_at: dict[str, float], read_seconds: float = 0.0
    ) -> None:
        self.clock = clock
        self.ended_at = ended_at
        self.read_seconds = read_seconds
        self.reads: list[float] = []

    def read(self) -> list[TransportEnd]:
        self.reads.append(self.clock.seconds)
        self.clock.seconds += self.read_seconds
        return [
            TransportEnd(transport, "done", "WarehouseManagementSystem1", None)
            for transport, at in self.ended_at.items()
            if at <= self.reads[-1]
        ]


def test_the_matcher_reads_at_its_interval_from_its_start_and_records_each_read():
    clock = FakeClock(Events())
    graph = EndingGraph(clock, {"Box1_transport": 4.0, "Box2_transport": 9.0})
    matcher = CompletionMatcher(graph.read, interval=2.5, clock=clock)

    matcher.wait(3.0)  # before the load's start: it only waits
    matcher.start()
    matcher.wait(10.0)

    assert graph.reads == [5.5, 8.0, 10.5, 13.0]
    record = matcher.record()
    assert record["interval_seconds"] == 2.5
    assert record["read_count"] == 4
    assert [read["at"] for read in record["reads"]] == [
        clock.at_seconds(seconds).isoformat() for seconds in (5.5, 8.0, 10.5, 13.0)
    ]
    assert [read["ended"] for read in record["reads"]] == [1, 1, 2, 2]
    assert record["ends"] == [
        {
            "transport": "Box1_transport",
            "status": "done",
            "place": "WarehouseManagementSystem1",
            "seen_at": clock.at_seconds(5.5).isoformat(),
        },
        {
            "transport": "Box2_transport",
            "status": "done",
            "place": "WarehouseManagementSystem1",
            "seen_at": clock.at_seconds(10.5).isoformat(),
        },
    ]
    assert clock.seconds == 13.0


def test_the_matcher_records_how_long_each_read_took_and_skips_the_reads_a_slow_one_missed():
    clock = FakeClock(Events())
    graph = EndingGraph(clock, {}, read_seconds=0.5)
    matcher = CompletionMatcher(graph.read, interval=2.0, clock=clock)

    matcher.start()
    matcher.wait(4.5)
    graph.read_seconds = 5.0  # the read at 6 s takes longer than two intervals
    matcher.wait(2.0)
    graph.read_seconds = 0.5
    matcher.wait(3.5)

    assert graph.reads == [2.0, 4.0, 6.0, 12.0, 14.0]
    record = matcher.record()
    assert [read["seconds"] for read in record["reads"]] == [0.5, 0.5, 5.0, 0.5, 0.5]
    assert record["read_seconds"] == pytest.approx(7.0)


def test_a_run_s_matcher_reads_at_the_configuration_s_interval_until_the_drain_s_end(
    tmp_path,
):
    """The record holds the interval and each read: the first one interval after the load's
    start, the warm-up's, and the last at the drain's end, after the graph's last read."""
    configuration = Configuration(
        **(vars(CROSS) | {"warm_up_seconds": 10.0, "matcher_interval_seconds": 2.5})
    )
    bench = Bench(tmp_path, open_reads=2)

    record = bench.run(configuration)

    matcher = record["matcher"]
    assert matcher["interval_seconds"] == 2.5
    times = [read["at"] for read in matcher["reads"]]
    clock = bench.clock
    # The warm-up and the window, 70 s from the load's start; then the drain, two reads of the
    # graph 2 s apart, through which the matcher reads at 72.5; a wait of 1 s for the log's last lines, through which
    # it reads at 75; then its last read, at 75.
    expected = [2.5 * n for n in range(1, 29)] + [72.5, 75.0, 75.0]
    assert times == [clock.at_seconds(seconds).isoformat() for seconds in expected]
    assert matcher["read_count"] == len(expected)
    [drained] = [a for a in record["actions"] if a["action"] == "drain ended"]
    assert times[-1] == drained["at"]
    last_graph_read = max(
        at
        for at, event in enumerate(bench.events)
        if event[0] == "read" and event[1] == "transports named"
    )
    assert bench.events[last_graph_read + 1] == (
        "read",
        "ended transports named",
        ("Transport99",),
    )
