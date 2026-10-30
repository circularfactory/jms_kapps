# A recorded refusal: a handover onto an occupied module

One refused commit, recorded from a real run of the write path of use case 2: module 1 hands box B
to module 2 while module 2 holds box A, without asking module 2 to reserve itself, as a module
does with `skip-reserve` armed (see [Fault refusals](use-case-2.md#fault-refusals)). The
handover is the middleware's: its entry commits the change of possession and the transport's
reassignment through the Object-Graph Mapper (OGM) and the triple store access module, and the
triple store validates the commit against the shapes of use case 2 at commit. The store refuses it at
`:FlexConveyorModulePossessionShape`, at most one box per module, with the validation report
below, and nothing changes.

The page, the report verbatim ([`skip-reserve.nq`](../tests/reports/skip-reserve.nq)) and
its Turtle copy ([`skip-reserve.ttl`](../tests/reports/skip-reserve.ttl)) are written by one
script, [`scripts/record_uc2_refusal.py`](../scripts/record_uc2_refusal.py), in one run on the dataset it is given, which it
clears. The run is deterministic: a rerun gives the same report, apart from
the blank-node labels the store gives it, and `tests/test_uc2_recorded_refusal_live.py` reruns
it and compares the two as RDF graphs.

**The setup.** The seed of two modules, module 1 connected east to module 2, each served as a
middleware instance as in a run, so that each registers its BoxTransportCapability (fci:Module1_capability_box_transport and
fci:Module2_capability_box_transport). Then, written directly as a seed writes, box A on module 2, with its transport executed
on module 2, and box B on module 1, with its transport from module 1 to module 2, `running` and
executed on module 1; each transport implements the capability of the module it is executed on.
Then module 1's giving side hands box B to module 2, with the entry a hop commits, and without a
reservation.

## Attempted commit

The handover's entry is one commit of three changes: the change of possession, which points box
B at module 2's possession state, and the reassignment of box B's transport to module 2 and to
module 2's capability. The OGM reads each changed instance, and hands the triple
store access module one update, which the store applies whole or not at all. The update removes

```turtle
fci:BoxB cfc:hasPossessedWorkpiece fci:Module1_possession .
fci:BoxB_transport cfc:executedOn fci:Module1 .
fci:BoxB_transport cfc:implementsCapability fci:Module1_capability_box_transport .
```

and adds

```turtle
fci:BoxB cfc:hasPossessedWorkpiece fci:Module2_possession .
fci:BoxB_transport cfc:executedOn fci:Module2 .
fci:BoxB_transport cfc:implementsCapability fci:Module2_capability_box_transport .
```

with the prefixes

```sparql
PREFIX fci: <http://w3id.org/circularfactory/FlexConveyorInstances#>
PREFIX cfc: <http://w3id.org/circularfactory/Core#>
```

## Validation report

The store answered the update with this report, 21 lines of N-Quads, and committed
nothing of it:

```nquads
_:1462ce188fb84264b15095c2ed49e49230253692 <http://www.w3.org/ns/shacl#conforms> "false"^^<http://www.w3.org/2001/XMLSchema#boolean> .
_:1462ce188fb84264b15095c2ed49e49230253692 <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://www.w3.org/ns/shacl#ValidationReport> .
_:1462ce188fb84264b15095c2ed49e49230253692 <http://rdf4j.org/schema/rdf4j#truncated> "false"^^<http://www.w3.org/2001/XMLSchema#boolean> .
_:1462ce188fb84264b15095c2ed49e49230253692 <http://www.w3.org/ns/shacl#result> _:1462ce188fb84264b15095c2ed49e49230253693 .
_:1462ce188fb84264b15095c2ed49e49230253693 <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://www.w3.org/ns/shacl#ValidationResult> .
_:1462ce188fb84264b15095c2ed49e49230253693 <http://www.w3.org/ns/shacl#focusNode> <http://w3id.org/circularfactory/FlexConveyorInstances#Module2> .
_:1462ce188fb84264b15095c2ed49e49230253693 <http://rdf4j.org/shacl-extensions#shapesGraph> <http://rdf4j.org/schema/rdf4j#SHACLShapeGraph> .
_:f65ced17-8b6a-4d50-99d5-e88a55fef704-5 <http://www.w3.org/1999/02/22-rdf-syntax-ns#first> <http://w3id.org/circularfactory/Core#hasPossessor> .
_:f65ced17-8b6a-4d50-99d5-e88a55fef704-5 <http://www.w3.org/1999/02/22-rdf-syntax-ns#rest> _:f65ced17-8b6a-4d50-99d5-e88a55fef704-5a26fe910-d022-43d2-9c5f-550010483feb0 .
_:f65ced17-8b6a-4d50-99d5-e88a55fef704-5a26fe910-d022-43d2-9c5f-550010483feb0 <http://www.w3.org/1999/02/22-rdf-syntax-ns#first> _:352e8e4f-c7da-4c9f-8904-03320006cd91-7 .
_:f65ced17-8b6a-4d50-99d5-e88a55fef704-5a26fe910-d022-43d2-9c5f-550010483feb0 <http://www.w3.org/1999/02/22-rdf-syntax-ns#rest> <http://www.w3.org/1999/02/22-rdf-syntax-ns#nil> .
_:352e8e4f-c7da-4c9f-8904-03320006cd91-7 <http://www.w3.org/ns/shacl#inversePath> <http://w3id.org/circularfactory/Core#hasPossessedWorkpiece> .
_:1462ce188fb84264b15095c2ed49e49230253693 <http://www.w3.org/ns/shacl#resultPath> _:f65ced17-8b6a-4d50-99d5-e88a55fef704-5 .
_:1462ce188fb84264b15095c2ed49e49230253693 <http://www.w3.org/ns/shacl#sourceConstraintComponent> <http://www.w3.org/ns/shacl#MaxCountConstraintComponent> .
_:1462ce188fb84264b15095c2ed49e49230253693 <http://www.w3.org/ns/shacl#resultSeverity> <http://www.w3.org/ns/shacl#Violation> .
_:1462ce188fb84264b15095c2ed49e49230253693 <http://www.w3.org/ns/shacl#resultMessage> "A FlexConveyorModule may possess at most one Box at a time." .
<http://w3id.org/circularfactory/FlexConveyor#FlexConveyorModulePossessionShape> <http://www.w3.org/ns/shacl#message> "A FlexConveyorModule may possess at most one Box at a time." .
<http://w3id.org/circularfactory/FlexConveyor#FlexConveyorModulePossessionShape> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://www.w3.org/ns/shacl#PropertyShape> .
_:1462ce188fb84264b15095c2ed49e49230253693 <http://www.w3.org/ns/shacl#sourceShape> <http://w3id.org/circularfactory/FlexConveyor#FlexConveyorModulePossessionShape> .
<http://w3id.org/circularfactory/FlexConveyor#FlexConveyorModulePossessionShape> <http://www.w3.org/ns/shacl#path> _:f65ced17-8b6a-4d50-99d5-e88a55fef704-5 .
<http://w3id.org/circularfactory/FlexConveyor#FlexConveyorModulePossessionShape> <http://www.w3.org/ns/shacl#maxCount> "1"^^<http://www.w3.org/2001/XMLSchema#integer> .
```

It names `:FlexConveyorModulePossessionShape` as `sh:sourceShape`,
`sh:MaxCountConstraintComponent` as the constraint, and `fci:Module2` as `sh:focusNode`.

**As Turtle, as the paper's Listing 1 prints it.** The same graph, with the prefixes of use case
2, as the Turtle copy [`skip-reserve.ttl`](../tests/reports/skip-reserve.ttl) holds it; only
the blank-node labels differ from the N-Quads above:

```turtle
@prefix : <http://w3id.org/circularfactory/FlexConveyor#> .
@prefix cfc: <http://w3id.org/circularfactory/Core#> .
@prefix fci: <http://w3id.org/circularfactory/FlexConveyorInstances#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdf4j: <http://rdf4j.org/schema/rdf4j#> .
@prefix rsx: <http://rdf4j.org/shacl-extensions#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

:FlexConveyorModulePossessionShape a sh:PropertyShape ;
    sh:maxCount 1 ;
    sh:message "A FlexConveyorModule may possess at most one Box at a time." ;
    sh:path _:Nbddfd155f27e4bd89d60f4df36f59c37 .

[] a sh:ValidationReport ;
    rdf4j:truncated false ;
    sh:conforms false ;
    sh:result [ a sh:ValidationResult ;
            rsx:shapesGraph rdf4j:SHACLShapeGraph ;
            sh:focusNode fci:Module2 ;
            sh:resultMessage "A FlexConveyorModule may possess at most one Box at a time." ;
            sh:resultPath _:Nbddfd155f27e4bd89d60f4df36f59c37 ;
            sh:resultSeverity sh:Violation ;
            sh:sourceConstraintComponent sh:MaxCountConstraintComponent ;
            sh:sourceShape :FlexConveyorModulePossessionShape ] .

_:N4acdab5efd84492cb05db5cfc28cf238 sh:inversePath cfc:hasPossessedWorkpiece .

_:Nfbebe05b3867448f9fb147b20ef05edc rdf:first _:N4acdab5efd84492cb05db5cfc28cf238 ;
    rdf:rest () .

_:Nbddfd155f27e4bd89d60f4df36f59c37 rdf:first cfc:hasPossessor ;
    rdf:rest _:Nfbebe05b3867448f9fb147b20ef05edc .
```

**As the store returned it, and as the module received it.** The report above is the same text,
3195 characters, SHA-256 `aa4e9b936a316eb4e192237dff6f9bf15ec90a794a86d9065a4076e9af87cb8f`, at every layer of the write path,
byte for byte:

1. The triple store access module's own record of the store's answer, which it logs at DEBUG:
   the status, 500, and the body unchanged, so that the record ends with the report.
2. The error the access module raises, `ShaclValidationError`, whose `report` is the body of the
   store's answer, as the middleware raised it out of its handover.
3. The middleware's record of the refused write, `refused_writes`, with the verb
   `handover` and the rules `FlexConveyorModulePossessionShape at Module2`.
4. The answer the module's giving side returns, `StepResult.report`, with the reason
   `the triple store refused the handover`, and the module's warning, whose first line reads
   `Module1: the triple store refused handing BoxB to Module2, by FlexConveyorModulePossessionShape at Module2; the recording keeps the report:` and whose last lines are the report.
5. The event `commit_refused` on module 1's handover event stream, which the monitor shows, with
   the report in its refusal.

The recording checked the five copies against each other, and keeps the first one verbatim as
[`skip-reserve.nq`](../tests/reports/skip-reserve.nq).

## State after the refusal

The same three queries, answered before the handover and after it, give the same rows: box B is
still possessed by module 1, module 2 still possesses only box A, and the transport of box B is
still executed on module 1, and still running.

**Box B is still possessed by module 1.**

```sparql
PREFIX cfc: <http://w3id.org/circularfactory/Core#>
PREFIX fci: <http://w3id.org/circularfactory/FlexConveyorInstances#>
SELECT ?possessor WHERE {
  fci:BoxB cfc:hasPossessedWorkpiece ?state .
  ?possessor cfc:hasPossessor ?state .
} ORDER BY ?possessor
```

| ?possessor |
|---|
| fci:Module1 |

**Module 2 still possesses box A only.**

```sparql
PREFIX cfc: <http://w3id.org/circularfactory/Core#>
PREFIX fci: <http://w3id.org/circularfactory/FlexConveyorInstances#>
SELECT ?box WHERE {
  fci:Module2 cfc:hasPossessor ?state .
  ?box cfc:hasPossessedWorkpiece ?state .
} ORDER BY ?box
```

| ?box |
|---|
| fci:BoxA |

**The transport of box B is still executed on module 1, and still running.**

```sparql
PREFIX cfc: <http://w3id.org/circularfactory/Core#>
PREFIX svc: <http://w3id.org/circularfactory/Service#>
PREFIX fci: <http://w3id.org/circularfactory/FlexConveyorInstances#>
SELECT ?module ?status WHERE {
  fci:BoxB_transport cfc:executedOn ?module ;
    svc:hasOperationStatus ?status .
} ORDER BY ?module
```

| ?module | ?status |
|---|---|
| fci:Module1 | "running" |


## Provenance

| | |
|---|---|
| jms_kapps | commit `3e26cdd67eed5cd8833e37237335489c4c440161`; the three files of the recording were written by this run and committed after it |
| `kapps-semantic-middleware` | 0.2.6 |
| `kapps-ogm` | 0.3.3 |
| `kapps-triplestore-interface` | 2.2.3 |
| triple store | GraphDB 10.8.5 (enterprise) |
| dataset | a disposable dataset, which the run cleared first |
| recorded | 2026-10-28 17:20:24 UTC |
| command | `TRIPLESTORE_ALLOW_DESTRUCTIVE=1 uv run python scripts/record_uc2_refusal.py --dataset <dataset>` |

The versions are those of `uv.lock`, which `uv sync --locked` installs.

## Make it again

On a triple store of your own, with `TRIPLESTORE_URL`, `TRIPLESTORE_USERNAME` and
`TRIPLESTORE_PASSWORD` set, and with the name of a dataset that may be cleared in place of
`<dataset>`:

```
TRIPLESTORE_ALLOW_DESTRUCTIVE=1 uv run python scripts/record_uc2_refusal.py --dataset <dataset>
```

It rewrites the page, the report and its Turtle copy. The live test makes the recording again
without writing them, and compares its report with the committed one as RDF graphs:

```
TRIPLESTORE_ALLOW_DESTRUCTIVE=1 uv run pytest tests/test_uc2_recorded_refusal_live.py --dataset <dataset>
```
