import pytest

from tools import parallel


def _square(x):
    return x * x


@pytest.mark.parametrize(
    ("setting", "cpus", "expected"),
    [("3", 8, 3), ("", 8, 8), (None, 8, 8), ("0", 8, 1), (None, None, 1)],
)
def test_workers_follows_study_workers_then_the_cpu_count(monkeypatch, setting, cpus, expected):
    if setting is None:
        monkeypatch.delenv("STUDY_WORKERS", raising=False)
    else:
        monkeypatch.setenv("STUDY_WORKERS", setting)
    monkeypatch.setattr(parallel.os, "cpu_count", lambda: cpus)
    assert parallel.workers() == expected


@pytest.mark.parametrize("workers", ["1", "2"])
def test_map_cases_keeps_the_case_order(monkeypatch, workers):
    monkeypatch.setenv("STUDY_WORKERS", workers)
    assert parallel.map_cases(_square, range(5)) == [0, 1, 4, 9, 16]


def test_one_case_runs_in_this_process(monkeypatch):
    monkeypatch.setenv("STUDY_WORKERS", "4")
    seen = []
    assert parallel.map_cases(seen.append, ["only"]) == [None]
    assert seen == ["only"]


def test_no_cases_gives_no_results(monkeypatch):
    monkeypatch.setenv("STUDY_WORKERS", "4")
    assert parallel.map_cases(_square, []) == []
