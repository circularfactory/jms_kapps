"""The server watch of a UC2 run (jms_kapps 95), against stand-ins only: the resource view and the
round trip every few seconds from the load's start to the drain's end, the monitoring counters of
every repository at the run's start and end, the statement counts of the run's repository where no
participant writes, the page cache at the window's start and the drain's end, and the license.

The run is the bench launcher's (`test_evaluation_bench.Bench`) on a clock that moves only when the
run waits; nothing reaches a store, a process or a network.
"""

from __future__ import annotations

from itertools import pairwise

from _evaluation_fakes import Events, FakeClock, FakeGraphDB
from test_evaluation_bench import CROSS, Bench

from jms_kapps.evaluation.bench import (
    STATEMENTS_AFTER_THE_PARTICIPANTS,
    STATEMENTS_BEFORE_THE_PARTICIPANTS,
)
from jms_kapps.evaluation.configuration import Configuration
from jms_kapps.evaluation.watch import (
    WATCH_INTERVAL_SECONDS,
    ServerWatch,
    WatchedClock,
)

WARM_UP = Configuration(**(vars(CROSS) | {"warm_up_seconds": 30.0}))
PROTECTED = {"jms-kapps", "kapps-demo", "Tests"}


def count(requests: list[str], request: str) -> int:
    return requests.count(request)


def watched(
    store: FakeGraphDB, interval: float
) -> tuple[ServerWatch, WatchedClock, FakeClock]:
    clock = FakeClock(Events())
    waiting = WatchedClock(clock)
    watch = ServerWatch(store.server(), interval=interval, clock=clock)
    waiting.watch = watch
    return watch, waiting, clock


def test_the_interval_is_about_five_seconds():
    assert WATCH_INTERVAL_SECONDS == 5.0


def test_a_wait_takes_one_sample_at_its_start_and_one_every_interval_after_it():
    store = FakeGraphDB()
    watch, waiting, clock = watched(store, 5.0)

    watch.start()
    waiting.sleep(60.0)
    watch.stop()

    assert [s["at"] for s in watch.samples] == [
        clock.at_seconds(seconds).isoformat() for seconds in range(0, 65, 5)
    ]
    assert clock.seconds == 60.0


def test_the_watch_sends_at_most_one_request_per_view_per_interval():
    store = FakeGraphDB()
    watch, waiting, _ = watched(store, 5.0)

    watch.start()
    waiting.sleep(1000.0)
    watch.stop()

    # One wait of 1000 s is split at the samples: 201 of them, never a burst.
    assert len(watch.samples) == 201
    assert count(store.requests, "GET rest/monitor/infrastructure") == 201
    assert count(store.requests, "GET rest/info/version") == 201
    assert len(store.requests) == 1 + 2 * 201, (
        "the login, and two views for each sample"
    )


def test_a_sample_that_takes_longer_than_the_interval_skips_the_samples_it_missed():
    store = FakeGraphDB()
    watch, waiting, clock = watched(store, 5.0)
    resources = watch._server.resources

    def slow():
        view = resources()
        clock.seconds += 12.0
        return view

    watch._server.resources = slow  # type: ignore[method-assign]

    watch.start()
    waiting.sleep(30.0)

    assert [s["at"] for s in watch.samples] == [
        clock.at_seconds(0).isoformat(),
        clock.at_seconds(15).isoformat(),
        clock.at_seconds(30).isoformat(),
    ]


def test_a_stopped_watch_takes_no_sample_and_its_clock_only_waits():
    store = FakeGraphDB()
    watch, waiting, clock = watched(store, 5.0)

    waiting.sleep(20.0)
    watch.start()
    watch.stop()
    waiting.sleep(20.0)

    assert len(watch.samples) == 1
    assert clock.seconds == 40.0


def test_each_sample_keeps_the_resource_view_as_the_server_answered_and_the_round_trip():
    store = FakeGraphDB()
    watch, _, _ = watched(store, 5.0)

    watch.start()

    [sample] = watch.samples
    assert sample["resources"]["status"] == 200
    assert sample["resources"]["answer"] == {
        "heapMemoryUsage": {"used": 1000, "max": 8000},
        "cpuLoad": 3,
    }
    assert sample["round_trip"]["status"] == 200
    assert sample["round_trip"]["milliseconds"] >= 0
    assert sample["round_trip"]["request"] == "GET rest/info/version"


def test_a_refused_or_lost_read_is_kept_with_its_status_and_the_watch_goes_on():
    store = FakeGraphDB(
        refusing={"rest/monitor/infrastructure": 403, "rest/info/version": 503}
    )
    watch, waiting, _ = watched(store, 5.0)

    watch.start()
    waiting.sleep(10.0)

    assert len(watch.samples) == 3
    for sample in watch.samples:
        assert sample["resources"]["status"] == 403
        assert "refused by the store" in sample["resources"]["error"]
        assert sample["round_trip"]["status"] == 503
        assert "milliseconds" in sample["round_trip"]


def test_a_read_that_raises_is_kept_as_a_failed_read():
    store = FakeGraphDB()
    watch, waiting, _ = watched(store, 5.0)

    def broken():
        raise RuntimeError("the connection broke")

    watch._server.resources = broken  # type: ignore[method-assign]

    watch.start()
    waiting.sleep(5.0)

    assert len(watch.samples) == 2
    assert watch.samples[1]["resources"] == {
        "request": "GET rest/monitor/infrastructure",
        "error": "the connection broke",
    }
    assert watch.samples[1]["round_trip"]["status"] == 200


# The run.


def test_a_run_samples_through_the_warm_up_the_window_and_the_drain(tmp_path):
    bench = Bench(tmp_path, open_reads=3)

    record = bench.run(WARM_UP, watch_interval_seconds=5.0)

    watch = record["server"]["watch"]
    assert watch["interval_seconds"] == 5.0
    assert record["run_settings"]["server_watch_interval_seconds"] == 5.0
    taken = {a["action"]: a for a in record["actions"]}
    first = next(a for a in record["actions"] if a["action"] == "spawn rate set")
    drain_end = taken["drain ended"]
    # The first sample is at the load's start (the warm-up's start), then one every 5 s, the last
    # at or before the drain's end.
    assert watch["samples"][0]["at"] == first["at"]
    span = drain_end["seconds"] - first["seconds"]
    assert span >= 30.0 + CROSS.window_seconds
    assert watch["sample_count"] == len(watch["samples"]) == int(span // 5) + 1
    times = [bench.clock.start.fromisoformat(s["at"]) for s in watch["samples"]]
    gaps = {(b - a).total_seconds() for a, b in pairwise(times)}
    assert gaps == {5.0}
    for sample in watch["samples"]:
        assert sample["resources"]["status"] == 200
        assert sample["round_trip"]["status"] == 200


def test_the_watch_sends_one_request_per_view_per_interval_in_a_run(tmp_path):
    bench = Bench(tmp_path, open_reads=3)

    record = bench.run(WARM_UP, watch_interval_seconds=5.0)

    samples = record["server"]["watch"]["sample_count"]
    requests = bench.store.requests
    # The resource view: at the run's start, the window's start and the drain's end, and once
    # for each sample. The version: the facts, five round trips at the start, and each sample.
    assert count(requests, "GET rest/monitor/infrastructure") == 3 + samples
    assert count(requests, "GET rest/info/version") == 1 + 1 + 5 + samples


def test_the_samples_end_with_the_drain_and_none_follow_it(tmp_path):
    bench = Bench(tmp_path, open_reads=3)

    record = bench.run(WARM_UP, watch_interval_seconds=5.0)

    drain_end = next(a for a in record["actions"] if a["action"] == "drain ended")["at"]
    assert all(s["at"] <= drain_end for s in record["server"]["watch"]["samples"])
    sleeps_after = bench.events.index_of("stop all")
    assert [e for e in bench.events[sleeps_after:] if e[0] == "sleep"] == [
        ("sleep", 1.0)
    ], "after the drain, only the end's wait for the output"


def test_a_refused_watch_read_is_recorded_and_the_run_completes(tmp_path):
    store = FakeGraphDB(
        refusing={"rest/monitor/infrastructure": 403, "rest/info/version": 500}
    )
    bench = Bench(tmp_path, store=store)

    record = bench.run(WARM_UP, watch_interval_seconds=5.0)

    assert record["outcome"] == "completed", record["outcome"]
    samples = record["server"]["watch"]["samples"]
    assert len(samples) > 10
    assert {s["resources"]["status"] for s in samples} == {403}
    assert {s["round_trip"]["status"] for s in samples} == {500}


def test_a_run_that_ends_early_keeps_the_samples_it_took(tmp_path):
    bench = Bench(tmp_path, failing_rate=True)

    record = bench.run(WARM_UP, watch_interval_seconds=5.0)

    assert record["outcome"].startswith("failed")
    assert record["server"]["watch"]["sample_count"] == 0


def test_a_run_without_a_sample_interval_of_its_own_samples_every_five_seconds(
    tmp_path,
):
    bench = Bench(tmp_path)

    record = bench.run(WARM_UP, watch_interval_seconds=WATCH_INTERVAL_SECONDS)

    assert record["server"]["watch"]["interval_seconds"] == 5.0


# The counters around the run.


def test_the_record_holds_the_monitoring_counters_of_every_repository_at_the_start_and_end(
    tmp_path,
):
    bench = Bench(tmp_path)

    record = bench.run()

    server = record["server"]
    at_start = server["repository_monitors_at_the_start"]
    assert set(at_start) == PROTECTED
    at_end = server["repository_monitors_at_the_drain_end"]
    assert set(at_end) == PROTECTED | {record["repository"]}
    for monitors in (at_start, at_end):
        for view in monitors.values():
            assert view["status"] == 200
            assert view["answer"] == {"activeTransactions": 0}
    # The start is before the harness creates the run's repository.
    requests = server["requests"]
    created = requests.index("POST rest/repositories")
    first = requests.index("GET rest/monitor/repository/jms-kapps")
    assert first < created
    assert "GET rest/monitor/repository/Tests" in requests[:created]


def test_the_counters_of_a_repository_that_the_server_refuses_are_kept_with_its_status(
    tmp_path,
):
    store = FakeGraphDB(refusing={"rest/monitor/repository/Tests": 403})
    bench = Bench(tmp_path, store=store)

    record = bench.run()

    assert record["outcome"] == "completed", record["outcome"]
    refused = record["server"]["repository_monitors_at_the_start"]["Tests"]
    assert refused["status"] == 403
    assert (
        record["server"]["repository_monitors_at_the_start"]["jms-kapps"]["status"]
        == 200
    )


def test_the_page_cache_is_read_at_the_window_s_start_and_the_drain_s_end_not_in_the_series(
    tmp_path,
):
    bench = Bench(tmp_path)

    record = bench.run(WARM_UP, watch_interval_seconds=5.0)

    server = record["server"]
    assert server["structures_at_the_window_start"]["status"] == 200
    assert server["structures_at_the_window_start"]["answer"]["cache"] == {
        "hit": 90,
        "miss": 10,
    }
    assert server["structures_at_the_drain_end"]["status"] == 200
    assert count(server["requests"], "GET rest/monitor/structures") == 2
    assert all("structures" not in s for s in server["watch"]["samples"])


def test_the_license_is_read_once_with_the_edition_and_the_licensed_cores(tmp_path):
    bench = Bench(tmp_path)

    record = bench.run(WARM_UP, watch_interval_seconds=5.0)

    license_ = record["server"]["license"]
    assert license_["request"] == "GET rest/graphdb-settings/license"
    assert license_["answer"] == {"productType": "enterprise", "maxCpuCores": 8}
    assert count(record["server"]["requests"], "GET rest/graphdb-settings/license") == 1


def test_a_refused_license_is_kept_with_its_status(tmp_path):
    store = FakeGraphDB(refusing={"rest/graphdb-settings/license": 403})
    bench = Bench(tmp_path, store=store)

    record = bench.run()

    assert record["outcome"] == "completed", record["outcome"]
    assert record["server"]["license"]["status"] == 403
    assert "answer" not in record["server"]["license"]


# The statement counts.


def test_the_statement_counts_are_read_before_the_participants_start_and_after_they_stop(
    tmp_path,
):
    bench = Bench(tmp_path)

    record = bench.run()

    for key in (STATEMENTS_BEFORE_THE_PARTICIPANTS, STATEMENTS_AFTER_THE_PARTICIPANTS):
        view = record["server"][key]
        assert view["request"] == f"GET rest/repositories/{record['repository']}/size"
        assert view["answer"] == {"explicit": 7, "inferred": 3, "total": 10}
    names = [a["action"] for a in record["actions"]]
    counts = [i for i, name in enumerate(names) if name == "statement counts read"]
    assert len(counts) == 2
    assert (
        names.index("seed began")
        < counts[0]
        < names.index("participants started")
        < names.index("spawn rate set")
        < names.index("participants stopped")
        < counts[1]
        < names.index("repository deleted")
    )
    assert [record["actions"][i]["key"] for i in counts] == [
        STATEMENTS_BEFORE_THE_PARTICIPANTS,
        STATEMENTS_AFTER_THE_PARTICIPANTS,
    ]


def test_the_statement_counts_come_after_the_prefill_load_and_before_the_first_participant(
    tmp_path,
):
    prefill = tmp_path / "prefill.ttl"
    prefill.write_text("<a> <b> <c> .\n", encoding="utf-8")
    bench = Bench(tmp_path)

    record = bench.run(Configuration(**(vars(CROSS) | {"prefill": prefill})))

    events = bench.events
    repository = record["repository"]
    loaded = max(i for i, e in enumerate(events) if e[:2] == ("write", "import"))
    sized = events.index_of("size", repository)
    assert loaded < sized < events.index_of("spawn")
    assert [e for e in events if e[0] == "size"] == [("size", repository)] * 2
    # The second is after the stop and before the deletion.
    last = max(i for i, e in enumerate(events) if e[0] == "size")
    assert events.index_of("stop all") < last < events.index_of("repository deleted")


def test_a_refused_statement_count_is_kept_with_its_status_and_the_run_completes(
    tmp_path,
):
    store = FakeGraphDB(refusing={"*/size": 403})
    bench = Bench(tmp_path, store=store)

    record = bench.run()

    assert record["outcome"] == "completed", record["outcome"]
    for key in (STATEMENTS_BEFORE_THE_PARTICIPANTS, STATEMENTS_AFTER_THE_PARTICIPANTS):
        assert record["server"][key]["status"] == 403


def test_no_statement_count_is_read_while_a_participant_may_write(tmp_path):
    """A run whose participants do not stop reads no count after them."""
    bench = Bench(tmp_path)

    def refuse_to_stop(*args: object, **kwargs: object) -> None:
        raise RuntimeError("a participant does not stop")

    original = bench.supervisor_for

    def supervisor_for(services, echo):
        supervisor = original(services, echo)
        supervisor.stop_all = refuse_to_stop  # type: ignore[method-assign]
        return supervisor

    bench.supervisor_for = supervisor_for  # type: ignore[method-assign]

    record = bench.run()

    assert STATEMENTS_BEFORE_THE_PARTICIPANTS in record["server"]
    assert STATEMENTS_AFTER_THE_PARTICIPANTS not in record["server"]
    assert "participants not stopped" in [a["action"] for a in record["actions"]]


def test_a_run_that_fails_before_its_repository_exists_reads_no_count(tmp_path):
    store = FakeGraphDB(repositories=["jms-kapps", "kapps-demo", "Tests"])
    bench = Bench(tmp_path, store=store)
    bench.server.create_repository = lambda *a, **k: (_ for _ in ()).throw(  # type: ignore[method-assign]
        RuntimeError("no repository")
    )

    record = bench.run()

    assert record["outcome"].startswith("failed")
    assert STATEMENTS_BEFORE_THE_PARTICIPANTS not in record["server"]
    assert STATEMENTS_AFTER_THE_PARTICIPANTS not in record["server"]
    assert not any("/size" in r for r in record["server"]["requests"])


def test_a_run_reads_the_monitoring_view_of_running_repositories_only(tmp_path):
    """The server lists one repository INACTIVE: reading its view might initialise it, which
    would load another user's repository into the shared server."""
    store = FakeGraphDB(
        repositories=["jms-kapps", "kapps-demo", "Tests", "someone-elses"],
        states={"someone-elses": "INACTIVE"},
    )
    bench = Bench(tmp_path, store=store)

    record = bench.run()

    assert record["outcome"] == "completed", record["outcome"]
    assert not any("someone-elses" in r for r in record["server"]["requests"])
    assert not any("someone-elses" in r for r in store.requests)
    for moment in ("the_start", "the_drain_end"):
        kept_ = record["server"][f"repository_monitors_at_{moment}"]["someone-elses"]
        assert kept_["state"] == "INACTIVE" and kept_["read"] is False
        assert kept_["request"] is None
        assert (
            record["server"][f"repository_monitors_at_{moment}"]["Tests"]["status"]
            == 200
        )
