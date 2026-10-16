"""The command `jms-eval-report`: the report of the runs of a spec, tables and charts beside their
records.

The report needs the optional extra `evaluation` (pandas, matplotlib), which a plain install of
jms_kapps leaves out. So this module imports the report only when the command runs, and a plain
install that runs it gets a line naming the extra instead of a traceback. The release's install
check imports each command's module on a plain install, so this module imports nothing of the extra.
"""

from __future__ import annotations

import sys

# The packages of the extra `evaluation` that the report imports at its top.
EXTRA_PACKAGES = frozenset({"pandas", "matplotlib"})


def main(argv: list[str] | None = None) -> int:
    try:
        from jms_kapps.evaluation import report
    except ModuleNotFoundError as missing:
        if (missing.name or "").partition(".")[0] not in EXTRA_PACKAGES:
            raise
        print(
            f"jms-eval-report needs {missing.name}, from the extra `evaluation`: "
            "install it with `uv sync --extra evaluation`.",
            file=sys.stderr,
        )
        return 2
    return report.main(argv)


if __name__ == "__main__":
    sys.exit(main())
