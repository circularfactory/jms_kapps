"""A module's transit time is read from the graph, once, when its process starts (jms_kapps 8).

Offline: the graph is a fake that answers the one read, and no store, middleware or process is
started. The live runs are in `test_uc2_transit_time_live.py`.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from kapps_triplestore_interface import IRI

from jms_kapps.uc2 import process
from jms_kapps.uc2.discovery import TransitTimeError, TransitTimes, transit_times
from jms_kapps.uc2.vocabulary import module_iri

M1, M2, M3 = module_iri(1), module_iri(2), module_iri(3)


class FakeGraph:
    """Answers `SELECT ?module ?time` with the transit times it is given, as the store does:
    one row for each value, a decimal as its converted value."""

    def __init__(self, *times: tuple[IRI, str]) -> None:
        self.rows = [
            {"module": str(module), "time": Decimal(seconds)}
            for module, seconds in times
        ]
        self.reads = 0

    def query(self, query: str, *, convert_bindings: bool = True) -> dict:
        assert "hasTransitTime" in query
        self.reads += 1
        return {"results": {"bindings": self.rows}}


def test_a_module_reads_its_own_transit_time_and_the_longest_in_one_read():
    graph = FakeGraph((M1, "0.5"), (M2, "0"), (M3, "2"))

    assert transit_times(graph, M2) == TransitTimes(own=0.0, longest=2.0)
    assert graph.reads == 1


def test_a_module_with_no_transit_time_does_not_start_and_the_reason_names_it():
    graph = FakeGraph((M1, "0.5"))

    with pytest.raises(TransitTimeError, match=r"^Module2 has no transit time"):
        transit_times(graph, M2)


def test_a_module_with_two_transit_times_does_not_start_and_the_reason_names_it():
    graph = FakeGraph((M2, "1"), (M2, "2"), (M1, "3"))

    with pytest.raises(
        TransitTimeError, match=r"^Module2 has 2 transit times \(1, 2 s\)"
    ):
        transit_times(graph, M2)


def test_the_build_of_a_module_without_a_transit_time_fails_before_it_registers_anything():
    ogm = SimpleNamespace(db=FakeGraph((M1, "1")))

    with pytest.raises(TransitTimeError, match="Module2"):
        process.build(str(M2), ogm, "127.0.0.1", 0)  # type: ignore[arg-type]


def test_the_process_of_such_a_module_ends_with_the_reason_and_the_exit_code_1(
    monkeypatch, capsys
):
    monkeypatch.setattr(process, "connect", lambda dataset: FakeGraph((M1, "1")))
    monkeypatch.setattr(process, "OGM", lambda db: SimpleNamespace(db=db))
    monkeypatch.setattr(
        "sys.argv", ["process", "--resource-iri", str(M2), "--dataset", "Tests"]
    )

    with pytest.raises(SystemExit) as ended:
        process.main()

    assert ended.value.code == 1
    assert capsys.readouterr().err.startswith(
        "jms-uc2 module: Module2 has no transit time"
    )


def test_the_real_simulated_move_at_0_s_calls_no_sleep_at_all(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(process.time, "sleep", slept.append)

    process.transit(0.0)(object(), object())

    assert slept == []


def test_the_real_simulated_move_waits_its_transit_time(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(process.time, "sleep", slept.append)

    process.transit(1.5)(object(), object())

    assert slept == [1.5]
