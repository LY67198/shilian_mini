# 简历支持 Word/PPT 格式（.docx/.pptx）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 简历上传从仅 PDF 扩展为 PDF + Word (.docx) + PPT (.pptx)，产出统一纯文本进入既有 AI 解析链路；老式 .doc/.ppt 给友好提示。

**Architecture:** 新增独立解析模块 `app/services/client/resume_extractor.py`，`validate_resume_ext()` 做两档后缀校验 + `extract_resume_text()` 按后缀分派（magic-byte 校验 + pdfplumber/docx/pptx 提取）。`resume_service` 和路由都收敛到该模块，删除 `_extract_pdf_text` 私有方法。

**Tech Stack:** python-docx（docx 全面提取，含 OOXML 文本框）、python-pptx（pptx 逐页 + 组形状递归）、pdfplumber（既有，PDF 迁移）。

**Spec:** `docs/superpowers/specs/2026-08-03-resume-format-support-design.md`

---

### Task 1: 添加 python-docx / python-pptx 依赖

**Files:**
- Modify: `ai-interview-backend/requirements.txt:61`（pdfplumber 行后）

- [ ] **Step 1: 在 requirements.txt 添加依赖**

在 `pdfplumber==0.11.6` 行之后追加两行：

```
python-docx==1.1.2
python-pptx==1.0.2
```

- [ ] **Step 2: 重建后端容器并验证依赖可导入**

Run: `cd ai-interview-backend && docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build`
Run: `docker exec shilian-app python -c "import docx, pptx; print('ok')"`
Expected: 输出 `ok`（若提示模块不存在说明 build 未生效，检查 requirements.txt 是否写入正确位置）

- [ ] **Step 3: Commit**

```bash
git add ai-interview-backend/requirements.txt
git commit -m "chore(deps): 新增 python-docx / python-pptx 简历解析依赖"
```

---

### Task 2: resume_extractor 骨架 — 后缀校验 + magic-byte + PDF 迁移

**Files:**
- Create: `ai-interview-backend/app/services/client/resume_extractor.py`
- Test: `ai-interview-backend/tests/unit/test_resume_extractor.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_resume_extractor.py`：

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `docker exec shilian-app pytest tests/unit/test_resume_extractor.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.services.client.resume_extractor'`）

- [ ] **Step 3: 实现 resume_extractor.py（骨架 + PDF）**

创建 `app/services/client/resume_extractor.py`：

```python
"""统一简历文本提取器 — PDF / DOCX / PPTX → 纯文本。

非 AI 业务工具，供 ResumeService 上传解析使用。文本提取产出与格式无关的
纯文本，后续统一交给 ai_service.parse_resume() 做 LLM 解析。
"""
from pathlib import Path
from app.exceptions.http_exceptions import ValidationError

# 支持的简历格式（现代 OOXML，纯 Python 库解析）
SUPPORTED_EXTS = {".pdf", ".docx", ".pptx"}

# 老式格式，提示另存为
LEGACY_EXTS = {".doc", ".ppt"}


def validate_resume_ext(file_name: str) -> None:
    """校验文件名后缀（两档提示）。

    - .pdf / .docx / .pptx：通过
    - .doc / .ppt：提示另存为现代格式
    - 其他：仅支持提示
    """
    ext = Path(file_name).suffix.lower()
    if ext in SUPPORTED_EXTS:
        return
    if ext in LEGACY_EXTS:
        raise ValidationError(message="不支持 .doc/.ppt 格式，请将文档另存为 .docx 或 .pptx 后上传")
    raise ValidationError(message="仅支持 PDF / Word (.docx) / PPT (.pptx) 格式文件")


def extract_resume_text(file_path: str, file_name: str) -> str:
    """按扩展名分派提取纯文本；先做 magic-byte 校验文件真实格式。"""
    validate_resume_ext(file_name)
    ext = Path(file_name).suffix.lower()
    _check_magic(file_path, ext)

    if ext == ".pdf":
        text = _extract_pdf_text(file_path)
    elif ext == ".docx":
        text = _extract_docx_text(file_path)
    else:
        text = _extract_pptx_text(file_path)
    return text


def _check_magic(file_path: str, ext: str) -> None:
    """校验文件头魔数，拒绝改名冒充的文件。"""
    with open(file_path, "rb") as f:
        head = f.read(5)
    if ext == ".pdf":
        if not head.startswith(b"%PDF-"):
            raise ValidationError(message="文件内容与格式不匹配")
    elif ext in {".docx", ".pptx"}:
        if not head.startswith(b"PK\x03\x04"):
            raise ValidationError(message="文件内容与格式不匹配")


def _extract_pdf_text(file_path: str) -> str:
    """从 PDF 提取文本（pdfplumber）。"""
    import pdfplumber
    text = ""
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text
```

注意：`_extract_docx_text` / `_extract_pptx_text` 在本任务先留空占位（`return ""`），后续任务逐个实现：

```python
def _extract_docx_text(file_path: str) -> str:
    return ""


def _extract_pptx_text(file_path: str) -> str:
    return ""
```

- [ ] **Step 4: 运行确认通过**

Run: `docker exec shilian-app pytest tests/unit/test_resume_extractor.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add ai-interview-backend/app/services/client/resume_extractor.py ai-interview-backend/tests/unit/test_resume_extractor.py
git commit -m "feat(extractor): resume_extractor 统一分派 + magic-byte 校验 + PDF 迁移"
```

---

### Task 3: docx 提取 — 段落 + 表格 + 页眉页脚

**Files:**
- Modify: `ai-interview-backend/app/services/client/resume_extractor.py`
- Test: `ai-interview-backend/tests/unit/test_resume_extractor.py`

- [ ] **Step 1: 追加失败测试**

在 `test_resume_extractor.py` 末尾追加：

```python
@pytest.mark.unit
class TestDocxExtraction:
    def _build_docx(self, path, include_header=True):
        from docx import Document
        doc = Document()
        doc.add_paragraph("Hello World")
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "A1"
        table.cell(0, 1).text = "B1"
        table.cell(1, 0).text = "A2"
        table.cell(1, 1).text = "B2"
        if include_header:
            doc.sections[0].header.paragraphs[0].text = "HeaderText"
        doc.save(str(path))

    def test_paragraph_table_header(self, tmp_path):
        p = tmp_path / "r.docx"
        self._build_docx(p)
        text = extract_resume_text(str(p), "r.docx")
        assert "Hello World" in text
        assert "A1 | B1" in text
        assert "A2 | B2" in text
        assert "HeaderText" in text
        assert text.index("Hello World") < text.index("A1 | B1")
```

- [ ] **Step 2: 运行确认失败**

Run: `docker exec shilian-app pytest tests/unit/test_resume_extractor.py::TestDocxExtraction -v`
Expected: FAIL（`assert "Hello World" in text`，当前 `_extract_docx_text` 返回空串）

- [ ] **Step 3: 实现 _extract_docx_text（段落 + 表格 + 页眉页脚）**

替换 `resume_extractor.py` 中 `_extract_docx_text` 的空实现（本任务先不含文本框，文本框在 Task 4 单独 TDD）：

```python
def _extract_docx_text(file_path: str) -> str:
    """全面提取 docx：段落 + 表格 + 页眉页脚（文本框在 Task 4 补齐）。"""
    from docx import Document

    doc = Document(file_path)
    parts: list[str] = []

    # 正文段落
    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text.strip())

    # 表格（每行单元格用 | 分隔）
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            cells = [c for c in cells if c]
            if cells:
                parts.append(" | ".join(cells))

    # 页眉页脚
    for section in doc.sections:
        for part in (section.header, section.footer):
            for para in part.paragraphs:
                if para.text.strip():
                    parts.append(para.text.strip())

    return "\n".join(parts)
```

- [ ] **Step 4: 运行确认通过**

Run: `docker exec shilian-app pytest tests/unit/test_resume_extractor.py::TestDocxExtraction -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ai-interview-backend/app/services/client/resume_extractor.py ai-interview-backend/tests/unit/test_resume_extractor.py
git commit -m "feat(extractor): docx 段落/表格/页眉页脚提取"
```

---

### Task 4: docx 文本框（txbxContent）提取

**Files:**
- Modify: `ai-interview-backend/app/services/client/resume_extractor.py`
- Test: `ai-interview-backend/tests/unit/test_resume_extractor.py`

- [ ] **Step 1: 追加失败测试**

在 `TestDocxExtraction` 类内追加文本框注入 helper 和测试（`_inject_textbox` 用 zipfile 重写 document.xml，注入 VML 文本框）：

```python
    def _inject_textbox(self, path):
        """在 word/document.xml 末尾注入一个文本框（VML + w:txbxContent）。"""
        import zipfile
        with zipfile.ZipFile(str(path)) as z:
            names = z.namelist()
            data = {n: z.read(n) for n in names}
        xml = data["word/document.xml"].decode("utf-8")
        textbox_xml = (
            '<w:p><w:r><w:pict>'
            '<v:shape xmlns:v="urn:schemas-microsoft-com:vml">'
            '<w:txbxContent><w:p><w:r><w:t>TextBoxLine</w:t></w:r></w:p></w:txbxContent>'
            '</v:shape></w:pict></w:r></w:p>'
        )
        xml = xml.replace("</w:body>", textbox_xml + "</w:body>")
        data["word/document.xml"] = xml.encode("utf-8")
        with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as z:
            for n, content in data.items():
                z.writestr(n, content)

    def test_textbox_extracted(self, tmp_path):
        p = tmp_path / "tb.docx"
        self._build_docx(p, include_header=False)
        self._inject_textbox(p)
        text = extract_resume_text(str(p), "tb.docx")
        assert "TextBoxLine" in text
        # 文本框内容排在最后（页眉页脚之后）
        assert text.index("TextBoxLine") > text.index("A2 | B2")
```

- [ ] **Step 2: 运行确认失败**

Run: `docker exec shilian-app pytest tests/unit/test_resume_extractor.py::TestDocxExtraction::test_textbox_extracted -v`
Expected: FAIL（`assert "TextBoxLine" in text`，Task 3 的 `_extract_docx_text` 未走 txbxContent 遍历）

- [ ] **Step 3: 在 _extract_docx_text 中补文本框遍历**

将 `resume_extractor.py` 中 `_extract_docx_text` 的 import 与返回前插入文本框段：

3a. import 行改为（加 `qn`）：

```python
    from docx import Document
    from docx.oxml.ns import qn
```

3b. 在 `return "\n".join(parts)` 之前插入：

```python
    # 文本框（走底层 OOXML w:txbxContent）
    for txbx in doc.element.body.iter(qn("w:txbxContent")):
        for t_para in txbx.iter(qn("w:p")):
            text = "".join(node.text or "" for node in t_para.iter(qn("w:t")))
            if text.strip():
                parts.append(text.strip())
```

- [ ] **Step 4: 运行确认通过**

Run: `docker exec shilian-app pytest tests/unit/test_resume_extractor.py::TestDocxExtraction -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add ai-interview-backend/app/services/client/resume_extractor.py ai-interview-backend/tests/unit/test_resume_extractor.py
git commit -m "feat(extractor): docx 文本框 (txbxContent) 提取"
```

---

### Task 5: pptx 提取 — 多页 + 表格 + 组形状递归

**Files:**
- Modify: `ai-interview-backend/app/services/client/resume_extractor.py`
- Test: `ai-interview-backend/tests/unit/test_resume_extractor.py`

- [ ] **Step 1: 追加失败测试**

在 `test_resume_extractor.py` 末尾追加：

```python
@pytest.mark.unit
class TestPptxExtraction:
    def _build_pptx(self, path):
        from pptx import Presentation
        from pptx.util import Inches
        prs = Presentation()
        # 页 1：标题文本
        s1 = prs.slides.add_slide(prs.slide_layouts[1])
        s1.shapes.title.text = "Page1Title"
        # 页 2：表格 + 组形状嵌套文本
        s2 = prs.slides.add_slide(prs.slide_layouts[1])
        tbl = s2.shapes.add_table(
            rows=1, cols=2, left=Inches(1), top=Inches(1),
            width=Inches(4), height=Inches(1),
        )
        tbl.table.cell(0, 0).text = "A1"
        tbl.table.cell(0, 1).text = "B1"
        group = s2.shapes.add_group_shape()
        tb = group.shapes.add_textbox(Inches(1), Inches(2), Inches(3), Inches(1))
        tb.text_frame.text = "NestedText"
        prs.save(str(path))

    def test_multi_slide_table_group(self, tmp_path):
        p = tmp_path / "r.pptx"
        self._build_pptx(p)
        text = extract_resume_text(str(p), "r.pptx")
        assert "Page1Title" in text
        assert "A1 | B1" in text
        assert "NestedText" in text
        assert text.index("Page1Title") < text.index("A1 | B1")
```

- [ ] **Step 2: 运行确认失败**

Run: `docker exec shilian-app pytest tests/unit/test_resume_extractor.py::TestPptxExtraction -v`
Expected: FAIL（`assert "Page1Title" in text`，当前 `_extract_pptx_text` 返回空串）

- [ ] **Step 3: 实现 _extract_pptx_text + _collect_slide_text**

替换 `resume_extractor.py` 中 `_extract_pptx_text` 的空实现，并追加递归 helper：

```python
def _extract_pptx_text(file_path: str) -> str:
    """逐页提取 pptx：文本框架 + 表格，组形状递归下钻；跳过备注。"""
    from pptx import Presentation

    prs = Presentation(file_path)
    pages: list[str] = []
    for slide in prs.slides:
        lines: list[str] = []
        _collect_slide_text(slide.shapes, lines)
        if lines:
            pages.append("\n".join(lines))
    return "\n\n".join(pages)


def _collect_slide_text(shapes, lines: list[str]) -> None:
    """递归收集一页内所有文本（含组形状嵌套）。"""
    for shape in shapes:
        if getattr(shape, "shapes", None):  # 组形状
            _collect_slide_text(shape.shapes, lines)
            continue
        if getattr(shape, "has_text_frame", False):
            for para in shape.text_frame.paragraphs:
                text = "".join(run.text for run in para.runs).strip()
                if text:
                    lines.append(text)
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                cells = [c.text.strip() for c in row.cells]
                cells = [c for c in cells if c]
                if cells:
                    lines.append(" | ".join(cells))
```

- [ ] **Step 4: 运行确认通过**

Run: `docker exec shilian-app pytest tests/unit/test_resume_extractor.py::TestPptxExtraction -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ai-interview-backend/app/services/client/resume_extractor.py ai-interview-backend/tests/unit/test_resume_extractor.py
git commit -m "feat(extractor): pptx 多页/表格/组形状递归提取"
```

---

### Task 6: 空文本统一报错

**Files:**
- Modify: `ai-interview-backend/app/services/client/resume_extractor.py`
- Test: `ai-interview-backend/tests/unit/test_resume_extractor.py`

- [ ] **Step 1: 追加失败测试**

在 `TestDocxExtraction` 类内追加：

```python
    def test_empty_docx_raises(self, tmp_path):
        from docx import Document
        p = tmp_path / "empty.docx"
        Document().save(str(p))
        with pytest.raises(ValidationError) as e:
            extract_resume_text(str(p), "empty.docx")
        assert "无法从文件中提取文本内容" in e.value.detail
```

- [ ] **Step 2: 运行确认失败**

Run: `docker exec shilian-app pytest tests/unit/test_resume_extractor.py::TestDocxExtraction::test_empty_docx_raises -v`
Expected: FAIL（当前 `extract_resume_text` 对空文本返回空串，未抛错）

- [ ] **Step 3: 在 extract_resume_text 末尾加空文本校验**

修改 `extract_resume_text`，在分派提取后加：

```python
    if not text.strip():
        raise ValidationError(message="无法从文件中提取文本内容")
    return text
```

即函数变为：

```python
def extract_resume_text(file_path: str, file_name: str) -> str:
    """按扩展名分派提取纯文本；先做 magic-byte 校验文件真实格式。"""
    validate_resume_ext(file_name)
    ext = Path(file_name).suffix.lower()
    _check_magic(file_path, ext)

    if ext == ".pdf":
        text = _extract_pdf_text(file_path)
    elif ext == ".docx":
        text = _extract_docx_text(file_path)
    else:
        text = _extract_pptx_text(file_path)

    if not text.strip():
        raise ValidationError(message="无法从文件中提取文本内容")
    return text
```

- [ ] **Step 4: 运行确认通过（含全量 extractor 回归）**

Run: `docker exec shilian-app pytest tests/unit/test_resume_extractor.py -v`
Expected: PASS（9 passed：TestValidateExt 3 + TestMagicByte 2 + TestDocxExtraction 3 + TestPptxExtraction 1）

- [ ] **Step 5: Commit**

```bash
git add ai-interview-backend/app/services/client/resume_extractor.py ai-interview-backend/tests/unit/test_resume_extractor.py
git commit -m "feat(extractor): 空文本统一报错"
```

---

### Task 7: resume_service 接入 extractor

**Files:**
- Modify: `ai-interview-backend/app/services/client/resume_service.py:12-14,69-77,103-119`
- Test: `ai-interview-backend/tests/unit/test_resume_service_upload.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_resume_service_upload.py`：

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `docker exec shilian-app pytest tests/unit/test_resume_service_upload.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.services.client.resume_extractor'` 或 `AttributeError`，因 resume_service 尚未调用 extractor / 无此名字）

- [ ] **Step 3: 改造 resume_service.py**

3a. 新增 import（在 `resume_service.py` 顶部 import 区，紧跟 `from app.models.resume import Resume` 后）：

```python
from app.services.client.resume_extractor import extract_resume_text
```

3b. 替换 `upload_and_parse` 中的提取段（原 69-77 行）：

```python
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
```

3c. 删除 `_extract_pdf_text` 私有方法（原 103-119 行整段）。

3d. 更新 `upload_and_parse` docstring 的 Raises 说明为 `ValidationError: 文件提取或 AI 解析失败`。

- [ ] **Step 4: 运行确认通过**

Run: `docker exec shilian-app pytest tests/unit/test_resume_service_upload.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 确认无残留引用**

Run: `docker exec shilian-app python -c "from app.services.client.resume_service import ResumeService; print('ok')"`
Run: `grep -rn "_extract_pdf_text" ai-interview-backend/app ai-interview-backend/tests`
Expected: import 输出 `ok`；grep 仅命中 `resume_extractor.py`（新迁移处），`resume_service.py` 不再有引用

- [ ] **Step 6: Commit**

```bash
git add ai-interview-backend/app/services/client/resume_service.py ai-interview-backend/tests/unit/test_resume_service_upload.py
git commit -m "refactor(service): resume_service 接入 extractor 统一提取"
```

---

### Task 8: 路由层两档校验

**Files:**
- Modify: `ai-interview-backend/app/api/client/v1/resume.py:8,24-32`

- [ ] **Step 1: 改造路由**

1a. 新增 import（`resume.py` 的 import 区）：

```python
from app.services.client.resume_extractor import validate_resume_ext
```

1b. 替换 24-27 行的文件类型校验块：

```python
    """上传简历（PDF / Word / PPT）并触发 AI 解析"""
    # 验证文件类型（两档提示：.doc/.ppt 另存为；其他不支持格式）
    validate_resume_ext(file.filename)
```

即删除原：

```python
    # 验证文件类型
    if not file.filename.lower().endswith(".pdf"):
        raise ValidationError(message="仅支持 PDF 格式文件")
```

同时更新函数 docstring（原 24 行 `"""上传简历 PDF 并触发 AI 解析"""`）。10MB 大小校验（30-32 行）保持不变。

- [ ] **Step 2: 验证**

Run: `docker exec shilian-app python -c "from app.api.client.v1.resume import router; print(len(router.routes))"`
Expected: 输出路由数（不报错，说明模块可导入）

- [ ] **Step 3: Commit**

```bash
git add ai-interview-backend/app/api/client/v1/resume.py
git commit -m "feat(api): resume 上传路由支持 docx/pptx 两档校验"
```

---

### Task 9: 前端上传页支持三格式

**Files:**
- Modify: `ai-interview-frontend/src/views/ResumeUpload.vue:9,11-12,238-242`

- [ ] **Step 1: 改 accept 与提示文案**

模板处：

- 第 9 行 `上传简历 (PDF)` → `上传简历 (PDF / Word / PPT)`
- 第 11 行 `accept=".pdf"` → `accept=".pdf,.docx,.pptx"`
- 第 12 行 `点击或拖拽上传 PDF 简历` → `点击或拖拽上传简历（PDF / Word / PPT，10MB 以内）`

- [ ] **Step 2: 加 SUPPORTED_EXTS 并更新校验函数**

在 `<script setup>` 内（`const error = ref('')` 附近）加常量：

```js
const SUPPORTED_EXTS = ['.pdf', '.docx', '.pptx']
```

替换第 238-242 行两个函数：

```js
function onFileChange(e) {
  const f = e.target.files[0]
  if (f && SUPPORTED_EXTS.some(ext => f.name.toLowerCase().endsWith(ext))) file.value = f
}
function onDrop(e) {
  const f = e.dataTransfer.files[0]
  if (f && SUPPORTED_EXTS.some(ext => f.name.toLowerCase().endsWith(ext))) file.value = f
}
```

- [ ] **Step 3: 前端构建验证**

Run: `cd ai-interview-frontend && npm run build`
Expected: `✓ built in ...`（无报错）

- [ ] **Step 4: Commit**

```bash
git add ai-interview-frontend/src/views/ResumeUpload.vue
git commit -m "feat(frontend): 上传页支持 PDF/Word/PPT 三格式"
```

---

### Task 10: 全量回归 + 收尾

**Files:** 无新增

- [ ] **Step 1: 全量 unit 测试**

Run: `docker exec shilian-app pytest -m "unit"`
Expected: 全绿（原 142 passed + 新增 12 项（extractor 9 + resume_service 3），共 154 passed，0 failed）

- [ ] **Step 2: 前端构建复验**

Run: `cd ai-interview-frontend && npm run build` 与 `cd ai-interview-admin && npm run build`
Expected: 均 `✓ built`

- [ ] **Step 3: 残留引用检查**

Run: `grep -rn "仅支持 PDF 格式文件\|无法从 PDF 中提取文本内容" ai-interview-backend/app ai-interview-backend/tests ai-interview-frontend/src`
Expected: 无命中（旧文案已全部替换）

- [ ] **Step 4: 更新 CLAUDE.md 当前状态**

在 `ai-interview-backend/` 上级 `CLAUDE.md`「当前状态」段追加一条完成记录（格式参照既有 2026-08-03 记录），并更新「待办」如涉及。设计/计划文档不 push 远程（仅本地 dev）。

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/superpowers/plans/2026-08-03-resume-format-support.md
git commit -m "docs: 简历支持 Word/PPT 格式完成记录 + 实施计划"
```
