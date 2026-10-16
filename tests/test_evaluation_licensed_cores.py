"""The ramp-judgement table shows the licensed cores beside the first broken level of the replay
(jms_kapps 108, from Sören's decision of 2026-10-13 as the product owner of the tests, jms_kapps 85,
note 1866296, decision 1): GraphDB Enterprise runs at most one concurrent query for each licensed
core, so the compressed replay's first broken level can be this limit of the license.

The seam is `jms-eval-table` (`make_table`, `markdown`, `main`) on fabricated records, as the tests
of the ramp's judgement build them (`test_evaluation_table_ramp`). No store, no run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from _evaluation_records import write
from test_evaluation_table_ramp import (  # noqa: F401
    level,
    limited,
    no_network,
    ramp_of,
    replay,
    spec,
    table_of,
    uc2,
)

import jms_kapps
from jms_kapps.evaluation import table
from jms_kapps.evaluation.table import markdown

EVALUATION = Path(jms_kapps.__file__).parent / "evaluation"
LICENSE_REQUEST = "GET rest/graphdb-settings/license"


def licensed(found: dict[str, Any], view: dict[str, Any] | None) -> dict[str, Any]:
    """`found`, a record, with the license view `view` as the server watch keeps it, or none."""
    if view is not None:
        found["server"] = {"license": view}
    return found


def read_license(cores: Any) -> dict[str, Any]:
    return {
        "request": LICENSE_REQUEST,
        "status": 200,
        "answer": {"edition": "Enterprise", "maxCpuCores": cores},
    }


def replays_with(spec, views: dict[float, list[dict[str, Any] | None]], broken_at):  # noqa: F811
    """The run of configuration 1 and its baselines at 1, 2, 4 and 8, broken from `broken_at` on
    (None: never), each level with the license views given for its factor (one record each)."""
    found = [uc2(spec, 1)]
    for factor in (1.0, 2.0, 4.0, 8.0):
        for n, view in enumerate(views.get(factor, [read_license(4)])):
            found.append(
                licensed(
                    replay(
                        spec,
                        f"replay-1-{factor:g}-{n}",
                        factor,
                        broken=broken_at is not None and factor >= broken_at,
                    ),
                    view,
                )
            )
    return found


def ramp_levels(spec):  # noqa: F811
    return [level(spec, 2, "r-10"), limited(spec, 3, "r-20")]


def test_each_ramp_s_comparison_gives_the_licensed_cores_of_its_replay(
    spec,  # noqa: F811
    tmp_path,
):
    runs = tmp_path / "runs"
    runs.mkdir()
    write(runs, *ramp_levels(spec), *replays_with(spec, {}, broken_at=4.0))

    code = table.main(
        [str(tmp_path / "spec.toml"), "--runs", str(runs), "--resamples", "50"]
    )

    assert code == 0
    [ramp] = [
        r
        for r in json.loads((runs / "table.json").read_text())["ramps"]
        if r["block"] == 2
    ]
    assert ramp["comparison"]["replay"]["licensed_cores"] == [4]
    assert ramp["comparison"]["replay"]["licensed_cores_text"] == "4"
    shown = (runs / "table.md").read_text(encoding="utf-8")
    assert (
        "Replay of configuration spec 1: broken at factor 4, offered 400 store requests per "
        "minute. Licensed cores: 4."
    ) in shown


def test_the_cores_come_from_the_first_broken_level_else_from_the_highest(spec):  # noqa: F811
    broken = table_of(
        spec,
        [
            *ramp_levels(spec),
            *replays_with(
                spec, {2.0: [read_license(2)], 4.0: [read_license(4)]}, broken_at=4.0
            ),
        ],
    )
    unbroken = table_of(
        spec,
        [
            *ramp_levels(spec),
            *replays_with(
                spec, {4.0: [read_license(4)], 8.0: [read_license(16)]}, None
            ),
        ],
    )

    assert ramp_of(broken, 2)["comparison"]["replay"]["licensed_cores"] == [4]
    assert ramp_of(unbroken, 2)["comparison"]["replay"]["licensed_cores"] == [16]


def test_different_values_in_the_records_show_each_value(spec):  # noqa: F811
    made = table_of(
        spec,
        [
            *ramp_levels(spec),
            *replays_with(
                spec, {4.0: [read_license(8), read_license(4)]}, broken_at=4.0
            ),
        ],
    )

    replay_side = ramp_of(made, 2)["comparison"]["replay"]
    assert replay_side["licensed_cores"] == [4, 8]
    assert replay_side["licensed_cores_text"] == "4, 8"
    assert "Licensed cores: 4, 8." in markdown(made)


@pytest.mark.parametrize(
    "view",
    [
        None,
        {"request": LICENSE_REQUEST, "status": 403, "error": "Forbidden"},
        {"request": LICENSE_REQUEST, "error": "ConnectError: refused"},
        {"request": LICENSE_REQUEST, "status": 200, "answer": {"edition": "Free"}},
        {"request": LICENSE_REQUEST, "status": 200, "answer": "not JSON"},
    ],
    ids=["no view", "a refused read", "a failed read", "no maximum", "no JSON"],
)
def test_no_license_view_a_failed_read_or_no_maximum_shows_not_read(spec, view):  # noqa: F811
    made = table_of(
        spec, [*ramp_levels(spec), *replays_with(spec, {4.0: [view]}, broken_at=4.0)]
    )

    replay_side = ramp_of(made, 2)["comparison"]["replay"]
    assert replay_side["licensed_cores"] == []
    assert replay_side["licensed_cores_text"] == "not read"
    assert "Licensed cores: not read." in markdown(made)


def test_a_ramp_with_no_replay_shows_no_licensed_cores(spec):  # noqa: F811
    """Block 4's ramp (8, 9) has history collection on, which no UC2 configuration has."""
    made = table_of(
        spec,
        [
            level(spec, 8, "h-10"),
            level(spec, 9, "h-20"),
            *replays_with(spec, {}, broken_at=4.0),
        ],
    )

    replay_side = ramp_of(made, 4)["comparison"]["replay"]
    assert replay_side["outcome"] == "no replay"
    assert replay_side["licensed_cores"] is None
    assert replay_side["licensed_cores_text"] is None
    shown = markdown(made)
    section = shown.split("### The ramp of block 4")[1].split("\n### ")[0]
    assert "Replay: no replay." in section
    assert "Licensed cores" not in section


def test_the_ramp_section_and_the_readme_say_what_the_number_means(spec):  # noqa: F811
    shown = " ".join(
        markdown(table_of(spec, [*ramp_levels(spec), *replays_with(spec, {}, 4.0)]))
        .split("## Ramps", 1)[1]
        .split()
    )
    meaning = (
        "GraphDB Enterprise runs at most one concurrent query for each licensed core"
    )
    assert meaning in shown
    readme = " ".join((EVALUATION / "README.md").read_text(encoding="utf-8").split())
    assert meaning in readme
    assert "jms_kapps 108" in readme
