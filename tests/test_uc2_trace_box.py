"""`jms-uc2 trace-box` without a store: the grouping of the history's changes by transaction, the
two columns of time, the cross-check against the participants' logs, the record it writes, and
its whole flow through the run core.

The flow runs the real run core and supervisor against the stand-in graph of
`_registering_graph.py`, whose processes print the lines the real record functions build, and
reads the history from a stand-in that answers the change list with canned rows, as the triple
store answered them on a local run. Every recorded trace under `tests/reports/trace-box/` parses.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from _registering_graph import RegisteringGraph
from _repo import find_repo_root
from kapps_semantic_middleware.vocabulary import CFC, SVC
from kapps_triplestore_interface import IRI

from jms_kapps.common.supervision import Supervisor
from jms_kapps.uc2 import history, trace_box
from jms_kapps.uc2.__main__ import main
from jms_kapps.uc2.handshake import conveyed_record
from jms_kapps.uc2.run_core import participants_of
from jms_kapps.uc2.topology import shipped_topology
from jms_kapps.uc2.transport import status_record
from jms_kapps.uc2.vocabulary import FCI_NS, WMS, module_iri
from jms_kapps.uc2.wms import spawned_record, taken_back_record

RECORDS = Path(__file__).parent / "reports" / "trace-box"

ROOT = find_repo_root(Path(__file__))

BOX = IRI(FCI_NS + "Box-8aa68034-418d-4efe-9be2-8abb70aad28f")
TRANSPORT = IRI(f"{BOX}_transport")
M1, M2, M3, M4 = (module_iri(n) for n in range(1, 5))

# The server's offset in the canned answers: the timestamps carry it.
PLUS_TWO = timezone(timedelta(hours=2))

# The prefix Python's default log format puts in front of a record, as a participant logs it.
MODULE_LOG = "INFO:jms_kapps.uc2.module:"
WMS_LOG = "INFO:jms_kapps.uc2.wms:"


def at(transaction: str) -> IRI:
    """A transaction of the history, `yyyyMMddHHmmssSSS` in UTC."""
    return IRI(history.HISTORY_NS + transaction)


def stamp(transaction: str) -> datetime:
    """The timestamp of `transaction`, with the server's offset of +02:00."""
    utc = datetime.strptime(transaction, "%Y%m%d%H%M%S%f").replace(tzinfo=UTC)
    return utc.astimezone(PLUS_TWO)


def state(possessor: IRI) -> str:
    return f"{possessor}_possession"


# The changes of a local run, as the triple store answered them, at +02:00: the spawn, the start of
# the transport, three handovers between modules, and the give-back.
SPAWN, START, TO_M2, TO_M3, TO_M4, BACK = (
    "20261024110530484",
    "20261024110530872",
    "20261024110531246",
    "20261024110532862",
    "20261024110534465",
    "20261024110536029",
)

POSSESSION = [
    (SPAWN, state(M1), True),
    (TO_M2, state(M1), False),
    (TO_M2, state(M2), True),
    (TO_M3, state(M2), False),
    (TO_M3, state(M3), True),
    (TO_M4, state(M3), False),
    (TO_M4, state(M4), True),
    (BACK, state(M4), False),
    (BACK, state(WMS), True),
]
STATUS = [
    (SPAWN, "queued", True),
    (START, "queued", False),
    (START, "running", True),
    (BACK, "running", False),
    (BACK, "done", True),
]
ASSIGNED = [
    (SPAWN, str(M1), True),
    (TO_M2, str(M1), False),
    (TO_M2, str(M2), True),
    (TO_M3, str(M2), False),
    (TO_M3, str(M3), True),
    (TO_M4, str(M3), False),
    (TO_M4, str(M4), True),
]


def changes(subject: IRI, predicate: IRI, rows) -> list[history.Change]:
    return [
        history.Change(at(tx), stamp(tx), subject, predicate, value, inserted)
        for tx, value, inserted in rows
    ]


def the_run_s_changes() -> list[history.Change]:
    return [
        *changes(BOX, CFC.hasPossessedWorkpiece, POSSESSION),
        *changes(TRANSPORT, SVC.hasOperationStatus, STATUS),
        *changes(TRANSPORT, CFC.executedOn, ASSIGNED),
    ]


POSSESSORS = {state(r): r for r in (M1, M2, M3, M4, WMS)}


class History:
    """A store that answers the change list of each subject's predicate with its canned rows, in
    reverse, and the possessor of each possession state."""

    def __init__(self, found: list[history.Change]) -> None:
        self.found = found
        self.queries: list[str] = []

    def query(self, query, **kwargs):
        text = str(query)
        self.queries.append(text)
        rows = []
        for change in reversed(self.found):
            if history.change_list_query(change.subject, change.predicate) == text:
                rows.append(
                    {
                        "transaction": {
                            "type": "uri",
                            "value": str(change.transaction),
                        },
                        "timestamp": {
                            "type": "literal",
                            "value": change.timestamp.isoformat(
                                timespec="milliseconds"
                            ),
                        },
                        "object": {"type": "uri", "value": change.value},
                        "insert": {
                            "type": "literal",
                            "value": "true" if change.inserted else "false",
                        },
                    }
                )
        if f"<{CFC.hasPossessor}>" in text:
            rows = [
                {
                    "possessor": {"type": "uri", "value": str(possessor)},
                    "state": {"type": "uri", "value": s},
                }
                for s, possessor in POSSESSORS.items()
                if f"<{s}>" in text
            ]
        return {"results": {"bindings": rows}}


# The lines each participant logs on the run, as the record functions build them.
LOGGED = {
    WMS: [
        WMS_LOG + status_record(WMS, TRANSPORT, "queued"),
        WMS_LOG + spawned_record(WMS, BOX, M1, M4),
        WMS_LOG + taken_back_record(WMS, BOX, TRANSPORT),
    ],
    M1: [
        MODULE_LOG + status_record(M1, TRANSPORT, "running"),
        MODULE_LOG + conveyed_record(M1, BOX, M2),
    ],
    M2: [MODULE_LOG + conveyed_record(M2, BOX, M3)],
    M3: [MODULE_LOG + conveyed_record(M3, BOX, M4)],
    M4: [MODULE_LOG + status_record(M4, TRANSPORT, "done")],
}


def all_lines(logged=LOGGED) -> list[str]:
    return [line for lines in logged.values() for line in lines]


# -- One row for each transaction ------------------------------------------------------------- #


def test_the_changes_become_one_row_for_each_transaction_in_their_order():
    """The spawn with the possession, `queued` and the assignment; the start with `running`
    alone; each handover between modules with the possession and the assignment; the give-back
    with the possession and `done`."""
    rows = trace_box.transactions(the_run_s_changes(), POSSESSORS, BOX)

    assert [(r.transaction, r.possessor, r.status, r.assigned) for r in rows] == [
        (at(SPAWN), M1, "queued", M1),
        (at(START), None, "running", None),
        (at(TO_M2), M2, None, M2),
        (at(TO_M3), M3, None, M3),
        (at(TO_M4), M4, None, M4),
        (at(BACK), WMS, "done", None),
    ]
    assert {r.box for r in rows} == {BOX}


def test_each_row_has_its_time_in_utc_and_in_the_server_s_local_time():
    """Both from the timestamp: as it is, with the server's offset, and converted to UTC."""
    [spawn, *_] = trace_box.transactions(the_run_s_changes(), POSSESSORS, BOX)

    assert spawn.server_time == datetime(
        2026, 10, 24, 13, 5, 30, 484000, tzinfo=PLUS_TWO
    )
    assert spawn.utc == datetime(2026, 10, 24, 11, 5, 30, 484000, tzinfo=UTC)
    assert spawn.utc.tzinfo is UTC


def test_the_table_prints_both_times_the_box_and_each_column_of_a_row():
    rows = trace_box.transactions(the_run_s_changes(), POSSESSORS, BOX)

    table = trace_box.table(rows)

    header, *lines = table.splitlines()
    for column in (
        "time (UTC)",
        "time (server)",
        "box",
        "possessor",
        "status",
        "assigned module",
        "transaction",
    ):
        assert column in header
    assert len(lines) == len(rows) + 1  # the rule under the header
    spawn = lines[1]
    for cell in (
        "2026-10-24 11:05:30.484",
        "2026-10-24 13:05:30.484+02:00",
        BOX.fragment,
        "Module1",
        "queued",
        SPAWN,
    ):
        assert cell in spawn
    assert "WarehouseManagementSystem1" in lines[-1] and "done" in lines[-1]


# -- The cross-check -------------------------------------------------------------------------- #


def test_the_logged_handovers_are_the_spawn_each_convey_and_the_take_back():
    """In the order of the box's way, whichever order the participants' lines come in."""
    lines = list(reversed(all_lines()))

    assert trace_box.logged_possessors(lines, BOX) == [
        "Module1",
        "Module2",
        "Module3",
        "Module4",
        "WarehouseManagementSystem1",
    ]


def test_the_patterns_read_the_lines_the_record_functions_write():
    """A reworded record fails here, offline."""
    assert trace_box.logged_possessors(
        [
            spawned_record(WMS, BOX, M1, M4),
            conveyed_record(M1, BOX, M2),
            taken_back_record(WMS, BOX, TRANSPORT),
        ],
        BOX,
    ) == ["Module1", "Module2", "WarehouseManagementSystem1"]


def test_another_box_s_lines_are_left_out():
    other = IRI(FCI_NS + "Box-other")
    lines = [
        *all_lines(),
        conveyed_record(M1, other, M2),
        spawned_record(WMS, other, M2, M4),
    ]

    assert trace_box.logged_possessors(lines, BOX)[:2] == ["Module1", "Module2"]


def test_the_cross_check_agrees_when_the_history_and_the_logs_name_the_same_possessors():
    rows = trace_box.transactions(the_run_s_changes(), POSSESSORS, BOX)

    check = trace_box.cross_check(rows, all_lines(), BOX)

    assert check.agree is True
    assert (
        check.from_history
        == check.from_logs
        == [
            "Module1",
            "Module2",
            "Module3",
            "Module4",
            "WarehouseManagementSystem1",
        ]
    )


def test_the_cross_check_disagrees_where_a_logged_handover_is_missing():
    rows = trace_box.transactions(the_run_s_changes(), POSSESSORS, BOX)
    logged = dict(LOGGED, **{M2: []})

    check = trace_box.cross_check(rows, all_lines(logged), BOX)

    assert check.agree is False
    assert check.from_logs == [
        "Module1",
        "Module2",
        "WarehouseManagementSystem1",
        "(unreached) Module4",
    ]


def test_the_cross_check_disagrees_where_a_logged_handover_is_left_over():
    rows = trace_box.transactions(the_run_s_changes(), POSSESSORS, BOX)
    lines = [*all_lines(), MODULE_LOG + conveyed_record(M4, BOX, M1)]

    assert trace_box.cross_check(rows, lines, BOX).agree is False


def test_the_cross_check_disagrees_where_the_history_misses_a_handover():
    found = [c for c in the_run_s_changes() if c.transaction != at(TO_M3)]
    rows = trace_box.transactions(found, POSSESSORS, BOX)

    assert trace_box.cross_check(rows, all_lines(), BOX).agree is False


# -- The record ------------------------------------------------------------------------------- #

# A small record, as `--out` writes it.
SMALL_RECORD = {
    "box": str(BOX),
    "transport": str(TRANSPORT),
    "dataset": "ogm-disposable-trace",
    "topology": "line",
    "transactions": [
        {
            "transaction": str(at(SPAWN)),
            "utc": "2026-10-24T11:05:30.484000+00:00",
            "server_time": "2026-10-24T13:05:30.484000+02:00",
            "box": str(BOX),
            "possessor": str(M1),
            "status": "queued",
            "assigned": str(M1),
        },
        {
            "transaction": str(at(BACK)),
            "utc": "2026-10-24T11:05:36.029000+00:00",
            "server_time": "2026-10-24T13:05:36.029000+02:00",
            "box": str(BOX),
            "possessor": str(WMS),
            "status": "done",
            "assigned": None,
        },
    ],
    "cross_check": {
        "from_history": ["Module1", "WarehouseManagementSystem1"],
        "from_logs": ["Module1", "WarehouseManagementSystem1"],
        "agree": True,
    },
}


def test_a_record_written_reads_back_the_same():
    rows = trace_box.transactions(the_run_s_changes(), POSSESSORS, BOX)
    check = trace_box.cross_check(rows, all_lines(), BOX)
    written = trace_box.record(
        box=BOX,
        transport=TRANSPORT,
        dataset="ogm-disposable-trace",
        rows=rows,
        check=check,
    )

    read = trace_box.parse_record(json.loads(json.dumps(written)))

    assert read.transactions == rows
    assert read.cross_check == check
    assert (read.box, read.transport) == (BOX, TRANSPORT)


def test_the_small_record_parses():
    read = trace_box.parse_record(SMALL_RECORD)

    assert [r.possessor for r in read.transactions] == [M1, WMS]
    assert read.transactions[1].assigned is None
    assert read.cross_check.agree is True


def test_a_record_without_its_cross_check_is_refused():
    broken = {k: v for k, v in SMALL_RECORD.items() if k != "cross_check"}

    with pytest.raises(ValueError, match="cross_check"):
        trace_box.parse_record(broken)


@pytest.mark.parametrize(
    "path",
    sorted(RECORDS.glob("*.json")),
    ids=lambda path: path.name,
)
def test_every_recorded_trace_parses_and_agrees(path):
    read = trace_box.parse_record(json.loads(path.read_text(encoding="utf-8")))

    assert read.transactions
    assert read.cross_check.agree is True


# -- The flow through the run core ------------------------------------------------------------ #


@pytest.mark.posix(
    reason="each stand-in process deregisters on SIGTERM, which a stop sends on POSIX"
)
def test_the_flow_seeds_the_line_with_history_on_spawns_one_box_and_writes_its_trace(
    tmp_path,
):
    """The seed with the collection on, the start of the four modules and the WMS through the
    run core, one spawn from the entry to the exit, the wait until the transport is done and its
    take-back logged, the stop, the change list, the table, the record, and exit code 0."""
    line = shipped_topology("line")
    graph = RegisteringGraph(tmp_path / "registry", says=LOGGED, workflows_later=0.2)
    (tmp_path / "registry").mkdir()
    supervisor = Supervisor(graph.services, echo=lambda _: None, stop_timeout=5.0)
    participants = participants_of(
        line, module_command=graph.command, wms_command=graph.command(WMS)
    )
    spawned: list[tuple[IRI, IRI]] = []

    def spawn(origin: IRI, destination: IRI) -> tuple[IRI, IRI]:
        spawned.append((origin, destination))
        return BOX, TRANSPORT

    out = io.StringIO()
    written = tmp_path / "trace.json"

    code = trace_box.trace(
        line,
        graph,
        supervisor,
        participants,
        history=History(the_run_s_changes()),
        spawn=spawn,
        status=lambda transport: "done",
        dataset="ogm-disposable-trace",
        out_path=written,
        out=out,
        interval=0.05,
    )

    assert code == 0, out.getvalue()
    assert graph.seeded == [line] and graph.collect_history == [True]
    assert spawned == [(M1, M4)]
    assert {c.state for c in supervisor.snapshot()} == {"stopped"}
    read = trace_box.parse_record(json.loads(written.read_text(encoding="utf-8")))
    assert [r.possessor for r in read.transactions if r.possessor] == [
        M1,
        M2,
        M3,
        M4,
        WMS,
    ]
    assert read.cross_check.agree is True
    printed = out.getvalue()
    assert trace_box.table(read.transactions) in printed
    assert "agree" in printed


@pytest.mark.posix(
    reason="each stand-in process deregisters on SIGTERM, which a stop sends on POSIX"
)
def test_the_flow_exits_1_when_the_logs_disagree_with_the_history(tmp_path, capsys):
    """Without `out`, the flow prints to the `sys.stdout` of the call, which the capture holds."""
    line = shipped_topology("line")
    registry = tmp_path / "registry"
    registry.mkdir()
    graph = RegisteringGraph(
        registry, says=dict(LOGGED, **{M3: []}), workflows_later=0.2
    )
    supervisor = Supervisor(graph.services, echo=lambda _: None, stop_timeout=5.0)

    code = trace_box.trace(
        line,
        graph,
        supervisor,
        participants_of(
            line, module_command=graph.command, wms_command=graph.command(WMS)
        ),
        history=History(the_run_s_changes()),
        spawn=lambda origin, destination: (BOX, TRANSPORT),
        status=lambda transport: "done",
        dataset="ogm-disposable-trace",
        out_path=tmp_path / "trace.json",
        interval=0.05,
    )

    assert code == 1
    assert "disagree" in capsys.readouterr().out


# -- The command line ------------------------------------------------------------------------- #


@pytest.mark.parametrize("dataset", ["jms-kapps", "Tests", "disposable-ogm-x"])
def test_the_command_takes_only_a_disposable_dataset(dataset, capsys):
    """It turns the dataset's history collection on for good."""
    assert main(["trace-box", "--dataset", dataset]) == 1

    assert "ogm-disposable-" in capsys.readouterr().err


def test_the_command_is_listed_in_the_help(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])

    assert "trace-box" in capsys.readouterr().out


def test_the_default_record_is_named_after_the_box_in_the_current_folder(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)

    assert trace_box.default_out(BOX) == Path(f"trace-{BOX.fragment}.json")


# -- The order of the live tier --------------------------------------------------------------- #


@pytest.mark.skipif(ROOT is None, reason="an installed copy has no suite to collect")
def test_the_live_trace_runs_after_the_history_live_tests_in_the_tier():
    """The history live tests need the collection off when they start, and the trace turns it on:
    the graphdb tier collects the whole suite, and its order puts the trace after them."""
    assert ROOT is not None
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("GRAPHDB_", "TRIPLESTORE_"))
        and name != "PYTEST_ADDOPTS"
    }
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-m",
            "graphdb",
            "-p",
            "no:cacheprovider",
            "tests",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=300,
    )
    assert run.returncode == 0, f"{run.stdout}\n{run.stderr}"
    files = [line.split("::")[0] for line in run.stdout.splitlines() if "::" in line]
    history_live = [
        i for i, f in enumerate(files) if f.endswith("test_uc2_history_live.py")
    ]
    trace_live = [
        i for i, f in enumerate(files) if f.endswith("test_uc2_trace_box_live.py")
    ]

    assert history_live and trace_live
    assert max(history_live) < min(trace_live)
