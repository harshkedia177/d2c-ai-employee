import asyncio

from packages.llm.embeddings import EMBEDDING_DIM, FakeEmbeddings


def test_fake_embeddings_are_deterministic():
    fake = FakeEmbeddings()
    v1 = asyncio.run(fake.embed("hello world"))
    v2 = asyncio.run(fake.embed("hello world"))
    assert v1 == v2
    assert len(v1) == EMBEDDING_DIM
    v3 = asyncio.run(fake.embed("different text"))
    assert v3 != v1


def test_fake_embeddings_records_calls():
    fake = FakeEmbeddings()
    asyncio.run(fake.embed("one"))
    asyncio.run(fake.embed("two"))
    assert fake.calls == ["one", "two"]


def test_fake_embeddings_default_dim_matches_table_schema():
    """EMBEDDING_DIM must stay 3072 to match core.few_shot_examples.embedding."""
    assert EMBEDDING_DIM == 3072
