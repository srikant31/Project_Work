import pytest


@pytest.mark.integration
def test_real_chroma_upsert_filter_and_scoped_delete(real_retriever):
    ret = real_retriever
    records = [("one", [1.0, 0.0, 0.0], "old text")]
    ret.add_chunks(records, "A")
    ret.add_chunks([("one", [1.0, 0.0, 0.0], "updated text")], "A")
    ret.add_chunks([("two", [0.9, 0.1, 0.0], "other source")], "B")
    assert ret.count() == 2
    ret.verify_document("A", ["one"])
    result = ret.query([1.0, 0.0, 0.0], 1, doc_ids=["B"])
    assert result["ids"] == [["two"]]
    assert ret.query([1.0, 0.0, 0.0], 1, doc_ids=[])["ids"] == [[]]
    ret.deactivate_doc("A")
    assert ret.query([1.0, 0.0, 0.0], 1)["ids"] == [["two"]]
    ret.delete_by_doc("A")
    assert ret.count() == 1
    with pytest.raises(ValueError):
        ret.query([1.0, 0.0, 0.0], 0)


@pytest.mark.integration
def test_vector_verification_rejects_missing_ids(real_retriever):
    with pytest.raises(RuntimeError, match="verification"):
        real_retriever.verify_document("missing", ["one"])


def test_existing_only_adapter_does_not_create_storage(tmp_path):
    from backend.services.retrieval_service import RetrievalService
    target = tmp_path / "missing_store"
    with pytest.raises(FileNotFoundError, match="nothing was created"):
        RetrievalService(target, create=False)
    assert not target.exists()
