# The triple store

Every run needs a triple store with a dataset that validates SHACL shapes. A *dataset* is the
triple store's unit of data: one set of graphs, which a run clears and fills. A run connects to the
triple store that the environment names. Both use cases run on that configured triple store. A
local triple store is only the fallback, for a machine on which none is configured: see
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

A triple store that is not on loopback also needs `TRIPLESTORE_ALLOW_DESTRUCTIVE=1` beside the
other `TRIPLESTORE_*` variables, because the semantic middleware's seeding refuses to clear such a
triple store without it; the local fallback needs nothing.

<!-- vendor-check: off -- the deprecated variables, which the store reads until the contract release -->
The variables that these three replace, `GRAPHDB_URL`, `GRAPHDB_USERNAME` and `GRAPHDB_PASSWORD`,
are deprecated: the triple store access module still reads each where its successor is not set,
with a warning. `./run-uc2` says so when only they name the triple store, and runs on that triple
store.
<!-- vendor-check: on -->

## The dataset

The environment names no dataset. The use cases' own default
is `jms-kapps`, and `--dataset` chooses another. On the configured triple store, create it once
from [`graphdb/jms-kapps-repo-config.ttl`](../../graphdb/jms-kapps-repo-config.ttl): a dataset
with SHACL validation and the `rdfsplus-optimized` ruleset, the triple store's set of inference
rules, which is enough because the use cases declare every class they name. The file says why.
The local fallback creates it itself. A launch loads the shipped ontologies and shapes into its
dataset for the demonstration;
a productive circular factory already holds them in its knowledge graph.
On 2026-10-23 the KIT triple store did not type AG1 a `cfc:ProductType` from the range of
`cfc:hasProductType`. The entry "Inference" of the [glossary](glossary.md) has the rest.

Do not point a run at a dataset that anything else uses: a run clears it. A `graphdb` test
**clears the dataset it runs against** too, so on a shared server the tests run on a throwaway
dataset of their own, named with `uv run pytest --dataset <name>` (the tests have no default
dataset), and never on the use cases' dataset. See [Tests](tests.md).

## The prefixes a seed registers

A seed also maps, in that dataset, the prefixes of the vocabularies the use cases write: `fc:`,
`fci:` (the FlexConveyor's individuals), `cfc:`, `svc:`, `mes:`, `sh:`, and use case 1's `uns:`, so
the triple store's workbench writes `fc:Box`, and, where the dataset lacks them, `rdf:`, `rdfs:`,
`owl:` and `xsd:`. A prefix the dataset already maps to another namespace stays as it is, and the
seed logs it.

After its last import, the seed, and only the seed, deletes each prefix that is empty or `ns`
followed by digits, such as the `ns1:` an earlier import made up, when one of these prefixes maps
the same namespace. Every other prefix stays, and no service and no other code path deletes or
changes a dataset's prefixes. The seed sets them through the triple store's prefix registry. A
triple store without one gets none: the seed logs one line and goes on.

## The local fallback

On a machine on which no triple store is configured, a local one runs from the clone's
compose file. The README's block [Run use case 2](../../README.md#run-use-case-2) starts it and sets the three
variables for it.

<!-- vendor-check: off -- the local fallback's setup steps, which start the local GraphDB of the compose file -->
Its `docker compose up -d --wait` starts a pinned GraphDB, creates the `jms-kapps` repository in it from
[`graphdb/jms-kapps-repo-config.ttl`](../../graphdb/jms-kapps-repo-config.ttl) and the `kapps-demo`
repository from [`graphdb/kapps-demo-repo-config.ttl`](../../graphdb/kapps-demo-repo-config.ttl), and
returns once both answer; GraphDB calls a dataset a repository. The use cases run on `jms-kapps`, and
the [factory demo of the middleware](../user-guide.md#protocol-mediation-the-transferunits-demo-of-kapps_semantic_middleware)
on `kapps-demo`, whose configuration is the first one's with only the id changed. It pulls two images
the first time. It also starts the InfluxDB that use case 1 keeps its curves in, on
<http://localhost:8181>. Running it again is safe: it checks for each repository first and leaves an
existing one alone, so a volume that holds only `jms-kapps` gets `kapps-demo` on the next start.
<!-- vendor-check: on -->

On Windows, set the three variables as [above](#the-connection-variables), with the values the
README's block gives. `./run-uc2` does all of this itself when no `TRIPLESTORE_URL` is set. When
port 7200 is taken, [the user guide](../user-guide.md#a-taken-port) shows how to move the local
triple store.

```bash
docker compose stop      # stops both, and keeps their data
docker compose down -v   # removes both, and their data
```
