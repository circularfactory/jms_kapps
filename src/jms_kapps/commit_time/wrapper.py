"""The wrapper: it starts one participant of a run, observed from outside its code.

    python -m jms_kapps.commit_time.wrapper --spans <file> -- -m <module> [<argument> ...]
    python -m jms_kapps.commit_time.wrapper --spans <file> -- <script> [<argument> ...]

The bench launcher starts each participant of a UC2 run through it: its module's or the
WarehouseManagementSystem's command line, with the wrapper in front (`wrapped`). The wrapper sets
up the observation in its own process (`instrumentation`), then runs the participant's module or
script in that process, as `python -m` or `python <script>` would, with the same arguments, exit
status and output. Nothing of the participant, of the semantic middleware, of the OGM or of the
access module changes: the observation is OpenTelemetry's instrumentation and the
wrapping of the mapper's public calls.

Every span goes into `--spans`, a file in the run's folder. A store request is a request to the
URL that `TRIPLESTORE_URL` names, the store the participant reaches.

OpenTelemetry is the extra `commit-time` of jms_kapps: `uv sync --extra commit-time`. A plain
install leaves it out, and then `available` says so, before a run starts.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import runpy
import sys
from collections.abc import Sequence
from pathlib import Path

# The module that the wrapper's command runs.
MODULE = "jms_kapps.commit_time.wrapper"

# What the observation needs installed: the extra `commit-time`.
REQUIRED = (
    "opentelemetry.sdk",
    "opentelemetry.instrumentation.fastapi",
    "opentelemetry.instrumentation.httpx",
    "opentelemetry.instrumentation.requests",
)

INSTALL = "uv sync --extra commit-time"

# The object-graph mapper's public calls that the wrapper gives a span each, by their module, their
# class and their name: the commit, the class-spec derivation, the fetch, the create, the delete
# and the diff.
OGM_CALLS = (
    ("kapps_ogm.ogm", "OGM", "commit"),
    ("kapps_ogm.ogm", "OGM", "get_class_spec"),
    ("kapps_ogm.ogm", "OGM", "fetch"),
    ("kapps_ogm.ogm", "OGM", "create"),
    ("kapps_ogm.ogm", "OGM", "delete"),
    ("kapps_ogm.node.core", "Node", "diff"),
)


def span_name(owner: str, call: str) -> str:
    """The name of the span of the mapper's call `owner.call`, as `kapps_ogm.OGM.commit`."""
    return f"kapps_ogm.{owner}.{call}"


# The span of a commit of the mapper.
COMMIT_SPAN = span_name("OGM", "commit")


class WrapperUnavailable(RuntimeError):
    """The wrapper cannot run here: OpenTelemetry, the extra `commit-time`, is not installed. The
    message says what is missing and how to install it."""


def missing() -> list[str]:
    """The modules of OpenTelemetry that the wrapper needs and this Python cannot import."""
    absent = []
    for name in REQUIRED:
        try:
            found = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            found = None
        if found is None:
            absent.append(name)
    return absent


def available() -> None:
    """Check that the wrapper can run here.

    Raises:
        WrapperUnavailable: If a module of OpenTelemetry that it needs is not installed.
    """
    absent = missing()
    if absent:
        raise WrapperUnavailable(
            f"the wrapper needs OpenTelemetry, the extra commit-time of jms_kapps, and "
            f"{', '.join(absent)} is not installed: run {INSTALL}, or turn the wrapper off "
            "in the configuration (wrapper = false)"
        )


def wrapped(command: Sequence[str], spans: Path) -> list[str]:
    """`command`, a participant's command line, run through the wrapper, which writes its spans
    into `spans`. The command runs a Python module (`<python> -m <module> ...`) or a script
    (`<python> <script> ...`), and keeps its Python."""
    python, *rest = command
    if not rest:
        raise ValueError(f"no module or script to wrap in {list(command)!r}")
    return [python, "-m", MODULE, "--spans", str(spans), "--", *rest]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=f"python -m {MODULE}",
        description="Run one participant of a UC2 run, observed: its spans go into a file.",
    )
    parser.add_argument(
        "--spans", type=Path, required=True, help="The file the spans are written into"
    )
    parser.add_argument(
        "target",
        nargs=argparse.REMAINDER,
        help="After --: -m <module> or <script>, and its arguments",
    )
    args = parser.parse_args(argv)
    target = list(args.target)
    if target[:1] == ["--"]:
        target = target[1:]
    if not target or (target[0] == "-m" and len(target) < 2):
        parser.error("name the participant to run after --: -m <module> or <script>")

    from jms_kapps.commit_time.instrumentation import instrument

    instrumentation = instrument(
        args.spans,
        participant=args.spans.stem,
        store_url=os.environ.get("TRIPLESTORE_URL"),
    )
    try:
        if target[0] == "-m":
            sys.argv = [target[1], *target[2:]]
            runpy.run_module(target[1], run_name="__main__", alter_sys=True)
        else:
            sys.argv = list(target)
            runpy.run_path(target[0], run_name="__main__")
    finally:
        instrumentation.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
