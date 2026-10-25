import pytest


@pytest.mark.integration
def test_real_model_batch_and_overflow(real_embedder):
    assert real_embedder.dimension == 384
    assert len(real_embedder.embed_text("A test sentence.")) == 384
    assert len(real_embedder.embed_batch(["First sentence", "Second sentence"])) == 2
    assert real_embedder.embed_batch([]) == []
    with pytest.raises(ValueError, match="token limit"):
        real_embedder.embed_batch(["example " * 600])
    with pytest.raises(ValueError, match="nonempty"):
        real_embedder.embed_text(" ")
