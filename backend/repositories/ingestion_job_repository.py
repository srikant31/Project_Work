from datetime import datetime

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
        job.completed_at = datetime.utcnow()
        self.session.flush()

    @staticmethod
    def record_failure(job_id, document_id, error_text: str) -> None:
        """Update the EXISTING job row to 'failed', on a separate session
        (the main transaction has just been rolled back, so writing on that
        session would vanish with it).

        job_id must be the id from start_job()'s returned row. Without it,
        this used to INSERT a new row instead of updating the original —
        leaving the original stuck at status='processing' forever, and two
        job rows on disk for what was actually one failed ingestion.
        """
        session = SessionLocal()
        try:
            job = session.get(IngestionJob, job_id)
            if job is not None:
                job.status = "failed"
                job.completed_at = datetime.utcnow()
                job.error_log = error_text[:10000]
                job.retry_count = (job.retry_count or 0) + 1
            else:
                # Shouldn't happen — start_job() always creates one first —
                # but don't lose the failure record if it somehow does.
                session.add(
                    IngestionJob(
                        document_id=document_id,
                        status="failed",
                        completed_at=datetime.utcnow(),
                        error_log=error_text[:10000],
                    )
                )
            session.commit()
        except Exception:
            session.rollback()
            # Swallow: failure logging must never mask the original exception.
        finally:
            session.close()