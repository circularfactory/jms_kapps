"""The local stack of `compose.yaml` creates both datasets, each checked on its own.

The stack's init container creates `jms-kapps`, for the use cases, and `kapps-demo`, for the
TransferUnit factory demo of the middleware. These tests read the compose file and the two
configuration files offline: the init script runs under `sh` with a stand-in `curl`, so the check
needs neither Docker nor a store. The run on a fresh volume and on a volume that holds only
`jms-kapps` is the live check of the merge request.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "compose.yaml"
GRAPHDB = REPO_ROOT / "graphdb"

# The stand-in answers the size of each dataset named in `HOLDS` with success and every other with
# a failure, as curl's `-f` does for a 404; it records each request line and answers a POST with
# the exit code in `POST_EXIT`.
STAND_IN = """#!/bin/sh
echo "$@" >> "$CALLS"
case "$*" in
  *"-X POST"*) exit "${POST_EXIT:-0}" ;;
  *"/repositories/"*"/size"*)
    for held in $HOLDS; do
      case "$*" in *"/repositories/$held/size"*) exit 0 ;; esac
    done
    exit 22 ;;
esac
exit 0
"""


def _init_script() -> str:
    """The shell script of the init container, as the container's shell receives it."""
    text = COMPOSE.read_text(encoding="utf-8")
    block = re.search(r'graphdb-init:.*?command: >-\n\s+/bin/sh -c "\n(.*?)\n\s+"\n', text, re.DOTALL)
    assert block, "the init container's command was not found"
    # Compose splits the folded line like a shell would, so `\"` is a quote of the script, and
    # `$$` is its spelling of a literal dollar sign.
    return block.group(1).replace('\\"', '"').replace("$$", "$")


def _run_init(tmp_path: Path, holds: str, post_exit: int = 0) -> tuple[int, list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(STAND_IN, encoding="utf-8")
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)
    calls = tmp_path / "calls.txt"
    calls.touch()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CALLS": str(calls),
        "HOLDS": holds,
        "POST_EXIT": str(post_exit),
    }
    done = subprocess.run(["sh", "-c", _init_script()], env=env, capture_output=True, text=True, check=False)
    return done.returncode, calls.read_text(encoding="utf-8").splitlines()


def _posts(calls: list[str]) -> list[str]:
    return [call for call in calls if "-X POST" in call]


@pytest.mark.posix
def test_a_fresh_volume_gets_both_datasets(tmp_path: Path) -> None:
    code, calls = _run_init(tmp_path, holds="")
    assert code == 0
    posts = _posts(calls)
    assert len(posts) == 2
    assert "config=@/config/jms-kapps-repo-config.ttl" in posts[0]
    assert "config=@/config/kapps-demo-repo-config.ttl" in posts[1]


@pytest.mark.posix
def test_a_volume_with_only_jms_kapps_gets_kapps_demo(tmp_path: Path) -> None:
    code, calls = _run_init(tmp_path, holds="jms-kapps")
    assert code == 0
    posts = _posts(calls)
    assert len(posts) == 1
    assert "config=@/config/kapps-demo-repo-config.ttl" in posts[0]


@pytest.mark.posix
def test_a_volume_with_both_datasets_is_left_alone(tmp_path: Path) -> None:
    code, calls = _run_init(tmp_path, holds="jms-kapps kapps-demo")
    assert code == 0
    assert _posts(calls) == []


@pytest.mark.posix
def test_a_refused_creation_fails_the_container(tmp_path: Path) -> None:
    """The InfluxDB service waits for this container to complete successfully."""
    code, _ = _run_init(tmp_path, holds="", post_exit=22)
    assert code != 0


def test_the_health_check_waits_for_both_datasets() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    check = re.search(r'test: \["CMD-SHELL", "([^"]*)"\]', text)
    assert check
    assert "/repositories/jms-kapps/size" in check.group(1)
    assert "/repositories/kapps-demo/size" in check.group(1)


def test_both_datasets_have_one_configuration_but_the_id_and_the_label() -> None:
    """`kapps-demo` is `jms-kapps` with the id and the label changed, and nothing else."""

    def settings(name: str) -> list[str]:
        lines = (GRAPHDB / name).read_text(encoding="utf-8").splitlines()
        return [
            line
            for line in lines
            if line.strip()
            and not line.lstrip().startswith("#")
            and "rep:repositoryID" not in line
            and "rdfs:label" not in line
        ]

    assert settings("kapps-demo-repo-config.ttl") == settings("jms-kapps-repo-config.ttl")
    demo = (GRAPHDB / "kapps-demo-repo-config.ttl").read_text(encoding="utf-8")
    assert 'rep:repositoryID "kapps-demo"' in demo


def test_the_guide_links_the_middleware_at_the_tag_that_the_lock_pins() -> None:
    """The demo's source is linked at the release that `uv.lock` installs."""
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    pinned = next(
        package["version"]
        for package in lock["package"]
        if package["name"] == "kapps-semantic-middleware"
    )
    guide = (REPO_ROOT / "docs" / "user-guide.md").read_text(encoding="utf-8")
    assert f"/kapps_semantic_middleware/tree/v{pinned}/demo/transferunits)" in guide


def test_the_extra_requires_the_middlewares_extra_and_the_lock_records_the_broker() -> None:
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extra = manifest["project"]["optional-dependencies"]["transferunits-demo"]
    assert any(
        requirement.startswith("kapps-semantic-middleware[examples]") for requirement in extra
    )
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    own = next(package for package in lock["package"] if package["name"] == "jms-kapps")
    assert {"name": "kapps-semantic-middleware", "extra": ["examples"]} in own[
        "optional-dependencies"
    ]["transferunits-demo"]
    assert any(package["name"] == "amqtt" for package in lock["package"])
