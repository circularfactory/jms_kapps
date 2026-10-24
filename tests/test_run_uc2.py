"""`run-uc2`, use case 2 from one line: what it hands to the stack and to `jms-uc2`, and what it
refuses.

Each test runs the real script, from a directory that is not the repository root, with a `PATH` of
stand-ins and nothing else: `docker`, `docker-compose`, `uv`, `curl` and `xdg-open` record how they
were called and answer as the test says. No container starts, no page opens, and nothing connects
to a store.
"""

from __future__ import annotations

import json
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from _repo import find_repo_root
from _wait import eventually

from jms_kapps.uc2.launcher.runner import LAUNCHER_HOST, LAUNCHER_PORT

ROOT = find_repo_root(Path(__file__))
SCRIPT = ROOT / "run-uc2" if ROOT is not None else None

pytestmark = [
    pytest.mark.skipif(
        SCRIPT is None, reason="an installed copy has no repository root and no run-uc2"
    ),
    pytest.mark.posix(reason="run-uc2 is a bash script, and so is each stand-in"),
]

# The local fallback of `compose.yaml`, as the script names it to the run.
LOCAL_STORE = {
    "TRIPLESTORE_URL": "http://localhost:7200",
    "TRIPLESTORE_USERNAME": "admin",
    "TRIPLESTORE_PASSWORD": "root",
}

# A store that the environment names, such as a shared server.
CONFIGURED_STORE = {
    "TRIPLESTORE_URL": "https://store.example",
    "TRIPLESTORE_USERNAME": "someone",
    "TRIPLESTORE_PASSWORD": "secret",
}

# The same store, named by the deprecated variables only, which the store still reads.
DEPRECATED_STORE = {
    "GRAPHDB_URL": "https://store.example",
    "GRAPHDB_USERNAME": "someone",
    "GRAPHDB_PASSWORD": "secret",
}

# The host port of the local fallback's store, which `compose.yaml` and the script both read.
PORT_VARIABLE = "JMS_KAPPS_GRAPHDB_PORT"

# Every variable a stand-in records.
WATCHED = sorted({*LOCAL_STORE, *DEPRECATED_STORE, PORT_VARIABLE})


def recorded(env: dict[str, str]) -> dict[str, str | None]:
    """What a stand-in records of `env`: each watched variable, None where it is not set."""
    return {name: env.get(name) for name in WATCHED}

# What a stand-in does after it recorded its call, unless the test says otherwise.
SUCCEEDS = "sys.exit(0)"

STAND_IN = """#!{python}
import json, os, sys, time
from pathlib import Path

record = Path(os.environ["RUN_UC2_RECORD"])
call = {{
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "env": {{name: os.environ.get(name) for name in {watched!r}}},
}}
with open(record / "{name}.jsonl", "a") as calls:
    calls.write(json.dumps(call) + "\\n")
{then}
"""


class Machine:
    """A machine with only the stand-ins on its `PATH`, and the tools the script itself needs."""

    def __init__(self, tmp_path: Path) -> None:
        self.bin = tmp_path / "bin"
        self.record = tmp_path / "record"
        self.cwd = tmp_path / "elsewhere"
        for directory in (self.bin, self.record, self.cwd):
            directory.mkdir()
        for tool in ("bash", "dirname", "sleep", "setsid"):
            found = shutil.which(tool)
            if found is not None:
                (self.bin / tool).symlink_to(found)

    def stand_in(self, name: str, then: str = SUCCEEDS) -> None:
        path = self.bin / name
        path.write_text(
            STAND_IN.format(
                python=sys.executable,
                name=name,
                watched=WATCHED,
                then=then,
            )
        )
        path.chmod(0o755)

    def calls(self, name: str) -> list[dict]:
        path = self.record / f"{name}.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        return {
            "PATH": str(self.bin),
            "RUN_UC2_RECORD": str(self.record),
            **(extra or {}),
        }

    def run(
        self, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SCRIPT), *args],
            cwd=self.cwd,
            env=self.env(env),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def start(self) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [str(SCRIPT)],
            cwd=self.cwd,
            env=self.env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )


@pytest.fixture
def machine(tmp_path: Path) -> Machine:
    """Docker with its Compose plugin, and uv."""
    machine = Machine(tmp_path)
    machine.stand_in("docker")
    machine.stand_in("uv")
    return machine


@pytest.mark.parametrize("docker", ["present", "absent"])
@pytest.mark.parametrize(
    "configured",
    [CONFIGURED_STORE, DEPRECATED_STORE],
    ids=["by-the-store-s-variables", "by-the-deprecated-variables"],
)
def test_a_configured_store_reaches_the_run_unchanged_and_no_local_stack_starts(
    machine: Machine, docker: str, configured: dict[str, str]
) -> None:
    """Also where only the deprecated variables name it, which the store still reads: the run
    must not go to the local fallback while a person looks at the configured store."""
    if docker == "absent":
        (machine.bin / "docker").unlink()

    result = machine.run(env=configured)

    assert result.returncode == 0, result.stderr
    [run] = machine.calls("uv")
    assert run["env"] == recorded(configured)
    assert machine.calls("docker") == []


@pytest.mark.parametrize("url", ["unset", "empty"])
def test_without_a_configured_store_the_run_gets_the_local_stack(
    machine: Machine, url: str
) -> None:
    # Credentials left over from another server, with no server to use them on.
    env = {"TRIPLESTORE_USERNAME": "someone", "TRIPLESTORE_PASSWORD": "secret"}
    if url == "empty":
        env["TRIPLESTORE_URL"] = ""

    result = machine.run(env=env)

    assert result.returncode == 0, result.stderr
    ups = [call["argv"] for call in machine.calls("docker") if "up" in call["argv"]]
    assert ups == [["compose", "up", "-d", "--wait"]]
    [run] = machine.calls("uv")
    assert run["env"] == recorded(LOCAL_STORE)


def test_a_free_port_for_the_local_store_reaches_compose_and_the_run(
    machine: Machine,
) -> None:
    """The variable is the compose file's to read; the script hands it on and points the run at
    the same port."""
    result = machine.run(env={PORT_VARIABLE: "7201"})

    assert result.returncode == 0, result.stderr
    [up] = [call for call in machine.calls("docker") if "up" in call["argv"]]
    assert up["env"][PORT_VARIABLE] == "7201"
    [run] = machine.calls("uv")
    assert run["env"] == {
        **recorded({**LOCAL_STORE, "TRIPLESTORE_URL": "http://localhost:7201"}),
        PORT_VARIABLE: "7201",
    }
    assert "http://localhost:7201" in result.stdout


def test_a_configured_store_ignores_the_port_variable(machine: Machine) -> None:
    result = machine.run(env={**CONFIGURED_STORE, PORT_VARIABLE: "7201"})

    assert result.returncode == 0, result.stderr
    assert machine.calls("docker") == []
    [run] = machine.calls("uv")
    assert run["env"]["TRIPLESTORE_URL"] == CONFIGURED_STORE["TRIPLESTORE_URL"]


@pytest.mark.parametrize(
    ("env", "named"),
    [
        (
            CONFIGURED_STORE,
            f"the store that TRIPLESTORE_URL names, {CONFIGURED_STORE['TRIPLESTORE_URL']}",
        ),
        (
            DEPRECATED_STORE,
            (
                "GRAPHDB_URL is deprecated; set TRIPLESTORE_URL, which replaces it. The run uses "
                f"the store that GRAPHDB_URL names, {DEPRECATED_STORE['GRAPHDB_URL']}"
            ),
        ),
        ({}, f"the local GraphDB of compose.yaml, {LOCAL_STORE['TRIPLESTORE_URL']}"),
    ],
    ids=["configured", "deprecated", "fallback"],
)
def test_one_line_says_which_store_the_run_uses(
    machine: Machine, env: dict[str, str], named: str
) -> None:
    result = machine.run(env=env)

    assert result.returncode == 0, result.stderr
    [line] = [line for line in result.stdout.splitlines() if "the run uses" in line.lower()]
    assert named in line


def test_every_argument_reaches_jms_uc2_unchanged(machine: Machine) -> None:
    arguments = [
        "--transit-time",
        "1",
        "--topology",
        "my topology.json",
        "--dataset",
        "jms-kapps",
        "",
        "*",
        "$TRIPLESTORE_URL",
    ]

    result = machine.run(*arguments)

    assert result.returncode == 0, result.stderr
    [run] = machine.calls("uv")
    assert run["argv"][0] == "run"
    command = run["argv"].index("jms-uc2")
    assert run["argv"][command + 1 :] == arguments
    # A relative path, a topology's among them, still means the one where the caller stands.
    assert run["cwd"] == str(machine.cwd)


def test_the_exit_code_is_jms_uc2s(machine: Machine) -> None:
    # A Launcher that never answers, so the script still waits for its page when `jms-uc2` exits.
    machine.stand_in("curl", then="sys.exit(7)")
    machine.stand_in("uv", then="sys.exit(3)")

    assert machine.run().returncode == 3


# `jms-uc2` stops on SIGTERM, as the real one does, and says so.
STOPS_ON_SIGTERM = """
import signal

def stop(signum, frame):
    (record / "stopped").write_text(str(signum))
    sys.exit(0)

signal.signal(signal.SIGTERM, stop)
(record / "running").touch()
time.sleep(20)
"""


def test_sigterm_to_the_script_reaches_jms_uc2(machine: Machine) -> None:
    machine.stand_in("uv", then=STOPS_ON_SIGTERM)
    script = machine.start()
    try:
        eventually(lambda: (machine.record / "running").exists())

        script.send_signal(signal.SIGTERM)

        assert script.wait(timeout=10) == 0
        assert (machine.record / "stopped").read_text() == str(int(signal.SIGTERM))
    finally:
        script.kill()
        script.wait()


# Docker whose Compose plugin does not answer, as `docker` answers a subcommand it does not have.
WITHOUT_PLUGIN = "sys.exit(1 if sys.argv[1:2] == ['compose'] else 0)"


@pytest.mark.parametrize("docker", ["absent", "without its Compose plugin"])
def test_without_compose_it_stops_with_one_message(
    machine: Machine, docker: str
) -> None:
    if docker == "absent":
        (machine.bin / "docker").unlink()
    else:
        machine.stand_in("docker", then=WITHOUT_PLUGIN)

    result = machine.run()

    assert result.returncode != 0
    [message] = result.stderr.splitlines()
    assert "`docker compose`" in message
    assert "`docker-compose`" in message
    assert machine.calls("uv") == []


@pytest.mark.parametrize("compose", ["docker compose", "docker-compose"])
def test_the_local_stack_comes_up_from_the_repository_root(
    machine: Machine, compose: str
) -> None:
    if compose == "docker-compose":
        machine.stand_in("docker", then=WITHOUT_PLUGIN)
        machine.stand_in("docker-compose")
    name, *subcommand = compose.split()

    result = machine.run()

    assert result.returncode == 0, result.stderr
    [up] = [call for call in machine.calls(name) if "up" in call["argv"]]
    assert up["argv"] == [*subcommand, "up", "-d", "--wait"]
    assert up["cwd"] == str(ROOT)


def test_a_stack_that_does_not_come_up_stops_the_run_before_it_starts(
    machine: Machine,
) -> None:
    machine.stand_in("docker", then="sys.exit(1 if 'up' in sys.argv else 0)")

    result = machine.run()

    assert result.returncode != 0
    assert machine.calls("uv") == []


LAUNCHER_PAGE = f"http://{LAUNCHER_HOST}:{LAUNCHER_PORT}/"

# On macOS the page opens with `open`, which needs no display.
linux_opener = pytest.mark.skipif(
    sys.platform == "darwin", reason="the page opens with `open` on macOS"
)

# The Launcher of this run answers once `jms-uc2` has started, and never before.
ANSWERS_ONCE_THE_RUN_STARTED = """
if (record / "uv.jsonl").exists():
    (record / "answered").touch()
    sys.exit(0)
sys.exit(7)
"""

# Another run's Launcher holds the port already.
ANSWERS_ALREADY = """
(record / "answered").touch()
sys.exit(0)
"""

# `jms-uc2` runs until its Launcher answered.
RUNS_UNTIL_ITS_LAUNCHER_ANSWERED = """
deadline = time.monotonic() + 10
while not (record / "answered").exists() and time.monotonic() < deadline:
    time.sleep(0.05)
"""

# `jms-uc2` prints its Launcher page's address, as the real one does once its launch is done: the
# first row of its block of page addresses.
PRINTS_THE_ADDRESS = f"""
print("  Launcher page     {LAUNCHER_PAGE}", flush=True)
"""


@linux_opener
def test_the_launcher_page_opens_once_it_answers(machine: Machine) -> None:
    machine.stand_in("curl", then=ANSWERS_ONCE_THE_RUN_STARTED)
    machine.stand_in("xdg-open")
    machine.stand_in("uv", then=PRINTS_THE_ADDRESS + RUNS_UNTIL_ITS_LAUNCHER_ANSWERED)

    result = machine.run(env={"DISPLAY": ":0"})

    assert result.returncode == 0, result.stderr
    [opened] = machine.calls("xdg-open")
    assert opened["argv"] == [LAUNCHER_PAGE]
    # `jms-uc2` printed it; the script only says that a browser opens it.
    assert result.stdout.count(LAUNCHER_PAGE) == 1, result.stdout


@linux_opener
@pytest.mark.parametrize("missing", ["a display", "xdg-open"])
def test_without_a_way_to_open_the_page_its_address_is_printed_once(
    machine: Machine, missing: str
) -> None:
    machine.stand_in("curl", then=ANSWERS_ONCE_THE_RUN_STARTED)
    machine.stand_in("uv", then=PRINTS_THE_ADDRESS + RUNS_UNTIL_ITS_LAUNCHER_ANSWERED)
    if missing == "xdg-open":
        env = {"DISPLAY": ":0"}
    else:
        # On a machine with no display, xdg-open falls back to a browser in the terminal.
        machine.stand_in("xdg-open")
        env = {}

    result = machine.run(env=env)

    assert result.returncode == 0, result.stderr
    assert machine.calls("xdg-open") == []
    assert result.stdout.count(LAUNCHER_PAGE) == 1, result.stdout


@linux_opener
def test_another_runs_launcher_is_not_opened(machine: Machine) -> None:
    machine.stand_in("curl", then=ANSWERS_ALREADY)
    machine.stand_in("xdg-open")
    machine.stand_in("uv", then=RUNS_UNTIL_ITS_LAUNCHER_ANSWERED)

    result = machine.run(env={"DISPLAY": ":0"})

    assert result.returncode == 0, result.stderr
    assert machine.calls("xdg-open") == []


def test_without_curl_it_opens_nothing_and_the_address_is_printed_once(
    machine: Machine,
) -> None:
    machine.stand_in("xdg-open")
    machine.stand_in("uv", then=PRINTS_THE_ADDRESS)

    result = machine.run(env={"DISPLAY": ":0"})

    assert result.returncode == 0, result.stderr
    assert len(machine.calls("uv")) == 1
    assert machine.calls("xdg-open") == []
    assert result.stdout.count(LAUNCHER_PAGE) == 1, result.stdout
