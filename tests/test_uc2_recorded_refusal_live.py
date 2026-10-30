"""A rerun of the recorded refusal gives the committed report, on a live triple store.

The recording script, `scripts/record_uc2_refusal.py`, seeds two modules on the dataset, serves
both as middleware instances, places box A on module 2 and box B on module 1, and lets module 1
hand box B to module 2 without a reservation, through the middleware's handover. The store refuses
the commit with a validation report. This test runs the recording again, in this process, and
compares the report it gets with `tests/reports/skip-reserve.nq` as RDF graphs: the same graph, up
to the blank-node labels the store gives each report. So the committed report is what a real run
gives, and stays so when the ontologies' namespaces change and the committed files are rewritten.

It clears the dataset it runs against, and runs only when the store's connection variables are
set, against the dataset `--dataset` names. The checkout and the published tree have the script;
an installed copy does not, and the test skips there.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from _repo import find_repo_root
from kapps_semantic_middleware.credentials import triplestore_env_present
from rdflib import URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import SH

from jms_kapps.uc2.vocabulary import FC, module_iri

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

ROOT = find_repo_root(Path(__file__))
COMMITTED = Path(__file__).parent / "reports" / "skip-reserve.nq"
SCRIPT = ROOT / "scripts" / "record_uc2_refusal.py" if ROOT is not None else None


@pytest.fixture(scope="module")
def script() -> ModuleType:
    if SCRIPT is None or not SCRIPT.exists():
        pytest.skip("an installed copy has no scripts")
    spec = importlib.util.spec_from_file_location("record_uc2_refusal", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: its dataclasses resolve their annotations through it.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_a_rerun_of_the_recording_gives_the_committed_report(dataset, script):
    recording = script.record(dataset)

    rerun = script.report_graph(recording.report)
    committed = script.report_graph(COMMITTED.read_text(encoding="utf-8"))
    assert isomorphic(rerun, committed)
    # What the paper's listing shows, read from the rerun itself.
    (result,) = rerun.subjects(
        SH.sourceShape, URIRef(FC.FlexConveyorModulePossessionShape)
    )
    assert rerun.value(result, SH.sourceConstraintComponent) == (
        SH.MaxCountConstraintComponent
    )
    assert rerun.value(result, SH.focusNode) == URIRef(module_iri(2))
    # Nothing changed: the same three queries answered the same before and after.
    assert recording.before == recording.after
    assert not recording.module_result.accepted
