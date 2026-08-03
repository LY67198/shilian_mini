"""简历服务 — 简历上传、调用 ai_service 解析、查询与删除

简历解析是 LLM 任务，上传后异步触发 ai_service.parse_resume()，
parsed_content 字段为 JSON 字符串（结构化解析结果）。
"""
import json
import logging
import os
from typing import Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.resume import Resume
from app.services.client.ai_service import ai_service
from app.services.client.resume_extractor import extract_resume_text
from app.exceptions.http_exceptions import NotFoundError, ValidationError, APIException

logger = logging.getLogger(__name__)

# 简历上传目录
UPLOAD_DIR = "uploads/resumes"
os.makedirs(UPLOAD_DIR, exist_ok=True)


class ResumeService:
    """简历服务 — 简历上传、PDF 解析、AI 解析分析和简历管理。"""

    async def upload_and_parse(
        self,
        db: AsyncSession,
        user_id: int,
        file_content: bytes,
        file_name: str,
        target_position: str
    ) -> Dict:
        """上传简历文件并触发 AI 解析和分析。

        流程：保存文件 → 写 Resume 记录 → extractor 抽文本（PDF / DOCX / PPTX）→ AI 解析 + 分析 → 落库。
        中途任何步骤失败都会把状态置为 failed 并向上抛错。

        Args:
            db: 数据库会话。
            user_id: 上传用户 ID。
            file_content: PDF 文件二进制内容。
            file_name: 原始文件名。
            target_position: 目标岗位，用于简历分析提示。

        Returns:
            包含 resume_id / status / message 的字典。

        Raises:
            ValidationError: 文件提取或 AI 解析失败。
        """
        # 保存文件到本地
        file_path = os.path.join(UPLOAD_DIR, f"{user_id}_{file_name}")
        with open(file_path, "wb") as f:
            f.write(file_content)

        # 创建简历记录
        resume = Resume(
            user_id=user_id,
            file_url=file_path,
            file_name=file_name,
            target_position=target_position,
            status="parsing"
        )
        db.add(resume)
        await db.commit()
        await db.refresh(resume)

        # 从文件中提取文本（PDF / DOCX / PPTX）
        try:
            resume_text = extract_resume_text(file_path, file_name)
        except ValidationError:
            resume.status = "failed"
            await db.commit()
            raise
        except Exception as e:
            resume.status = "failed"
            await db.commit()
            raise ValidationError(message=f"文件解析失败: {str(e)}")

        # 调用 AI 解析简历
        try:
            parsed = await ai_service.parse_resume(resume_text)
            resume.parsed_content = json.dumps(parsed, ensure_ascii=False)

            # 调用 AI 分析简历质量
            analysis = await ai_service.analyze_resume(parsed, target_position)
            resume.analysis = json.dumps(analysis, ensure_ascii=False)

            resume.status = "completed"
            await db.commit()
            await db.refresh(resume)
        except Exception as e:
            logger.error(f"AI 简历解析失败: {e}")
            resume.status = "failed"
            await db.commit()
            raise

        return {
            "resume_id": resume.id,
            "status": resume.status,
            "message": "简历上传并解析成功"
        }

    async def get_resume(self, db: AsyncSession, resume_id: int, user_id: int) -> Dict:
        """根据 ID 获取简历详情。

    Args:
        db: 数据库会话。
        resume_id: 简历主键。
        user_id: 当前用户 ID（用于归属权校验）。

    Returns:
        包含 resume_id / status / file_name / target_position / parsed_content / analysis / created_at 的字典。

    Raises:
            NotFoundError: 简历不存在。
    """
        query = select(Resume).where(
            Resume.id == resume_id,
            Resume.user_id == user_id
        )
        result = await db.execute(query)
        resume = result.scalar_one_or_none()

        if not resume:
            raise NotFoundError(message="简历不存在")

        parsed_content = None
        if resume.parsed_content:
            try:
                parsed_content = json.loads(resume.parsed_content)
            except json.JSONDecodeError:
                parsed_content = None

        analysis = None
        if resume.analysis:
            try:
                analysis = json.loads(resume.analysis)
            except json.JSONDecodeError:
                analysis = None

        return {
            "resume_id": resume.id,
            "status": resume.status,
            "file_name": resume.file_name,
            "target_position": resume.target_position,
            "parsed_content": parsed_content,
            "analysis": analysis,
            "created_at": resume.created_at.isoformat() if resume.created_at else None
        }

    async def get_user_resumes(self, db: AsyncSession, user_id: int) -> list:
        """获取用户的所有简历列表（按创建时间倒序）。

    Args:
        db: 数据库会话。
        user_id: 当前用户 ID。

    Returns:
        简历摘要信息字典列表（不含解析正文）。
    """
        query = select(Resume).where(
            Resume.user_id == user_id
        ).order_by(Resume.created_at.desc())
        result = await db.execute(query)
        resumes = result.scalars().all()

        return [
            {
                "resume_id": r.id,
                "file_name": r.file_name,
                "target_position": r.target_position,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None
            }
            for r in resumes
        ]

    async def delete_resume(self, db: AsyncSession, resume_id: int, user_id: int) -> None:
        """删除简历（含物理文件）。已关联面试的简历不允许删除。
        如简历解析尚未完成（parsing/pending），可以安全删除。

    Args:
        db: 数据库会话。
        resume_id: 简历主键。
        user_id: 当前用户 ID（用于归属权校验）。

    Raises:
        NotFoundError: 简历不存在。
        APIException: 该简历已关联面试记录，无法删除。
    """
        query = select(Resume).where(
            Resume.id == resume_id,
            Resume.user_id == user_id
        )
        result = await db.execute(query)
        resume = result.scalar_one_or_none()
        if not resume:
            raise NotFoundError(message="简历不存在")

        # 检查是否有关联面试
        from app.models.interview import Interview
        interview_count_q = select(Interview.id).where(Interview.resume_id == resume_id).limit(1)
        interview_result = await db.execute(interview_count_q)
        if interview_result.scalar_one_or_none() is not None:
            raise APIException(
                status_code=400,
                message="该简历已关联面试记录，无法删除。如需删除请先删除相关面试。",
            )

        # 删除物理文件（best-effort）
        if resume.file_url:
            file_path = resume.file_url.lstrip("/")
            if os.path.isabs(resume.file_url):
                file_path = resume.file_url
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except OSError as e:
                logger.warning(f"删除简历文件失败 {file_path}: {e}")

        await db.delete(resume)
        await db.commit()


resume_service = ResumeService()
