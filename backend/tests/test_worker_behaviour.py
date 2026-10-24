"""Behavioural tests for ``app.worker`` (standalone Redis queue worker).

The backend and handler registry are monkeypatched with recording fakes so
every branch of the failure ladder can be driven deterministically: claim,
complete, retry with exponential backoff, dead-letter after MAX_ATTEMPTS.
time.monotonic/time.sleep are faked where the loop waits; no network is
involved and every assertion uses literal expected values.
"""

from __future__ import annotations

import os
import socket

import app.worker as worker_module
from app.worker import Worker, main


class RecordingLogger:
    """Stands in for ``app.worker.logger`` and records formatted messages."""

    def __init__(self) -> None:
        self.info_msgs = []
        self.warning_msgs = []
        self.error_msgs = []

    @staticmethod
    def _fmt(msg, args):
        return msg % args if args else msg

    def info(self, msg, *args):
        self.info_msgs.append(self._fmt(msg, args))

    def warning(self, msg, *args):
        self.warning_msgs.append(self._fmt(msg, args))

    def error(self, msg, *args):
        self.error_msgs.append(self._fmt(msg, args))

    def exception(self, msg, *args):
        self.error_msgs.append(self._fmt(msg, args))


class FakeBackend:
    """Replays a scripted claim sequence and records every backend call."""

    def __init__(self, claims) -> None:
        self.claims = list(claims)
        self.reclaim_result = 0
        self.claim_calls = []
        self.completions = []
        self.requeues = []      # (entry, delay_seconds)
        self.dead_letters = []  # (entry, error)
        self.reclaim_calls = []

    def claim(self, worker_id, timeout_seconds=1.0):
        self.claim_calls.append((worker_id, timeout_seconds))
        if self.claims:
            return self.claims.pop(0)
        return None

    def complete(self, entry):
        self.completions.append(entry)

    def requeue(self, entry, *, delay_seconds=0):
        self.requeues.append((entry, delay_seconds))

    def dead_letter(self, entry, error):
        self.dead_letters.append((entry, error))

    def reclaim_stale(self, older_than_seconds):
        self.reclaim_calls.append(older_than_seconds)
        return self.reclaim_result


def _install(monkeypatch, backend, handlers):
    """Point the worker module at fakes; returns (logger, sleeps)."""
    logger = RecordingLogger()
    sleeps = []
    monkeypatch.setattr(worker_module, "get_backend", lambda: backend)
    monkeypatch.setattr(worker_module, "get_handler", lambda job_type: handlers.get(job_type))
    monkeypatch.setattr(worker_module, "logger", logger)
    monkeypatch.setattr(worker_module.time, "sleep", lambda s: sleeps.append(s))
    # Constant clock: first loop iteration sees now - 0 >= 30 (reclaim fires),
    # later iterations see no elapsed time. Deterministic durations of 0.0s.
    monkeypatch.setattr(worker_module.time, "monotonic", lambda: 100.0)
    return logger, sleeps


# --- Worker identity / shutdown ---------------------------------------------


def test_worker_id_embeds_host_pid_and_random_suffix():
    first = Worker()
    second = Worker()
    assert first.worker_id != second.worker_id
    host, pid, suffix = first.worker_id.split(":")
    assert host == socket.gethostname()
    assert pid == str(os.getpid())
    assert len(suffix) == 6


def test_request_stop_sets_flag_and_ignores_signal_arguments():
    worker = Worker()
    assert worker._stop is False
    worker.request_stop(15, None)
    assert worker._stop is True


# --- run() loop ---------------------------------------------------------------


def test_run_processes_a_job_after_an_idle_poll_and_returns_zero(monkeypatch):
    entry = {"id": "job-1", "type": "crawl", "payload": {"url": "https://example.com"}}
    backend = FakeBackend([None, dict(entry)])
    handled = []

    def crawl(payload):
        handled.append(payload)

    logger, sleeps = _install(monkeypatch, backend, handlers={"crawl": crawl})
    worker = Worker()
    original_claim = backend.claim

    def claim_then_stop(worker_id, timeout_seconds=1.0):
        claimed = original_claim(worker_id, timeout_seconds)
        if claimed is not None:
            worker.request_stop()
        return claimed

    monkeypatch.setattr(backend, "claim", claim_then_stop)

    assert worker.run() == 0
    assert handled == [{"url": "https://example.com"}]
    completed = backend.completions[0]
    assert completed["id"] == "job-1"
    assert completed["worker_id"] == worker.worker_id
    assert (worker.processed, worker.failed) == (1, 0)
    assert sleeps == [0.2]
    assert "Job job-1 completed in 0.0s" in logger.info_msgs


def test_run_logs_when_reclaim_returns_abandoned_jobs(monkeypatch):
    backend = FakeBackend([])
    backend.reclaim_result = 2
    logger, sleeps = _install(monkeypatch, backend, handlers={})
    worker = Worker()
    original_claim = backend.claim

    def claim_then_stop(worker_id, timeout_seconds=1.0):
        claimed = original_claim(worker_id, timeout_seconds)
        if len(backend.claim_calls) >= 2:
            worker.request_stop()
        return claimed

    monkeypatch.setattr(backend, "claim", claim_then_stop)

    assert worker.run() == 0
    assert backend.reclaim_calls == [300.0]
    assert "Reclaimed 2 abandoned job(s)" in logger.warning_msgs
    assert sleeps == [0.2, 0.2]


# --- _work_once: success path -------------------------------------------------


def test_work_once_returns_false_when_the_queue_is_empty(monkeypatch):
    backend = FakeBackend([])
    _install(monkeypatch, backend, handlers={})
    worker = Worker()

    assert worker._work_once(backend) is False
    assert backend.claim_calls == [(worker.worker_id, 1.0)]
    assert (worker.processed, worker.failed) == (0, 0)


def test_work_once_completes_a_job_and_stamps_the_worker_id(monkeypatch):
    entry = {"id": "job-9", "type": "crawl", "payload": {"url": "https://example.com"}}
    backend = FakeBackend([dict(entry)])
    handled = []

    def crawl(payload):
        handled.append(payload)

    _install(monkeypatch, backend, handlers={"crawl": crawl})
    worker = Worker()

    assert worker._work_once(backend) is True
    assert handled == [{"url": "https://example.com"}]
    completed = backend.completions[0]
    assert completed["id"] == "job-9"
    assert completed["worker_id"] == worker.worker_id
    assert (worker.processed, worker.failed) == (1, 0)


# --- _work_once: failure ladder -----------------------------------------------


def test_work_once_unknown_job_type_retries_then_dead_letters(monkeypatch):
    entry = {"id": "job-x", "type": "mystery", "payload": {}}
    backend = FakeBackend([entry] * 3)
    _install(monkeypatch, backend, handlers={})
    worker = Worker()

    assert worker._work_once(backend) is True
    assert [delay for _, delay in backend.requeues] == [5.0]
    assert entry["retry_count"] == 1

    assert worker._work_once(backend) is True
    assert [delay for _, delay in backend.requeues] == [5.0, 15.0]
    assert entry["retry_count"] == 2

    assert worker._work_once(backend) is True
    assert len(backend.dead_letters) == 1
    dead_entry, error = backend.dead_letters[0]
    assert dead_entry is entry
    assert error == "No handler registered for job type 'mystery'"
    assert (worker.processed, worker.failed) == (0, 3)


def test_work_once_handler_failure_dead_letters_with_the_exception_text(monkeypatch):
    entry = {"id": "job-y", "type": "crawl", "payload": {}}
    backend = FakeBackend([entry] * 3)

    def boom(payload):
        raise ValueError("boom")

    _install(monkeypatch, backend, handlers={"crawl": boom})
    worker = Worker()
    for _ in range(3):
        assert worker._work_once(backend) is True

    assert [delay for _, delay in backend.requeues] == [5.0, 15.0]
    dead_entry, error = backend.dead_letters[0]
    assert dead_entry is entry
    assert error == "boom"


def test_work_once_dead_letters_immediately_when_retry_budget_is_exhausted(monkeypatch):
    entry = {"id": "job-z", "type": "crawl", "payload": {}, "retry_count": 2}
    backend = FakeBackend([entry])

    def boom(payload):
        raise RuntimeError("again")

    _install(monkeypatch, backend, handlers={"crawl": boom})
    worker = Worker()

    assert worker._work_once(backend) is True
    assert backend.requeues == []
    dead_entry, error = backend.dead_letters[0]
    assert dead_entry is entry
    assert error == "again"


# --- main -----------------------------------------------------------------------


def test_main_returns_the_exit_code_of_worker_run(monkeypatch):
    class StubWorker:
        def run(self):
            return 0

    monkeypatch.setattr(worker_module, "Worker", StubWorker)
    assert main() == 0

