# Running the helpdesk with Docker

This runs a separate installation at http://localhost:8080. It does not migrate
your existing PostgreSQL database, users, conversations, chunks or vector store.
The Docker database starts empty. Source corpus files are mounted read-only;
they are not automatically indexed. The local application on port 8006 can
continue running independently.

## Install the prerequisite

Install [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/),
select the WSL 2/Linux-containers backend, and start Docker Desktop. Complete
any requested Windows restart. In a fresh VS Code PowerShell terminal verify:

```powershell
docker version
docker compose version
```

Both the Docker client and server must respond. Docker is not installed by this
repository. No additional Python or Node installation is needed for the images.

## Prepare private configuration once

From the project root:

```powershell
cd C:\Users\HP\Desktop\Sopra_work
powershell -NoProfile -ExecutionPolicy Bypass -File .\docker\setup.ps1
```

The script creates ignored `docker/.env`, generates two independent database
passwords and never overwrites an existing configuration. The existing root
`.env` is untouched. If Docker configuration has already been prepared, skip
this step. Do not print or share the private configuration.

For Groq, supply the path to your existing private key file on the first run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\docker\setup.ps1 -GroqKeyFile "C:\path\to\your\groq-key.txt"
```

If `docker/.env` already exists, open it with `code docker/.env` and set
`DOCKER_GROQ_KEY_FILE` to that file path, using forward slashes and single quotes
if the path contains spaces. Set the path, not the API key. The Groq overlay
mounts that file at runtime as a Compose secret; it is not copied into the image.

## Build and start

With Groq enabled:

```powershell
docker compose --env-file docker/.env -f compose.yaml -f compose.groq.yaml up -d --build
docker compose --env-file docker/.env -f compose.yaml -f compose.groq.yaml ps
```

Without an answer provider, use the base configuration instead:

```powershell
docker compose --env-file docker/.env up -d --build
```

The base configuration supports login, administration and retrieval; answer
generation is explicitly disabled. It does not silently use another provider.
Use the same `-f` flags for subsequent commands when you enable Groq.

The first build downloads the pinned Python dependencies, CPU-only PyTorch,
frontend dependencies and the MiniLM embedding model. Allow time and disk space
for that build. The model is cached inside the image for offline runtime loading.
Optional Docling OCR and Ollama are not configured by this Docker setup.

## Create an administrator and add knowledge

The following examples use the Groq overlay. Omit both `-f` arguments for the
base installation.

```powershell
docker compose --env-file docker/.env -f compose.yaml -f compose.groq.yaml exec app python -m scripts.create_user --email admin@helpdesk --role admin
```

Enter and confirm a password of at least 10 characters at the hidden prompt.
Open http://localhost:8080 and sign in. Your existing laptop accounts do not
exist in this separate database. API documentation is at http://localhost:8080/docs.

Upload supported documents using the admin interface, or explicitly ingest the
mounted 35-document corpus with the selected development chunk settings:

```powershell
docker compose --env-file docker/.env -f compose.yaml -f compose.groq.yaml exec app python -m scripts.ingest_folder --folder data/documents/ABC-CORP-helpdesk-corpus --chunk-size 200 --overlap 0
```

`MANIFEST.csv` is inventory metadata and is rejected by the document loader;
the CLI consequently returns a nonzero status for that rejection. Verify 35
documents ingested/skipped and no other failures. This command changes only the
Docker stores. Changing the chunk settings later requires explicit re-indexing.

## Inspect and stop

```powershell
docker compose --env-file docker/.env -f compose.yaml -f compose.groq.yaml logs --tail 100 app db
Invoke-RestMethod http://localhost:8080/api/health
docker compose --env-file docker/.env -f compose.yaml -f compose.groq.yaml down
```

The application health endpoint reports application/generation configuration;
it does not verify the entire RAG pipeline. Test a question and inspect its
citations after ingestion. Database health controls initial service ordering.

Named volumes retain the database, managed uploads, Chroma vectors and app
output across normal `down`/`up` cycles. Avoid volume-removal options unless you
intend to erase that Docker installation. Database initialisation runs only on
an empty database volume; it is not a migration system for an existing database.
Changing database passwords in the env file does not rotate an existing role's
password; use an explicit database administration operation for that.

## Deployment scope and validation

The configuration binds only to localhost and uses HTTP development cookies.
For an internet-facing deployment, configure HTTPS, `AUTH_COOKIE_SECURE=true`,
the public origin, secret delivery, backup/restore and operational monitoring.
The application's existing department-isolation and table-handling limitations
still apply.

The initial authoring machine had no Docker CLI/engine. YAML/schema and setup
script checks can be performed there, but image builds, Linux dependencies,
database initialisation and container smoke tests require Docker and must be
verified after installation. No host database or existing `.env` is required
or modified by this setup.
