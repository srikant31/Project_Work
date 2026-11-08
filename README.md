# AI Helpdesk Knowledge Workspace

React/TypeScript and FastAPI RAG helpdesk with PostgreSQL, Chroma, document administration, source-backed answers, owned history, feedback and analytics.

## Current state and limitations

The local knowledge database and vector collections have been emptied. Add approved documents and index them before expecting evidence-backed answers. Source files are not automatically ingested at startup. Regression inputs in tests/fixtures/documents are separate from the live knowledge base.

Table handling is not reliable yet. Extraction can read table text, but token-window chunking can split rows. Atomic tables, row-aware splitting and repeated headers are not implemented. Prefer prose and inspect extracted evidence.

Department is account metadata only; departmental document restrictions and multi-company isolation are not implemented. Formal evaluation and release hardening remain pending. Similarity scores are not confidence probabilities.

## Setup and launch

Use PowerShell from this project root:

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements_utf8.txt
npm --prefix frontend ci
npm --prefix frontend run build
```

Provision PostgreSQL and an application role. For a new database use backend/database/main_schema.sql; existing databases require additive migrations rather than rerunning the schema. Configure fields from .env.example in ignored .env. The scripts.apply_ingestion_migrations tool creates backups as part of its normal operation.

The all-MiniLM-L6-v2 embedding model must be cached locally. Initial download is an explicit setup step through preview/ingestion CLI --allow-model-download. Optional OCR has separate requirements_ocr.txt and an isolated environment.

```powershell
.\venv\Scripts\python.exe -m scripts.create_user --email admin@example.com --role admin
.\venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8006
```

Open http://127.0.0.1:8006/ in a browser. API documentation: http://127.0.0.1:8006/docs. FastAPI serves frontend/dist and /api. For frontend development use backend port8000 and npm --prefix frontend run dev, then open http://127.0.0.1:5173/. Use the same host consistently for cookies.

## Add new knowledge

Admin uploads accept PDF, DOCX, TXT and Markdown up to20MiB. Processing extracts text, chunks, generates embeddings, verifies vectors and publishes the document. New sources need indexing after a reset. Default chunk/overlap:200/40model tokens. Offline documents are retained but excluded from fresh retrieval.

Knowledge Search supports vector, BM25 and hybrid modes. RETRIEVAL_MODE sets the server default. Experiments separately compare chunk/overlap/TopK using isolated cosine retrieval. Local retrieval works offline; external answer generation requires connectivity. Main prompt/schema: backend/prompts/rag_prompts.py; bounded follow-up rewriting: backend/services/query_rewrite_service.py.

## Checks

```powershell
.\venv\Scripts\python.exe -m pytest -q
.\venv\Scripts\python.exe -m pytest --run-integration -q
npm --prefix frontend test
npm --prefix frontend run build
```

Integration tests use disposable database schemas and require PostgreSQL and cached dependencies. Keep credentials, source uploads, private corpora, vector data and local reference material out of Git. Ignore rules do not remove older commits.
