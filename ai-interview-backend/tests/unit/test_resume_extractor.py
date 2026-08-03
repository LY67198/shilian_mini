"""resume_extractor — 简历多格式文本提取器单测"""
import pytest
from app.exceptions.http_exceptions import ValidationError
from app.services.client.resume_extractor import (
    extract_resume_text,
    validate_resume_ext,
)


@pytest.mark.unit
class TestValidateExt:
    def test_supported_passes(self):
        for name in ("r.pdf", "r.docx", "r.PPTX"):
            validate_resume_ext(name)  # 不应抛错

    def test_legacy_doc_ppt_friendly(self):
        for name in ("r.doc", "r.ppt"):
            with pytest.raises(ValidationError) as e:
                validate_resume_ext(name)
            assert "另存为" in e.value.detail

    def test_unknown_ext_generic(self):
        with pytest.raises(ValidationError) as e:
            validate_resume_ext("r.txt")
        assert "仅支持" in e.value.detail


@pytest.mark.unit
class TestMagicByte:
    def test_pdf_ext_zip_content_rejected(self, tmp_path):
        p = tmp_path / "fake.pdf"
        p.write_bytes(b"PK\x03\x04 fakezip")
        with pytest.raises(ValidationError) as e:
            extract_resume_text(str(p), "fake.pdf")
        assert "文件内容与格式不匹配" in e.value.detail

    def test_docx_ext_nonzip_rejected(self, tmp_path):
        p = tmp_path / "fake.docx"
        p.write_bytes(b"not a zip")
        with pytest.raises(ValidationError) as e:
            extract_resume_text(str(p), "fake.docx")
        assert "文件内容与格式不匹配" in e.value.detail
