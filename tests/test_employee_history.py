"""Owned history works with PostgreSQL alone and never initializes AI resources."""
from datetime import datetime, timedelta
from types import SimpleNamespace
import uuid
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def history_api(db_factory, tmp_path):
    from fastapi.testclient import TestClient
    from backend.database.models import User, Conversation, Message, Feedback
    from backend.main import create_app
    from backend.api.dependencies import current_user
    with db_factory() as session:
        owner = User(email='history-owner@example.test', role='employee', password_hash='unused')
        other = User(email='history-other@example.test', role='employee', password_hash='unused')
        session.add_all([owner, other]); session.flush()
        ids = []
        for index in range(23):
            conversation = Conversation(user_id=owner.user_id, title=f'Saved question {index}',
                                        started_at=datetime(2026, 1, 1) + timedelta(days=index))
            session.add(conversation); session.flush(); ids.append(conversation.id)
        hidden = Conversation(user_id=other.user_id, title='Other owner')
        session.add(hidden); session.flush()
        question = Message(conversation_id=ids[-1], message_provider='user', content='How does this work?')
        session.add(question); session.flush()
        answer = Message(conversation_id=ids[-1], message_provider='assistant', content='Saved answer',
            reply_to_message_id=question.message_id, generation_metadata={
                'api_key': 'must-not-leak', 'prompt_version': 'test',
                'citations': [{'doc_name': 'Policy', 'quote': 'Saved evidence', 'secret': 'must-not-leak'}]})
        session.add(answer); session.flush()
        session.add(Feedback(message_id=answer.message_id, user_id=owner.user_id, rating='down'))
        session.commit()
        actor = SimpleNamespace(user_id=owner.user_id, role='employee', email=owner.email)
        other_id = hidden.id; answer_id = answer.message_id
    def forbidden(*args, **kwargs):
        raise AssertionError('History initialized AI resources')
    app = create_app(session_factory=db_factory, upload_root=tmp_path / 'uploads')
    app.dependency_overrides[current_user] = lambda: actor
    for name in ('get_embedder', 'get_retriever', 'get_llm'):
        setattr(app.state.resources, name, forbidden)
    with TestClient(app) as client:
        client.headers['X-Requested-With'] = 'helpdesk-ui'
        yield SimpleNamespace(client=client, app=app, ids=ids, other=other_id, answer=answer_id)


def test_history_pagination_is_owned_stable_and_complete(history_api):
    api = history_api
    first = api.client.get('/api/conversations?limit=20').json()
    second = api.client.get('/api/conversations?limit=20&offset=20').json()
    assert first['total'] == second['total'] == 23
    assert len(first['conversations']) == 20 and len(second['conversations']) == 3
    actual = [row['conversation_id'] for row in first['conversations'] + second['conversations']]
    assert actual == [str(value) for value in reversed(api.ids)]
    assert str(api.other) not in actual


def test_transcript_restores_owner_rating_exact_link_and_safe_citation(history_api):
    result = history_api.client.get(f'/api/conversations/{history_api.ids[-1]}').json()
    question = next(row for row in result['messages'] if row['role'] == 'user')
    answer = next(row for row in result['messages'] if row['role'] == 'assistant')
    assert answer['rating'] == 'down'
    assert answer['reply_to_message_id'] == question['message_id']
    assert answer['generation']['citations'] == [{'doc_name': 'Policy', 'quote': 'Saved evidence'}]
    assert 'must-not-leak' not in str(result)
    vote = history_api.client.post('/api/feedback', json={'response_id': str(history_api.answer), 'rating': 'up'})
    assert vote.status_code == 200
    reread = history_api.client.get(f'/api/conversations/{history_api.ids[-1]}').json()
    assert next(row for row in reread['messages'] if row['role'] == 'assistant')['rating'] == 'up'


def test_history_rejects_other_owner_missing_and_invalid_ids(history_api):
    assert history_api.client.get(f'/api/conversations/{history_api.other}').status_code == 403
    assert history_api.client.get(f'/api/conversations/{uuid.uuid4()}').status_code == 404
    assert history_api.client.get('/api/conversations/not-a-uuid').status_code == 422


@pytest.mark.parametrize('query', ['limit=0', 'limit=101', 'offset=-1'])
def test_history_rejects_bad_pagination(history_api, query):
    assert history_api.client.get('/api/conversations?' + query).status_code == 422


def test_history_requires_login(history_api):
    history_api.app.dependency_overrides.clear()
    assert history_api.client.get('/api/conversations').status_code == 401
    assert history_api.client.get(f'/api/conversations/{history_api.ids[-1]}').status_code == 401
