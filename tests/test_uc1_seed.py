"""The seed of use case 1: the demo's two grinders and their Screws, the model, and `:M4`'s example
thresholds.

These tests need no store. What they check is the one thing a seed must get right: that the two
recordings the paper's artifact ships classify as what their names say, under the values the seed
writes. A threshold that does not fit those recordings makes the demo report a wrong outcome for a
good screw, and raise on the missing one. `test_uc1_seed_live.py` writes the seed to a store.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from kapps_ogm import Create, Update
from kapps_triplestore_interface import IRI
from rdflib import RDF, RDFS, Graph, Literal, URIRef

from jms_kapps.uc1.anomaly_detector import attempt_signals
from jms_kapps.uc1.decision_tree import classify
from jms_kapps.uc1.effective_values import resolve
from jms_kapps.uc1.replay import EXAMPLE_RECORDINGS, example_frame
from jms_kapps.uc1.seed import (
    CONTAINS_WORKPIECE,
    GRINDER,
    GRINDER_2,
    GRINDERS,
    M4_VALUES,
    PLACEMENT_VALUES,
    PRODUCT_TYPE,
    RECORDING_OF,
    ROBOT,
    SCREW,
    SCREW_2,
    SCREWING_RESOURCE,
    UNMEASURED_GRINDER,
    UNMEASURED_PRODUCT_TYPE,
    UNMEASURED_SCREW,
    UNMEASURED_SCREW_TYPE,
    seed_demo,
    seed_robot,
    unmeasured_screw_type_turtle,
)
from jms_kapps.uc1.vocabulary import JMS, PLACEMENT, TYPE_LEVEL

# What each of the artifact's two recordings must classify as. Their names say it, and a demo that
# says anything else is showing the thresholds, not the screw.
EXPECTED = {"success": "ScrewIntact", "missing_screw": "MissingScrew"}


def seeded_values():
    """The effective values of the seeded Screws: their own placement values, else `:M4`'s."""
    return resolve(
        SCREW,
        {prop: [value] for prop, value in PLACEMENT_VALUES.items()},
        JMS.M4,
        {prop: [value] for prop, value in M4_VALUES.items()},
    )


@pytest.mark.parametrize("recording", sorted(EXPECTED))
def test_each_recording_of_the_artifact_classifies_as_its_name(recording: str) -> None:
    """The acceptance the seed exists for. With `:M4`'s example values and placement values
    that do not fit, `success` reaches `LooseAnchor` or `HeadOccluded` and `missing_screw` raises,
    because the screw-head band then sits where the recording never touches and the preload is
    reached only at the last sample."""
    outcome = classify(attempt_signals(example_frame(recording)), seeded_values())

    assert outcome == EXPECTED[recording]


def test_the_seed_covers_every_recording_the_artifact_ships() -> None:
    """A recording added later must be classified too, or this file stops proving what it claims."""
    assert set(EXAMPLE_RECORDINGS) == set(EXPECTED)


def test_the_loosening_minimum_stays_above_the_preload_minimum() -> None:
    """So that a force held at the preload level is not read as a rise.
    The ticket of the UC1 runtime names this as a criterion of the seed."""
    assert (
        M4_VALUES[JMS.hasMinLooseningAxialForce]
        > M4_VALUES[JMS.hasMinPreloadAxialForce]
    )


def test_the_screw_type_holds_every_type_level_value_and_no_placement_value() -> None:
    """A placement value belongs to where the screw sits, not to its type."""
    assert set(M4_VALUES) == set(TYPE_LEVEL)
    assert set(M4_VALUES).isdisjoint(PLACEMENT)


def test_the_screws_hold_every_placement_value_and_no_type_value() -> None:
    """UC1's demo Screws take their thresholds from their type; only where each sits is its own."""
    assert set(PLACEMENT_VALUES) == set(PLACEMENT)
    assert set(PLACEMENT_VALUES).isdisjoint(TYPE_LEVEL)


def test_the_demo_individuals_are_six_and_distinct() -> None:
    """Two grinders, one Screw in each, one model they are — one Screw per grinder for UC1."""
    individuals = (GRINDER, GRINDER_2, SCREW, SCREW_2, PRODUCT_TYPE)

    assert all(isinstance(iri, IRI) for iri in individuals)
    assert len(set(individuals)) == 5


def test_the_grinders_cover_exactly_the_demo_grinders() -> None:
    """`GRINDERS` is the tuple that `jms-uc1` passes to the planner, both grinders, in order."""
    assert GRINDERS == (GRINDER, GRINDER_2)


def test_recording_of_covers_exactly_the_two_demo_screws() -> None:
    """`RECORDING_OF` names a recording for each demo Screw, and for no one else."""
    assert set(RECORDING_OF) == {SCREW, SCREW_2}
    assert RECORDING_OF[SCREW] == "success"
    assert RECORDING_OF[SCREW_2] == "missing_screw"


@pytest.mark.parametrize(
    "screw, recording",
    [(SCREW, "success"), (SCREW_2, "missing_screw")],
)
def test_each_demo_screws_recording_classifies_as_expected(
    screw: IRI, recording: str
) -> None:
    """The recording that `RECORDING_OF` names for each Screw classifies as its name says, under
    the seeded values. A demo run shows both exits, `ScrewIntact` and `MissingScrew`."""
    outcome = classify(attempt_signals(example_frame(recording)), seeded_values())

    assert outcome == EXPECTED[recording]


class RecordingOGM:
    """An object-graph mapper that keeps every write the seed makes, in order, and answers the
    seed's one read of a grinder's design from what was created."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, IRI, dict]] = []
        self._created: dict[IRI, dict] = {}

    def commit(
        self,
        *,
        instance_iri: IRI | None = None,
        data: dict | None = None,
        changes: list | None = None,
    ) -> None:
        if changes is None:
            assert instance_iri is not None and data is not None
            self.writes.append(("commit", instance_iri, data))
            return
        # One commit of several changes, as the seed writes the robot: each change is kept as a
        # write of its own, a creation as a creation.
        for change in changes:
            iri = IRI(change.instance_iri)
            if isinstance(change, Create):
                self.writes.append(("create", iri, change.data))
                self._created[iri] = change.data
            else:
                self.writes.append(("commit", iri, change.data))

    def create(self, *, class_iri, instance_iri: IRI, data: dict, **_: object) -> None:
        self.writes.append(("create", instance_iri, data))
        self._created[instance_iri] = data

    def fetch(self, *, instance_iri: IRI, class_scope: object) -> SimpleNamespace:
        def linked(value: dict) -> SimpleNamespace:
            iri = IRI(value["id"])
            expected = self._created.get(iri, {}).get(str(JMS.hasExpectedScrewType), [])
            return SimpleNamespace(
                id=str(iri),
                data={JMS.hasExpectedScrewType: [linked(v) for v in expected]},
            )

        data = self._created[instance_iri]
        return SimpleNamespace(
            data={
                IRI(prop): [linked(value) for value in values]
                for prop, values in data.items()
                if values and isinstance(values[0], dict)
            }
        )

    def written(self) -> set[IRI]:
        return {instance for _, instance, _ in self.writes}


UNMEASURED = {UNMEASURED_PRODUCT_TYPE, UNMEASURED_SCREW, UNMEASURED_GRINDER}


def test_a_normal_seed_writes_nothing_of_the_unmeasured_screw_type() -> None:
    """A launch seeds the demo as it was: its page and the planner's offer stay as they are."""
    ogm = RecordingOGM()

    seed_demo(ogm)  # type: ignore[arg-type]

    assert not ogm.written() & (UNMEASURED | {UNMEASURED_SCREW_TYPE})


def test_asked_for_the_seed_writes_a_screw_of_the_unmeasured_type_in_its_grinder() -> (
    None
):
    """The model expects the unmeasured Screw type, the grinder contains the Screw and names the
    model, and the Screw's own Screw type is taken from the design. Nothing writes a value to the
    Screw type itself."""
    ogm = RecordingOGM()

    seed_demo(ogm, unmeasured_screw_type=True)  # type: ignore[arg-type]

    assert UNMEASURED <= ogm.written()
    created = {
        instance: data for kind, instance, data in ogm.writes if kind == "create"
    }
    assert created[UNMEASURED_PRODUCT_TYPE] == {
        str(JMS.hasExpectedScrewType): [{"id": str(UNMEASURED_SCREW_TYPE)}]
    }
    assert created[UNMEASURED_GRINDER][str(CONTAINS_WORKPIECE)] == [
        {"id": str(UNMEASURED_SCREW)}
    ]
    assert (
        "commit",
        UNMEASURED_SCREW,
        {str(JMS.hasScrewType): [{"id": str(UNMEASURED_SCREW_TYPE)}]},
    ) in ogm.writes
    assert UNMEASURED_SCREW_TYPE not in ogm.written()


def test_the_unmeasured_screw_type_is_declared_with_its_label_and_no_value() -> None:
    graph = Graph().parse(data=unmeasured_screw_type_turtle(), format="turtle")
    screw_type = URIRef(str(UNMEASURED_SCREW_TYPE))

    assert set(graph.predicate_objects(screw_type)) == {
        (RDF.type, URIRef(str(JMS.ScrewType))),
        (RDFS.label, Literal("M3", lang="en")),
    }


def test_the_unmeasured_screw_has_no_recording() -> None:
    """The store refuses its operation before the screwing resource is notified."""
    assert UNMEASURED_SCREW not in RECORDING_OF


class _RecordingMapper:
    """A mapper that records each write the seed makes, and writes nothing."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, object]] = []

    def create(self, **kwargs: object) -> None:
        self.writes.append(("create", kwargs))

    def commit(self, **kwargs: object) -> None:
        self.writes.append(("commit", kwargs))


def test_the_robot_and_its_link_from_the_screwing_resource_are_one_commit() -> None:
    """The store infers `:hasRobot` from `:isRobotOf`, and the mapper's read before a write
    includes inferred links. An update of the screwing resource after the robot exists finds the
    link already there and writes nothing; the middleware, which reads only written links, then
    finds no robot. So the seed writes the link and creates the robot in one commit, whose read
    comes before the robot exists."""
    mapper = _RecordingMapper()

    seed_robot(mapper)  # type: ignore[arg-type]

    assert len(mapper.writes) == 1
    verb, kwargs = mapper.writes[0]
    assert verb == "commit"
    changes = kwargs["changes"]
    assert isinstance(changes, list)
    updates = [c for c in changes if isinstance(c, Update)]
    creates = [c for c in changes if isinstance(c, Create)]
    assert [u.instance_iri for u in updates] == [SCREWING_RESOURCE]
    assert updates[0].data == {str(JMS.hasRobot): [{"id": str(ROBOT)}]}
    assert [(c.class_iri, c.instance_iri) for c in creates] == [(JMS.Robot, ROBOT)]
    assert creates[0].data[str(JMS.isRobotOf)] == [{"id": str(SCREWING_RESOURCE)}]
