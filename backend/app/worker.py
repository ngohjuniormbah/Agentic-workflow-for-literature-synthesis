"""Database-backed workflow worker. One queued run is claimed per worker loop."""
import logging
import signal
import time
import uuid

from sqlalchemy import select

from .db import SessionLocal
from .models import Artifact, Event, Project, Run
from .workflow import advance

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("synthesis.worker")
stopping = False


def stop(_signum, _frame):
    global stopping
    stopping = True


def recover_interrupted_runs():
    """Requeue in-flight work after worker restarts, resuming after last committed artifact."""
    with SessionLocal() as db:
        runs = db.scalars(select(Run).where(Run.status == "running")).all()
        for run in runs:
            latest = db.scalar(select(Artifact).where(Artifact.run_id == run.id).order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(1))
            resume_from = run.settings.get("resume_from_stage")
            if latest and latest.status == "approved":
                resume_from = latest.stage
            run.settings = {**run.settings, "resume_from_stage": resume_from}
            run.status = "queued"
            db.add(Event(id=str(uuid.uuid4()), project_id=run.project_id, run_id=run.id, type="worker_recovered", message="Interrupted run requeued after worker restart", details={"resume_from_stage": resume_from}))
        db.commit()
        if runs:
            log.warning("Requeued %d interrupted runs", len(runs))


def claim_next_run():
    """Atomically claim one run; SKIP LOCKED allows multiple PostgreSQL workers."""
    with SessionLocal() as db:
        query = select(Run).where(Run.status == "queued").order_by(Run.created_at).limit(1).with_for_update(skip_locked=True)
        run = db.scalar(query)
        if run is None:
            return None
        run.status = "running"
        project_id = run.project_id
        run_id = run.id
        resume_from = run.settings.get("resume_from_stage")
        db.commit()
        return run_id, project_id, resume_from


def process_run(run_id: str, project_id: str, resume_from: str | None):
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        project = db.get(Project, project_id)
        if not run or not project or run.status != "running":
            return
        try:
            advance(db, project, run, from_stage=resume_from)
        except Exception:
            log.exception("Workflow run %s failed", run_id)


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    recover_interrupted_runs()
    log.info("Workflow worker started")
    while not stopping:
        claimed = claim_next_run()
        if claimed is None:
            time.sleep(1)
            continue
        process_run(*claimed)
    log.info("Workflow worker stopped")


if __name__ == "__main__":
    main()
