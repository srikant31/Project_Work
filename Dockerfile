FROM node:24-bookworm-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS application
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/models \
    HF_HUB_DISABLE_TELEMETRY=1 \
    ANONYMIZED_TELEMETRY=False
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements_utf8.txt ./
# Select the CPU build before installing the project's existing pinned stack.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.14.0 \
    && pip install --no-cache-dir -r requirements_utf8.txt
# Runtime embedding loads are offline; cache the same model during the build.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY --from=frontend-build /frontend/dist/ ./frontend/dist/
RUN groupadd --gid 10001 helpdesk \
    && useradd --uid 10001 --gid helpdesk --create-home helpdesk \
    && mkdir -p /app/backend/data/uploads /app/chroma_db /app/output \
    && chown -R helpdesk:helpdesk /app/backend/data /app/chroma_db /app/output /opt/models
USER helpdesk
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)" || exit 1
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
