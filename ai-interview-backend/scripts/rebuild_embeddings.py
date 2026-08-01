"""一次性重建 pgvector embedding（两表全量）

场景：DB 停留在 Milvus 期的"已删 embedding 列"状态，alembic upgrade head
刚补回 embedding 列但数据为空。本脚本遍历 question_bank / knowledge_chunks，
用现有文本重新生成 embedding 写回。

用法（容器内）：
  docker exec shilian-app python scripts/rebuild_embeddings.py

注意：
- question_bank 用 embedding_text（已有 embed 源文本）
- knowledge_chunks 用 content（该表无 embedding_text 列）
- 纯一次性工具，跑完可删；不影响业务代码
"""
import asyncio
import logging
import sys

from sqlalchemy import select

from app.db.base import get_session_local
from app.llm.embedding import embed_text
from app.models.knowledge import KnowledgeChunk
from app.models.question_bank import QuestionBank
from app.vector_db.collections import knowledge as knowledge_vdb
from app.vector_db.collections import question_bank as question_vdb

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def rebuild_question_bank() -> None:
    sf = get_session_local()
    async with sf() as db:
        rows = (await db.execute(
            select(QuestionBank.id, QuestionBank.embedding_text)
        )).all()
        logger.info(f"question_bank 共 {len(rows)} 条待重建")
        ok = 0
        for qid, emb_text in rows:
            if not emb_text:
                logger.warning(f"  skip {qid}: embedding_text 为空")
                continue
            try:
                emb = await embed_text(emb_text)
                await question_vdb.insert_questions(db, [{"id": qid, "embedding": emb}])
                ok += 1
                if ok % 10 == 0:
                    logger.info(f"  ...{ok}/{len(rows)}")
            except Exception as e:
                logger.error(f"  重建失败 {qid}: {e}")
        logger.info(f"question_bank 完成：成功 {ok}/{len(rows)}")


async def rebuild_knowledge() -> None:
    sf = get_session_local()
    async with sf() as db:
        rows = (await db.execute(
            select(KnowledgeChunk.id, KnowledgeChunk.content)
        )).all()
        logger.info(f"knowledge_chunks 共 {len(rows)} 条待重建")
        ok = 0
        for cid, content in rows:
            if not content:
                logger.warning(f"  skip {cid}: content 为空")
                continue
            try:
                emb = await embed_text(content)
                await knowledge_vdb.insert_chunks(db, [{"id": cid, "embedding": emb}])
                ok += 1
            except Exception as e:
                logger.error(f"  重建失败 {cid}: {e}")
        logger.info(f"knowledge_chunks 完成：成功 {ok}/{len(rows)}")


async def main() -> None:
    await rebuild_question_bank()
    await rebuild_knowledge()
    logger.info("全部重建完成")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
