import uuid
from types import SimpleNamespace
import pytest
from backend.services.hybrid_ranking import bm25, fuse


def test_bm25_exact_error_codes_case_and_no_match():
    docs = {'a': 'Restart the VPN client.', 'b': 'VPN-809 requires checking the firewall.', 'c': 'VPN-810 is another error.'}
    assert bm25('vpn-809', docs)[0][0] == 'b'
    assert bm25('unfindable-token', docs) == []
    assert bm25('anything', {}) == []


def test_fusion_rewards_both_branches_without_duplicate_votes():
    scores = fuse(['a', 'b', 'b'], ['b', 'c'])
    assert scores[0][0] == 'b'
    assert len(scores) == 3
    assert dict(scores)['b'] == pytest.approx(1/62 + 1/61)


@pytest.fixture
def evidence_fixture(db_factory):
    from backend.database.models import User, Conversation, Message, Document, DocumentCategory, DocumentChunk, RetrievedContext
    from backend.main import create_app
    from fastapi.testclient import TestClient
    from backend.services.auth_service import AuthService
    from backend.utils.security import hash_password
    with db_factory() as s:
        user = User(email='review@example.test', role='employee', password_hash=hash_password('Review-test-123'))
        other = User(email='other@example.test', role='employee', password_hash=hash_password('Review-test-123'))
        s.add_all([user, other]); s.flush()
        cat = s.query(DocumentCategory).first()
        doc = Document(doc_name='Old VPN policy', doc_type='md', category_id=cat.category_id,
            status='active', is_online=True, doc_path='fixture.md', version=1)
        s.add(doc); s.flush()
        chunk = DocumentChunk(document_id=doc.doc_id, chunk_text='VPN-809 needs firewall access. Never share passwords.',
            chunk_index=0, is_active=True)
        s.add(chunk); s.flush()
        chat = Conversation(user_id=user.user_id, title='VPN problem'); s.add(chat); s.flush()
        question = Message(conversation_id=chat.id, message_provider='user', content='VPN-809?'); s.add(question);s.flush()
        answer = Message(conversation_id=chat.id, message_provider='assistant', content='VPN-809 needs firewall access.',
            reply_to_message_id=question.message_id, generation_metadata={'citations':[{'chunk_id':str(chunk.chunk_id), 'quote':'VPN-809 needs firewall access.'}]})
        s.add(answer); s.flush();s.add(RetrievedContext(message_id=answer.message_id, chunk_id=chunk.chunk_id, rank=1));s.commit()
    client = TestClient(create_app(session_factory=db_factory))
    assert client.post('/api/auth/login', json={'email':user.email,'password':'Review-test-123'}).status_code == 200
    return SimpleNamespace(client=client, factory=db_factory, user=user, other=other, doc=doc, chunk=chunk, answer=answer, chat=chat)


@pytest.mark.integration
def test_feedback_persists_reason_exposes_admin_and_clear_on_up(evidence_fixture):
    from backend.services.admin_review_service import AdminReviewService
    f=evidence_fixture;url='/api/feedback';body={'response_id':str(f.answer.message_id),'rating':'down','comment':'Missing important details: Explain the firewall rule.'}
    assert f.client.post(url,json=body).status_code==200
    saved=next(m for m in f.client.get(f'/api/conversations/{f.chat.id}').json()['messages'] if m['message_id']==str(f.answer.message_id))
    assert saved['feedback_comment']==body['comment']
    review=AdminReviewService(f.factory).review(f.answer.message_id)
    assert review['review']['feedback']['comment']==body['comment'] if 'review' in review else review['feedback']['comment']==body['comment']
    assert f.client.post(url,json={**body,'comment':'x'*601}).status_code==422
    assert f.client.post(url,json={**body,'rating':'up'}).json()['comment'] is None


@pytest.mark.integration
def test_saved_evidence_checks_owner_membership_and_source_availability(evidence_fixture):
    from backend.database.models import Document
    f=evidence_fixture;url=f'/api/responses/{f.answer.message_id}/evidence/{f.chunk.chunk_id}'
    response=f.client.get(url);assert response.status_code==200
    assert response.json()['text']==f.chunk.chunk_text and response.json()['quotes']==['VPN-809 needs firewall access.']
    assert f.client.get(f'/api/responses/{f.answer.message_id}/evidence/{uuid.uuid4()}').status_code==404
    with f.factory() as s:
        d=s.get(Document,f.doc.doc_id);d.status='deactivated';s.commit()
    assert f.client.get(url).status_code==200 # immutable historical chunk, not new version
    with f.factory() as s:
        d=s.get(Document,f.doc.doc_id);d.is_online=False;s.commit()
    assert f.client.get(url).status_code==403
    f.client.post('/api/auth/logout')
    assert f.client.get(url).status_code==401
    f.client.post('/api/auth/login',json={'email':f.other.email,'password':'Review-test-123'})
    assert f.client.get(url).status_code==403


@pytest.mark.integration
def test_hybrid_and_bm25_exclude_offline_and_inactive_chunks(evidence_fixture):
    from backend.services.document_processing_service import DocumentProcessingService
    from backend.database.models import Document, DocumentChunk
    f=evidence_fixture
    with f.factory() as s:
        doc=Document(doc_name='Hidden',doc_type='md',category_id=f.doc.category_id,status='active',is_online=False,doc_path='hidden.md')
        s.add(doc);s.flush();s.add(DocumentChunk(document_id=doc.doc_id,chunk_text='VPN-809 VPN-809',chunk_index=0,is_active=True));s.commit()
    with DocumentProcessingService(session_factory=f.factory, retriever=SimpleNamespace(default_top_k=3)) as service:
        service._vector_search=lambda *args,**kwargs: []
        for mode in ['bm25','hybrid']:
            hits=service.search('VPN-809',3,mode=mode)
            assert [h['chunk_id'] for h in hits]==[str(f.chunk.chunk_id)]
            assert hits[0]['bm25_score']>0
        with pytest.raises(ValueError):service.search('VPN',3,mode='invalid')
    response=f.client.post('/api/search?mode=bm25',json={'question':'VPN-809','top_k':3})
    assert response.status_code==200
    assert [h['chunk_id'] for h in response.json()['hits']]==[str(f.chunk.chunk_id)]
