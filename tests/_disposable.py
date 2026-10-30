"""The guard of the live tests that may run only on a disposable dataset of the store.

A disposable dataset is one created for a test run, from `graphdb/jms-kapps-repo-config.ttl`
with only its ID changed, and deleted afterwards. Its name starts with `ogm-disposable-`, as the
disposable datasets on the remote store are named. The live tests of the history collection run on
a disposable dataset only, never on `Tests`.
"""

from __future__ import annotations

import pytest

DISPOSABLE_PREFIX = "ogm-disposable-"


def disposable(dataset: str, why: str) -> str:
    """`dataset`, if it is a disposable dataset. Any other is refused before anything
    connects to it: the test is skipped, with `why` and the name it refused.

    A skip, not a failure: a run of every live test on a shared dataset, such as `Tests`, stays
    green, and says in its skip reasons what it did not run.
    """
    if not dataset.startswith(DISPOSABLE_PREFIX):
        pytest.skip(
            f"refused on {dataset!r}: {why}, so this test runs only on a disposable "
            f"dataset, whose name starts with {DISPOSABLE_PREFIX!r}"
        )
    return dataset
