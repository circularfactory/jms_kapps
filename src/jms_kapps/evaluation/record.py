"""The record of one run, and the facts it holds besides what the run did.

A record is a JSON file, `record.json`, in the run's folder. It holds its run kind, `uc2`, and the
words "iteration one"; the resolved configuration in the TOML form of a configuration file, so that
it can be run again; the actions the run took, each with its time; each run setting with its
value, since none is swept; the facts listed for a record (decided by Etienne on 2026-10-11): the
version and the commit of jms_kapps, the middleware, the object-graph mapper and the triple-store
interface, Python and the OS, the client's CPU, its memory and the network's round trip to the
server, and what the server's Workbench shows (`store`); the prefill file's digest and header
lines; the run's transports as the graph holds them at the drain's end; and the spawns the
WarehouseManagementSystem refused, with their reasons, from its output. Since the observation
(added on 2026-10-12, jms_kapps 48) it also holds the completion matcher's reads and, for a run
that completed, its measures, whether it is valid, the checks it failed and made, and its stuck
Operations (`observation`). Since jms_kapps 91 it also holds the client host's CPU and memory load
through the warm-up, the window and the drain, as samples with their times (`client_load`;
the module `client_load` takes them); a record made before has none. Its run kind is
`uc2-unwrapped` for a run without the wrapper.

No secret goes into a record: the login to the server is never read here, and the server's URL is
kept without a user or a password in it (`store.public_url`).
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import statistics
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

# What every record says it is: a UC2 run, through the wrapper (`uc2`) or without it
# (`uc2-unwrapped`; added on 2026-10-12, jms_kapps 48), of iteration one.
RUN_KIND = "uc2"
UNWRAPPED_RUN_KIND = "uc2-unwrapped"
ITERATION = "iteration one"

# The distributions whose version and commit a record holds: jms_kapps and the three it is built
# on.
DISTRIBUTIONS = (
    "jms-kapps",
    "kapps-semantic-middleware",
    "kapps-ogm",
    "kapps-triplestore-interface",
)

# The WarehouseManagementSystem's one record of a spawn, in its output, as its logging writes it:
# a box spawned, or a spawn refused with why, and the origin and the destination where they are
# known (`uc2.wms.spawn_record`). A box is named by a number or by a UUID, `Box-<UUID>` (changed on
# 2026-10-12, jms_kapps 92: until then the box's name was matched as a word, which no name with a
# hyphen is).
_SPAWN_RECORD = re.compile(
    r"INFO:jms_kapps\.uc2\.wms:\w+: "
    r"(?:(?P<spawned>spawned) [A-Za-z0-9_.\-]+ onto .*|spawn refused: (?P<reason>.*?)"
    r"(?: \((?P<where>(?:origin|destination) [^()]*)\))?)$"
)


# The source of a distribution installed from a package index, which records no commit.
INDEX_SOURCE = "a package index"


def software_facts() -> dict[str, dict[str, str | bool | None]]:
    """The version and the commit of jms_kapps and of each distribution it is built on, with
    Python and the OS.

    The commit of a distribution installed from a Git repository is the one its installer
    recorded. Of one installed from a folder, as jms_kapps is in a checkout, it is the folder's
    commit, and `modified` says whether a file Git tracks there differs from it; a file it does
    not track, as a run's folder, does not count. Of one installed from a
    package index there is none.
    """
    facts: dict[str, dict[str, str | bool | None]] = {
        name: _distribution(name) for name in DISTRIBUTIONS
    }
    facts["python"] = {"version": platform.python_version(), "path": sys.executable}
    facts["os"] = {"platform": platform.platform()}
    return facts


def _distribution(name: str) -> dict[str, str | bool | None]:
    try:
        distribution = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return {"version": None, "commit": None, "source": "not installed"}
    found: dict[str, str | bool | None] = {
        "version": distribution.version,
        "commit": None,
        "source": INDEX_SOURCE,
    }
    direct = json.loads(distribution.read_text("direct_url.json") or "null")
    if not isinstance(direct, dict):
        return found
    vcs = direct.get("vcs_info")
    if isinstance(vcs, dict):
        return found | {"commit": vcs.get("commit_id"), "source": direct.get("url")}
    url = str(direct.get("url", ""))
    if url.startswith("file://"):
        folder = unquote(urlsplit(url).path)
        return found | {"source": url} | _git_commit(Path(folder))
    return found | {"source": url}


def _git_commit(folder: Path) -> dict[str, str | bool | None]:
    """The commit of the Git checkout at `folder`, and whether the checkout differs from it."""

    def git(*arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(folder), *arguments],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()

    try:
        return {
            "commit": git("rev-parse", "HEAD"),
            "modified": bool(git("status", "--porcelain", "--untracked-files=no")),
        }
    except (OSError, subprocess.SubprocessError):
        return {"commit": None}


def client_facts(round_trip_ms: list[float]) -> dict[str, Any]:
    """The client the run runs on: its CPU, its memory, and the network's round trip to the
    server, `round_trip_ms`, each in milliseconds."""
    return {
        "cpu": _cpu_model(),
        "logical_cpus": os.cpu_count(),
        "memory_bytes": _memory_bytes(),
        "round_trip_ms": round_trip_ms,
        "round_trip_median_ms": (
            statistics.median(round_trip_ms) if round_trip_ms else None
        ),
    }


def _cpu_model() -> str | None:
    """The CPU's model, as Linux names it, or else as Python's platform module does."""
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or None


def _memory_bytes() -> int | None:
    """The client's physical memory, where the OS says it (POSIX)."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, ValueError, OSError):
        return None


def prefill_facts(path: Path | None) -> dict[str, Any] | None:
    """The prefill file at `path`: its SHA-256 digest, and its header, the comment lines it
    begins with, as its generator wrote them. None for a run without one."""
    if path is None:
        return None
    digest = hashlib.sha256()
    header: list[str] = []
    in_header = True
    with path.open("rb") as file:
        for raw in file:
            digest.update(raw)
            if in_header:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line.startswith("#"):
                    header.append(line)
                else:
                    in_header = False
    return {"path": str(path), "sha256": digest.hexdigest(), "header": header}


def spawns(wms_output: Iterable[str]) -> dict[str, Any]:
    """The spawns that the WarehouseManagementSystem's output records: how many were spawned,
    and each that was refused, with its reason and where it was known, its origin and its
    destination, and how many were refused for each reason."""
    spawned = 0
    refused: list[dict[str, str | None]] = []
    for line in wms_output:
        found = _SPAWN_RECORD.search(line)
        if found is None:
            continue
        if found["spawned"]:
            spawned += 1
            continue
        where = dict(
            part.split(" ", 1) for part in (found["where"] or "").split(", ") if part
        )
        refused.append(
            {
                "reason": found["reason"],
                "origin": where.get("origin"),
                "destination": where.get("destination"),
            }
        )
    return {
        "spawned": spawned,
        "refused": refused,
        "refused_by_reason": dict(
            Counter(str(r["reason"]) for r in refused).most_common()
        ),
    }


def spawns_in_window(
    stamped_wms_output: Iterable[tuple[datetime | None, str]],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    """The spawns the WarehouseManagementSystem was asked for in the measured window, from its
    output lines and the times they arrived (jms_kapps 99, interim names): the **offered** spawns,
    the ones it spawned and the ones it refused, the **accepted** spawns, those it spawned, and
    the **accepted share**, accepted over offered, or None where none was offered. A line counts
    when it arrived from `start` up to `end`; a line without a time stamp counts in no window. No
    number is read from a box's name.

    Args:
        stamped_wms_output: Each line of the output with the time it arrived, or None.
        start: The window's start.
        end: The window's end, which is not in it.
    """
    accepted = refused = 0
    for arrived, line in stamped_wms_output:
        if arrived is None or not start <= arrived < end:
            continue
        found = _SPAWN_RECORD.search(line)
        if found is None:
            continue
        if found["spawned"]:
            accepted += 1
        else:
            refused += 1
    offered = accepted + refused
    return {
        "spawns_offered_in_window": offered,
        "spawns_accepted_in_window": accepted,
        "spawns_accepted_share": accepted / offered if offered else None,
    }


def write_record(folder: Path, record: dict[str, Any]) -> Path:
    """Write `record` as `record.json` in `folder`, and return its path."""
    path = folder / "record.json"
    path.write_text(
        json.dumps(record, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    return path
