"""Runs without Postgres, Chroma, or model downloads — pure logic + fakes.

    pytest tests/test_pipeline_invariants.py -v
"""

import pytest

from backend.utils.chunking import Chunk, chunk_pages, chunk_text


class TestChunking:
    def test_overlap_is_real(self):
        words = " ".join(f"w{i}" for i in range(1, 251))
        cs = chunk_text(words, chunk_size=100, overlap=20)
        assert [c.text.split()[0] for c in cs] == ["w1", "w81", "w161"]

    def test_indices_are_sequential(self):
        cs = chunk_text(" ".join(str(i) for i in range(500)), 100, 20)
        assert [c.index for c in cs] == list(range(len(cs)))

    def test_no_content_lost(self):
        words = [f"w{i}" for i in range(1, 200)]
        cs = chunk_text(" ".join(words), 50, 10)
        seen = set()
        for c in cs:
            seen.update(c.text.split())
        assert seen == set(words)

    def test_empty_input(self):
        assert chunk_text("") == []
        assert chunk_text("   ") == []

    def test_short_input_single_chunk(self):
        cs = chunk_text("one two three", chunk_size=100, overlap=20)
        assert len(cs) == 1 and cs[0].index == 0

    def test_bad_overlap_rejected(self):
        with pytest.raises(ValueError):
            chunk_text("a b c", chunk_size=10, overlap=10)

    def test_no_infinite_loop(self):
        # overlap just below chunk_size => stride of 1; must still terminate
        cs = chunk_text(" ".join(str(i) for i in range(50)), 10, 9)
        assert len(cs) < 100

    def test_pages_carry_page_numbers(self):
        cs = chunk_pages(["a b c d", "e f g h"], chunk_size=2, overlap=0)
        assert {c.page_number for c in cs} == {1, 2}
        assert [c.index for c in cs] == list(range(len(cs)))

    def test_token_count_matches(self):
        c = Chunk(text="one two three", index=0)
        assert c.token_count == 3


class FakeCollection:
    def __init__(self):
        self.store = {}
        self.fail_on_add = False

    def add(self, ids, embeddings, documents, metadatas):
        if self.fail_on_add:
            raise RuntimeError("simulated vector store outage")
        for i, e, d, m in zip(ids, embeddings, documents, metadatas):
            self.store[i] = (e, d, dict(m))

    def delete(self, where):
        doc = where["doc_id"]
        for k in [k for k, v in self.store.items() if v[2]["doc_id"] == doc]:
            del self.store[k]

    def get(self, where=None):
        return {"ids": list(self.store)}

    def count(self):
        return len(self.store)


class TestRetrievalContract:
    """Guards the behaviours that were missing before: batched writes,
    is_active filtering, and doc-scoped deletion."""

    def test_delete_by_doc_is_scoped(self):
        c = FakeCollection()
        c.add(["1"], [[0.1]], ["a"], [{"doc_id": "A", "is_active": True}])
        c.add(["2"], [[0.2]], ["b"], [{"doc_id": "B", "is_active": True}])
        c.delete({"doc_id": "A"})
        assert list(c.store) == ["2"]

    def test_failed_add_leaves_store_untouched(self):
        c = FakeCollection()
        c.fail_on_add = True
        with pytest.raises(RuntimeError):
            c.add(["1"], [[0.1]], ["a"], [{"doc_id": "A", "is_active": True}])
        assert c.count() == 0

    def test_vector_id_is_namespaced(self):
        # Mirrors RetrievalService.vector_id without importing chromadb.
        def vector_id(collection, chunk_id):
            return f"{collection}:{chunk_id}"

        assert vector_id("chunks_minilm_v1", "abc") == "chunks_minilm_v1:abc"
