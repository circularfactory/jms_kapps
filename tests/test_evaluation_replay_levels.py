"""The store-only baseline replays a recorded run compressed in time: the factor, the offered and the achieved rate of a level,
the rule that breaks a level, and the approval that every factor above 1 needs.

Everything here is a stand-in of the store (`test_evaluation_baseline`): a recorded run made against
the stand-ins of parts 1 and 2, a store that answers at the times a test gives it, and a clock that
moves only when the replay waits. No test holds an approval that covers a real spec of the
repository, and nothing reaches a store. The connection variables of a store are removed first.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from _evaluation_approvals import approval_file
from test_evaluation_baseline import (
    HEAP_REASON,
    SHACL_MEDIA_TYPE,
    SHACL_REASON,
    WARM_UP,
    Baseline,
    measured,
    no_answer,
    read,
    recorded_run,
    refused,
    seconds_of,
)

from jms_kapps.evaluation import baseline as baseline_module
from jms_kapps.evaluation.approval import (
    Covered,
    HeavyRunRefused,
    cover,
    read_approval,
)
from jms_kapps.evaluation.baseline import (
    BaselineRefused,
    read_recorded_run,
)
from jms_kapps.evaluation.replay import Request, Sent
from jms_kapps.evaluation.replay_levels import (
    ACHIEVED_BELOW_OFFERED,
    compressed,
    is_heavy,
    level_key,
    level_record,
)
from jms_kapps.evaluation.spec import configurations, read_spec, valid_factor

# The start of the clocks of the stand-ins (`FakeClock`).
CLOCK_START = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

# A recorded run's window runs from 30 s to 90 s on its clock; `read(clock, 31.0, ...)` is a
# request 1 s into the window.
WINDOW = 60.0


@pytest.fixture(autouse=True)
def no_store(monkeypatch):
    for prefix in ("GRAPHDB", "TRIPLESTORE"):
        for name in ("URL", "USERNAME", "PASSWORD"):
            monkeypatch.delenv(f"{prefix}_{name}", raising=False)


@pytest.fixture
def store(monkeypatch):
    """A store is configured, so that a refusal below is the approval's, not the missing store's."""
    for name in ("URL", "USERNAME", "PASSWORD"):
        monkeypatch.setenv(f"TRIPLESTORE_{name}", "x")


def four_requests(tmp_path: Path) -> Path:
    """A recorded run with four requests in its window, at 1 s, 11 s, 21 s and 31 s."""
    return recorded_run(
        tmp_path,
        lambda clock: [
            read(clock, 31.0, "SELECT ?first"),
            read(clock, 41.0, "SELECT ?second"),
            read(clock, 51.0, "SELECT ?third"),
            read(clock, 61.0, "SELECT ?fourth"),
        ],
    )


def covering(folder: Path, recorded: Path, **approval_options: Any) -> Covered:
    """The covered record that `cover` makes for the configuration of `recorded`: a spec of
    a temporary folder that lists it, and an approval of that spec for the clocks of the
    stand-ins. The record of the recorded run gets the key the spec gives the configuration."""
    configuration = read_recorded_run(recorded).configuration
    values = replace(configuration, wms_pick_seed=None).to_toml()
    spec_path = folder / "spec.toml"
    spec_path.write_text(
        f"[defaults]\n{values}repetitions = 1\n\n[[sweep]]\n", encoding="utf-8"
    )
    [chosen] = configurations(read_spec(spec_path))
    record = json.loads((recorded / "record.json").read_text())
    record["sweep"] = {"spec": str(spec_path), "key": chosen.key}
    (recorded / "record.json").write_text(json.dumps(record))
    approval = read_approval(
        approval_file(
            folder,
            spec_path,
            start=CLOCK_START - timedelta(hours=1),
            end=CLOCK_START + timedelta(hours=1),
            **approval_options,
        )
    )
    return cover(
        approval,
        spec=spec_path,
        key=chosen.key,
        configuration=configuration,
        now=CLOCK_START,
    )


# The replay sends the recorded requests in their order, with the gaps divided by the factor.


def test_a_replay_at_a_factor_sends_the_requests_in_their_order_with_the_gaps_divided(
    tmp_path,
):
    recorded = four_requests(tmp_path)
    covered = covering(tmp_path, recorded)
    baseline = Baseline(tmp_path)

    record = baseline.run(recorded, factor=2.0, approval=covered)

    assert record["outcome"] == "completed", record["outcome"]
    store = baseline.replay_store
    assert store.texts() == [
        "SELECT ?first",
        "SELECT ?second",
        "SELECT ?third",
        "SELECT ?fourth",
    ]
    # The recorded gaps are 1 s (from the window's start), 10 s, 10 s and 10 s: here 0.5 s, 5 s,
    # 5 s and 5 s.
    window_start = seconds_of(baseline, record["window"]["start"])
    assert [a.seconds - window_start for a in store.arrived] == [0.5, 5.5, 10.5, 15.5]
    # The measured window is as short: 30 s in place of the recorded 60 s.
    assert seconds_of(baseline, record["window"]["end"]) == window_start + 30.0
    assert record["measures"]["window"]["minutes"] == 0.5
    # The record says each request's offset, which its lateness is measured against.
    requests = record["measures"]["store_requests"]
    assert [r["offset_seconds"] for r in requests] == [0.5, 5.5, 10.5, 15.5]
    assert [r["late_seconds"] for r in requests] == [0.0, 0.0, 0.0, 0.0]


def test_a_higher_factor_compresses_the_gaps_further(tmp_path):
    recorded = four_requests(tmp_path)
    covered = covering(tmp_path, recorded)
    baseline = Baseline(tmp_path)

    record = baseline.run(recorded, factor=4.0, approval=covered)

    window_start = seconds_of(baseline, record["window"]["start"])
    assert [
        a.seconds - window_start for a in baseline.replay_store.arrived
    ] == pytest.approx([0.25, 2.75, 5.25, 7.75])
    assert seconds_of(baseline, record["window"]["end"]) == window_start + 15.0


def test_the_requests_before_the_window_are_not_compressed(tmp_path):
    """They go out one after the other, each after the answer to the one before: they have no
    offset to divide."""
    recorded = recorded_run(
        tmp_path,
        lambda clock: [
            read(clock, 2.0, "SELECT ?before"),
            read(clock, 31.0, "SELECT ?in_the_window"),
        ],
    )
    covered = covering(tmp_path, recorded)
    baseline = Baseline(tmp_path)

    record = baseline.run(recorded, factor=2.0, approval=covered)

    requests = record["measures"]["store_requests"]
    assert [(r["text"], r["window"], r["offset_seconds"]) for r in requests] == [
        ("SELECT ?before", False, None),
        ("SELECT ?in_the_window", True, 0.5),
    ]


def test_compressing_divides_each_offset_and_leaves_the_rest_as_it_is():
    requests = [
        Request("POST", "repositories/a", "q1", offset_seconds=None),
        Request("POST", "repositories/a", "q2", offset_seconds=1.0, keep_answer=True),
        Request("POST", "repositories/a", "q3", offset_seconds=10.0, accept="x"),
    ]

    assert compressed(requests, 1.0) == requests
    halved = compressed(requests, 2.0)
    assert [r.offset_seconds for r in halved] == [None, 0.5, 5.0]
    assert [(r.body, r.keep_answer, r.accept) for r in halved] == [
        ("q1", False, None),
        ("q2", True, None),
        ("q3", False, "x"),
    ]


# A factor of 1 gives the same replay as today.


def test_a_factor_of_one_gives_the_same_replay_as_none(tmp_path):
    recorded = recorded_run(
        tmp_path,
        lambda clock: [
            read(clock, 2.0, "SELECT ?first"),
            read(clock, 31.0, "SELECT ?in_the_window_at_1"),
            read(clock, 45.5, "SELECT ?in_the_window_at_15.5"),
            read(clock, 95.0, "SELECT ?after_the_window"),
        ],
    )
    default = Baseline(tmp_path / "default")
    one = Baseline(tmp_path / "one")

    without = default.run(recorded)
    with_one = one.run(recorded, factor=1.0)

    assert with_one["outcome"] == without["outcome"] == "completed"
    assert [(a.seconds, a.text) for a in one.replay_store.arrived] == [
        (a.seconds, a.text) for a in default.replay_store.arrived
    ]
    window_start = seconds_of(one, with_one["window"]["start"])
    assert [a.seconds - window_start for a in one.replay_store.arrived[1:]] == [
        1.0,
        15.5,
    ]
    assert with_one["window"] == without["window"]
    assert with_one["replay"] == without["replay"]
    # The path names the run's own repository, which has a random part.
    assert without_paths(with_one["measures"]) == without_paths(without["measures"])
    assert with_one["replay_level"] == without["replay_level"]
    assert with_one["heavy_run_approval"] is None
    assert with_one["replay_level"]["factor"] == 1.0


def without_paths(measures: dict) -> dict:
    return measures | {
        "store_requests": [
            {k: v for k, v in request.items() if k != "path"}
            for request in measures["store_requests"]
        ]
    }


def test_a_factor_of_one_needs_no_approval_and_a_factor_below_one_is_refused(tmp_path):
    recorded = four_requests(tmp_path)
    baseline = Baseline(tmp_path)

    assert baseline.run(recorded, factor=1.0)["outcome"] == "completed"
    with pytest.raises(BaselineRefused, match="a number from 1"):
        Baseline(tmp_path / "slow").run(recorded, factor=0.5)
    with pytest.raises(BaselineRefused, match="a number from 1"):
        Baseline(tmp_path / "nan").run(recorded, factor=float("nan"))


def test_the_factors_of_a_replay_are_numbers_from_one_and_every_one_above_one_is_heavy():
    assert [valid_factor(f) for f in (1, 1.0, 2, 4.5)] == [True] * 4
    assert [valid_factor(f) for f in (0, 0.5, -2, True, "2", None, float("inf"))] == [
        False
    ] * 7
    assert [is_heavy(f) for f in (1.0, 1.5, 2.0)] == [False, True, True]
    assert level_key("0123456789abcdef", 1.0) == "0123456789abcdef@x1"
    assert level_key("0123456789abcdef", 2.5) == "0123456789abcdef@x2.5"


# The record of each level holds the factor, the offered rate, the achieved rate, the refusals and
# the timeouts.


def test_the_record_of_a_level_holds_the_factor_the_rates_the_refusals_and_the_timeouts(
    tmp_path,
):
    recorded = four_requests(tmp_path)
    covered = covering(tmp_path, recorded)
    baseline = Baseline(
        tmp_path,
        answers={
            "SELECT ?second": refused(400, SHACL_REASON, SHACL_MEDIA_TYPE),
            "SELECT ?fourth": no_answer(at=25.0),
        },
    )

    record = baseline.run(recorded, factor=2.0, approval=covered)

    assert record["outcome"] == "completed", record["outcome"]
    level = record["replay_level"]
    assert level["factor"] == 2.0
    # Four requests in a window of 60 s over 2: 30 s, so 8 a minute.
    assert level["offered_per_minute"] == pytest.approx(8.0)
    # Three got an answer, a refusal too, the last at 10.5 s: 3 over the window of 30 s, since the
    # replay span is at least the window. The record keeps
    # the literal span.
    assert level["answered_in_window"] == 3
    assert level["replay_span_seconds"] == pytest.approx(10.5)
    assert level["achieved_per_minute"] == pytest.approx(6.0)
    assert level["requests_in_window"] == 4
    assert (level["refused_in_window"], level["timed_out_in_window"]) == (1, 1)
    assert level["heap_refusals_in_window"] == 0
    measures = record["measures"]
    assert (measures["refused_in_window"], measures["timed_out_in_window"]) == (1, 1)
    assert level["broken"] is True
    # 6 a minute is below 90 % of the 8 offered: the level, broken by its unanswered and refused
    # requests already, names the achieved rate too.
    assert [s["signal"] for s in level["signals"]] == [
        "refused or unanswered",
        ACHIEVED_BELOW_OFFERED,
    ]
    assert level["signals"][0]["share"] == pytest.approx(0.5)
    assert level["thresholds"] == {
        "refused_or_unanswered_share": 0.01,
        "achieved_share_of_offered": 0.9,
    }
    assert (
        record["heavy_run_approval"]
        == read_approval(tmp_path / "approval.toml").record()
    )


def test_a_level_that_the_store_keeps_up_with_is_not_broken(tmp_path):
    recorded = four_requests(tmp_path)
    baseline = Baseline(tmp_path)

    record = baseline.run(recorded)

    level = record["replay_level"]
    assert level["broken"] is False
    assert level["signals"] == []
    # Four requests in the recorded minute: 4 a minute offered; the last answered at 31 s, so 4 over
    # the window, the offered rate (the replay span is at least the window).
    assert level["offered_per_minute"] == pytest.approx(4.0)
    assert level["replay_span_seconds"] == pytest.approx(31.0)
    assert level["achieved_per_minute"] == pytest.approx(4.0)


# A level is judged broken by the rule above, each signal on its own.


def sent(at: float, seconds: float = 0.0, *, status: int | None = 204) -> Sent:
    """One window request of a replay that left `at` seconds after the window's start, was
    answered `seconds` later with `status`, or got no answer (None)."""
    return Sent(
        request=Request("POST", "repositories/x", "q", offset_seconds=at),
        path="repositories/x",
        at=CLOCK_START + timedelta(seconds=at),
        late_seconds=0.0,
        seconds=seconds,
        status_code=status,
        error=None if status is not None else "ReadTimeout: read timed out",
    )


def level_of(
    window: list[Sent],
    *,
    factor: float = 1.0,
    refused_count: int = 0,
    timed_out: int = 0,
    heap: int = 0,
) -> dict[str, Any]:
    return level_record(
        factor=factor,
        recorded_window_seconds=WINDOW,
        window_start=CLOCK_START,
        window=window,
        refused=refused_count,
        timed_out=timed_out,
        heap_refusals=heap,
    )


def hundred(*, last_answer_at: float = 59.0, **options: Any) -> list[Sent]:
    """100 requests spread over the recorded minute, all answered, the last at `last_answer_at`."""
    window = [sent(i * 0.5) for i in range(99)]
    return [*window, sent(last_answer_at - 0.5, 0.5, **options)]


def test_a_level_with_no_signal_is_not_broken():
    level = level_of(hundred())

    assert level["broken"] is False
    assert level["signals"] == []
    assert level["offered_per_minute"] == pytest.approx(100.0)
    # The last answer at 59 s: 100 over the window of 60 s.
    assert level["achieved_per_minute"] == pytest.approx(100.0)


def test_a_refusal_for_want_of_heap_breaks_a_level_on_its_own():
    # 1 of 150 requests: well below 1 % refused, and the rates are met.
    window = [sent(i * 0.4) for i in range(150)]

    level = level_of(window, refused_count=1, heap=1)

    assert [s["signal"] for s in level["signals"]] == ["heap refusal"]
    assert level["signals"][0]["heap_refusals"] == 1
    assert level["broken"] is True


def test_more_than_one_percent_refused_or_unanswered_breaks_a_level():
    just_enough = level_of(hundred(), refused_count=1)
    too_many = level_of(hundred(), refused_count=1, timed_out=1)

    # Exactly 1 % is not more than 1 %.
    assert just_enough["broken"] is False
    assert too_many["broken"] is True
    assert [s["signal"] for s in too_many["signals"]] == ["refused or unanswered"]
    assert too_many["signals"][0]["share"] == pytest.approx(0.02)


def test_a_timeout_is_no_answer_and_counts_against_the_achieved_rate():
    window = [sent(i * 0.5) for i in range(98)] + [
        sent(49.0, 10.0, status=None),
        sent(49.5, 10.0, status=None),
    ]

    level = level_of(window, timed_out=2)

    assert level["answered_in_window"] == 98
    assert level["requests_in_window"] == 100
    assert {s["signal"] for s in level["signals"]} == {"refused or unanswered"}


def test_an_achieved_rate_below_ninety_percent_of_the_offered_rate_breaks_a_level():
    # 100 requests offered in the recorded minute; the last answer comes at 80 s.
    slow = level_of(hundred(last_answer_at=80.0))
    # At 64 s the store keeps up with 60/64 = 94 % of the offer.
    nearly = level_of(hundred(last_answer_at=64.0))

    assert [s["signal"] for s in slow["signals"]] == [ACHIEVED_BELOW_OFFERED]
    assert slow["signals"][0]["share"] == pytest.approx(0.75)
    assert slow["achieved_per_minute"] == pytest.approx(75.0)
    assert slow["offered_per_minute"] == pytest.approx(100.0)
    assert slow["broken"] is True
    assert nearly["broken"] is False
    assert nearly["achieved_per_minute"] == pytest.approx(100 / (64.0 / 60))


def test_the_offered_rate_is_the_window_s_requests_over_its_length_over_the_factor():
    window = hundred()

    assert level_of(window, factor=1.0)["offered_per_minute"] == pytest.approx(100.0)
    assert level_of(window, factor=2.0)["offered_per_minute"] == pytest.approx(200.0)
    assert level_of(window, factor=4.0)["offered_per_minute"] == pytest.approx(400.0)


def test_a_level_none_of_whose_requests_got_an_answer_has_an_achieved_rate_of_zero():
    window = [sent(i * 0.5, 60.0, status=None) for i in range(10)]

    level = level_of(window, timed_out=10)

    assert level["achieved_per_minute"] == 0.0
    assert level["replay_span_seconds"] is None
    assert level["broken"] is True
    assert {s["signal"] for s in level["signals"]} == {
        "refused or unanswered",
        ACHIEVED_BELOW_OFFERED,
    }


def test_a_window_without_a_request_is_not_broken():
    level = level_of([])

    assert level["offered_per_minute"] == 0.0
    assert level["achieved_per_minute"] is None
    assert level["broken"] is False


def test_a_refusal_for_want_of_heap_is_named_in_the_record_of_a_replay(tmp_path):
    recorded = four_requests(tmp_path)
    baseline = Baseline(
        tmp_path,
        answers={"SELECT ?third": refused(500, HEAP_REASON)},
    )

    record = baseline.run(recorded)

    level = record["replay_level"]
    assert level["heap_refusals_in_window"] == 1
    assert "heap refusal" in [s["signal"] for s in level["signals"]]
    assert measured(record)["SELECT ?third"]["refusal"] == "heap"


# A factor above 1 refuses to start without an approval that covers the spec.


def test_a_factor_above_one_is_refused_without_an_approval_and_nothing_is_sent(
    tmp_path,
):
    recorded = four_requests(tmp_path)
    baseline = Baseline(tmp_path)

    with pytest.raises(HeavyRunRefused, match="any person may approve") as refused_run:
        baseline.run(recorded, factor=2.0)

    assert "factor 2" in str(refused_run.value)
    assert baseline.store.requests == []
    assert baseline.replay_store.arrived == []
    assert not baseline.runs.exists()


def test_a_factor_above_one_takes_only_the_covered_record_of_its_own_configuration(
    tmp_path,
):
    recorded = four_requests(tmp_path)
    (tmp_path / "one").mkdir()
    covered = covering(tmp_path / "one", recorded)
    other = recorded_run(
        tmp_path / "other",
        lambda clock: [read(clock, 31.0, "SELECT ?x")],
        replace(WARM_UP, boxes_per_minute=9.0),
    )
    for given, named in (
        (covered.approval, "nothing has checked"),
        (covered, "another"),
    ):
        baseline = Baseline(tmp_path / named.split()[0])

        with pytest.raises(HeavyRunRefused, match=named):
            baseline.run(other, approval=given, factor=2.0)

        assert baseline.store.requests == []
        assert baseline.replay_store.arrived == []
        assert not baseline.runs.exists()


def test_the_heavy_switch_stays_off_by_default(tmp_path):
    recorded = four_requests(tmp_path)

    record = Baseline(tmp_path).run(recorded)

    assert record["heavy_run_approval"] is None
    assert record["outcome"] == "completed"


# The single command.


class StandIn:
    """In place of a baseline run on the configured store: it keeps what it was given."""

    def __init__(self) -> None:
        self.made: list[tuple[Any, dict[str, Any]]] = []

    def __call__(self, what: Any, **options: Any) -> dict[str, Any]:
        self.made.append((what, options))
        return {"outcome": "completed", "run_id": "stand-in"}


def command(tmp_path: Path, recorded: Path, *more: str) -> list[str]:
    return [str(recorded), "--runs", str(tmp_path / "b"), *more]


def test_the_command_replays_at_the_factor_it_is_given_under_an_approval(
    tmp_path, store, monkeypatch
):
    recorded = four_requests(tmp_path)
    # The approval of the stand-in's spec is for the real clock here: the command checks it
    # against now.
    covering(tmp_path, recorded)
    spec = tmp_path / "spec.toml"
    approval = approval_file(tmp_path, spec, name="now.toml")
    stand_in = StandIn()
    monkeypatch.setattr(baseline_module, "run_on_the_configured_store", stand_in)

    code = baseline_module.main(
        command(
            tmp_path,
            recorded,
            "--factor",
            "4",
            "--spec",
            str(spec),
            "--heavy-run-approval",
            str(approval),
        )
    )

    assert code == 0
    [(_, options)] = stand_in.made
    assert options["factor"] == 4.0
    assert options["approval"].approval == read_approval(approval)


def test_the_command_without_a_factor_replays_at_one_and_needs_no_approval(
    tmp_path, store, monkeypatch
):
    recorded = four_requests(tmp_path)
    stand_in = StandIn()
    monkeypatch.setattr(baseline_module, "run_on_the_configured_store", stand_in)

    assert baseline_module.main(command(tmp_path, recorded)) == 0
    assert baseline_module.main(command(tmp_path, recorded, "--factor", "1")) == 0

    assert [options["factor"] for _, options in stand_in.made] == [1.0, 1.0]
    assert [options["approval"] for _, options in stand_in.made] == [None, None]


@pytest.mark.parametrize(
    "case",
    [
        "no approval",
        "no spec given",
        "ended",
        "another spec",
        "no key",
        "a key the spec does not list",
    ],
)
def test_the_command_refuses_a_factor_above_one_that_no_approval_covers_before_it_reads_the_store(
    tmp_path, store, monkeypatch, capsys, case
):
    recorded = four_requests(tmp_path)
    covering(tmp_path, recorded)
    spec = tmp_path / "spec.toml"
    other = tmp_path / "other.toml"
    other.write_text(spec.read_text() + "# other\n", encoding="utf-8")
    if case in ("no key", "a key the spec does not list"):
        record = json.loads((recorded / "record.json").read_text())
        record["sweep"] = (
            None if case == "no key" else {"spec": str(spec), "key": "0123456789abcdef"}
        )
        (recorded / "record.json").write_text(json.dumps(record))
    now = datetime.now(UTC)
    approval = approval_file(
        tmp_path,
        other if case == "another spec" else spec,
        **(
            {"start": now - timedelta(days=2), "end": now - timedelta(days=1)}
            if case == "ended"
            else {}
        ),
    )
    stand_in = StandIn()
    monkeypatch.setattr(baseline_module, "run_on_the_configured_store", stand_in)
    options = ["--factor", "2"]
    if case != "no spec given":
        options += ["--spec", str(spec)]
    if case != "no approval":
        options += ["--heavy-run-approval", str(approval)]

    code = baseline_module.main(command(tmp_path, recorded, *options))

    assert code == 2
    error = capsys.readouterr().err
    assert {
        "no approval": "any person may approve",
        "no spec given": "no spec",
        "ended": "outside the period",
        "another spec": "it is another spec",
        "no key": "holds no configuration key",
        "a key the spec does not list": "is no configuration of the approved spec",
    }[case] in error
    if case == "no approval":
        assert "factor 2" in error
    assert stand_in.made == []
    assert not (tmp_path / "b").exists()


def test_the_command_refuses_a_factor_below_one_before_anything_is_read(
    tmp_path, monkeypatch, capsys
):
    stand_in = StandIn()
    monkeypatch.setattr(baseline_module, "run_on_the_configured_store", stand_in)

    code = baseline_module.main(
        command(tmp_path, tmp_path / "no-such-run", "--factor", "0.5")
    )

    assert code == 2
    assert "--factor is a number from 1" in capsys.readouterr().err
    assert stand_in.made == []
