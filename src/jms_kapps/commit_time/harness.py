"""The local store of the measurement harness: the GraphDB of `compose.yaml`, which the commit
time experiment script (`commit_time/experiment.py`) starts for its local runs, and the
connection variables a run reads.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# The local fallback's store, of `compose.yaml`, with its login (the README's setup sets the same).
LOCAL_STORE_URL = "http://localhost:7200"
LOCAL_STORE_USERNAME = "admin"
LOCAL_STORE_PASSWORD = "root"


class HarnessRefused(ValueError):
    """The local store cannot start: the message is why, for a person to read."""


# --- The store -----------------------------------------------------------------------------


def use_deprecated_variables() -> None:
    """Set each connection variable that is not set from its deprecated predecessor, for this
    process: a run reads only `TRIPLESTORE_*`, where `jms-uc2` reads the deprecated variables
    itself."""
    for new, old in (
        ("TRIPLESTORE_URL", "GRAPHDB_URL"),
        ("TRIPLESTORE_USERNAME", "GRAPHDB_USERNAME"),
        ("TRIPLESTORE_PASSWORD", "GRAPHDB_PASSWORD"),
    ):
        if not os.environ.get(new) and os.environ.get(old):
            os.environ[new] = os.environ[old]


def start_local_store(root: Path | None = None) -> None:
    """Start the local fallback, the services of `compose.yaml` in the checkout's root,
    with `docker compose up -d --wait`, or `docker-compose`, and set the
    connection variables for this process. Returns once the services answer.

    Raises:
        HarnessRefused: If this is no checkout (no `compose.yaml`), Docker Compose is missing, or
            the services do not start.
    """
    folder = root or Path(__file__).resolve().parents[3]
    if not (folder / "compose.yaml").is_file():
        raise HarnessRefused(
            "no store is configured, and the local fallback needs a checkout of jms_kapps, "
            f"which has the compose.yaml; there is none at {str(folder)!r}. Set TRIPLESTORE_URL, "
            "TRIPLESTORE_USERNAME and TRIPLESTORE_PASSWORD"
        )
    if _compose_works(["docker", "compose"]):
        compose = ["docker", "compose"]
    elif shutil.which("docker-compose"):
        compose = ["docker-compose"]
    else:
        raise HarnessRefused(
            "needs Docker Compose to start the local GraphDB, and finds neither "
            "`docker compose` nor `docker-compose`."
        )
    try:
        subprocess.run([*compose, "up", "-d", "--wait"], cwd=folder, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise HarnessRefused(f"the local GraphDB did not start: {error}") from None
    os.environ["TRIPLESTORE_URL"] = LOCAL_STORE_URL
    os.environ["TRIPLESTORE_USERNAME"] = LOCAL_STORE_USERNAME
    os.environ["TRIPLESTORE_PASSWORD"] = LOCAL_STORE_PASSWORD


def _compose_works(command: list[str]) -> bool:
    if shutil.which(command[0]) is None:
        return False
    try:
        return (
            subprocess.run(
                [*command, "version"], capture_output=True, check=False
            ).returncode
            == 0
        )
    except OSError:
        return False
