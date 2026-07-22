import pytest

from app.db.base import get_session_local
from app.llm.embedding import embed_text
from app.vector_db.collections import knowledge, question_bank


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_question_vector_search_returns_bank_source():
    session_factory = get_session_local()
    async with session_factory() as session:
        vec = await embed_text("Python 的 GIL 是什么")
        results = await question_bank.search(
            session,
            vec,
            top_k=5,
            position_tag="python_backend",
            min_score=0.0,
        )
        assert isinstance(results, list)
        if results:
            assert results[0]["source"] == "from_bank"
            assert "similarity" in results[0]
            assert results[0]["similarity"] <= 1.0


@pytest.mark.asyncio
async def test_knowledge_vector_search_shape():
    session_factory = get_session_local()
    async with session_factory() as session:
        vec = await embed_text("异步编程")
        results = await knowledge.search(session, vec, top_k=3, min_score=0.0)
        assert isinstance(results, list)
        for result in results:
            assert {"id", "document_id", "chunk_index", "content", "similarity"}.issubset(
                result.keys()
            )
