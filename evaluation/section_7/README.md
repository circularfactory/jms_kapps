# Section 7: the latency KAPPS adds to a commit

The scripts of the paper's section 7, written to be read and stepped through. They import the
measurement harness (`jms_kapps.evaluation`, its README in `src/jms_kapps/evaluation/`) and
reimplement none of it.

| File | What it does |
|---|---|
| `figure.py` | reads recorded run folders and writes the two tidy tables, the figures, the percentile CSV, the phase table and the manifest |
| `results/` | the experiment's output, one folder per invocation; git-ignored |
| `prefills/` | the preload files; git-ignored |

Every name here is interim.

## Make the figures

```sh
uv sync --extra evaluation
uv run python evaluation/section_7/figure.py results/<start>/kit results/<start>/local
```

It takes one or more folders and `--output <folder>` (default: `figures/` beside the first
folder). A folder is one of:

- a store folder of the experiment: `<store>/heartbeats-<level>/runs/<run id>/`, with the store
  `kit` or `local` and the level `on` or `off`;
- a bare runs folder of the harness, `<runs>/<run id>/`, as `jms-eval` and `jms-eval-uc2` leave it;
- a single run folder.

It works on any recorded run folder of the harness, old runs included. A plot whose data the
folders lack is skipped, and the script prints why: no spans gives no table; no writes-only replay
gives no writes-only curve and no paired difference; no SHACL-off run gives no SHACL difference.
The script prints each step and where it writes.

## The labels of a run

- **Store**: the name of the store folder (`kit`, `local`), else the host of the record's server.
  KIT's panel is left, the local store's right; one store gives one panel.
- **Heartbeat level**: `off` where the record's run settings hold a heartbeat interval of null or
  0, `on` where they hold another; else the folder `heartbeats-on` or `heartbeats-off`; else `on`.
  A replay takes the level of the run it replays.
- **Arm**: a run of use case 2 is KAPPS with SHACL on or off by its configuration; a store-only
  baseline in the mode "writes only" is the writes-only arm, when the run it replays is a
  SHACL-on run among the inputs.

A run counts when it completed and is valid. Left out, and named in the manifest with why: a run
that does not count, an all-requests baseline, a replay of a run that is not among the inputs or
not SHACL on, a replay with an outcome mismatch (invalid), and every valid replay of a run but the
earliest.

## The two tables

**`commits.csv`**, one row per commit of the object-graph mapper of every run that counts: the
store, the heartbeat level, the arm, the run, its repetition and pick seed, the participant and its
role (WMS or module), the commit's span id, its send time and its seconds since the window's start,
its latency and outcome, whether it was admitted (its span ended without an error) and is in the
window (it started in the measured window), its kind, its checked write (send time, latency,
outcome, refusal, applied, how many), its reads, the replayed update of the writes-only replay
(replay, latency, lateness, outcome, match), the difference commit minus replayed update, and the
ten phases (columns `phase_<name>_seconds`).

- **The kind**: `heartbeat` where the checked write names the heartbeat predicate (either
  spelling, `lastHeartbeat` or `hasLastHeartbeat`), else `status: ` and the statuses it sets, else
  `other update`; `no write` without a checked write.
- **The phases**: each store request inside a commit goes to one phase, by the first rule that
  matches: the checked write → `update`; a receipt query → `receipt_ask`; a receipt removal →
  `receipt_removal`; a read below the class-spec derivation, or a read of the schema directly
  below the commit → `class_query`; a read below a fetch → `fetch_reads`; any other request →
  `other_requests`. Every other span below the commit gives its own time (its duration minus its
  children's) to `class_derivation` where it or a span above it is the class-spec derivation, else
  to `fetch_client` where one is a fetch, else to `diff` where one is the diff, else to
  `client_remainder`. So a fetch inside a fetch counts once, under the outer one. The ten parts
  are whole microseconds and sum to the commit's latency.
- **The replayed update** of a commit is the replayed write that carries the commit's span id and
  inserts a receipt (never the receipt's removal); a replay record without the commit id pairs by
  the participant and the recorded send time.

**`replayed_writes.csv`**, one row per replayed write of every writes-only replay that counts: the
store, the level, the repetition, the replay and the run it replays, the participant, the commit,
the category, the recorded send time and its seconds since the KAPPS run's window start, the
recorded offset, the sent offset (the recorded one plus the lateness), the lateness, the latency,
the status code, the outcome, refusal and applied state, the recorded outcome and applied state,
whether they match, and whether the write left before the participant's previous write was
answered.

## The figures and tables

| File | What it shows |
|---|---|
| `commit-latency-cdf-heartbeats-<level>.pdf`, `.png` | the share of commits against the time per commit (ms, log scale), a panel per store with the same axes; three curves: writes only (solid, circles), KAPPS with SHACL off (dashed, squares), KAPPS with SHACL on (dash-dot, triangles); the module transit time (3 s) as a dashed line, labelled on the line; the legend below the panels, out of the axes, with each curve's n for each store |
| `commit-latency-box-heartbeats-<level>.pdf`, `.png` | a panel per store, the three arms as horizontal boxes on a log scale: the box the interquartile range, the line the median, the diamond the mean, whiskers at the 1st and the 99th percentile, every commit beyond them a hollow point; n and the share beyond the whiskers above each box, on two short lines; the module transit time (3 s) as a dashed line, labelled on the line |
| `percentiles.csv` | per store, level and curve: n, the median, the 95th and the 99th percentile, each with its bootstrap interval; KAPPS with SHACL on minus writes only, per commit, as median and 95th percentile with intervals and the number of pairs; the mean difference SHACL on minus SHACL off with its interval |
| `phases.csv`, `phases.md` | supplementary: per store, level and KAPPS arm, each phase's median, mean and share of the commit's mean, beside the commit itself and, for SHACL on, the writes-only latency of the same commits |
| `figures.json` | the manifest: every figure and table made, the runs with their labels, the commit counts (admitted, not admitted by kind, in the window, paired, without a replayed update), the kind mix per arm, the runs left out and the plots skipped with why, and the caption drafts |

Each figure is one function of `figure.py` over the two tables, registered in `FIGURES`; a plot
is added as one function and one entry. The sizes are named constants: one column, 8.5 cm wide,
8 pt fonts, 1 pt lines.

## The method

- **Clocks.** A commit's latency is the duration of its span, both ends from the participant's
  wall clock. A replayed update's latency is the replay's monotonic clock around its HTTP request.
  Durations only, each from one clock, on one client host.
- **Which commits.** The KAPPS curves use every admitted commit of the window of every run that
  counts, heartbeat commits included; the manifest and the caption say the kind mix. The
  writes-only curve and the paired difference use the replayed checked writes of the SHACL-on
  commits among them. A commit whose checked write was recorded after the window's end has no
  replayed update; the manifest counts it.
- **Pairing.** KAPPS minus writes only is paired by the commit's span id. SHACL on minus SHACL off
  is a difference of pooled means: the two arms are different runs.
- **Intervals.** Every quantile by linear interpolation between the two closest ranks. Every
  interval is a bootstrap that resamples whole repetitions (seed 0, 10 000 resamples, 2.5 % and
  97.5 %), because the latencies within a run are not independent; for the SHACL difference each
  arm's repetitions are resampled independently.

## Interim choices

| Choice | Taken | Would be wrong if |
|---|---|---|
| The phases | ten parts, each span's own time to one phase, the class-spec derivation before the fetch | the class-spec derivation inside a fetch should count as fetch client time |
| The heartbeat level of an old record | `on`, where neither the run settings nor the folder say it | a run before the setting ran with heartbeats off |
| The pairing of a replayed update | by "inserts a receipt", not by the category, as a heartbeat's checked write is categorised a heartbeat update | — |
| The phase table | both KAPPS arms, the writes-only latency beside SHACL on | SHACL on alone wanted |
| Box plot | whiskers at the 1st and 99th percentile, commits beyond them as hollow points, log scale | Tukey whiskers or a linear axis wanted |
| Figure sizes | one column (8.5 cm), 8 pt | double column wanted |
