"""`jms-uc2 trace-box` on a live store: every change of a box's possession, read back from the
triple store's history, agrees with the handovers the participants logged.

**Only on a disposable dataset, after the history live tests.** The command turns the dataset's
history collection on for good, and `test_uc2_history_live.py` needs it off when it starts; this
file's name sorts after that one's, so a run of the whole tier collects it later. The test takes
the dataset by `--dataset`, as every graphdb test does, refuses one whose name does not start
with `ogm-disposable-` with a skip that says why, and clears it. It runs only when the store's
connection variables are set.
"""

from __future__ import annotations

import json

import pytest
from _disposable import disposable
from kapps_semantic_middleware.credentials import triplestore_env_present

from jms_kapps.uc2 import trace_box
from jms_kapps.uc2.vocabulary import WMS, module_iri

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

WHY = "trace-box turns the store's history collection on, for good"


def test_every_change_of_a_box_s_possession_is_read_back_from_the_store_s_history(
    dataset, tmp_path, capsys
):
    """A run of use case 2 with four modules in a line reads every change of
    possession of a box back from the history of the triple store. The spawn is one
    transaction, with the possession, the `queued` status and the assignment; each handover
    between modules is one, with the possession and the assignment; the give-back to the
    WarehouseManagementSystem is one, with the possession and the `done` status. The possessors
    agree with the handovers the participants logged."""
    out = tmp_path / "trace.json"

    code = trace_box.run(disposable(dataset, WHY), out_path=out)

    printed = capsys.readouterr()
    assert code == 0, printed.out + printed.err
    read = trace_box.parse_record(json.loads(out.read_text(encoding="utf-8")))
    moves = [
        (row.possessor, row.status, row.assigned)
        for row in read.transactions
        if row.possessor is not None
    ]
    m1, m2, m3, m4 = (module_iri(n) for n in range(1, 5))
    assert moves == [
        (m1, "queued", m1),
        (m2, None, m2),
        (m3, None, m3),
        (m4, None, m4),
        (WMS, "done", None),
    ], trace_box.table(read.transactions)
    assert read.cross_check.agree is True
    assert read.cross_check.from_logs == [
        "Module1",
        "Module2",
        "Module3",
        "Module4",
        WMS.fragment,
    ]
    assert trace_box.table(read.transactions) in printed.out
