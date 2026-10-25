"""The live runs of use case 1 are off unless `--uc1-save-measurements-as-uq` turns them on.

Each case is a small pytest session in this process (pytest's own `pytester`), with the option's
plugin module loaded and one test that asks for its fixture. No store is needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

TESTS = Path(__file__).parent


@pytest.mark.parametrize(
    ("args", "expected"), [((), False), (("--uc1-save-measurements-as-uq",), True)]
)
def test_the_option_selects_the_mode_of_the_live_runs(
    pytester: pytest.Pytester, args: tuple[str, ...], expected: bool
) -> None:
    pytester.syspathinsert(TESTS)
    pytester.makepyfile(
        f"""
def test_mode(uc1_saves_uq):
    assert uc1_saves_uq is {expected}
"""
    )

    result = pytester.runpytest_inprocess(
        "-p", "_uc1_mode_option", "-p", "no:playwright", *args
    )

    result.assert_outcomes(passed=1)
