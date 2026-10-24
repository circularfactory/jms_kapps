"""The ids of the passthrough that both use cases share: one for each process of a run, safe in a
path, and the lookup of a process by its id among those of the current run.

The requests that pass through are tested on each page that serves them: use case 2's Launcher in
`test_uc2_launcher_passthrough.py`, the page of use case 1 in `test_uc1_page_passthrough.py`.
"""

from __future__ import annotations

import pytest
from kapps_triplestore_interface import IRI

from jms_kapps.common.passthrough import process_id, process_of, process_path
from jms_kapps.common.supervision import ChildSnapshot
from jms_kapps.uc1.anomaly_detector import RESOURCE as DETECTOR
from jms_kapps.uc1.screwing_resource import RESOURCE as SCREWING_RESOURCE
from jms_kapps.uc1.vocabulary import JMS
from jms_kapps.uc2.topology import load_topology
from jms_kapps.uc2.vocabulary import FCI_NS, MONITOR, WMS, module_iri


def test_use_case_1_s_processes_go_by_the_names_its_ontology_gives_them():
    resources = [DETECTOR, SCREWING_RESOURCE, JMS.Learner1, JMS.Planner1]

    assert [process_id(r) for r in resources] == [
        "AnomalyDetector1",
        "ScrewingResource1",
        "Learner1",
        "Planner1",
    ]
    assert process_path(JMS.Planner1) == "p/Planner1/"


def test_use_case_2_s_processes_keep_their_name_in_fci():
    resources = [WMS, MONITOR] + [
        module_iri(m.id)
        for name in ("cross", "grid", "pair", "tee", "merge-fault-demo")
        for m in load_topology(name).modules
    ]

    for resource in resources:
        assert process_id(resource) == str(resource).removeprefix(FCI_NS)
        assert process_id(resource).isidentifier(), resource


def test_a_resource_without_a_fragment_has_no_id():
    with pytest.raises(ValueError, match="no name after a '#'"):
        process_id(IRI("http://example.org/test/Planner1"))


def _child(resource: IRI) -> ChildSnapshot:
    return ChildSnapshot(
        resource=resource,
        name=resource.fragment,
        state="live",
        pid=1,
        command="python -m x",
        output=[],
        address=None,
        unconfirmed_for=None,
        stoppable=True,
        why_failed=None,
    )


def test_a_process_is_found_by_its_id_among_those_of_the_run_only():
    children = [_child(JMS.Learner1), _child(JMS.Planner1)]

    assert process_of(children, "Planner1") is children[1]
    assert process_of(children, "AnomalyDetector1") is None
