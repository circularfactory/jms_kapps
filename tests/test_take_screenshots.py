"""The screenshot script of the reviewer guide: what it takes, and where it looks.

`scripts/take_screenshots.py` takes the base URL of a running Launcher and a directory. These tests
load it from its file and check its arguments, the address of each page it opens, and when it
counts a run as live, without a run, a network or a browser.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from _repo import find_repo_root

ROOT = find_repo_root(Path(__file__))
SCRIPT = ROOT / "scripts" / "take_screenshots.py" if ROOT is not None else None

pytestmark = pytest.mark.skipif(
    SCRIPT is None or not SCRIPT.exists(),
    reason="an installed copy and the released tree have no scripts",
)


@pytest.fixture(scope="module")
def script() -> ModuleType:
    assert SCRIPT is not None
    spec = importlib.util.spec_from_file_location("take_screenshots", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def process(kind: str, state: str = "live", name: str = "Module1") -> dict:
    return {"kind": kind, "state": state, "name": name}


@pytest.mark.parametrize(
    ("given", "base"),
    [
        ("http://127.0.0.1:8090", "http://127.0.0.1:8090"),
        ("http://127.0.0.1:8090/", "http://127.0.0.1:8090"),
        ("https://example.org/forwarded/8090/", "https://example.org/forwarded/8090"),
    ],
)
def test_it_takes_the_launchers_base_url_and_a_directory(script, tmp_path, given, base):
    arguments = script.parse([given, str(tmp_path / "images")])

    assert arguments.launcher == base
    assert arguments.out == tmp_path / "images"


@pytest.mark.parametrize("given", ["127.0.0.1:8090", "ftp://127.0.0.1:8090", "http://"])
def test_a_base_url_that_is_no_http_address_is_refused(script, tmp_path, capsys, given):
    with pytest.raises(SystemExit) as refused:
        script.parse([given, str(tmp_path)])

    assert refused.value.code == 2
    assert "an http:// or https:// address" in capsys.readouterr().err


def test_both_arguments_are_needed(script, capsys):
    with pytest.raises(SystemExit) as refused:
        script.parse(["http://127.0.0.1:8090"])

    assert refused.value.code == 2
    assert "out" in capsys.readouterr().err


def test_a_directory_that_is_a_file_is_refused(script, tmp_path, capsys):
    taken = tmp_path / "images"
    taken.write_text("not a directory", encoding="utf-8")

    with pytest.raises(SystemExit) as refused:
        script.parse(["http://127.0.0.1:8090", str(taken)])

    assert refused.value.code == 2
    assert "is a file" in capsys.readouterr().err


def test_the_two_images_are_named_after_their_pages(script):
    assert (script.LAUNCHER_IMAGE, script.MONITOR_IMAGE) == (
        "uc2-launcher.png",
        "uc2-monitor.png",
    )


@pytest.mark.parametrize(
    ("launcher", "href", "page"),
    [
        ("http://127.0.0.1:8090", "p/Monitor1/", "http://127.0.0.1:8090/p/Monitor1/"),
        (
            "https://example.org/forwarded/8090",
            "p/Monitor1/",
            "https://example.org/forwarded/8090/p/Monitor1/",
        ),
    ],
)
def test_a_page_the_launcher_links_is_opened_under_the_launchers_address(
    script, launcher, href, page
):
    assert script.page_url(launcher, href) == page


def test_a_run_counts_as_live_once_every_process_is_live_and_the_monitor_is_linked(
    script,
):
    live = {
        "monitor": "p/Monitor1/",
        "processes": [process("module"), process("wms"), process("monitor")],
    }

    assert script.is_live(live)
    assert not script.is_live(live | {"monitor": None})
    assert not script.is_live(live | {"processes": []})
    assert not script.is_live(
        live | {"processes": [process("module", "starting"), process("monitor")]}
    )


def test_the_monitor_draws_one_square_for_each_module(script):
    state = {
        "processes": [
            process("module", name="Module1"),
            process("module", name="Module2"),
            process("wms", name="WarehouseManagementSystem1"),
            process("monitor", name="Monitor1"),
        ]
    }

    assert script.module_count(state) == 2
