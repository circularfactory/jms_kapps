# The triple store

Every run needs a triple store with a dataset that validates SHACL shapes. A *dataset* is the
store's unit: one set of graphs, which a run clears and fills. A run connects to the store that
the environment names. **That configured store is the one every demo runs on.** A local store is
only the fallback, for a machine on which no store is configured: see
[The local fallback](#the-local-fallback).

## The connection variables

```bash
export TRIPLESTORE_URL=https://your-store.example
export TRIPLESTORE_USERNAME=...
export TRIPLESTORE_PASSWORD=...
```

On Windows, the same three variables:

```powershell
$env:TRIPLESTORE_URL = "https://your-store.example"   # PowerShell, this session
$env:TRIPLESTORE_USERNAME = "..."
$env:TRIPLESTORE_PASSWORD = "..."
```

```bat
set TRIPLESTORE_URL=https://your-store.example
setx TRIPLESTORE_URL "https://your-store.example"
```

`set` lasts for the `cmd.exe` session; `setx` writes the variable for later sessions and does not
change the one you are in.

A person who points jms_kapps at a store that is not on loopback, such as the KIT store, sets
`TRIPLESTORE_ALLOW_DESTRUCTIVE=1` beside the other `TRIPLESTORE_*` variables, because the
middleware's seeding refuses to clear such a store without it; the local fallback needs nothing.

<!-- vendor-check: off -- the deprecated variables, which the store reads until the contract release -->
The variables that these three replace, `GRAPHDB_URL`, `GRAPHDB_USERNAME` and `GRAPHDB_PASSWORD`,
are deprecated: the store still reads each where its successor is not set, with a warning, until
the contract release of the triple-store interface removes them. `./run-uc2` says so when only
they name the store, and runs on that store.
<!-- vendor-check: on -->

## The dataset

The dataset never comes from the environment, because a run clears it. The use cases' own
default is `jms-kapps`, and `--dataset` chooses another. On the configured store, create it once
from [`graphdb/jms-kapps-repo-config.ttl`](../../graphdb/jms-kapps-repo-config.ttl): a dataset
with SHACL validation and the `rdfsplus-optimized` ruleset, the store's set of inference rules,
which is enough because the use cases declare every class they name. The file says why. The local
fallback creates it itself.

Do not point a run at a dataset anything else uses: the demo's dataset is the demo's alone. A
`graphdb` test **clears the dataset it runs against** too, so on a shared server the tests run on
a throwaway dataset of their own, named with `uv run pytest --dataset <name>` (the tests have no
default dataset), and never on the demo's. See [Tests](tests.md).

## The prefixes a seed registers

A seed also maps, in that dataset, the prefixes of the vocabularies the use cases write: `fc:`,
`fci:` (the FlexConveyor's individuals), `cfc:`, `svc:`, `mes:`, `sh:`, and use case 1's `jms:`, so
the store's workbench writes `fc:Box`, and, where the dataset lacks them, `rdf:`, `rdfs:`, `owl:`
and `xsd:`. A prefix the dataset already maps to another namespace stays as it is, and the seed
logs it.

After its last import, the seed, and only the seed, deletes each prefix that is empty or `ns`
followed by digits, such as the `ns1:` an earlier import made up, when one of these prefixes maps
the same namespace. Every other prefix stays, and no participant and no other code path deletes or
changes a dataset's prefixes. The seed sets them through the store's prefix registry. A store
without one gets none: the seed logs one line and goes on, because the prefixes serve only the
workbench's display.

## The local fallback

On a machine on which no store is configured, a local one needs no account anywhere. In the
repository root:

```bash
docker compose up -d --wait
export TRIPLESTORE_URL=http://localhost:7200
export TRIPLESTORE_USERNAME=admin
export TRIPLESTORE_PASSWORD=root
```

<!-- vendor-check: off -- the local fallback's setup steps, which start the local GraphDB of the compose file -->
The command starts a pinned GraphDB, creates the `jms-kapps` repository in it from
[`graphdb/jms-kapps-repo-config.ttl`](../../graphdb/jms-kapps-repo-config.ttl), and returns once the
repository answers. It pulls two images the first time. It also starts the InfluxDB that use case
1 keeps its curves in, on <http://localhost:8181>. Running it again is safe: it checks for the
repository first and leaves an existing one alone.
<!-- vendor-check: on -->

On Windows, set the three variables as [above](#the-connection-variables). `./run-uc2` does all
of this itself when no `TRIPLESTORE_URL` is set.

```bash
docker compose stop      # stops both, and keeps their data
docker compose down -v   # removes both, and their data
```
