"""Oversized bodies are rejected before or during streamed request parsing."""

import pytest
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.testclient import TestClient

from backend.utils.request_limits import RequestBodyLimitMiddleware


@pytest.fixture(params=[False, True], ids=["direct", "with_http_middleware"])
def client(request):
    app = FastAPI()

    if request.param:
        @app.middleware("http")
        async def browser_layer(request: Request, call_next):
            return await call_next(request)

    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=512)

    @app.post("/body")
    async def body(request: Request):
        return {"size": len(await request.body())}

    @app.post("/file")
    async def file(file: UploadFile = File(...)):
        return {"size": len(await file.read())}

    with TestClient(app) as result:
        yield result


def test_request_limit_rejects_content_length_before_reading(client):
    response = client.post("/body", content=b"ok", headers={"Content-Length": "10000"})
    assert response.status_code == 413


def test_request_limit_counts_stream_without_content_length(client):
    response = client.post("/body", content=iter([b"x" * 300, b"y" * 300]))
    assert response.status_code == 413


def test_streamed_multipart_limit_returns_413(client):
    boundary = b"corpus-boundary"
    header = b"--" + boundary + b'\r\nContent-Disposition: form-data; name="file"; filename="policy.txt"\r\nContent-Type: text/plain\r\n\r\n'
    response = client.post("/file", content=iter([header, b"a" * 400, b"b" * 400, b"\r\n--" + boundary + b"--\r\n"]),
                           headers={"Content-Type": "multipart/form-data; boundary=corpus-boundary"})
    assert response.status_code == 413


def test_small_request_is_accepted(client):
    response = client.post("/body", content=b"small")
    assert response.status_code == 200
    assert response.json() == {"size": 5}
