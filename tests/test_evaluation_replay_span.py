"""The replay span is at least the window (jms_kapps 107, from Sören's decision of 2026-10-13 as the
product owner of the tests, jms_kapps 85, note 1866296, the changed reading "The replay span is read
literally"): the achieved rate of a replay level is its answered requests over the larger of its
replay span and its window, so it is never more than the offered rate, and 100 % means that the
store kept up. The record keeps the literal span.

The seams: a replay level's record (`replay_levels.level_record`), the store-only baseline against
its stand-in store (`test_evaluation_baseline`), and `jms-eval-table`'s text. No store, no run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_evaluation_baseline import Baseline, no_answer, read, recorded_run
from test_evaluation_replay_levels import hundred, level_of, sent
from test_evaluation_table_replay import baseline as baseline_record
from test_evaluation_table_replay import one, spec_path, table_of, uc2  # noqa: F401

import jms_kapps
from jms_kapps.evaluation.replay_levels import ACHIEVED_BELOW_OFFERED
from jms_kapps.evaluation.table import markdown

EVALUATION = Path(jms_kapps.__file__).parent / "evaluation"


@pytest.fixture(autouse=True)
def no_store(monkeypatch):
    for prefix in ("GRAPHDB", "TRIPLESTORE"):
        for name in ("URL", "USERNAME", "PASSWORD"):
            monkeypatch.delenv(f"{prefix}_{name}", raising=False)


def four_requests(tmp_path: Path) -> Path:
    """A recorded run with four requests in its window of 60 s, at 1 s, 11 s, 21 s and 31 s."""
    return recorded_run(
        tmp_path,
        lambda clock: [
            read(clock, 31.0, "SELECT ?first"),
            read(clock, 41.0, "SELECT ?second"),
            read(clock, 51.0, "SELECT ?third"),
            read(clock, 61.0, "SELECT ?fourth"),
        ],
    )


def test_a_level_whose_last_answer_comes_before_the_window_s_end_achieves_its_answers_over_the_window():
    """100 requests in the recorded minute, all answered, the last at 59 s: 100 a minute, the
    offered rate, where the literal span gave 101.7 a minute."""
    level = level_of(hundred(last_answer_at=59.0))

    assert level["achieved_per_minute"] == pytest.approx(100.0)
    assert level["achieved_per_minute"] <= level["offered_per_minute"]
    # The record keeps the literal span beside the achieved rate.
    assert level["replay_span_seconds"] == pytest.approx(59.0)
    assert level["broken"] is False


def test_with_a_fake_store_the_rate_is_not_more_than_the_offered_rate_when_every_request_is_answered(
    tmp_path,
):
    """The store answers each of the four requests at once; the last answer comes at 31 s of the
    window of 60 s."""
    baseline = Baseline(tmp_path)

    record = baseline.run(four_requests(tmp_path))

    level = record["replay_level"]
    assert level["answered_in_window"] == 4
    assert level["offered_per_minute"] == pytest.approx(4.0)
    assert level["achieved_per_minute"] == pytest.approx(4.0)
    assert level["replay_span_seconds"] == pytest.approx(31.0)
    assert level["broken"] is False


def test_a_level_whose_last_answer_comes_after_the_window_s_end_achieves_the_same_rate_as_before():
    """The levels of the replay-levels tests: the last answer at 80 s, 75 a minute, and at 64 s,
    93.75 a minute, each its answers over its span, as before."""
    slow = level_of(hundred(last_answer_at=80.0))
    nearly = level_of(hundred(last_answer_at=64.0))

    assert slow["achieved_per_minute"] == pytest.approx(75.0)
    assert slow["replay_span_seconds"] == pytest.approx(80.0)
    assert nearly["achieved_per_minute"] == pytest.approx(100 / (64.0 / 60))
    assert nearly["replay_span_seconds"] == pytest.approx(64.0)


def test_a_rate_above_the_offered_one_falls_to_the_answers_over_the_window_and_may_name_the_signal():
    """85 of 100 requests answered, the last at 50 s: the literal span gave 102 a minute, above
    the offered 100; over the window it is 85 a minute, below 90 %. The level was broken by the
    unanswered requests already, and now names the achieved rate too."""
    window = [sent(i * 0.5) for i in range(84)] + [
        sent(49.5, 0.5),
        *[sent(42.0 + i * 0.5, 30.0, status=None) for i in range(15)],
    ]

    level = level_of(window, timed_out=15)

    assert level["answered_in_window"] == 85
    assert level["replay_span_seconds"] == pytest.approx(50.0)
    assert level["achieved_per_minute"] == pytest.approx(85.0)
    assert level["broken"] is True
    assert [s["signal"] for s in level["signals"]] == [
        "refused or unanswered",
        ACHIEVED_BELOW_OFFERED,
    ]


def test_a_fake_store_that_answers_every_request_but_one_achieves_less_than_the_offer(
    tmp_path,
):
    """Three of four answered, the last at 21 s: 3 a minute over the window, 75 % of the offer;
    a level that falls below 90 % this way names the achieved rate too."""
    baseline = Baseline(tmp_path, answers={"SELECT ?fourth": no_answer(at=50.0)})

    record = baseline.run(four_requests(tmp_path))

    level = record["replay_level"]
    assert level["replay_span_seconds"] == pytest.approx(21.0)
    assert level["achieved_per_minute"] == pytest.approx(3.0)
    assert ACHIEVED_BELOW_OFFERED in [s["signal"] for s in level["signals"]]


def test_the_documentation_states_the_rule(spec_path, one):  # noqa: F811
    readme = " ".join((EVALUATION / "README.md").read_text(encoding="utf-8").split())
    assert "the replay span is at least the window" in readme.replace("*", "").lower()
    assert "jms_kapps 107" in readme
    from jms_kapps.evaluation import replay_levels

    assert "at least the window" in " ".join((replay_levels.__doc__ or "").split())
    shown = markdown(table_of(spec_path, one, [uc2(one), baseline_record("b1", 1.0)]))
    section = " ".join(shown.split("## Replay levels")[1].split("\n## ")[0].split())
    assert "at least the window" in section
