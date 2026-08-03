"""resume_service.upload_and_parse — 接入 extractor 后单测"""
import pytest
from unittest.mock import AsyncMock, patch
from app.exceptions.http_exceptions import ValidationError
from app.services.client.resume_service import ResumeService


@pytest.mark.unit
class TestUploadAndParse:
    def _svc_db(self):
        db = AsyncMock()
        db.add = AsyncMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        return ResumeService(), db

    async def test_success_completed(self, tmp_path):
        svc, db = self._svc_db()
        with (
            patch("app.services.client.resume_service.UPLOAD_DIR", str(tmp_path)),
            patch("app.services.client.resume_service.extract_resume_text", return_value="简历文本"),
            patch("app.services.client.resume_service.ai_service") as ai,
        ):
            ai.parse_resume = AsyncMock(return_value={"name": "张三"})
            ai.analyze_resume = AsyncMock(return_value={"overall_score": 6})
            result = await svc.upload_and_parse(
                db=db, user_id=1, file_content=b"fake", file_name="r.docx",
                target_position="Python",
            )
        assert result["status"] == "completed"

    async def test_validation_error_passthrough(self, tmp_path):
        svc, db = self._svc_db()
        with (
            patch("app.services.client.resume_service.UPLOAD_DIR", str(tmp_path)),
            patch(
                "app.services.client.resume_service.extract_resume_text",
                side_effect=ValidationError(message="无法从文件中提取文本内容"),
            ),
        ):
            with pytest.raises(ValidationError) as e:
                await svc.upload_and_parse(
                    db=db, user_id=1, file_content=b"fake", file_name="r.docx",
                    target_position="Python",
                )
        assert "无法从文件" in e.value.detail

    async def test_generic_error_wrapped(self, tmp_path):
        svc, db = self._svc_db()
        with (
            patch("app.services.client.resume_service.UPLOAD_DIR", str(tmp_path)),
            patch(
                "app.services.client.resume_service.extract_resume_text",
                side_effect=RuntimeError("boom"),
            ),
        ):
            with pytest.raises(ValidationError) as e:
                await svc.upload_and_parse(
                    db=db, user_id=1, file_content=b"fake", file_name="r.docx",
                    target_position="Python",
                )
        assert "文件解析失败" in e.value.detail
