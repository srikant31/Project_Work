"""Bounded, immutable local upload storage. Never trust a client-supplied path."""

import ntpath
import uuid
from pathlib import Path


class UploadTooLargeError(ValueError):
    pass


class FileStorage:
    allowed_extensions = {".pdf", ".docx", ".txt", ".md"}

    def __init__(self, root, max_bytes=20 * 1024 * 1024):
        self.root = Path(root).resolve()
        self.max_bytes = max_bytes

    def save(self, stream, filename):
        name = ntpath.basename(filename or "")
        extension = Path(name).suffix.lower()
        if extension not in self.allowed_extensions:
            raise ValueError("Supported file types are PDF, DOCX, TXT and Markdown")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / (uuid.uuid4().hex + extension)
        size = 0
        try:
            with path.open("xb") as target:
                while piece := stream.read(64 * 1024):
                    size += len(piece)
                    if size > self.max_bytes:
                        raise UploadTooLargeError(f"File exceeds the {self.max_bytes // 1024} KB upload limit")
                    target.write(piece)
            if size == 0:
                raise ValueError("The uploaded file is empty")
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path, name

    def resolve_source(self, stored_path):
        path = Path(stored_path).resolve()
        # Older approved sample files remain usable for explicit reindexing.
        samples = Path(__file__).resolve().parents[2] / "data" / "documents"
        if not (path.is_relative_to(self.root) or path.is_relative_to(samples.resolve())):
            raise ValueError("The stored source is outside the approved document directories; upload a replacement")
        if not path.is_file():
            raise FileNotFoundError("The source file is unavailable; upload a replacement")
        return path

    def remove_unreferenced_upload(self, path):
        path = Path(path).resolve()
        if path.parent != self.root:
            raise ValueError("Cleanup is restricted to newly saved managed uploads")
        path.unlink(missing_ok=True)
