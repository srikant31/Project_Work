"""RAG HTTP contracts with real isolated stores and explicit test providers.

No test configures a production fallback, downloads a generation model, or
contacts an LLM API. Live provider/model quality remains a separate milestone.
"""

import copy
import json
import uuid
from contextlib import ExitStack
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.integration
POLICY = "SMS verification is not accepted for remote access."
PASSWORD = "Rag-validation-password-2026!"


class TestProvider:
    __test__ = False
    model_name = "explicit-http-test-provider"
    provider_name = "test-only"

    def __init__(self, payload=None):
        self.payload = payload if payload is not None else {
            "claims": [{"source_id": "S1", "quote": POLICY}], "insufficient_evidence": False,
        }
        self.calls = []
        self.error = None

    def generate(self, messages, *, max_output_tokens, response_schema):
        self.calls.append(copy.deepcopy(messages))
        if 'query' in response_schema.get('properties', {}):
            return {'query': json.loads(messages[-1]['content'])['question'], 'needs_clarification': False, 'clarification': ''}
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.payload)


def login(api, name="owner", *, client=None):
    client = api.client if client is None else client
    client.cookies.clear()
    response = client.post("/api/auth/login", json={"email": api.emails[name], "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response


@pytest.fixture
def rag_api(db_factory, real_embedder, real_retriever, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.database.models import User
    from backend.main import create_app
    from backend.services.document_processing_service import DocumentProcessingService
    from backend.utils.security import hash_password

    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("FRONTEND_ORIGINS", "http://localhost:5173")
    # A developer's configured provider must never turn an unconfigured-mode
    # regression into an external API call. Injected providers still override
    # this explicit test-only disabled configuration.
    monkeypatch.setenv("LLM_PROVIDER", "disabled")
    monkeypatch.setenv("RAG_MAX_CONTEXT_BYTES", "12000")
    monkeypatch.setenv("RAG_MAX_OUTPUT_TOKENS", "512")
    users, emails = {}, {}
    password_hash = hash_password(PASSWORD)
    with db_factory() as session:
        for name, role in (("admin", "admin"), ("owner", "employee"), ("other", "employee")):
            emails[name] = f"rag-{name}@example.test"
            user = User(email=emails[name], role=role, password_hash=password_hash)
            session.add(user)
            session.flush()
            users[name] = str(user.user_id)
        session.commit()
    uploads = tmp_path / "managed_uploads"
    uploads.mkdir()
    source = uploads / "remote_access_policy.txt"
    source.write_text(POLICY, encoding="utf-8")
    with DocumentProcessingService(session_factory=db_factory, embedder=real_embedder, retriever=real_retriever) as processing:
        document = processing.process_file(source, category_name="IT Support", source_key="rag-http-policy.txt",
                                            uploaded_by=uuid.UUID(users["admin"]))
    provider = TestProvider()
    with ExitStack() as stack:
        def new_client(llm):
            app = create_app(session_factory=db_factory, embedder=real_embedder, retriever=real_retriever,
                             upload_root=uploads, llm=llm)
            client = stack.enter_context(TestClient(app))
            client.headers.update({"X-Requested-With": "helpdesk-ui"})
            return client

        api = SimpleNamespace(factory=db_factory, embedder=real_embedder, retriever=real_retriever,
                              users=users, emails=emails, source=source, document=document,
                              provider=provider, client=new_client(provider), new_client=new_client)
        login(api)
        yield api


def chat(api, *, conversation_id=None, client=None):
    payload = {"question": "Is SMS verification accepted for remote access?", "top_k": 3}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    return (api.client if client is None else client).post("/api/chat", json=payload)


def memory_provider(api, monkeypatch, decision):
    calls = []
    def generate(messages, *, max_output_tokens, response_schema):
        rewrite = 'query' in response_schema.get('properties', {})
        calls.append(('rewrite' if rewrite else 'answer', json.loads(messages[-1]['content'])))
        return copy.deepcopy(decision if rewrite else api.provider.payload)
    monkeypatch.setattr(api.provider, 'generate', generate)
    return calls


def test_memory_uses_only_current_owned_chat_and_new_chat_has_none(rag_api, monkeypatch):
    from backend.database.models import Message
    first = answered(rag_api)
    secret = 'PRIVATE_OTHER_CHAT_NEVER_INCLUDE'
    other = rag_api.client.post('/api/chat', json={'question': secret, 'top_k':3}).json()
    calls = memory_provider(rag_api, monkeypatch, {'query':'Is SMS verification accepted for remote access?', 'needs_clarification':False, 'clarification':''})
    followup = 'Is that method accepted?'
    result = rag_api.client.post('/api/chat', json={'question':followup, 'top_k':3, 'conversation_id':first['conversation_id']})
    assert result.status_code == 200, result.text
    assert [kind for kind, _ in calls] == ['rewrite', 'answer']
    assert secret not in json.dumps(calls)
    assert len(calls[0][1]['current_chat_history']) == 2
    assert calls[1][1]['question'] == 'Is SMS verification accepted for remote access?'
    assert calls[1][1]['original_question'] == followup
    assert calls[1][1]['current_chat_history'] == calls[0][1]['current_chat_history']
    with rag_api.factory() as session:
        answer = session.get(Message, uuid.UUID(result.json()['response_id']))
        question = session.get(Message, answer.reply_to_message_id)
        assert question.content == followup
        meta = answer.generation_metadata
        assert all(str(session.get(Message, uuid.UUID(i)).conversation_id) == first['conversation_id'] for i in meta['history_message_ids'])
        assert str(other['conversation_id']) not in json.dumps(meta)
    calls.clear()
    assert chat(rag_api).status_code == 200
    assert [kind for kind,_ in calls] == ['answer']
    assert 'current_chat_history' not in calls[0][1]
    calls.clear(); login(rag_api, 'other')
    assert chat(rag_api, conversation_id=first['conversation_id']).status_code == 403
    assert calls == []


def test_ambiguous_reference_is_saved_clarification_without_retrieval(rag_api, monkeypatch):
    from backend.database.models import Message
    from backend.services.search_service import SearchService
    original_search = SearchService.search
    first = answered(rag_api)
    calls = memory_provider(rag_api, monkeypatch, {'query':'', 'needs_clarification':True, 'clarification':'Invented entitlement: 999 days?'})
    def forbidden(*args, **kwargs):
        raise AssertionError('Clarification must not search')
    monkeypatch.setattr(SearchService, 'search', forbidden)
    response = rag_api.client.post('/api/chat', json={'question':'How many does he get?', 'conversation_id':first['conversation_id']})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['status'] == 'clarification_required' and result['citations'] == []
    assert '999' not in result['answer']
    assert [kind for kind,_ in calls] == ['rewrite']
    with rag_api.factory() as session:
        row = session.get(Message, uuid.UUID(result['response_id']))
        assert row.insufficient_context is None
        assert row.generation_metadata['answer_status'] == 'clarification_required'
    history = rag_api.client.get('/api/conversations/'+first['conversation_id']).json()
    assert history['messages'][-1]['content'] == result['answer']
    monkeypatch.setattr(SearchService, 'search', original_search)
    calls = memory_provider(rag_api, monkeypatch, {'query':'Is SMS verification accepted for remote access?', 'needs_clarification':False, 'clarification':''})
    followup = rag_api.client.post('/api/chat', json={'question':'By that method I mean SMS verification. Is it accepted?', 'conversation_id':first['conversation_id']})
    assert followup.status_code == 200 and followup.json()['status'] == 'answered'
    assert any(item['content'] == result['answer'] for item in calls[0][1]['current_chat_history'])


def test_recent_history_is_bounded_and_oversized_latest_turn_requests_restatement(rag_api, monkeypatch):
    from backend.database.models import Message
    from datetime import datetime, timedelta
    first = answered(rag_api)
    with rag_api.factory() as session:
        for n in range(8):
            session.add(Message(conversation_id=uuid.UUID(first['conversation_id']), message_provider='user',
                content=f'RECENT-{n}', created_at=datetime(2030,1,1)+timedelta(seconds=n)))
        session.commit()
    calls = memory_provider(rag_api, monkeypatch, {'query':'SMS verification?', 'needs_clarification':False, 'clarification':''})
    assert chat(rag_api, conversation_id=first['conversation_id']).status_code == 200
    history = calls[0][1]['current_chat_history']
    assert [item['content'] for item in history] == [f'RECENT-{n}' for n in range(2,8)]
    assert len(json.dumps(history,ensure_ascii=False,separators=(',',':')).encode()) <= 6000
    with rag_api.factory() as session:
        session.add(Message(conversation_id=uuid.UUID(first['conversation_id']),message_provider='user',content='ç•Œ'*3000,created_at=datetime(2031,1,1)))
        session.commit()
    calls.clear()
    result = chat(rag_api, conversation_id=first['conversation_id'])
    assert result.status_code == 200 and result.json()['status'] == 'clarification_required'
    assert calls == []


@pytest.mark.parametrize('decision', [None, {'query':'invented'}, {'query':'','needs_clarification':False,'clarification':''}])
def test_invalid_rewrite_never_saves_a_turn(rag_api, monkeypatch, decision):
    from backend.database.models import Message
    first = answered(rag_api)
    calls = memory_provider(rag_api, monkeypatch, decision)
    with rag_api.factory() as session:
        before = session.query(Message).count()
    assert chat(rag_api, conversation_id=first['conversation_id']).status_code == 502
    assert [kind for kind,_ in calls] == ['rewrite']
    with rag_api.factory() as session:
        assert session.query(Message).count() == before


def answered(api):
    response = chat(api)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "answered"
    return response.json()


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_both_roles_receive_only_validated_cited_quotes(rag_api, role):
    from backend.database.models import Document

    login(rag_api, role)
    result = answered(rag_api)
    assert POLICY in result["answer"]
    assert result["model"] == rag_api.provider.model_name
    assert result["context_tokens"] is None and result["context_bytes"] > 0
    assert result["grounding"]["validated_quotes"] == 1
    assert result["response_id"] and result["conversation_id"] and result["prompt_version"]
    assert result["elapsed_ms"] >= 0
    with rag_api.factory() as session:
        for citation in result["citations"]:
            row = session.get(Document, uuid.UUID(citation["doc_id"]))
            assert row.status == "active" and row.is_online is True
            assert row.extracted_text[citation["start_char"]:citation["end_char"]] == citation["quote"]
            assert citation["quote"] == POLICY and citation["page_number"] is None
            assert citation["version"] == row.version and citation["source_id"] == "S1"


def test_unconfigured_provider_returns_honest_503_and_creates_no_fake_answer(rag_api):
    from backend.database.models import Conversation, Message

    client = rag_api.new_client(None)
    login(rag_api, client=client)
    config = client.get("/api/rag/config")
    assert config.status_code == 200 and config.json()["configured"] is False
    response = chat(rag_api, client=client)
    assert response.status_code == 503, response.text
    assert "answer" not in response.json()
    assert rag_api.provider.calls == []
    with rag_api.factory() as session:
        assert session.query(Conversation).count() == 0
        assert session.query(Message).count() == 0


def test_context_preview_works_without_configuring_or_calling_a_provider(rag_api):
    from backend.database.models import Conversation, Message

    client = rag_api.new_client(None)
    login(rag_api, client=client)
    response = client.post("/api/rag/preview", json={"question": "Is SMS verification accepted?", "top_k": 3})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["sources"] and preview["sources"][0]["source_id"] == "S1"
    assert preview["sources"][0]["text"] == POLICY
    assert preview["context_tokens"] is None and preview["context_bytes"] > 0
    assert preview["token_count_method"] == "utf8_bytes"
    assert rag_api.provider.calls == []
    with rag_api.factory() as session:
        assert session.query(Conversation).count() == session.query(Message).count() == 0


def test_chat_and_feedback_require_authentication(rag_api):
    rag_api.client.cookies.clear()
    assert chat(rag_api).status_code == 401
    response = rag_api.client.post("/api/feedback", json={"response_id": str(uuid.uuid4()), "rating": "up"})
    assert response.status_code == 401
    assert rag_api.provider.calls == []


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_offline_documents_are_never_generation_context_for_either_role(rag_api, role):
    from backend.database.models import Document

    with rag_api.factory() as session:
        session.get(Document, uuid.UUID(rag_api.document["doc_id"])).is_online = False
        session.commit()
    login(rag_api, role)
    response = chat(rag_api)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "insufficient_evidence" and result["citations"] == []
    assert POLICY not in result["answer"]
    assert rag_api.provider.calls == []


@pytest.mark.parametrize("payload", [
    {"claims": [{"source_id": "S999", "quote": POLICY}], "insufficient_evidence": False},
    {"claims": [{"source_id": "S1", "quote": "SMS verification is accepted for remote access."}], "insufficient_evidence": False},
], ids=["invented-source", "fabricated-quote"])
def test_model_hallucinations_return_refusal_instead_of_fabricated_citations(rag_api, payload):
    rag_api.provider.payload = payload
    response = chat(rag_api)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "insufficient_evidence" and result["citations"] == []
    assert result["grounding"]["rejected_claims"] >= 1
    assert "SMS verification is accepted" not in result["answer"]


def test_formally_malformed_provider_payload_returns_502_without_fake_answer(rag_api):
    rag_api.provider.payload = {"claims": "not a claim list", "insufficient_evidence": False}
    response = chat(rag_api)
    assert response.status_code == 502, response.text
    assert "answer" not in response.json()


def test_provider_failure_returns_service_error_without_fake_answer(rag_api):
    from backend.services.llm_service import LLMProviderError

    rag_api.provider.error = LLMProviderError("Explicit test provider unavailable")
    response = chat(rag_api)
    assert response.status_code == 502, response.text
    assert "answer" not in response.json()


def test_continuation_uses_owned_conversation_and_cannot_access_another_users_history(rag_api):
    from backend.database.models import Conversation, Message

    first = answered(rag_api)
    second = chat(rag_api, conversation_id=first["conversation_id"])
    assert second.status_code == 200, second.text
    assert second.json()["conversation_id"] == first["conversation_id"]
    with rag_api.factory() as session:
        assert session.query(Conversation).count() == 1 and session.query(Message).count() == 4
    calls = len(rag_api.provider.calls)
    login(rag_api, "other")
    assert chat(rag_api, conversation_id=first["conversation_id"]).status_code == 403
    assert len(rag_api.provider.calls) == calls
    with rag_api.factory() as session:
        assert session.query(Message).count() == 4


def test_saved_conversations_and_generation_metadata_are_visible_only_to_their_owner(rag_api):
    result = answered(rag_api)
    listing = rag_api.client.get("/api/conversations")
    assert listing.status_code == 200
    assert [row["conversation_id"] for row in listing.json()["conversations"]] == [result["conversation_id"]]
    endpoint = f"/api/conversations/{result['conversation_id']}"
    history = rag_api.client.get(endpoint)
    assert history.status_code == 200, history.text
    messages = history.json()["messages"]
    assert [row["role"] for row in messages] == ["user", "assistant"]
    assert messages[-1]["content"] == result["answer"]
    assert messages[-1]["generation"]["prompt_version"] == result["prompt_version"]
    assert messages[-1]["generation"]["citations"] == result["citations"]
    login(rag_api, "other")
    assert rag_api.client.get(endpoint).status_code == 403
    assert rag_api.client.get("/api/conversations").json()["conversations"] == []


def test_feedback_upserts_only_the_owners_assistant_response_and_has_no_comments(rag_api):
    from backend.database.models import Feedback, Message

    result = answered(rag_api)
    for rating in ("up", "down"):
        response = rag_api.client.post("/api/feedback", json={"response_id": result["response_id"], "rating": rating})
        assert response.status_code == 200, response.text
    with rag_api.factory() as session:
        rows = session.query(Feedback).all()
        assert len(rows) == 1 and rows[0].rating == "down" and rows[0].comment is None
        assert str(rows[0].message_id) == result["response_id"] and str(rows[0].user_id) == rag_api.users["owner"]
        user_message = session.query(Message).filter_by(message_provider="user").one()
        question_id = str(user_message.message_id)
    response = rag_api.client.post("/api/feedback", json={"response_id": question_id, "rating": "up"})
    assert response.status_code in {400, 404}


@pytest.mark.parametrize("other", ["other", "admin"])
def test_feedback_cannot_be_submitted_for_someone_elses_response(rag_api, other):
    result = answered(rag_api)
    login(rag_api, other)
    response = rag_api.client.post("/api/feedback", json={"response_id": result["response_id"], "rating": "up"})
    assert response.status_code == 403, response.text
    from backend.database.models import Feedback
    with rag_api.factory() as session:
        assert session.query(Feedback).count() == 0


@pytest.mark.parametrize("extra", [{"comment": "x" * 601}, {"comments": "Unknown field"}])
def test_feedback_comment_fields_are_rejected_by_strict_schema(rag_api, extra):
    result = answered(rag_api)
    response = rag_api.client.post("/api/feedback", json={"response_id": result["response_id"], "rating": "up", **extra})
    assert response.status_code == 422, response.text
    from backend.database.models import Feedback
    with rag_api.factory() as session:
        assert session.query(Feedback).count() == 0


def test_feedback_unknown_response_and_invalid_rating_are_not_written(rag_api):
    assert rag_api.client.post("/api/feedback", json={"response_id": str(uuid.uuid4()), "rating": "up"}).status_code == 404
    result = answered(rag_api)
    assert rag_api.client.post("/api/feedback", json={"response_id": result["response_id"], "rating": "excellent"}).status_code == 422
    from backend.database.models import Feedback
    with rag_api.factory() as session:
        assert session.query(Feedback).count() == 0


def test_runtime_feedback_does_not_change_reviewed_evaluation_questions(rag_api):
    from sqlalchemy import text

    sentinel = uuid.uuid4()
    with rag_api.factory() as session:
        session.execute(text("CREATE TABLE IF NOT EXISTS evaluationquestions (eval_question_id UUID PRIMARY KEY, question_text TEXT NOT NULL, expected_answer_text TEXT)"))
        session.execute(text("INSERT INTO evaluationquestions (eval_question_id,question_text,expected_answer_text) VALUES (:id,:question,:answer)"),
                        {"id": sentinel, "question": "Reviewed golden question", "answer": "Reviewed golden answer"})
        session.commit()
    result = answered(rag_api)
    response = rag_api.client.post("/api/feedback", json={"response_id": result["response_id"], "rating": "down"})
    assert response.status_code == 200
    with rag_api.factory() as session:
        rows = session.execute(text("SELECT eval_question_id,question_text,expected_answer_text FROM evaluationquestions")).all()
        assert rows == [(sentinel, "Reviewed golden question", "Reviewed golden answer")]


def test_history_config_and_unconfigured_chat_do_not_initialize_retrieval_resources(rag_api, monkeypatch):
    from backend.database.models import Message

    saved = answered(rag_api)
    client = rag_api.new_client(None)
    login(rag_api, client=client)
    resources = client.app.state.resources
    calls = []

    def forbidden_embedder():
        calls.append("embedder")
        raise RuntimeError("This request must not initialize an embedding model")

    def forbidden_retriever():
        calls.append("retriever")
        raise RuntimeError("This request must not initialize a vector store")

    monkeypatch.setattr(resources, "get_embedder", forbidden_embedder)
    monkeypatch.setattr(resources, "get_retriever", forbidden_retriever)
    config = client.get("/api/rag/config")
    assert config.status_code == 200 and config.json()["configured"] is False
    listing = client.get("/api/conversations")
    assert listing.status_code == 200 and listing.json()["conversations"]
    history = client.get(f"/api/conversations/{saved['conversation_id']}")
    assert history.status_code == 200 and history.json()["messages"]
    response = chat(rag_api, client=client)
    assert response.status_code == 503 and "not configured" in response.json()["detail"].lower()
    assert calls == []
    with rag_api.factory() as session:
        assert session.query(Message).count() == 2
