"""The vertical slice: how it reads a validation report, its done condition, and its stop.

The live test runs `jms-uc2 slice` as a user would, with a transit time of 0, against the dataset
`--dataset` names (there is no default), which it clears. What each act checks is in
`jms_kapps.uc2.vertical_slice`. Beyond the exit code, the test asserts three claims from its own
evidence: Module1 logged the validation report, it asked Module2's Reserve again after the retry
time instead of giving up, and each module's service carried a heartbeat written during the run.

The live test of the slice's stop runs `jms-uc2 slice` too, sends SIGTERM to its process alone once
both modules have registered, and reads both services after the exit: neither has an address any
more. It runs only on a disposable dataset, whose name starts with `ogm-disposable-`, and
refuses any other with a skip that says why (jms_kapps 71). Its offline counterparts, with
stand-in modules and no store, are in `test_uc2_shutdown.py`.

The other tests need no GraphDB. They pin how the slice reads what the modules say: it matches one
validation result on its focus node, its whole path and its source shape, and it reads a logged
report, or a step a module accepted, only from the module's own logger.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
from datetime import UTC, datetime

import pytest
from _disposable import disposable
from _terminal import POSIX_ONLY
from _wait import eventually
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.validation_report import (
    ValidationResult,
    validation_results,
)

from jms_kapps.common.supervision import STOP_TIMEOUT_SECONDS
from jms_kapps.common.triplestore import connect
from jms_kapps.uc2.discovery import TransitTimes, transit_times
from jms_kapps.uc2.handshake import ReceiveRequest, ReserveRequest
from jms_kapps.uc2.module import FlexConveyorModule
from jms_kapps.uc2.module import logger as module_logger
from jms_kapps.uc2.seed import MODULES
from jms_kapps.uc2.vertical_slice import (
    POSSESSION_PATH,
    REGISTRATION_TIMEOUT_SECONDS,
    asked_again,
    lines_of,
    logged_report,
    logs_acceptance,
    services_of,
)
from jms_kapps.uc2.vocabulary import FC, box_iri

# A refusal's report as the module passes it on: N-Triples, as GraphDB 10.8.5 sends it. Fault 1 of
# the slice, a second box on Module2.
REFUSAL = """\
_:335a55b1c907436582368c9afb612b0434136 <http://www.w3.org/ns/shacl#conforms> "false"^^<http://www.w3.org/2001/XMLSchema#boolean> .
_:335a55b1c907436582368c9afb612b0434136 <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://www.w3.org/ns/shacl#ValidationReport> .
_:335a55b1c907436582368c9afb612b0434136 <http://rdf4j.org/schema/rdf4j#truncated> "false"^^<http://www.w3.org/2001/XMLSchema#boolean> .
_:335a55b1c907436582368c9afb612b0434136 <http://www.w3.org/ns/shacl#result> _:335a55b1c907436582368c9afb612b0434137 .
_:335a55b1c907436582368c9afb612b0434137 <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://www.w3.org/ns/shacl#ValidationResult> .
_:335a55b1c907436582368c9afb612b0434137 <http://www.w3.org/ns/shacl#focusNode> <http://w3id.org/circularfactory/FlexConveyorInstances#Module2> .
_:335a55b1c907436582368c9afb612b0434137 <http://rdf4j.org/shacl-extensions#shapesGraph> <http://rdf4j.org/schema/rdf4j#SHACLShapeGraph> .
_:e0258dc3-cf6b-4749-9a48-4d1f6b060217-8 <http://www.w3.org/1999/02/22-rdf-syntax-ns#first> <https://w3id.org/circularfactory/Core#hasPossessor> .
_:e0258dc3-cf6b-4749-9a48-4d1f6b060217-8 <http://www.w3.org/1999/02/22-rdf-syntax-ns#rest> _:e0258dc3-cf6b-4749-9a48-4d1f6b060217-88a295a24-9df7-4fa3-b866-40fa0876e9990 .
_:e0258dc3-cf6b-4749-9a48-4d1f6b060217-88a295a24-9df7-4fa3-b866-40fa0876e9990 <http://www.w3.org/1999/02/22-rdf-syntax-ns#first> _:9ba896e8-14ba-49cf-a58d-cc93e74a1e8d-10 .
_:e0258dc3-cf6b-4749-9a48-4d1f6b060217-88a295a24-9df7-4fa3-b866-40fa0876e9990 <http://www.w3.org/1999/02/22-rdf-syntax-ns#rest> <http://www.w3.org/1999/02/22-rdf-syntax-ns#nil> .
_:9ba896e8-14ba-49cf-a58d-cc93e74a1e8d-10 <http://www.w3.org/ns/shacl#inversePath> <https://w3id.org/circularfactory/Core#hasPossessedWorkpiece> .
_:335a55b1c907436582368c9afb612b0434137 <http://www.w3.org/ns/shacl#resultPath> _:e0258dc3-cf6b-4749-9a48-4d1f6b060217-8 .
_:335a55b1c907436582368c9afb612b0434137 <http://www.w3.org/ns/shacl#sourceConstraintComponent> <http://www.w3.org/ns/shacl#MaxCountConstraintComponent> .
_:335a55b1c907436582368c9afb612b0434137 <http://www.w3.org/ns/shacl#resultSeverity> <http://www.w3.org/ns/shacl#Violation> .
_:335a55b1c907436582368c9afb612b0434137 <http://www.w3.org/ns/shacl#resultMessage> "A FlexConveyorModule may possess at most one Box at a time." .
<http://w3id.org/circularfactory/FlexConveyor#FlexConveyorModulePossessionShape> <http://www.w3.org/ns/shacl#message> "A FlexConveyorModule may possess at most one Box at a time." .
<http://w3id.org/circularfactory/FlexConveyor#FlexConveyorModulePossessionShape> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://www.w3.org/ns/shacl#PropertyShape> .
_:335a55b1c907436582368c9afb612b0434137 <http://www.w3.org/ns/shacl#sourceShape> <http://w3id.org/circularfactory/FlexConveyor#FlexConveyorModulePossessionShape> .
<http://w3id.org/circularfactory/FlexConveyor#FlexConveyorModulePossessionShape> <http://www.w3.org/ns/shacl#path> _:e0258dc3-cf6b-4749-9a48-4d1f6b060217-8 .
<http://w3id.org/circularfactory/FlexConveyor#FlexConveyorModulePossessionShape> <http://www.w3.org/ns/shacl#maxCount> "1"^^<http://www.w3.org/2001/XMLSchema#integer> .
"""

# The result the slice expects of that refusal.
REFUSED_AT_MODULE2 = ValidationResult(
    focus=str(MODULES[1]),
    path=POSSESSION_PATH,
    shape=str(FC.FlexConveyorModulePossessionShape),
)


def echoed(child: str, *records: str) -> list[str]:
    """`records`, logged by `child` one after the other, as the slice's supervisor echoes them."""
    return [f"{child}  {line}" for record in records for line in record.split("\n")]


def test_a_refusal_names_the_focus_node_the_whole_path_and_the_source_shape():
    assert validation_results(REFUSAL) == {REFUSED_AT_MODULE2}


def test_the_report_is_read_from_its_result_not_from_the_shapes_description():
    """A search of the whole text finds all three names in this report. No result names them."""
    other_shape = "http://w3id.org/circularfactory/FlexConveyor#SomeOtherShape"
    first_step_only = REFUSAL.replace(
        "<http://www.w3.org/ns/shacl#resultPath> _:e0258dc3-cf6b-4749-9a48-4d1f6b060217-8 .",
        "<http://www.w3.org/ns/shacl#resultPath> <https://w3id.org/circularfactory/Core#hasPossessor> .",
    ).replace(
        "<http://www.w3.org/ns/shacl#sourceShape> <http://w3id.org/circularfactory/FlexConveyor#FlexConveyorModulePossessionShape> .",
        f"<http://www.w3.org/ns/shacl#sourceShape> <{other_shape}> .",
    )
    for needle in (
        f"<{MODULES[1]}>",
        "Core#hasPossessor>",
        "FlexConveyor#FlexConveyorModulePossessionShape>",
    ):
        assert needle in first_step_only

    assert validation_results(first_step_only) == {
        ValidationResult(
            focus=str(MODULES[1]),
            path="<https://w3id.org/circularfactory/Core#hasPossessor>",
            shape=other_shape,
        )
    }


def test_the_logged_report_is_the_modules_not_the_clients():
    """The client logs the store's error too. Only the module's own record counts."""
    client = f"ERROR:GraphDB:Error while querying GraphDB (500) - {REFUSAL}"
    module = f"WARNING:{module_logger.name}:Module1: refused, asking again:\n{REFUSAL}"
    after = 'INFO:httpx:HTTP Request: POST http://127.0.0.1:1/x "HTTP/1.1 200 OK"'
    module1 = MODULES[0].fragment

    logged = echoed(module1, client, module, after)
    client_only = echoed(module1, client, after)
    elsewhere = echoed(MODULES[1].fragment, module)

    assert validation_results(logged_report(lines_of(logged, module1))) == {
        REFUSED_AT_MODULE2
    }
    for output in (client_only, elsewhere):
        assert logged_report(lines_of(output, module1)) == ""


class NothingHeld:
    """A graph in which no module holds a box, and no WarehouseManagementSystem is named."""

    def boxes_held_by(self, resource):
        return []

    def wms(self):
        return None


def test_a_step_counts_as_run_on_the_peer_only_by_the_peers_own_record_that_it_accepted_it(
    caplog,
):
    """The records as the module logs them, matched whole. The caller's log of the same call does
    not count, nor a record of the peer's with another verb or from another giver."""
    caplog.set_level(logging.INFO, logger="jms_kapps")
    m1, m2 = MODULES
    b1 = box_iri(1)
    module2 = FlexConveyorModule(
        m2,
        graph=NothingHeld(),
        handover=None,
        call=None,
        records=None,
        notify=None,
        move=None,
        start=lambda work: None,
        later=lambda seconds, work: None,
    )
    module2.reserve(ReserveRequest(box=str(b1), source_module=str(m1)))
    module2.receive(ReceiveRequest(box=str(b1), source_module=str(m1)))
    records = [f"{r.levelname}:{r.name}:{r.getMessage()}" for r in caplog.records]
    reserved_only = records[:1]
    caller = 'INFO:httpx:HTTP Request: POST http://127.0.0.1:1/workflows/receive/execute "HTTP/1.1 200 OK"'
    refused = (
        f"INFO:{module_logger.name}:Module2: ReserveWorkflow refused Box1 from Module1"
    )
    other_giver = (
        f"INFO:{module_logger.name}:Module2: ReserveWorkflow accepted Box1 from Module3"
    )

    assert logs_acceptance(records, m2, FC.ReserveWorkflow, b1, m1)
    assert logs_acceptance(records, m2, FC.ReceiveWorkflow, b1, m1)
    assert not logs_acceptance(records, m2, FC.ReserveWorkflow, box_iri(2), m1)
    assert not logs_acceptance([*reserved_only, caller], m2, FC.ReceiveWorkflow, b1, m1)
    assert not logs_acceptance([refused], m2, FC.ReserveWorkflow, b1, m1)
    assert not logs_acceptance([other_giver], m2, FC.ReserveWorkflow, b1, m1)


# Two supervisor drain threads and the main thread write at once, to an unbuffered stdout.
_WRITERS = """
import threading
from jms_kapps.uc2.vertical_slice import Transcript, say

transcript = Transcript()

def drain(child):
    for n in range(2000):
        transcript(f"{child}  _:b{n} <http://x#p> <http://x#o> .")

threads = [threading.Thread(target=drain, args=(c,)) for c in ("Module1", "Module2")]
for thread in threads:
    thread.start()
for n in range(2000):
    say(f"  ok   check {n}")
for thread in threads:
    thread.join()
"""


def test_every_line_the_slice_writes_stays_whole_on_an_unbuffered_stdout():
    """The live test reads the slice's stdout line by line, so no line may be split."""
    run = subprocess.run(
        [sys.executable, "-u", "-c", _WRITERS],
        capture_output=True,
        check=True,
        text=True,
        timeout=60,
    )

    lines = run.stdout.splitlines()
    split = [
        line
        for line in lines
        if not (
            line.startswith(("Module1  _:b", "Module2  _:b")) and line.endswith(" .")
        )
        and not (line.startswith("  ok   check ") and line[13:].isdigit())
    ]
    assert len(lines) == 6000
    assert split == []


@pytest.mark.graphdb
@pytest.mark.skipif(not triplestore_env_present(), reason="no triple store connection variables")
def test_one_command_runs_the_slice_end_to_end(dataset):
    command = shutil.which("jms-uc2")
    assert command is not None, "the jms-uc2 console script is not installed"
    started = datetime.now(UTC)

    run = subprocess.run(
        [
            command,
            "slice",
            "--dataset",
            dataset,
            "--transit-time",
            "0",
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=300,
    )

    assert run.returncode == 0, f"{run.stdout}\n{run.stderr}"
    assert "Both acts behaved as claimed." in run.stdout

    # Module2 ran Reserve and Receive for Box1 itself: its own records say so.
    m1, m2 = MODULES
    module2 = lines_of(run.stdout.splitlines(), m2.fragment)
    for step in (FC.ReserveWorkflow, FC.ReceiveWorkflow):
        assert logs_acceptance(module2, m2, step, box_iri(1), m1), "\n".join(module2)

    # Module1 logged the report it was refused with.
    module1 = lines_of(run.stdout.splitlines(), MODULES[0].fragment)
    logged = logged_report(module1)
    assert REFUSED_AT_MODULE2 in validation_results(logged), "\n".join(module1)
    # It then asked first, and again after the retry time, while Module2 held Box1.
    occupied = "Module2 refused to reserve: occupied by Box1"
    assert asked_again(module1, occupied) >= 2, "\n".join(module1)

    # Each module registered its own service, and that service carried a heartbeat. The service
    # outlives its process: stopping removes its address and endpoints, not its heartbeat.
    db = connect(dataset)
    # The slice's seed gave both modules the slice's transit time, which each read at its start.
    for module in MODULES:
        assert transit_times(db, module) == TransitTimes(own=0.0, longest=0.0)
    services = {module.fragment: services_of(db, module) for module in MODULES}
    for module, found in services.items():
        assert len(found) == 1, f"{module}: {found}"
        heartbeat = found[0].heartbeat
        assert heartbeat is not None, f"{module}: {found}"
        assert heartbeat >= started, f"{module}: {found}"
    assert len({found[0].iri for found in services.values()}) == len(MODULES)


# Why the live test of the slice's stop runs only on a disposable dataset.
STOP_WHY = (
    "the slice clears the dataset it runs on, and a stop that goes wrong leaves both modules "
    "running there, registered and live"
)

# What the slice prints once both modules have registered their workflows.
REGISTERED = "Both modules registered their workflows."

# How long the slice may take to clear and seed the dataset on the shared KIT store, and then
# wait for both modules to register: its own wait for them is `REGISTRATION_TIMEOUT_SECONDS`.
REGISTERED_WITHIN_SECONDS = REGISTRATION_TIMEOUT_SECONDS + 120


@pytest.mark.graphdb
@POSIX_ONLY
@pytest.mark.skipif(not triplestore_env_present(), reason="no triple store connection variables")
def test_sigterm_to_the_slice_alone_stops_both_modules_and_each_deregisters(dataset):
    """Once both modules have registered, SIGTERM to the slice's process alone, as `kill` sends
    it, stops both: after the slice has exited, the graph holds no address of either module's
    service. A stopped slice exits non-zero, with 128 plus the signal's number (jms_kapps 71).

    The slice runs in a session of its own, so that the cleanup can reach its modules too if the
    test fails; the test signals the slice's process alone. The services are read before any
    cleanup, so that the cleanup's own stop cannot make up for the slice's.
    """
    db = connect(disposable(dataset, STOP_WHY))
    command = shutil.which("jms-uc2")
    assert command is not None, "the jms-uc2 console script is not installed"
    process = subprocess.Popen(
        [command, "slice", "--dataset", dataset, "--transit-time", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    lines: list[str] = []

    def read() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line.rstrip("\n"))

    threading.Thread(target=read, daemon=True).start()
    try:
        eventually(
            lambda: REGISTERED in lines or process.poll() is not None,
            timeout=REGISTERED_WITHIN_SECONDS,
        )
        # The slice's own readiness gate found both services live, each with an address. The
        # test reads nothing more before the signal, so that the acts cannot end first.
        assert process.poll() is None, "\n".join(lines)

        process.send_signal(signal.SIGTERM)

        code = process.wait(timeout=STOP_TIMEOUT_SECONDS + 30)
        after = {module: services_of(db, module) for module in MODULES}
    finally:
        # A slice or a module that still runs would keep its service live: stop the whole session,
        # then kill what is left of it.
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=STOP_TIMEOUT_SECONDS + 5)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    output = "\n".join(lines)
    assert code == 128 + signal.SIGTERM, output
    for module, found in after.items():
        assert len(found) == 1, f"{module.fragment}: {found}"
        assert found[0].address is None, (
            f"{module.fragment} still has an address: {found}\n{output}"
        )
