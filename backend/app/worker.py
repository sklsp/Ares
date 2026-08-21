"""Standalone research worker.

Runs against the same database as the API, so a job submitted by any
process is executed here. Multiple workers can run concurrently; claiming
is atomic (UPDATE ... WHERE status=QUEUED), so a job runs exactly once.

Local development does not need this process: the API runs an embedded
worker by default (`EMBEDDED_WORKER=true`, the default). Production runs
`python -m app.worker` in one or more replicas with `EMBEDDED_WORKER=false`
on the API.
"""

from __future__ import annotations

import os
import signal
import socket
import sys
import time
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.config import settings
from app.db.base import SessionLocal
from app.db.models import ResearchJob, utcnow
from app.intelligence.crawler import CrawlPolicy, ResponsibleCrawler
from app.jobs import JobStatus
from app.jobs.queue import claim_next, due_jobs, record_failure, transition
from app.logging_config import configure_logging, get_logger
from app.services.intelligence import run_investigation

logger = get_logger(__name__)

POLL_SECONDS = 2.0
HEARTBEAT_SECONDS = 15.0
# A RUNNING job whose heartbeat is older than this was orphaned by a crash.
STALE_THRESHOLD = timedelta(minutes=10)


class Worker:
    def __init__(self) -> None:
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self._stop = False
        self.processed = 0
        self.failed = 0
        self.started_at = time.monotonic()

    def request_stop(self, *_: object) -> None:
        logger.info("Shutdown requested; finishing current job")
        self._stop = True

    # -- lifecycle -------------------------------------------------------
    def run(self) -> int:
        configure_logging(settings.log_level)
        logger.info("Worker %s starting", self.worker_id)
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)
        last_heartbeat = 0.0
        while not self._stop:
            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_SECONDS:
                self._reclaim_stale()
                last_heartbeat = now
            worked = self._work_once()
            if not worked:
                time.sleep(POLL_SECONDS)
        logger.info(
            "Worker %s stopped: processed=%d failed=%d uptime=%.0fs",
            self.worker_id, self.processed, self.failed,
            time.monotonic() - self.started_at,
        )
        return 0

    # -- core loop -------------------------------------------------------
    def _work_once(self) -> bool:
        db = SessionLocal()
        try:
            job = due_jobs(db, limit=1)
            if not job:
                return False
            claimed = claim_next(db, self.worker_id)
            if claimed is None or claimed.id != job[0].id:
                return bool(claimed)
            started = time.monotonic()
            try:
                crawler = ResponsibleCrawler(CrawlPolicy(
                    max_pages=20, max_depth=1, delay_seconds=0.5,
                    allow_private_addresses=settings.crawler_allow_private_addresses,
                ))
                try:
                    run_investigation(db, claimed, crawler=crawler)
                finally:
                    crawler.close()
                transition(db, claimed, JobStatus.COMPLETED)
                self.processed += 1
                logger.info("Job %s completed in %.1fs", claimed.id, time.monotonic() - started)
            except Exception as exc:  # noqa: BLE001 - failures must not kill the worker
                logger.exception("Job %s failed", claimed.id)
                db.rollback()
                fresh = db.get(ResearchJob, claimed.id)
                if fresh is not None:
                    record_failure(db, fresh, str(exc))
                self.failed += 1
            return True
        finally:
            db.close()

    def _reclaim_stale(self) -> None:
        """Requeue RUNNING jobs whose worker died mid-flight."""
        db = SessionLocal()
        try:
            cutoff = utcnow() - STALE_THRESHOLD
            stale = db.execute(
                select(ResearchJob).where(
                    ResearchJob.status == JobStatus.RUNNING.value,
                    ResearchJob.started_at < cutoff,
                )
            ).scalars().all()
            for job in stale:
                logger.warning("Reclaiming stale job %s from worker %s",
                               job.id, job.worker_id)
                job.status = JobStatus.QUEUED.value
                job.stage = "reclaimed after worker loss"
                job.worker_id = None
            db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Stale reclaim failed")
            db.rollback()
        finally:
            db.close()


def main() -> int:
    return Worker().run()


if __name__ == "__main__":
    sys.exit(main())
