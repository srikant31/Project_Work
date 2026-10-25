"""Read eligible corpus metadata; atomically save private local experiment reports.

This repository never updates the live corpus or its vector index. Full results
stay under ignored output/ and can only be read through the admin API.
"""

import json
import os
from pathlib import Path
import tempfile
import uuid

from backend.database.models import Document


class ExperimentNotFoundError(LookupError):
    pass


class ExperimentRepository:
    def __init__(self, session_factory, output_root=None):
        self.session_factory = session_factory
        self.output_root = Path(output_root or Path(__file__).resolve().parents[2] / "output/experiments").resolve()

    def eligible_documents(self, document_ids=None, *, include_source=False):
        with self.session_factory() as session:
            columns = [Document.doc_id, Document.doc_name, Document.source_key,
                       Document.version, Document.source_hash, Document.text_hash, Document.doc_type]
            if include_source:
                columns += [Document.doc_path, Document.extracted_text]
            query = session.query(*columns).filter(
                Document.status == "active", Document.is_online.is_(True),
                Document.source_key.is_not(None), Document.source_hash.is_not(None),
                Document.text_hash.is_not(None), Document.pipeline_fingerprint.is_not(None),
                Document.extracted_text.is_not(None),
            )
            if document_ids is not None:
                query = query.filter(Document.doc_id.in_(document_ids))
            rows = query.order_by(Document.doc_name, Document.doc_id).all()
            result = []
            for row in rows:
                item = dict(row._mapping)
                item["doc_id"] = str(item["doc_id"])
                result.append(item)
            return result

    def _path(self, run_id):
        try:
            identifier = uuid.UUID(str(run_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ExperimentNotFoundError("Experiment not found") from exc
        return self.output_root / (str(identifier) + ".json")

    def save(self, report):
        target = self._path(report["run_id"])
        self.output_root.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.output_root,
                prefix=".experiment-", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def get(self, run_id):
        path = self._path(run_id)
        if not path.is_file():
            raise ExperimentNotFoundError("Experiment not found")
        with path.open(encoding="utf-8") as handle:
            report = json.load(handle)
        if report.get("run_id") != str(uuid.UUID(str(run_id))):
            raise ValueError("Saved experiment metadata is inconsistent")
        return report
