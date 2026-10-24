"""The client host's load: the sampler reads the operating system's own figures
(`/proc`), keeps each sample with its time, and says "not available" on a host without them. The
tests point the sampler at a folder that stands in for `/proc`. No store, no network, no run.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jms_kapps.evaluation import client_load
from jms_kapps.evaluation.client_load import (
    NOT_AVAILABLE,
    SAMPLED,
    ClientLoadSampler,
    read_cpu_ticks,
    read_memory,
)


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def fake_proc(
    folder: Path, *, user: int, idle: int, iowait: int = 0, available_kib: int = 4_000
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "stat").write_text(
        f"cpu  {user} 0 0 {idle} {iowait} 0 0 0 0 0\ncpu0 1 2 3 4 5 6 7 8 9 10\n",
        encoding="utf-8",
    )
    (folder / "meminfo").write_text(
        f"MemTotal:       10000 kB\nMemFree:         100 kB\n"
        f"MemAvailable:   {available_kib} kB\n",
        encoding="utf-8",
    )
    return folder


def test_the_cpu_ticks_count_everything_but_idle_and_waiting(tmp_path):
    proc = fake_proc(tmp_path, user=30, idle=60, iowait=10)

    assert read_cpu_ticks(proc) == (30, 100)


def test_the_memory_in_use_is_the_total_less_what_is_available(tmp_path):
    proc = fake_proc(tmp_path, user=1, idle=1, available_kib=4_000)

    assert read_memory(proc) == (6_000 * 1024, 10_000 * 1024)


def test_a_sample_has_its_time_the_cpu_share_since_the_sample_before_and_the_memory(
    tmp_path,
):
    proc = fake_proc(tmp_path, user=0, idle=100)
    sampler = ClientLoadSampler(Clock(), interval=3600, proc=proc)
    sampler.start()
    fake_proc(tmp_path, user=25, idle=175)  # 100 ticks later, 25 of them busy

    sampler.stop()  # the last sample

    record = sampler.record()
    assert record["status"] == SAMPLED
    assert record["interval_seconds"] == 3600
    assert record["memory_total_bytes"] == 10_000 * 1024
    assert record["samples"] == [
        {
            "at": "2026-10-12T12:00:00+00:00",
            "cpu_percent": 25.0,
            "memory_used_bytes": 6_000 * 1024,
        }
    ]


def test_a_sample_taken_when_no_time_passed_has_no_cpu_share(tmp_path):
    proc = fake_proc(tmp_path, user=5, idle=5)
    sampler = ClientLoadSampler(Clock(), interval=3600, proc=proc)
    sampler.start()

    sampler.stop()

    [sample] = sampler.record()["samples"]
    assert sample["cpu_percent"] is None
    assert sample["memory_used_bytes"] == 6_000 * 1024


def test_the_sampler_samples_every_interval_until_it_stops(tmp_path):
    proc = fake_proc(tmp_path, user=0, idle=0)
    sampler = ClientLoadSampler(Clock(), interval=0.01, proc=proc)

    sampler.start()
    deadline = time.monotonic() + 10
    while len(sampler.record()["samples"]) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    sampler.stop()
    taken = len(sampler.record()["samples"])
    time.sleep(0.1)

    assert taken >= 3
    assert len(sampler.record()["samples"]) == taken


def test_a_host_without_the_figures_says_not_available(tmp_path):
    sampler = ClientLoadSampler(Clock(), interval=0.01, proc=tmp_path / "no-proc")

    sampler.start()
    sampler.stop()

    assert sampler.started
    assert sampler.record() == {
        "status": NOT_AVAILABLE,
        "interval_seconds": 0.01,
        "memory_total_bytes": None,
        "samples": [],
    }


def test_a_sampler_that_never_started_stops_without_a_word(tmp_path):
    sampler = ClientLoadSampler(Clock(), proc=tmp_path)

    sampler.stop()

    assert not sampler.started


def test_the_interval_is_the_run_setting_unless_one_is_given(monkeypatch):
    assert (
        ClientLoadSampler(Clock()).interval == client_load.CLIENT_LOAD_INTERVAL_SECONDS
    )
    monkeypatch.setattr(client_load, "CLIENT_LOAD_INTERVAL_SECONDS", 7.0)

    assert ClientLoadSampler(Clock()).interval == 7.0


@pytest.mark.skipif(
    not Path("/proc/stat").exists(), reason="a host without /proc has no figures"
)
def test_this_host_s_own_figures_can_be_read():
    busy, total = read_cpu_ticks(Path("/proc"))
    used, size = read_memory(Path("/proc"))

    assert 0 <= busy <= total
    assert 0 < used <= size
