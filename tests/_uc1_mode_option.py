"""The `--uc1-save-measurements-as-uq` option of the live runs of use case 1.

With it, every run of `jms-uc1` that a live test starts (`_uc1_run.started_uc1`) saves
measurements as uncertainty quantifications from its start; without it, the runs keep the
default, off. So the same live run tests and claim tests run in either mode, and the land runs
both without guessing. A plugin module, as `_dataset_option.py` is.
"""

from __future__ import annotations

import pytest

OPTION = "--uc1-save-measurements-as-uq"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        OPTION,
        action="store_true",
        default=False,
        help=(
            "Start each live run of jms-uc1 with the mode 'Save measurements as uncertainty "
            "quantifications' on (default: off)"
        ),
    )


@pytest.fixture(scope="session")
def uc1_saves_uq(request: pytest.FixtureRequest) -> bool:
    """Whether the live runs of `jms-uc1` save measurements as uncertainty quantifications."""
    return bool(request.config.getoption(OPTION))
