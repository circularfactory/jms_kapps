"""What the graph does with a write that breaks a shape, proven on a live GraphDB.

A box has no lifecycle state of its own. Its BoxTransportOperation's status says where the journey
is, and its possessor says where the box is, so use case 2's rules tie the two together. Each test
makes the write that breaks one rule, as a participant or the seed makes it: a module hands a box
over through the middleware's handover, and a status changes in one commit of the middleware. The
store refuses each such write at commit: the report names the focus node, the shape, and the path
where the report carries one, and the graph does not change. Every shape is SHACL Core.

Every refusal raises one type, the triplestore interface's `ShaclValidationError`, with the store's
report verbatim, at both of the middleware's write paths: fault scenario 1
through its handover, fault scenario 2 through its commit.

Possession is one possession state per resource, as the middleware writes it. A resource links to
its state once, and a handover changes only the box's link to a state.

These tests clear the dataset they run against. They run only when the store's connection
variables are set, against the dataset `--dataset` names (there is no default).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from _bulk import bulk_validation_report
from _explicit import explicit_statements
from _uc2_run import POSSESSION_PATH
from kapps_ogm import OGM, Update
from kapps_semantic_middleware import Mode, SemanticMiddleware, possession
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.validation_report import (
    ValidationResult,
    validation_results,
)
from kapps_semantic_middleware.vocabulary import CFC, MES, SVC, OperationStatus
from kapps_triplestore_interface import IRI, ShaclValidationError
from rdflib import Literal
from rdflib.namespace import RDF, SH

from jms_kapps.common.triplestore import bindings, connect
from jms_kapps.uc2.seed import MODULES, place_box, possession_triples, seed_two_modules
from jms_kapps.uc2.vocabulary import FC, FCI_NS, WMS, box_iri, transport_iri

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

# The path of the rule that every box has exactly one possessor, as its reports give it: from the
# box to its possession state. The rule checks the state's possessors one step further, inside it.
BOX_POSSESSION_PATH = f"<{CFC.hasPossessedWorkpiece}>"


@pytest.fixture(scope="module")
def db(dataset):
    return connect(dataset)


@pytest.fixture
def ogm(db):
    """Two modules, and box 1 on module 1, its transport to module 2 running."""
    ogm = OGM(db=db)
    m1, m2 = seed_two_modules(db, transit_seconds=0)
    place_box(ogm, box_iri(1), on=m1, origin=m1, destination=m2)
    return ogm


def possessors(ogm: OGM, box: IRI) -> list[str]:
    rows = bindings(
        ogm.db,
        f"SELECT ?r WHERE {{ <{box}> <{CFC.hasPossessedWorkpiece}> ?ps . ?r <{CFC.hasPossessor}> ?ps }}",
    )
    return sorted(str(row["r"]) for row in rows)


def possession_state(ogm: OGM, box: IRI) -> IRI:
    rows = bindings(
        ogm.db, f"SELECT ?ps WHERE {{ <{box}> <{CFC.hasPossessedWorkpiece}> ?ps }}"
    )
    return IRI(str(rows[0]["ps"]))


def states_of(ogm: OGM, resource: IRI) -> list[str]:
    """Every possession state `resource` links to with `cfc:hasPossessor`."""
    rows = bindings(
        ogm.db, f"SELECT ?ps WHERE {{ <{resource}> <{CFC.hasPossessor}> ?ps }}"
    )
    return sorted(str(row["ps"]) for row in rows)


def middleware(ogm: OGM, resource: IRI) -> SemanticMiddleware:
    """The middleware of `resource`, never started, so it registers no service: its handover and
    its commit need only the graph."""
    return SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=str(resource),
        service_class=str(FC.FlexConveyorModuleService),
        ogm=ogm,
    )


def hand_over(ogm: OGM, box: IRI, *, by: IRI, to: IRI) -> None:
    """`by` hands `box` to `to` through the possession extension's handover, as a module does."""
    with possession.handover(
        middleware(ogm, by), mode=MES.Pass, workpiece=box, counterpart=to
    ):
        pass


def give_to_the_wms(ogm: OGM, box: IRI, *, status: str | None) -> None:
    """Create `box` in the WarehouseManagementSystem's possession, with a transport in `status`,
    or with no transport if `status` is None. A box the WarehouseManagementSystem holds breaks no
    rule."""
    triples = [*possession_triples(ogm.db, box, WMS), (box, RDF.type, FC.Box)]
    if status is not None:
        operation = transport_iri(box)
        m1, m2 = MODULES
        triples += [
            (box, FC.hasTransport, operation),
            (operation, RDF.type, FC.BoxTransportOperation),
            (operation, FC.hasBox, box),
            (operation, FC.hasOrigin, m1),
            (operation, FC.hasDestination, m2),
            (operation, SVC.hasOperationStatus, Literal(status)),
        ]
    assert ogm.db.triples_add(triples)


def hand_to_the_wms(ogm: OGM, box: IRI, *, done: bool) -> None:
    """Point `box` at the WarehouseManagementSystem's state, and set its transport done in the
    same write if `done`.

    Plain triples, as a handover writes them: the WarehouseManagementSystem has no handover
    ability in this seed, so the middleware's handover would refuse before the store sees it.
    """
    old = [(box, CFC.hasPossessedWorkpiece, possession_state(ogm, box))]
    new = possession_triples(ogm.db, box, WMS)
    if done:
        transport = transport_iri(box)
        old.append(
            (transport, SVC.hasOperationStatus, Literal(OperationStatus.RUNNING))
        )
        new.append((transport, SVC.hasOperationStatus, Literal(OperationStatus.DONE)))
    assert ogm.db.triples_update(old_triples=old, new_triples=new)


def set_status(ogm: OGM, transport: IRI, status: str) -> None:
    """Set `transport`'s status in one `mw.commit()` of Module 1's middleware, as a participant
    commits a status."""
    change = Update(
        instance_iri=transport, data={str(SVC.hasOperationStatus): [status]}
    )
    with middleware(ogm, MODULES[0]).commit(entry=[change]):
        pass


def refused(ogm: OGM, write: Callable[[], object]) -> set[ValidationResult]:
    """Make `write`, which the store must refuse, and return the results of its report.

    The refusal is the triplestore interface's `ShaclValidationError`, whichever layer the write
    goes through: the middleware and the object-graph mapper pass it on unchanged, as the same
    type and with no error of their own around it. Checks that the graph did not change.
    """
    before = explicit_statements(ogm.db)
    with pytest.raises(ShaclValidationError) as raised:
        write()
    assert type(raised.value) is ShaclValidationError
    assert raised.value.__cause__ is None
    assert explicit_statements(ogm.db) == before
    return validation_results(raised.value.report)


def result(focus: IRI, shape: IRI, path: str = "") -> ValidationResult:
    """The validation result that names `focus`, `shape` and `path`.

    A report of a shape that is an `sh:or` carries no path, so `path` is empty for those.
    """
    return ValidationResult(focus=str(focus), path=path, shape=str(shape))


def test_a_placed_box_travels_on_its_module_with_a_running_transport(ogm):
    """The seed: the box on its module, and its transport running from origin to destination."""
    m1, m2 = MODULES
    transport = transport_iri(box_iri(1))

    rows = bindings(
        ogm.db,
        f"""SELECT ?status ?origin ?destination WHERE {{
            <{transport}> <{FC.hasBox}> <{box_iri(1)}> ;
                <{FC.hasOrigin}> ?origin ;
                <{FC.hasDestination}> ?destination ;
                <{SVC.hasOperationStatus}> ?status .
        }}""",
    )

    assert [
        (str(row["status"]), str(row["origin"]), str(row["destination"]))
        for row in rows
    ] == [(OperationStatus.RUNNING, str(m1), str(m2))]
    assert possessors(ogm, box_iri(1)) == [str(m1)]


def test_a_seed_gives_each_resource_one_possession_state(ogm):
    """One possession state per resource, as the middleware writes it. Box 1 leaves module 1, box
    2 is placed on module 1, which keeps its state, and two boxes go to the
    WarehouseManagementSystem. Each resource then links to one state, `<resource>_possession`,
    and every box has exactly one possessor."""
    m1, m2 = MODULES
    hand_over(ogm, box_iri(1), by=m1, to=m2)
    place_box(ogm, box_iri(2), on=m1, origin=m1, destination=m2)
    give_to_the_wms(ogm, box_iri(3), status=None)
    give_to_the_wms(ogm, box_iri(4), status=None)

    assert {str(r): states_of(ogm, r) for r in (m1, m2, WMS)} == {
        str(m1): [f"{FCI_NS}Module1_possession"],
        str(m2): [f"{FCI_NS}Module2_possession"],
        str(WMS): [f"{FCI_NS}WarehouseManagementSystem1_possession"],
    }
    assert {
        box.fragment: possessors(ogm, box) for box in map(box_iri, (1, 2, 3, 4))
    } == {
        "Box1": [str(m2)],
        "Box2": [str(m1)],
        "Box3": [str(WMS)],
        "Box4": [str(WMS)],
    }


def test_the_seed_places_no_box_on_a_module_with_two_possession_states(ogm):
    """A resource has one possession state. The seed raises on a graph that gives one two, as the
    middleware does, instead of picking either, and writes nothing."""
    m1, m2 = MODULES
    first, second = (IRI(f"Module2_state_{n}", base=FCI_NS) for n in (1, 2))
    assert ogm.db.triples_add(
        [(m2, CFC.hasPossessor, first), (m2, CFC.hasPossessor, second)]
    )
    before = explicit_statements(ogm.db)

    with pytest.raises(ValueError, match="has 2 possession states") as raised:
        place_box(ogm, box_iri(2), on=m2, origin=m1, destination=m2)

    assert str(first) in str(raised.value) and str(second) in str(raised.value)
    assert explicit_statements(ogm.db) == before


def test_a_clean_dataset_passes_bulk_validation(ogm):
    """The control for every refusal: a box on one module, its transport running, breaks nothing."""
    report = bulk_validation_report(ogm.db)
    assert list(report.objects(None, SH.conforms)) == [Literal(True)], (
        report.serialize()
    )


def test_a_clean_handover_is_accepted(ogm):
    hand_over(ogm, box_iri(1), by=MODULES[0], to=MODULES[1])

    assert possessors(ogm, box_iri(1)) == [str(MODULES[1])]


def test_the_start_of_a_journey_is_accepted(ogm):
    """The box goes onto its first module while its transport is queued, and the transport is set
    running after: a queued transport breaks no rule, so the two need no common write."""
    give_to_the_wms(ogm, box_iri(2), status=OperationStatus.QUEUED)

    hand_over(ogm, box_iri(2), by=WMS, to=MODULES[1])
    set_status(ogm, transport_iri(box_iri(2)), OperationStatus.RUNNING)

    assert possessors(ogm, box_iri(2)) == [str(MODULES[1])]


def test_the_end_of_a_journey_is_accepted(ogm):
    """The box goes back to the WarehouseManagementSystem as its transport is set done, in one
    write: each change alone breaks a rule. Module 1 keeps its state."""
    hand_to_the_wms(ogm, box_iri(1), done=True)

    assert possessors(ogm, box_iri(1)) == [str(WMS)]
    assert states_of(ogm, MODULES[0]) == [f"{FCI_NS}Module1_possession"]


def test_a_second_box_on_an_occupied_module_is_refused(ogm):
    """Fault scenario 1: `:FlexConveyorModulePossessionShape`, `sh:maxCount 1`. The entry of the
    middleware's handover raises `ShaclValidationError`.

    The store refuses the handover onto an occupied module, and the graph does not change (§5.2).
    The handover's write path: the store's report reaches the module that asked as the interface's typed exception, at the handover's commit,
    unchanged by the middleware and the object-graph mapper."""
    m1, m2 = MODULES
    place_box(ogm, box_iri(2), on=m2, origin=m1, destination=m2)

    results = refused(ogm, lambda: hand_over(ogm, box_iri(1), by=m1, to=m2))

    assert (
        result(m2, FC.FlexConveyorModulePossessionShape, POSSESSION_PATH) in results
    ), results


def test_the_box_of_a_running_transport_handed_to_no_module_is_refused(ogm):
    """The WarehouseManagementSystem takes the box back while its transport still runs, so no
    module possesses it."""
    results = refused(ogm, lambda: hand_to_the_wms(ogm, box_iri(1), done=False))

    assert result(box_iri(1), FC.RunningTransportBoxPossessedShape) in results, results


def test_the_box_of_a_running_transport_held_by_two_modules_is_refused(ogm):
    """A second module links to module 1's possession state, so the box module 1 holds has two
    possessors. The same rule refuses it, and so does the rule that every box has exactly one
    possessor: `:BoxHasOnePossessorShape`, `sh:maxCount 1` on the possessors of the box's state."""
    possession = possession_state(ogm, box_iri(1))

    results = refused(
        ogm, lambda: ogm.db.triple_add((MODULES[1], CFC.hasPossessor, possession))
    )

    assert result(box_iri(1), FC.RunningTransportBoxPossessedShape) in results, results
    assert (
        result(box_iri(1), FC.BoxHasOnePossessorShape, BOX_POSSESSION_PATH) in results
    ), results


def test_a_box_with_no_possessor_is_refused(ogm):
    """Every box has exactly one possessor: `:BoxHasOnePossessorShape`, `sh:minCount 1` on the
    box's possession state. A box without a transport breaks no other rule, so this one alone
    refuses it."""
    results = refused(ogm, lambda: ogm.db.triple_add((box_iri(2), RDF.type, FC.Box)))

    assert (
        result(box_iri(2), FC.BoxHasOnePossessorShape, BOX_POSSESSION_PATH) in results
    ), results


@pytest.mark.parametrize("status", [None, OperationStatus.DONE])
def test_a_box_handed_to_a_module_without_an_open_transport_is_refused(ogm, status):
    """A box the WarehouseManagementSystem holds, with no transport or with a done one, cannot
    go onto a module. With a done transport, the done rule names the box too."""
    give_to_the_wms(ogm, box_iri(2), status=status)

    results = refused(ogm, lambda: hand_over(ogm, box_iri(2), by=WMS, to=MODULES[1]))

    assert result(box_iri(2), FC.BoxOnModuleHasTransportShape) in results, results


def test_a_second_transport_for_a_box_is_refused(ogm):
    """A box travels once: `:BoxShape`, `sh:maxCount 1` on the inverse of `:hasBox`."""
    m1, m2 = MODULES
    second = IRI("SecondTransport", base=FCI_NS)

    results = refused(
        ogm,
        lambda: ogm.db.triples_add(
            [
                (second, RDF.type, FC.BoxTransportOperation),
                (second, FC.hasBox, box_iri(1)),
                (second, FC.hasOrigin, m1),
                (second, FC.hasDestination, m2),
                (second, SVC.hasOperationStatus, Literal(OperationStatus.QUEUED)),
            ]
        ),
    )

    assert result(box_iri(1), FC.BoxShape, f"^<{FC.hasBox}>") in results, results


# The two writes below never touch the box. Each changes a fact one link away from it, and the
# store follows the link back to the box whose rule the change breaks.


def test_a_transport_set_done_while_a_module_holds_its_box_is_refused(ogm):
    """Fault scenario 2. An `mw.commit()` sets the transport's status, and nothing else: the box
    stays on its module. It raises `ShaclValidationError`, the type the handover raises. A box on a
    module needs an open transport, so that rule names it too.

    The commit's write path (§5.2): the store's report reaches the module
    that asked as the interface's typed exception, at the commit, and the graph does not change."""
    results = refused(
        ogm, lambda: set_status(ogm, transport_iri(box_iri(1)), OperationStatus.DONE)
    )

    assert result(box_iri(1), FC.DoneTransportBoxReleasedShape) in results, results


def test_the_module_letting_go_of_the_box_of_a_running_transport_is_refused(ogm):
    """The module's link to its possession state goes, which no handover writes, and the box keeps
    pointing at the state. A state without a possessor leaves the box with none, so the rule that
    every box has exactly one possessor names it too."""
    possession = possession_state(ogm, box_iri(1))

    results = refused(
        ogm, lambda: ogm.db.triple_delete((MODULES[0], CFC.hasPossessor, possession))
    )

    assert result(box_iri(1), FC.RunningTransportBoxPossessedShape) in results, results
    assert (
        result(box_iri(1), FC.BoxHasOnePossessorShape, BOX_POSSESSION_PATH) in results
    ), results
