"""The `--dataset` option of the graphdb tests, and the refusal to run them without it.

The `graphdb` tests clear the dataset they run against. So no dataset is a default: it comes from
the command line only, never from the environment, and a run that would clear one without having
been told which stops before any test runs. A run that cannot reach a store needs none.

This is a plugin module, not part of `conftest.py`, so that a test can load it into a small pytest
session of its own: see `test_dataset_option.py`. The option and its fixture use the triple-store interface's name of
the store's unit, the dataset.
"""

from __future__ import annotations

import pytest
from kapps_semantic_middleware.credentials import triplestore_env_present

NO_DATASET_MESSAGE = (
    "the graphdb tests clear the dataset they run against; name it with "
    "`--dataset <name>`, for example `--dataset Tests`; there is no default"
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--dataset",
        default=None,
        help=(
            "The dataset of the triple store that the graphdb tests clear and use. Required "
            "where the store's connection variables are set and a selected test needs it; there "
            "is no default"
        ),
    )


def pytest_collection_finish(session: pytest.Session) -> None:
    """Stop, once and before any test runs, when a selected test needs a dataset and none is
    named. It counts the tests left after `-m` and `-k`, and asks the connection variables as the
    graphdb tests' own skip does: without them no test reaches a store, so none needs a name."""
    if session.config.getoption("--dataset") is not None or not triplestore_env_present():
        return
    if any("dataset" in item.fixturenames for item in session.items):
        raise pytest.UsageError(NO_DATASET_MESSAGE)


@pytest.fixture(scope="session")
def dataset(request: pytest.FixtureRequest) -> str:
    """The dataset named on the command line."""
    return request.config.getoption("--dataset")
