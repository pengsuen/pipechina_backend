"""Opt-in tests against isolated real indexes; never target a business generation."""

import os
from uuid import uuid4

import pytest

from app.bootstrap.config import Settings
from app.infrastructure.knowledge.http import ElasticsearchIndex, Neo4jIndex, QdrantIndex
from app.ports.knowledge import EvidenceRecord, GraphFact, KnowledgeScope

pytestmark = pytest.mark.skipif(
    os.getenv("RAG_EXTERNAL_TEST") != "1", reason="external indexes opt-in"
)


async def test_real_indexes_filter_versions_and_delete():
    settings = Settings(
        embedding_dimensions=3,
        es_url="http://127.0.0.1:19200",
        es_password=None,
        es_api_key=None,
        qdrant_url="http://127.0.0.1:16333",
        qdrant_api_key=None,
        neo4j_url="http://127.0.0.1:17474",
        neo4j_password="isolated-rag-test-password",
    )
    indexes = [ElasticsearchIndex(settings), QdrantIndex(settings), Neo4jIndex(settings)]
    generation = "test" + uuid4().hex
    allowed, denied = uuid4(), uuid4()
    records = [
        EvidenceRecord(
            id=uuid4(),
            version_id=version,
            title="DEMO",
            text="演示设备标签 DEMO-1",
            vector=[1.0, 0.0, 0.0],
        )
        for version in [allowed, denied]
    ]
    scope = KnowledgeScope(
        generation=generation, version_ids=[allowed], authorization_digest="test"
    )
    try:
        for index in indexes:
            await index.initialize(generation)
        for index in indexes[:2]:
            await index.upsert(generation, records)
        facts = [
            GraphFact(
                id=uuid4(),
                version_id=r.version_id,
                evidence_id=r.id,
                source="DEMO-1",
                relation="HAS_LABEL",
                target="DEMO-LABEL",
            )
            for r in records
        ]
        await indexes[2].upsert(generation, facts)
        text_hits = await indexes[0].search("DEMO-1", scope)
        vector_hits = await indexes[1].search([1.0, 0.0, 0.0], scope)
        graph_hits = await indexes[2].neighbors("DEMO-1", scope)
        assert [h.evidence.version_id for h in text_hits] == [allowed]
        assert [h.evidence.version_id for h in vector_hits] == [allowed]
        assert [h.version_id for h in graph_hits] == [allowed]
        for index in indexes:
            await index.delete_version(generation, allowed)
        assert not await indexes[0].search("DEMO-1", scope)
        assert not await indexes[1].search([1.0, 0.0, 0.0], scope)
        assert not await indexes[2].neighbors("DEMO-1", scope)
    finally:
        for index in indexes:
            await index.close()
