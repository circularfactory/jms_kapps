"""The §7 figure script (`evaluation/section_7/figure.py`), loaded from its file and run on
fabricated folders of the experiment's shape (`_section_7_runs`): the files it writes, the figures
its functions return, the percentile numbers against the harness's statistics, and the plots it
skips with why. No store, no run.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import warnings
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest
from _section_7_runs import (
    kapps_record,
    kapps_spans,
    replay_record,
    replay_writes,
    write_run,
)
from matplotlib.backends.backend_agg import FigureCanvasAgg

from jms_kapps.evaluation import commit_table, stats

SCRIPT = Path(__file__).resolve().parents[1] / "evaluation" / "section_7" / "figure.py"


@pytest.fixture(scope="module")
def figure() -> ModuleType:
    spec = importlib.util.spec_from_file_location("section_7_figure", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # A dataclass looks its module up by name while it is made.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _experiment(root: Path) -> Path:
    """Two store folders, two heartbeat levels each, with two SHACL-on runs and their replays and
    one SHACL-off run in each level."""
    for s, store in enumerate(("local", "kit")):
        for h, (level, interval) in enumerate((("on", 30.0), ("off", None))):
            runs = root / store / f"heartbeats-{level}" / "runs"
            for repetition in (1, 2):
                run_id = f"20261025-10{s}{h}{repetition}0-on{repetition}{s}"
                write_run(
                    runs,
                    kapps_record(
                        run_id, repetition=repetition, heartbeat_interval=interval
                    ),
                    kapps_spans(f"{run_id}-"),
                )
                write_run(
                    runs,
                    replay_record(
                        f"20261025-11{s}{h}{repetition}0-rp{repetition}{s}",
                        run_id,
                        replay_writes(f"{run_id}-"),
                    ),
                    None,
                )
            off_id = f"20261025-10{s}{h}30-off{s}"
            write_run(
                runs,
                kapps_record(off_id, shacl=False, heartbeat_interval=interval),
                kapps_spans(f"{off_id}-"),
            )
    return root


def _tables(folders: list[Path]) -> tuple[pd.DataFrame, pd.DataFrame]:
    runs = commit_table.read_runs(folders)
    commits, replayed = [], []
    for run in runs.kapps:
        replay = runs.replays.get(run.run_id)
        commits += commit_table.commit_rows(run, replay) or []
        if replay is not None:
            replayed += commit_table.replayed_write_rows(replay, run)
    return (
        commit_table.table(commits, commit_table.COMMIT_COLUMNS),
        commit_table.table(replayed, commit_table.REPLAYED_WRITE_COLUMNS),
    )


def test_the_script_writes_every_file_for_two_stores_and_two_levels(
    figure: ModuleType, tmp_path: Path
) -> None:
    root = _experiment(tmp_path / "results")
    output = tmp_path / "figures"
    assert (
        figure.main([str(root / "kit"), str(root / "local"), "--output", str(output)])
        == 0
    )
    names = {path.name for path in output.iterdir()}
    for kind in ("cdf", "box"):
        for level in ("on", "off"):
            for suffix in ("pdf", "png"):
                assert f"commit-latency-{kind}-heartbeats-{level}.{suffix}" in names
    for name in (
        "commits.csv",
        "replayed_writes.csv",
        "percentiles.csv",
        "phases.csv",
        "phases.md",
        "figures.json",
    ):
        assert name in names
    commits = pd.read_csv(output / "commits.csv")
    assert len(commits) == 5 * 3 * 2 * 2
    assert len(pd.read_csv(output / "replayed_writes.csv")) == 3 * 2 * 2 * 2
    manifest = json.loads((output / "figures.json").read_text(encoding="utf-8"))
    assert set(manifest["made"]) == {f.__name__ for f in figure.FIGURES}
    [counts] = [
        c
        for c in manifest["commit_counts"]
        if (c["store"], c["heartbeats"], c["arm"]) == ("kit", "on", "kapps-shacl-on")
    ]
    assert counts["admitted_in_window"] == 6
    assert counts["paired"] == 2
    assert counts["without_a_replayed_update"] == 4
    assert counts["kind_mix"]["heartbeat"] == pytest.approx(1 / 3)
    assert counts["not_admitted_in_window_by_kind"] == {"no write": 2}
    assert list(counts["not_admitted_in_window_by_run"].values()) == [1, 1]
    assert "commit-latency-cdf-heartbeats-on" in manifest["captions"]
    assert len(manifest["runs"]) == 2 * 2 * 5


def test_the_cdf_has_a_panel_per_store_three_curves_and_the_transit_line(
    figure: ModuleType, tmp_path: Path
) -> None:
    root = _experiment(tmp_path / "results")
    commits, replayed = _tables([root / "kit", root / "local"])
    made = figure.cdf_figure(commits, replayed, tmp_path)
    assert len(made.figures) == 2
    axes = made.figures[0].axes
    assert [ax.get_title() for ax in axes] == ["KIT GraphDB", "local GraphDB"]
    # Side by side, KIT left.
    assert axes[0].get_position().x0 < axes[1].get_position().x0
    for ax in axes:
        assert ax.get_xscale() == "log"
        curves = [line for line in ax.lines if line.get_gid() != figure.TRANSIT_GID]
        assert [line.get_linestyle() for line in curves] == ["-", "--", "-."]
        assert len({line.get_marker() for line in curves}) == 3
        assert [line.get_label() for line in curves] == [
            "writes only",
            "KAPPS, SHACL off",
            "KAPPS, SHACL on",
        ]
        [transit] = [line for line in ax.lines if line.get_gid() == figure.TRANSIT_GID]
        assert list(transit.get_xdata()) == [3000, 3000]
        assert transit.get_linestyle() == "--"
        # Labelled on the line, not in the legend.
        [label] = [text for text in ax.texts if text.get_gid() == figure.TRANSIT_GID]
        assert label.get_text() == figure.TRANSIT_LABEL
        assert ax.get_legend() is None
    width, _ = made.figures[0].get_size_inches()
    assert width == pytest.approx(8.5 / 2.54)


def test_the_cdf_legend_covers_no_curve_and_gives_n_for_each_store(
    figure: ModuleType, tmp_path: Path
) -> None:
    # Inside a panel at column width, the legend hid the lower part of every curve.
    root = _experiment(tmp_path / "results")
    commits, replayed = _tables([root / "kit", root / "local"])
    made = figure.cdf_figure(commits, replayed, tmp_path)
    for drawn in made.figures:
        # Drawn on a raster canvas at the figure's size, so that the extents are the printed ones.
        canvas = FigureCanvasAgg(drawn)
        canvas.draw()
        renderer = canvas.get_renderer()
        [legend] = drawn.legends
        box = legend.get_window_extent(renderer)
        for ax in drawn.axes:
            assert not box.overlaps(ax.get_window_extent(renderer))
        n_kit = len(
            figure.pooled(figure.curve_values(commits, "kit", "on", "kapps-shacl-on"))
        )
        n_local = len(
            figure.pooled(figure.curve_values(commits, "local", "on", "kapps-shacl-on"))
        )
        assert [text.get_text() for text in legend.get_texts()][2] == (
            f"KAPPS, SHACL on (n = {n_kit} KIT, {n_local} local)"
        )


def test_the_box_plot_has_its_whiskers_outliers_and_log_scale(
    figure: ModuleType, tmp_path: Path
) -> None:
    root = _experiment(tmp_path / "results")
    commits, replayed = _tables([root / "kit"])
    made = figure.box_figure(commits, replayed, tmp_path)
    [ax] = made.figures[0].axes
    assert ax.get_xscale() == "log"
    labels = [label.get_text() for label in ax.get_yticklabels()]
    assert labels == ["writes only", "KAPPS, SHACL off", "KAPPS, SHACL on"]
    notes = [text.get_text() for text in ax.texts if text.get_gid() is None]
    assert len(notes) == 3 and all("beyond whiskers" in note for note in notes)
    # The transit time is labelled on its line, right of it, where the axis makes room.
    [label] = [text for text in ax.texts if text.get_gid() == figure.TRANSIT_GID]
    assert label.get_text() == figure.TRANSIT_LABEL
    assert ax.get_xlim()[1] == pytest.approx(figure.TRANSIT_MS * figure.TRANSIT_ROOM)
    assert figure.WHISKERS == (1, 99)
    on = [
        v * 1000
        for v in figure.pooled(
            figure.curve_values(commits, "kit", "on", "kapps-shacl-on")
        )
    ]
    high = stats.quantile(on, 0.99)
    assert any(list(line.get_xdata()) == [high, high] for line in ax.lines)
    hollow = [line for line in ax.lines if line.get_markerfacecolor() == "none"]
    assert hollow and all(line.get_alpha() == figure.OUTLIER_ALPHA for line in hollow)


def test_the_box_plot_keeps_its_panels_wide(figure: ModuleType, tmp_path: Path) -> None:
    # Its notes of n and the share beyond the whiskers once took part in the layout, which
    # squeezed each panel to a sliver ("axes sizes collapsed to zero").
    root = _experiment(tmp_path / "results")
    commits, replayed = _tables([root / "kit", root / "local"])
    # At the latencies of the runs of 2026-10-26, from about 15 ms (writes only) to 700 ms: the
    # fabricated runs' 50 ms to 1 s would leave the log axis under two decades, and its minor
    # labels would narrow the panels.
    for column in ("seconds", "replayed_update_seconds"):
        commits[column] = commits[column] * 0.3
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message=".*collapsed to zero.*")
        made = figure.box_figure(commits, replayed, tmp_path)
    for drawn in made.figures:
        assert drawn.get_supxlabel() == figure.LATENCY_LABEL
        canvas = FigureCanvasAgg(drawn)
        canvas.draw()
        renderer = canvas.get_renderer()
        for ax in drawn.axes:
            assert ax.get_position().width > 0.2
            assert ax.get_xlabel() == ""
            # The transit time's label overprinted the notes' ends ("whiskers(3 s)").
            [label] = [
                text for text in ax.texts if text.get_gid() == figure.TRANSIT_GID
            ]
            notes = [text for text in ax.texts if text.get_gid() is None]
            for note in notes:
                assert not label.get_window_extent(renderer).overlaps(
                    note.get_window_extent(renderer)
                )


def test_the_box_plot_cuts_no_box_of_a_faster_store(
    figure: ModuleType, tmp_path: Path
) -> None:
    # The room for the transit label was once set panel by panel, which froze the shared axis at
    # the first (KIT) panel's data and cut the local store's faster writes-only box at the left.
    root = _experiment(tmp_path / "results")
    commits, replayed = _tables([root / "kit", root / "local"])
    local = commits["store"] == "local"
    for column in ("seconds", "replayed_update_seconds"):
        commits.loc[local, column] = commits.loc[local, column] * 0.1
    made = figure.box_figure(commits, replayed, tmp_path)
    for drawn in made.figures:
        for ax in drawn.axes:
            low, high = ax.get_xlim()
            for line in ax.lines:
                if line.get_gid() == figure.TRANSIT_GID:
                    continue
                xs = [float(x) for x in line.get_xdata()]
                if xs:
                    assert low <= min(xs) and max(xs) <= high, ax.get_title()
            assert high == pytest.approx(figure.TRANSIT_MS * figure.TRANSIT_ROOM)


def test_the_transit_line_is_labelled_on_the_line_along_either_axis(
    figure: ModuleType,
) -> None:
    drawn = figure.Figure()
    ax = drawn.subplots()
    figure.transit_line(ax, figure.TRANSIT_MS / figure.MS_PER_SECOND, axis="y")
    [line] = ax.lines
    assert list(line.get_ydata()) == [3.0, 3.0]
    assert line.get_gid() == figure.TRANSIT_GID
    assert line.get_label().startswith("_")
    [label] = ax.texts
    assert label.get_text() == figure.TRANSIT_LABEL
    assert label.get_gid() == figure.TRANSIT_GID
    # Under the middle of the line: above it, the label ran into the frame where 3 s is near the
    # top (B9), and at the right end the window's end guide crossed it (C1).
    assert label.xy == (0.5, 3.0)
    assert label.get_verticalalignment() == "top"
    assert label.get_horizontalalignment() == "center"
    figure.transit_line(ax, 3.0, axis="x", text=figure.TRANSIT_SHORT_LABEL)
    assert ax.texts[-1].get_text() == figure.TRANSIT_SHORT_LABEL


def test_the_percentiles_are_the_harness_statistics(
    figure: ModuleType, tmp_path: Path
) -> None:
    root = _experiment(tmp_path / "results")
    commits, replayed = _tables([root / "kit", root / "local"])
    figure.percentiles_csv(commits, replayed, tmp_path)
    found = pd.read_csv(tmp_path / "percentiles.csv")
    on = figure.curve_values(commits, "kit", "off", "kapps-shacl-on")
    expected = stats.pooled_quantiles(
        on,
        [0.5, 0.95, 0.99],
        seed=0,
        drawn_for="kit/off/kapps-shacl-on/latency",
        resamples=10_000,
    )
    rows = found[
        (found["store"] == "kit")
        & (found["heartbeats"] == "off")
        & (found["measure"] == "latency")
        & (found["curve"] == "kapps-shacl-on")
    ]
    assert list(rows["statistic"]) == ["p50", "p95", "p99"]
    assert list(rows["value_seconds"]) == pytest.approx([e["value"] for e in expected])
    assert list(rows["interval_low_seconds"]) == pytest.approx(
        [e["interval"][0] for e in expected]
    )
    off = figure.curve_values(commits, "kit", "off", "kapps-shacl-off")
    difference = stats.difference_of_means(
        on, off, seed=0, drawn_for="kit/off/shacl on minus off", resamples=10_000
    )
    [row] = found[
        (found["store"] == "kit")
        & (found["heartbeats"] == "off")
        & (found["statistic"] == "mean difference")
    ].to_dict("records")
    assert row["value_seconds"] == pytest.approx(difference["value"])
    paired = found[found["measure"].str.contains("minus writes only")]
    assert set(paired["statistic"]) == {"p50", "p95"}
    # c1's commit of 1 s against its replayed update of 0.15 s, in each run.
    assert paired["value_seconds"].iloc[0] == pytest.approx(0.85)
    assert paired["n"].iloc[0] == 2


def test_the_phase_table(figure: ModuleType, tmp_path: Path) -> None:
    root = _experiment(tmp_path / "results")
    commits, replayed = _tables([root / "local"])
    figure.phase_table(commits, replayed, tmp_path)
    found = pd.read_csv(tmp_path / "phases.csv")
    on = found[(found["heartbeats"] == "on") & (found["arm"] == "kapps-shacl-on")]
    parts = on[on["part"].isin(commit_table.PHASES)]
    assert parts["share_of_commit_mean"].sum() == pytest.approx(1.0)
    assert figure.WRITES_ONLY_PART in set(on["part"])
    assert "| local | on | kapps-shacl-on | commit |" in (
        tmp_path / "phases.md"
    ).read_text(encoding="utf-8")


def test_an_old_runs_folder_gets_what_its_data_supports(
    figure: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = tmp_path / "runs"
    write_run(
        runs,
        kapps_record("20261013-094506-5f98", heartbeat_interval="absent"),
        kapps_spans(),
    )
    output = tmp_path / "figures"
    assert figure.main([str(runs), "--output", str(output)]) == 0
    printed = capsys.readouterr().out
    assert "no writes only curve" in printed
    assert "no paired difference" in printed
    assert "no SHACL difference" in printed
    for name in (
        "commit-latency-cdf-heartbeats-on.pdf",
        "commit-latency-box-heartbeats-on.png",
        "percentiles.csv",
        "phases.csv",
        "commits.csv",
    ):
        assert (output / name).is_file()
    percentiles = pd.read_csv(output / "percentiles.csv")
    assert set(percentiles["curve"]) == {"kapps-shacl-on"}


def test_the_default_output_is_beside_the_first_folder(
    figure: ModuleType, tmp_path: Path
) -> None:
    runs = tmp_path / "runs"
    write_run(runs, kapps_record("20261013-094506-5f98"), kapps_spans())
    assert figure.main([str(runs)]) == 0
    assert (tmp_path / "figures" / "figures.json").is_file()


def test_a_run_without_spans_says_why_and_exits_without_error(
    figure: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = tmp_path / "runs"
    write_run(runs, kapps_record("20261013-094506-5f98"), None)
    output = tmp_path / "figures"
    assert figure.main([str(runs), "--output", str(output)]) == 0
    printed = capsys.readouterr().out
    assert "no spans" in printed
    manifest = json.loads((output / "figures.json").read_text(encoding="utf-8"))
    assert manifest["left_out"] == [
        {"run_id": "20261013-094506-5f98", "why": "no spans: no per-commit table"}
    ]
