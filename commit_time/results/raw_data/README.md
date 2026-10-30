# Raw data of the commit time run

- One tar.xz per used repetition and store, `<store>-repetition-<NN>.tar.xz`, with the folder
  of each run and replay of that repetition, as the run left it, below
  `<store>/heartbeats-<level>/runs/<run id>/`. Unpacking every archive here gives the store
  folders back.
- The run's records as it wrote them: `parameters.json`, `section_7.toml`.
- `MANIFEST.json`: each run with its archive, configuration, repetition, validity and the
  software its record names; the repetitions excluded, with why; each replaced string.

A string that the release's hygiene checks ban is replaced by a placeholder that names its list,
as `MANIFEST.json` lists. The reports part beside this one is derived from these archives.
