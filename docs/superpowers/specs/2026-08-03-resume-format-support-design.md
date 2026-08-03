# 简历支持 Word / PPT 格式（.docx / .pptx）设计

> 日期：2026-08-03
> 状态：已确认
> 范围：简历上传从仅 PDF 扩展为 PDF + Word (.docx) + PPT (.pptx)；老式 .doc/.ppt 不直接支持，给出友好提示

## 背景与目标

当前简历上传仅接受 `.pdf`（`resume.py:26-27` 按后缀校验），解析用 `pdfplumber` 纯文本提取（`resume_service.py:103-119`），无 OCR。用户实际简历常以 Word/PPT 形式存在，需支持现代 OOXML 格式。

**目标**：`.docx` / `.pptx` 简历可上传并解析，产出与 PDF 一致的纯文本进入既有 AI 解析链路；老式 `.doc` / `.ppt` 给出友好提示。

**非目标**（显式排除）：
- 老式 `.doc` / `.ppt`（需 LibreOffice/antiword，对 2GB ECS 是负担）——提示用户另存为现代格式
- 扫描版 / 图片型内容 OCR（PaddleOCR 等）——后续独立二期
- 后台知识库 PDF 解析（`knowledge_service.py:34-36`）本次不动

## 决策

| 决策点 | 结论 |
|--------|------|
| 格式范围 | 仅 `.pdf` + `.docx` + `.pptx`（现代 OOXML，纯 Python 库可解析） |
| docx 提取深度 | 全面提取：段落 + 表格 + 页眉页脚 + 文本框（走 OOXML `w:txbxContent`） |
| 实现方式 | 独立解析模块 `app/services/client/resume_extractor.py`（方案 A），不内联进 resume_service |
| 解析库 | `python-docx` + `python-pptx`（纯 Python，自带 lxml wheel，镜像体积增加极小） |
| pptx 备注 | 跳过演讲者备注（避免噪声干扰 LLM 解析） |
| 安全 | 分派前做 magic-byte 校验（PDF 以 `%PDF-` 开头，docx/pptx 以 `PK` zip 头开头） |

## 架构

新增 `app/services/client/resume_extractor.py`（非 AI 业务工具，与 `resume_service.py` 平级）：

```
resume_extractor.py
├── SUPPORTED_EXTS = {".pdf", ".docx", ".pptx"}        # 唯一允许清单
├── extract_resume_text(file_path, file_name) -> str   # 统一入口，按后缀分派
│   ├── magic-byte 校验：PDF %PDF- / docx、pptx PK 头
│   ├── 后缀不在清单 → ValidationError("仅支持 PDF / Word (.docx) / PPT (.pptx) 格式文件")（.doc/.ppt 的"另存为"提示在路由层）
│   └── magic-byte 与后缀不符 → ValidationError("文件内容与格式不匹配")
├── _extract_pdf_text()      # 现有 pdfplumber 逻辑迁入
├── _extract_docx_text()     # 全面提取（python-docx）
└── _extract_pptx_text()     # 逐页提取（python-pptx）
```

`resume_service.py` 删除私有 `_extract_pdf_text()`（约 17 行），`upload_and_parse()` 改调 `extract_resume_text(file_path, file_name)`。

## 组件细节

### docx 全面提取（_extract_docx_text）

- 正文段落：`doc.paragraphs` 非空段落
- 表格：`doc.tables` 逐行 `" | ".join(非空单元格)`
- 页眉页脚：各 section 非空段落
- 文本框：`body.iter(qn('w:txbxContent'))` 遍历其中所有 `w:t` 文本节点
- 拼接为纯文本，与 PDF 流程产出结构一致（供 AI 解析）

### pptx 提取（_extract_pptx_text）

- 逐页遍历 shapes：
  - 文本框架：`shape.has_text_frame` → 按段提取 run 文本
  - 表格：`shape.has_table` → 逐行 `" | ".join(cell.text)`
  - 组形状：递归下钻 `group.shapes` 防漏嵌套
- 跳过备注（`slide.notes_slide` 不读取）
- 各页内容以空行分隔

### 提取为空

合法格式但提取空文本（如全图型）→ `ValidationError("无法从文件中提取文本内容")`，与现有 PDF 空文本行为一致。

## 改动清单

| 文件 | 改动 |
|------|------|
| `app/services/client/resume_extractor.py` | 新增，上述架构 |
| `app/services/client/resume_service.py` | 删 `_extract_pdf_text`，改调 `extract_resume_text` |
| `app/api/client/v1/resume.py:26-32` | 两档校验：`.pdf/.docx/.pptx` 通过；`.doc/.ppt` → "请另存为"提示；其他后缀 → "仅支持…"提示；10MB 不变 |
| `ai-interview-frontend/src/views/ResumeUpload.vue:11,239-242` | `accept=".pdf,.docx,.pptx"`；onDrop 三格式校验；提示文案 |
| `requirements.txt` | 新增 `python-docx`、`python-pptx` |

## 错误处理矩阵

| 场景 | 行为 |
|------|------|
| `.doc` / `.ppt` | 路由层：`ValidationError("不支持 .doc/.ppt 格式，请将文档另存为 .docx 或 .pptx 后上传")` |
| 其他未知后缀 | 路由层：`ValidationError("仅支持 PDF / Word (.docx) / PPT (.pptx) 格式文件")`；extractor 层同款兜底（防御非路由调用） |
| magic-byte 与后缀不符 | `ValidationError("文件内容与格式不匹配")` |
| 合法格式但提取为空 | `ValidationError("无法从文件中提取文本内容")` |
| 超过 10MB | 现有路由限制不变 |
| 文件损坏 / 解压失败 | 复用现有 `except Exception` 兜底 → status=failed |

## 测试

新增 `tests/test_resume_extractor.py`（`pytest -m "unit"`）：

1. **docx 全面提取**：python-docx 构造段落+表格，手写 OOXML 注入文本框，断言三类内容全部提取且顺序正确
2. **pptx 提取**：python-pptx 构造 2 页（含表格页），断言逐页内容与表格拼接
3. **pptx 组形状递归**：嵌套 group shape，断言内层文本被提取
4. **magic-byte 拒绝**："PDF 后缀但 zip 内容" → 报"文件内容与格式不匹配"
5. **空文本**：全空白文档 → 报"无法从文件中提取文本内容"
6. **老式格式**：`.doc` 后缀 → 报友好提示

迁移后 grep 确认无残留引用 `_extract_pdf_text`；现有 resume 相关测试同步调整。

## 不做的事（YAGNI）

- 不引入 docx2txt / mammoth / textract（文本框覆盖弱或重依赖）
- 不改 `knowledge_service.py` 的后台 PDF 解析
- 不做 .doc/.ppt 转换支持
- 不做扫描件 OCR
