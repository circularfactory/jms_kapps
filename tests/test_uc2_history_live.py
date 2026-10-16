"""The store's history collection on a live GraphDB: a launch's reset turns it on only with the
launch option `collect_history`, and a past possession is read back by a query for one point in
time (jms_kapps 22).

**Only on a disposable dataset.** A reset with the option turns the dataset's history
collection on, and every later write there is recorded. So these tests run only on a dataset
whose name starts with `ogm-disposable-`, created for them from `graphdb/jms-kapps-repo-config.ttl`
with only its ID changed and deleted afterwards, and refuse any other with a skip that says why:
never `Tests`, which every library's live suite shares (decided by Etienne on 2026-10-07). The
dataset's history collection must be off when they start, as in a dataset just created;
they stop with the reason where it is on. They leave it on, as a launch with the option does, and
nothing turns it off: so a second run needs a dataset created afresh, as the first did.

They take the dataset by `--dataset`, as every graphdb test does, and clear it. They run
only when the store's connection variables are set.
"""

from __future__ import annotations

import time

import pytest
from _disposable import disposable
from kapps_ogm import OGM
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.vocabulary import CFC, MES
from kapps_triplestore_interface import IRI, TripleStore

from jms_kapps.common.triplestore import bindings, connect
from jms_kapps.uc2.history import (
    history_enabled,
    point_in_time,
    possessor_query,
    server_time,
)
from jms_kapps.uc2.launcher.backend import TripleStoreGraph
from jms_kapps.uc2.seed import place_box
from jms_kapps.uc2.topology import load_topology
from jms_kapps.uc2.vocabulary import FC, box_iri, module_iri

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

WHY = "a reset with the option turns the store's history collection on, for good"

# How long the store's clock moves on between the box's first possession, the point in time the
# test reads, and the handover: more than the second to which a point in time is given, so that
# the point falls between the two writes whichever end of its second the store takes.
APART_SECONDS = 1.5


@pytest.fixture(scope="module")
def db(dataset) -> TripleStore:
    """The disposable dataset, whose history collection is off when the module starts."""
    db = connect(disposable(dataset, WHY))
    if history_enabled(db):
        pytest.fail(
            f"The history collection of {dataset!r} is on already: these tests need a "
            "dataset whose collection is off, as one just created from "
            "graphdb/jms-kapps-repo-config.ttl"
        )
    return db


def a_launch_s_reset(db: TripleStore, *, collect_history: bool) -> None:
    """What a launch of the pair from the Launcher seeds, with the option or without it."""
    TripleStoreGraph(db).seed(load_topology("pair"), 0, collect_history=collect_history)


def middleware(ogm: OGM, module: IRI) -> SemanticMiddleware:
    """The middleware of `module`, never started, so it registers no service: its handover needs
    only the graph."""
    return SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=str(module),
        service_class=str(FC.FlexConveyorModuleService),
        ogm=ogm,
    )


def possession_state(db: TripleStore, resource: IRI) -> str:
    [row] = bindings(
        db, f"SELECT ?ps WHERE {{ <{resource}> <{CFC.hasPossessor}> ?ps }}"
    )
    return str(row["ps"])


def test_a_reset_turns_the_history_on_only_with_the_option_and_never_off(db):
    a_launch_s_reset(db, collect_history=False)
    assert history_enabled(db) is False

    a_launch_s_reset(db, collect_history=True)
    assert history_enabled(db) is True

    # With the collection on, every clear of the reset works, and a reset without the option
    # leaves the collection on.
    a_launch_s_reset(db, collect_history=False)
    assert history_enabled(db) is True


def test_a_past_possessor_is_read_back_by_a_query_that_names_the_box(db):
    first, second, box = module_iri(1), module_iri(2), box_iri(1)
    a_launch_s_reset(db, collect_history=True)
    ogm = OGM(db=db)
    place_box(ogm, box, on=first, origin=first, destination=second)
    time.sleep(APART_SECONDS)
    then = server_time(db)
    time.sleep(APART_SECONDS)

    with middleware(ogm, first).handover(
        mode=MES.Pass, workpiece=box, counterpart=second
    ):
        pass

    now = bindings(
        db,
        f"SELECT ?state WHERE {{ <{box}> <{CFC.hasPossessedWorkpiece}> ?state }}",
    )
    assert [str(row["state"]) for row in now] == [possession_state(db, second)]
    # The box's possession state then, by a pattern that names the box alone.
    state_then = bindings(
        db,
        f"SELECT ?state FROM <{point_in_time(then)}> WHERE "
        f"{{ <{box}> <{CFC.hasPossessedWorkpiece}> ?state }}",
    )
    assert [str(row["state"]) for row in state_then] == [possession_state(db, first)]
    # The possessor then, by the query the reference page on past possessions shows.
    possessor_then = bindings(db, possessor_query(box, then))
    assert [str(row["possessor"]) for row in possessor_then] == [str(first)]
