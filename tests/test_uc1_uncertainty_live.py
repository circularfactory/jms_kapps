"""A measured position as an uncertainty quantification, on a live GraphDB: the Gaussian-mixture
literal travels through the knowledge graph with its datatype, and the store's admission refuses
what the shapes of a measured position refuse.

A random variable created through the middleware's `create` with the literal of one measurement
is fetched back as an rdflib Literal of the datatype `uq:gmmLiteral` with the same lexical form,
and a plain SPARQL `DATATYPE()` of the stored value is `uq:gmmLiteral`. An update of its
distribution leaves one value, the new one. The refused writes are plain SPARQL updates, so that
the store, not the mapper, refuses them; each report names its shape.

**Only on a dataset you can lose.** The reset clears the dataset `--dataset` names, as every
graphdb test does.
"""

from __future__ import annotations

import pytest
from kapps_ogm import OGM, ClassScope, Update
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.validation_report import validation_results
from kapps_triplestore_interface import IRI, ShaclValidationError
from rdflib import Literal

from jms_kapps.common.triplestore import bindings, connect
from jms_kapps.uc1.anomaly_detector import RESOURCE as DETECTOR
from jms_kapps.uc1.run_core import resources_turtle
from jms_kapps.uc1.seed import reset_uc1
from jms_kapps.uc1.uncertainty import GMM_LITERAL, measurement_literal, read_measurement
from jms_kapps.uc1.vocabulary import JMS, JMS_NS, UQ

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

VARIABLE_SCOPE = ClassScope.from_property_chains([[UQ.hasDistribution], [UQ.hasDomain]])

# A lexical form without `weights`: the pattern refuses it.
MISSING_FIELD = (
    '{"n_components":1,"dimensions":1,"covariance_type":"full",'
    '"means":[[0.0]],"covariances":[[[0.01]]]}'
)


def shape(name: str) -> str:
    return f"{JMS_NS}{name}"


@pytest.fixture
def db(dataset):
    """The dataset, cleared, with use case 1's ontology, its uncertainty module, its shapes and
    the seeded resources."""
    db = connect(dataset)
    reset_uc1(db, individuals=resources_turtle())
    return db


@pytest.fixture
def mw(db) -> SemanticMiddleware:
    return SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=str(DETECTOR),
        service_class=str(JMS.AnomalyDetectorService),
        ogm=OGM(db=db),
    )


def distributions(db, variable: str) -> list[dict]:
    return bindings(
        db,
        f"SELECT ?d (DATATYPE(?d) AS ?type) WHERE {{ <{variable}> <{UQ.hasDistribution}> ?d }}",
    )


def created(mw: SemanticMiddleware, literal: Literal) -> str:
    with mw.create(str(UQ.RandomVariable), VARIABLE_SCOPE) as variable:
        variable.update(
            {
                str(UQ.hasDistribution): [literal],
                str(UQ.hasDomain): [{"id": str(UQ.double)}],
            }
        )
    return str(variable.iri)


def test_a_created_variable_keeps_its_literal_and_its_datatype(db, mw) -> None:
    literal = measurement_literal(0.25, 0.1)

    variable = created(mw, literal)

    data = OGM(db=db).fetch(instance_iri=IRI(variable), class_scope=VARIABLE_SCOPE).data
    (fetched,) = data[UQ.hasDistribution]
    assert isinstance(fetched, Literal)
    assert fetched.datatype == GMM_LITERAL
    assert str(fetched) == str(literal)
    assert read_measurement(fetched) == read_measurement(literal)
    (row,) = distributions(db, variable)
    assert str(row["type"]) == str(UQ.gmmLiteral)


def test_an_update_of_the_distribution_leaves_only_the_new_one(db, mw) -> None:
    variable = created(mw, measurement_literal(0.25, 0.1))
    new = measurement_literal(0.3, 0.1)

    OGM(db=db).commit(
        changes=[Update(instance_iri=IRI(variable), data={str(UQ.hasDistribution): [new]})]
    )

    (row,) = distributions(db, variable)
    assert str(row["d"]) == str(new)
    assert str(row["type"]) == str(UQ.gmmLiteral)


def gmm(lexical: str) -> str:
    return f'"{lexical.replace(chr(34), chr(92) + chr(34))}"^^<{UQ.gmmLiteral}>'


VARIABLE = f"{JMS_NS}UncertaintyTestVariable"
SCREW = f"{JMS_NS}UncertaintyTestScrew"
GOOD = gmm(str(measurement_literal(0.25, 0.1)))
OTHER = gmm(str(measurement_literal(0.3, 0.1)))
RV = f"<{UQ.RandomVariable}>"

REFUSED = {
    "a literal with a missing field": (
        (
            f"<{VARIABLE}> a {RV} ; <{UQ.hasDistribution}> {gmm(MISSING_FIELD)} ;"
            f" <{UQ.hasDomain}> <{UQ.double}> ."
        ),
        VARIABLE,
        UQ.hasDistribution,
        "RandomVariableDistributionShape",
    ),
    "a second distribution": (
        (
            f"<{VARIABLE}> a {RV} ; <{UQ.hasDistribution}> {GOOD}, {OTHER} ;"
            f" <{UQ.hasDomain}> <{UQ.double}> ."
        ),
        VARIABLE,
        UQ.hasDistribution,
        "RandomVariableDistributionShape",
    ),
    "a variable without a domain": (
        f"<{VARIABLE}> a {RV} ; <{UQ.hasDistribution}> {GOOD} .",
        VARIABLE,
        UQ.hasDomain,
        "RandomVariableDomainShape",
    ),
    "a second best estimate on a Screw": (
        f"<{SCREW}> a <{JMS.Screw}> ; <{JMS.hasMeasuredScrewHeadPosition}> 0.1e0, 0.2e0 .",
        SCREW,
        JMS.hasMeasuredScrewHeadPosition,
        "ScrewMeasuredHeadPositionShape",
    ),
    "a second variable on a Screw": (
        f"<{SCREW}> a <{JMS.Screw}> ; <{JMS.hasScrewHeadPositionVariable}> <{VARIABLE}1>,"
        f" <{VARIABLE}2> .\n"
        + "".join(
            f"<{VARIABLE}{n}> a {RV} ; <{UQ.hasDistribution}> {GOOD} ;"
            f" <{UQ.hasDomain}> <{UQ.double}> .\n"
            for n in (1, 2)
        ),
        SCREW,
        JMS.hasScrewHeadPositionVariable,
        "ScrewHeadPositionVariableShape",
    ),
}


@pytest.mark.parametrize("why", REFUSED)
def test_the_store_refuses_it_by_its_named_shape(db, why: str) -> None:
    triples, focus, path, name = REFUSED[why]

    with pytest.raises(ShaclValidationError) as raised:
        db.query(f"INSERT DATA {{ {triples} }}", update=True)

    results = validation_results(raised.value.report)
    assert any(
        r.focus == focus and r.path == f"<{path}>" and r.shape == shape(name)
        for r in results
    ), results
    assert not bindings(db, f"SELECT ?p WHERE {{ <{focus}> ?p ?o }}")
