from datetime import datetime, timezone

from backend.database.connection import SessionLocal
from backend.database.models import IngestionJob


class IngestionJobRepository:
    def __init__(self, session):
        self.session = session

    def start_job(self, document_id) -> IngestionJob:
        job = IngestionJob(document_id=document_id, status="processing")
        self.session.add(job)
        self.session.flush()
        return job

    def mark_completed(self, job: IngestionJob) -> None:
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        job.error_log = None
        self.session.flush()

    def mark_failed(
        self, job: IngestionJob, error_text: str, recovery_pending: bool = False
    ) -> None:
        job.status = "failed"
        job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        job.error_log = error_text[:10000]
        job.retry_count = (job.retry_count or 0) + 1
        if recovery_pending:
            job.document.cleanup_pending = True
        self.session.flush()

    @staticmethod
    def record_failure(job_id, document_id, error_text: str, session_factory=None) -> bool:
        """Update the EXISTING job row to 'failed', on a separate session
        (the main transaction has just been rolled back, so writing on that
        session would vanish with it).

        job_id must be the id from start_job()'s returned row. Without it,
        this used to INSERT a new row instead of updating the original —
        leaving the original stuck at status='processing' forever, and two
        job rows on disk for what was actually one failed ingestion.
        """
        session = (session_factory or SessionLocal)()
        try:
            job = session.get(IngestionJob, job_id)
            if job is not None:
                # A lost commit acknowledgement may reach this helper after
                # successful publication. Never rewrite that success as failure.
                if job.status == "completed":
                    return False
                IngestionJobRepository(session).mark_failed(job, error_text)
            else:
                # Shouldn't happen — start_job() always creates one first —
                # but don't lose the failure record if it somehow does.
                session.add(
                    IngestionJob(
                        document_id=document_id,
                        status="failed",
                        completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                        error_log=error_text[:10000],
                    )
                )
            session.commit()
            return True
        except Exception:
            session.rollback()
            # Failure logging must never mask the original exception; callers
            # can report a recovery requirement from the False return value.
            return False
        finally:
            session.close()
