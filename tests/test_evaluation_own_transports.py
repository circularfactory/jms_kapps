"""The run's own transports (jms_kapps 90): the harness learns them from the
WarehouseManagementSystem's "queued" lines, and every read of its names them.

The synthetic run is part 1's run against the stand-ins of `_evaluation_fakes`: boxes named by a
number and boxes named by a UUID, the prefill's past transports in the fake store, and a spawn
whose origin cannot be told after its create. Nothing reaches a store, a process or a network.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest
from _evaluation_fakes import FakeClock
from kapps_semantic_middleware.vocabulary import OperationStatus
from kapps_triplestore_interface import IRI
from test_evaluation_bench import CROSS, Bench, queued_line, wms_line
from test_evaluation_observation import checked_write

from jms_kapps.evaluation.bench import ParticipantOutputs, TransportEnd
from jms_kapps.evaluation.configuration import Configuration
from jms_kapps.evaluation.own_transports import OwnTransports, queued_transport
from jms_kapps.uc2.run_core import Participant
from jms_kapps.uc2.transport import status_record
from jms_kapps.uc2.vocabulary import (
    FCI_NS,
    WMS,
    local_name,
    module_iri,
    new_box_iri,
    transport_iri,
)
from jms_kapps.uc2.wms import spawned_record

UUID_BOX = "5b0e1c52-9d37-4c8e-a1f4-2f6c3a7d9e10"
UUID_FAILED = "c7a14e03-2b5d-49f6-8e0a-91d3b6f2a4c8"
UUID_PREFILL = "0a9f77d2-6e41-4b13-b8c5-d2e07a31f9b4"

NUMBERED = "Box1001_transport"
UUID_NAMED = f"Box{UUID_BOX}_transport"
FAILED_AFTER_CREATE = f"Box{UUID_FAILED}_transport"
OPEN_ONE = "Box1003_transport"

# What the prefill left in the graph: done, and none of the run's.
PREFILL = [
    TransportEnd("Box7_transport", "done", "Module4", None),
    TransportEnd(f"Box{UUID_PREFILL}_transport", "done", "Module2", None),
]


@pytest.mark.parametrize(
    ("line", "name"),
    [
        (f"WarehouseManagementSystem1: {NUMBERED} is queued", NUMBERED),
        (
            f"INFO:jms_kapps.uc2.wms:WarehouseManagementSystem1: {UUID_NAMED} is queued",
            UUID_NAMED,
        ),
        (queued_line("Box5_transport"), "Box5_transport"),
    ],
)
def test_the_queued_line_names_the_transport_whatever_its_box_is_called(line, name):
    assert queued_transport(line) == name


@pytest.mark.parametrize(
    "line",
    [
        f"WarehouseManagementSystem1: {NUMBERED} is running",
        f"WarehouseManagementSystem1: {NUMBERED} is failed",
        f"Module1: {NUMBERED} is queued",
        "WarehouseManagementSystem1: spawned Box1001 onto Module1, for Module4",
        f"WarehouseManagementSystem1: {NUMBERED} is queued, then more",
        "",
    ],
)
def test_any_other_line_names_no_transport(line):
    assert queued_transport(line) is None


def test_the_transports_are_collected_while_the_log_grows_and_each_is_named_once():
    lines: list[str] = []
    own = OwnTransports(lambda: [lines.pop(0) for _ in range(len(lines))])

    lines += [queued_line(NUMBERED), wms_line("something else")]
    assert own.refresh() == [NUMBERED]
    lines += [queued_line(UUID_NAMED), queued_line(NUMBERED)]
    assert own.refresh() == [UUID_NAMED]
    assert own.names == [NUMBERED, UUID_NAMED]
    assert own.without({NUMBERED}) == [UUID_NAMED]


def test_the_output_gives_only_the_lines_written_since_the_last_call(tmp_path):
    outputs = ParticipantOutputs(tmp_path / "output", lambda line: None)
    participant = Participant(WMS, "wms", ["wms"])
    outputs.add([participant])
    path = tmp_path / "output" / "WarehouseManagementSystem1.log"

    assert outputs.new_lines_of(WMS) == []
    outputs.echo("wms  first")
    outputs.echo("wms  second")
    assert outputs.new_lines_of(WMS) == ["first", "second"]
    assert outputs.new_lines_of(WMS) == []
    # A line whose end has not been written yet is not read half.
    with path.open("a", encoding="utf-8") as file:
        file.write("2026-10-12T00:00:00+00:00  third")
    assert outputs.new_lines_of(WMS) == []
    with path.open("a", encoding="utf-8") as file:
        file.write("\n")
    assert outputs.new_lines_of(WMS) == ["third"]
    outputs.close()


def run_spans(clock: FakeClock) -> dict[str, list[dict]]:
    """Module1 carries the numbered and the UUID box, and ends the UUID one; the
    WarehouseManagementSystem ends the spawn that failed after its create."""
    return {
        "Module1": [
            *checked_write(clock, "Module1", 5.0, NUMBERED, "running", receipt="r1"),
            *checked_write(clock, "Module1", 20.0, NUMBERED, "done", receipt="r2"),
            *checked_write(clock, "Module1", 36.0, UUID_NAMED, "running", receipt="r3"),
            *checked_write(clock, "Module1", 50.0, UUID_NAMED, "done", receipt="r4"),
            *checked_write(clock, "Module1", 70.0, OPEN_ONE, "running", receipt="r5"),
        ],
        "WarehouseManagementSystem1": checked_write(
            clock,
            "WarehouseManagementSystem1",
            60.0,
            FAILED_AFTER_CREATE,
            "failed",
            receipt="r6",
        ),
    }


def synthetic_run(tmp_path: Path, *, queued: list[str] | None = None, **graph):
    ends = [
        *PREFILL,
        TransportEnd(NUMBERED, "done", "WarehouseManagementSystem1", None),
        TransportEnd(UUID_NAMED, "done", "WarehouseManagementSystem1", None),
        TransportEnd(
            FAILED_AFTER_CREATE, "failed", "Module1", "Module4 is not reachable"
        ),
    ]
    bench = Bench(
        tmp_path,
        ends=ends,
        ended_at={NUMBERED: 20.0, UUID_NAMED: 50.0, FAILED_AFTER_CREATE: 60.0},
        # The prefill's transports are in the graph; the log names the run's only. The spawn that
        # failed after its create has its queued line too.
        queued=[NUMBERED, UUID_NAMED, FAILED_AFTER_CREATE, OPEN_ONE]
        if queued is None
        else queued,
        **graph,
    )
    bench.spans = run_spans(bench.clock)
    configuration = Configuration(**(vars(CROSS) | {"warm_up_seconds": 30.0}))
    return bench, bench.run(configuration)


def test_the_harness_finds_exactly_the_run_s_ended_transports_and_none_of_the_prefill_s(
    tmp_path,
):
    _, record = synthetic_run(tmp_path, open_reads=0)

    assert record["outcome"] == "completed"
    transports = record["transports"]
    assert [t["transport"] for t in transports["done"]] == [NUMBERED, UUID_NAMED]
    assert [t["transport"] for t in transports["failed"]] == [FAILED_AFTER_CREATE]
    assert transports["failed"][0]["reason"] == "Module4 is not reachable"
    assert transports["open"] == []
    assert transports["queued_by_the_wms"] == 4
    seen = {end["transport"] for end in record["matcher"]["ends"]}
    assert seen == {NUMBERED, UUID_NAMED, FAILED_AFTER_CREATE}


def test_a_spawn_that_fails_after_its_create_is_found_through_its_queued_line(tmp_path):
    _, record = synthetic_run(tmp_path, open_reads=0)

    assert FAILED_AFTER_CREATE in [
        t["transport"] for t in record["transports"]["failed"]
    ]

    # Without its line the harness cannot know it: the line is the only way in.
    _, without = synthetic_run(
        tmp_path / "again", queued=[NUMBERED, UUID_NAMED, OPEN_ONE], open_reads=0
    )
    assert without["transports"]["failed"] == []


def test_the_validity_checks_hold_on_the_synthetic_run(tmp_path):
    _, record = synthetic_run(tmp_path, open_reads=0)

    # One accepted done or failed for each ended transport, and the matcher saw the graph's ends.
    assert record["failed_checks"] == []
    assert record["valid"] is True


def test_every_read_of_the_run_names_the_run_s_transports_and_none_the_prefill_s(
    tmp_path,
):
    bench, _ = synthetic_run(tmp_path, open_reads=2)

    reads = [e for e in bench.events if e[0] == "read" and "transports" in str(e[1])]
    assert reads, "the run reads the transports"
    run_s = {NUMBERED, UUID_NAMED, FAILED_AFTER_CREATE, OPEN_ONE}
    for kind, what, names in reads:
        assert what in ("transports named", "ended transports named")
        assert names, "a read names transports"
        assert set(names) <= run_s
    # The read at the drain's end, the record's, names every one of them.
    named = [e[2] for e in bench.events if e[:2] == ("read", "transports named")]
    assert set(named[-1]) == run_s
    assert not [e for e in bench.events if e[:2] == ("read", "highest box")]


def test_the_matcher_reads_an_ended_transport_once(tmp_path):
    bench, _ = synthetic_run(tmp_path, open_reads=0)

    matcher_reads = [
        e[2] for e in bench.events if e[:2] == ("read", "ended transports named")
    ]
    # After the first read that saw it ended, a transport is not named again.
    ended = {NUMBERED, UUID_NAMED, FAILED_AFTER_CREATE}
    for before, after in pairwise(matcher_reads):
        assert len(after) <= len(before)
    assert matcher_reads[-1] == (OPEN_ONE,)
    assert ended <= set(matcher_reads[0])


def test_the_record_no_longer_holds_the_highest_box_before_the_window(tmp_path):
    _, record = synthetic_run(tmp_path, open_reads=0)

    assert "highest_box_before_the_window" not in record["transports"]


def test_the_status_record_of_the_wms_is_what_the_harness_reads():
    """The harness reads the line that `status_record` makes; it has no format of its own."""
    line = status_record(WMS, IRI(UUID_NAMED, base=FCI_NS), OperationStatus.QUEUED)

    assert queued_transport(line) == UUID_NAMED


def test_a_synthetic_run_of_boxes_the_wms_names_by_uuid_counts_every_spawn_and_finds_each_transport(
    tmp_path,
):
    """The WarehouseManagementSystem names each box by a new UUID (jms_kapps 92): its own records,
    the box spawned and its transport queued, made by its own formatters, name three such boxes.
    The harness counts the three spawns, finds the three transports in the graph, and none of the
    queued transports is missing there."""
    boxes = [new_box_iri() for _ in range(3)]
    transports = [local_name(transport_iri(box)) for box in boxes]
    origin, destination = module_iri(1), module_iri(4)
    bench = Bench(
        tmp_path,
        ends=[*PREFILL]
        + [
            TransportEnd(transport, "done", "WarehouseManagementSystem1", None)
            for transport in transports
        ],
        open_reads=0,
        queued=transports,
        wms_output=[
            wms_line(spawned_record(WMS, box, origin, destination)) for box in boxes
        ],
    )

    record = bench.run(Configuration(**(vars(CROSS) | {"drain_bound_seconds": 0.0})))

    assert record["spawns"]["spawned"] == 3
    assert record["transports"]["queued_by_the_wms"] == 3
    assert record["transports"]["queued_not_in_the_graph"] == []
    assert [t["transport"] for t in record["transports"]["done"]] == transports
