# Measurement of commit time

This folder holds the measurement of the time of a commit in the reference implementation, which
Section 7 and Figure 10 of the paper report.

The scripts import the measurement harness (`jms_kapps.commit_time`) and reimplement none of it.

| File | What it does |
|---|---|
| [`experiment.py`](experiment.py) | the one command: runs use case 2 with SHACL validation on and off on the two stores, replays the recorded updates of each SHACL-on run, leaves one folder per store, and calls `figure.py` |
| [`figure.py`](figure.py) | reads recorded run folders and writes the two tidy tables, the paper figure with its two CSVs, and the manifest |
| [`pack.py`](pack.py) | packs a finished run into the raw-data part and the reports part that the repository publishes |
| [`results/raw_data/`](results/raw_data/README.md), [`results/reports/`](results/reports/REPRODUCE.md) | the run behind Figure 10, packed: the raw data as one tar.xz per repetition and store, and what the scripts derive from it |
| `results/` | otherwise the experiment's output, one folder per invocation; git-ignored |

[`results/reports/REPRODUCE.md`](results/reports/REPRODUCE.md) gives the steps to unpack the run behind Figure 10, draw the figure
again and repeat a run.

## Run

```sh
uv sync --locked --extra commit-time
uv run python commit_time/experiment.py --debug
```

This runs the debug values: the 5-module cross at 6 boxes a minute, a preload of 12 statements, a
30 s window, the local store only, both SHACL and both heartbeat levels: about 9 minutes. The cross
carries this load, so no spawn is refused and the exit code is 0.

```sh
TRIPLESTORE_ALLOW_DESTRUCTIVE=1 uv run python commit_time/experiment.py
```

This runs the paper's values, heartbeats on, on the remote store and then on the local store:
several hours on each store, tables, reports and the packed run included.

The `FULL_RUN` block holds the values of Figure 10: the shipped 30-module `grid`, 30 boxes a minute
in round robin, a transit time of 3 s, a preload of 1,000,000 statements, and a 300 s window after
a 60 s warm-up, 14 repetitions on each store.

The remote store is the GraphDB that `TRIPLESTORE_URL`, `TRIPLESTORE_USERNAME` and
`TRIPLESTORE_PASSWORD` name; its store id is `kit`, and its folder in the run data is `kit/`. The local store is
the GraphDB of `compose.yaml`, which the script starts as the harness does
(`docker compose up -d --wait`) with a 12g heap, the remote store's.

- `--debug` takes the `DEBUG_RUN` block instead of `FULL_RUN`; both are at the top of the script.
- `--stores kit`, `--stores local` or `--stores local,kit` narrows or orders the stores (default:
  the remote store, then local).

On a store that is not on loopback, it refuses to start unless the command sets
`TRIPLESTORE_ALLOW_DESTRUCTIVE=1`.

## Figure

```sh
uv sync --locked --extra commit-time
uv run python commit_time/figure.py commit_time/results/<start>/kit commit_time/results/<start>/local
```

It takes one or more folders and `--output <folder>` (default: `figures/` beside the first
folder).

Panel (a) of Figure 10 draws the time per commit; `time_per_commit.csv` gives its numbers. Panel (b)
stacks the mean of the ten phases of a commit in four parts; `time_per_commit_by_phase.csv` gives
the mean of each part. The figure of the run behind Figure 10 is
[`results/reports/figures/commit-time-paper.pdf`](results/reports/figures/commit-time-paper.pdf), with
[`time_per_commit.csv`](results/reports/figures/time_per_commit.csv) and
[`time_per_commit_by_phase.csv`](results/reports/figures/time_per_commit_by_phase.csv).
