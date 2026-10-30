# How to reproduce the commit time run

The release **v0.1.0** of jms_kapps (https://github.com/circularfactory/jms_kapps) holds this run: its raw data in
`commit_time/results/raw_data/`, one tar.xz per used repetition and store, and what the scripts
derive from it here, in `commit_time/results/reports/`. The runs ran the jms_kapps commit
`bc2d4d5edfffc4323a5fe0adeb1ce3dbb2e4bdba`, a development commit; the release publishes that code's measurement.

## Unpack the raw data

With `tar`:

```sh
cd commit_time/results/raw_data
for archive in *.tar.xz; do tar -xJf "$archive"; done
```

or with Python's `tarfile`:

```sh
uv run python -c "import pathlib, tarfile; [tarfile.open(a).extractall('.', filter='data') for a in sorted(pathlib.Path('.').glob('*.tar.xz'))]"
```

Either gives one folder per store, each with `heartbeats-<level>/runs/<run id>/`.

## Redraw the figure

From the repository's root:

```sh
uv sync --locked --extra commit-time
uv run python commit_time/figure.py commit_time/results/raw_data/kit commit_time/results/raw_data/local --output figures-again
```

The CSVs in `figures-again/` are the ones in `commit_time/results/reports/figures/`, byte for byte.

## Repeat one run

1. Point the release at a GraphDB whose login may create and delete datasets and read the
   server's monitoring views: set `TRIPLESTORE_URL`, `TRIPLESTORE_USERNAME` and
   `TRIPLESTORE_PASSWORD`. A run creates its own dataset and deletes it at its end.
2. Make the preload file again. The run's record gives the topology, the size and the seed in
   `prefill.header`, and the file's digest in `prefill.sha256`:

   ```sh
   uv run python -m jms_kapps.commit_time.prefill --topology <topology> --size <size> --seed <seed> --output preload.ttl
   ```

3. Run the run's configuration again, with the preload file made in step 2:

   ```sh
   uv run python -c "import sys, tomllib; from pathlib import Path; from jms_kapps.commit_time.configuration import resolve; from jms_kapps.commit_time.run_uc2 import run_on_the_configured_store; values = tomllib.loads(Path(sys.argv[1]).read_text()); values['prefill'] = str(Path(sys.argv[2]).absolute()); run_on_the_configured_store(resolve(values, base=Path.cwd()), runs=Path('repeated'))" commit_time/results/raw_data/kit/heartbeats-on/runs/20261027-180421-e676/configuration.toml preload.ttl
   ```

   The new record is in `repeated/<run id>/record.json`; it runs at heartbeats on, the level of
   the published run. The record of a run gives its heartbeat settings in `run_settings`.
4. Compare it with the archived record, or draw the figure of the repeated runs with
   `commit_time/figure.py`.

The whole run is repeated with `commit_time/experiment.py`; its README gives the time it takes.
The store's server is not part of the release: the record's `server` and `client` sections say
what the runs ran on.
