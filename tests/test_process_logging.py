"""A process's own logging setup takes effect, whatever a dependency configured at import.

The middleware's transport, `transitional_sync_middleware`, calls `basicConfig` at INFO when it is
imported, so the root logger has a handler before the process start runs. The setup of the shared
process start must take over from it: this package's records from INFO up, every other logger's
from WARNING up. An import-time effect cannot be seen in the test process, where the packages are
imported already, so each test runs a fresh interpreter. None of them needs a triple store.
"""

from __future__ import annotations

import subprocess
import sys

# A fresh interpreter that imports the middleware (and with it the transport), then runs the setup
# of the shared process start.
SETUP = """
import logging

import kapps_semantic_middleware  # noqa: F401 - its import configures the root logger

from jms_kapps.common.process import _log_to_stderr

_log_to_stderr()
"""


# Python and the packages may write their own warnings to stderr; the tests read only the lines of
# their own records, which all start with one of these level prefixes.
LEVEL_PREFIXES = ("INFO:", "WARNING:", "DEBUG:", "ERROR:")


def stderr_lines(body: str) -> list[str]:
    """The lines a fresh interpreter writes to stderr when it runs the setup and then `body`."""
    run = subprocess.run(
        [sys.executable, "-c", SETUP + body],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in run.stderr.splitlines() if line.startswith(LEVEL_PREFIXES)]


def test_a_library_s_info_record_writes_no_line():
    lines = stderr_lines(
        'logging.getLogger("httpx").info("HTTP Request: GET http://x")'
    )

    assert lines == []


def test_this_package_s_info_record_writes_one_line_in_pythons_default_format():
    lines = stderr_lines(
        'logging.getLogger("jms_kapps.uc2.module").info("Module1: accepted")'
    )

    assert lines == ["INFO:jms_kapps.uc2.module:Module1: accepted"]


def test_a_library_s_warning_record_still_writes_one_line():
    lines = stderr_lines('logging.getLogger("httpx").warning("slow")')

    assert lines == ["WARNING:httpx:slow"]


def test_the_dashboard_logs_tab_handler_is_on_the_root_logger_after_the_setup_and_the_build():
    # The build is the dashboard's: a stand-in with an app, as the activity-feed test's child has.
    body = """
from types import SimpleNamespace

from fastapi import FastAPI
from kapps_semantic_middleware.dashboard import enable_dashboard

enable_dashboard(SimpleNamespace(app=FastAPI()))
names = [type(h).__name__ for h in logging.getLogger().handlers]
logging.getLogger().warning("handlers " + ",".join(names))
"""
    lines = stderr_lines(body)

    assert len(lines) == 1
    handlers = lines[0].removeprefix("WARNING:root:handlers ").split(",")
    assert "_Handler" in handlers
    assert handlers.count("StreamHandler") == 1


# The activity feed sets the middleware's package logger to INFO; a stand-in for that here.
MIDDLEWARE_AT_INFO = (
    'logging.getLogger("kapps_semantic_middleware").setLevel(logging.INFO)\n'
)


def test_the_middleware_s_info_record_writes_no_line_where_its_logger_is_at_info():
    lines = stderr_lines(
        MIDDLEWARE_AT_INFO
        + 'logging.getLogger("kapps_semantic_middleware.activity").info("heartbeat")'
    )

    assert lines == []


def test_the_middleware_s_warning_record_still_writes_one_line_where_its_logger_is_at_info():
    lines = stderr_lines(
        MIDDLEWARE_AT_INFO
        + 'logging.getLogger("kapps_semantic_middleware").warning("slow")'
    )

    assert lines == ["WARNING:kapps_semantic_middleware:slow"]


def test_this_package_s_info_record_writes_one_line_where_the_middleware_s_logger_is_at_info():
    lines = stderr_lines(
        MIDDLEWARE_AT_INFO + 'logging.getLogger("jms_kapps.uc2.wms").info("step")'
    )

    assert lines == ["INFO:jms_kapps.uc2.wms:step"]


def test_a_handler_on_the_root_logger_after_the_setup_still_gets_the_middleware_s_info_record():
    # As the middleware's Logs-tab handler is attached at build time.
    body = (
        MIDDLEWARE_AT_INFO
        + """
seen = []


class Collect(logging.Handler):
    def emit(self, record):
        seen.append(record.getMessage())


logging.getLogger().addHandler(Collect())
logging.getLogger("kapps_semantic_middleware").info("heartbeat")
logging.getLogger().warning("seen " + ",".join(seen))
"""
    )
    lines = stderr_lines(body)

    assert lines == ["WARNING:root:seen heartbeat"]
