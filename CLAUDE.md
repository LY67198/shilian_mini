# CLAUDE.md

> ⚠️ **本仓库是 `D:\ai-interview-agent\ai-interview-agent` 的复刻版本，专用于云服务器部署。**
>
> 原仓库保留本地开发环境（完整 7 容器：app + celery worker/beat + postgres + redis + milvus + minio + nginx + flower）。
>
> 本复刻版针对**阿里云 99 元基础版**（2 vCPU / 2 GiB / 40 GiB ESSD Entry / 3 Mbps 固定带宽）做了精简：
>
> | 调整项 | 原版 | 复刻版 |
> |---|---|---|
> | 向量库 | Milvus standalone（~1.5 GB RAM） | **pgvector**（已含在 `pgvector/pgvector:pg16` 镜像中，零额外进程） |
> | MinIO | 独立容器（仅 Milvus 用） | **删除** |
> | Nginx | 反向代理容器 | **保留**（反向代理 + 静态文件托管，端口 80） |
> | Celery beat | 独立容器 | **并入 app 容器**（`worker --beat` 内嵌） |
> | Celery worker | 独立容器 | **并入 app 容器** |
> | Flower | monitoring profile 可选 | **删除** |
> | Whisper | 音频转录依赖（~150 MB + ffmpeg/torch） | **删除**（非核心面试流） |
> | 前端部署 | nginx 托管 dist | **Nginx 容器托管 dist**（本地 build → scp 上传） |
> | 目标内存预算 | ~3 GB（OOM on 2 GiB） | **~2 GB 紧贴上限** |
>
> **不要把原版的新功能直接 merge 过来**——Milvus 相关代码（`app/vector_db/` Milvus 部分、`app/retrieval/vector.py` 的 Milvus 实现）在本仓库已被 pgvector 替代，merge 会导致启动缺依赖。
>
> **Phase 5（Milvus→pgvector 迁移）已于 2026-07-22 完成**，共计 20 个 commit 推送到 `dev`。代码中 `pymilvus` 依赖已移除，`MilvusClient` 全部替换为 `AsyncSession`，向量检索走 PostgreSQL pgvector HNSW 索引。
>
> 复刻日期：2026-07-22。原仓库快照版本：`Phase 4 完成` 状态。

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

**试炼（MockPilot）** — 企业级 AI 模拟面试平台。围绕"简历解析 → 岗位匹配 → RAG 题库出题 → 多轮 LangGraph 面试 → AI 评分 → 结构化报告"构建的完整求职训练闭环。站在候选人立场，强调"反复练习锻造面试能力"。

> **2026-08-01 Agent 正名**：`app/agents/` 目录已删除。原 BaseAgent/EvaluatorAgent/ReportAgent/QuestionAgent 四个类并非真 Agent（无工具调用、无 ReAct 循环），已内联到各 workflow node 中直接使用 `load_prompt() + get_chat_llm() + with_structured_output()`。全仓库唯一使用 LangChain `create_agent()` 的是 `position_agent_service.py`，但该 Agent 的 system prompt 锁死了工具调用顺序（固定 DAG），后续应拆 Agent 壳改为显式调用。

技术栈：FastAPI + LangGraph + LangChain + pgvector + PostgreSQL + Redis + DeepSeek + DashScope。

**面试准备文档**：`面试准备-LangGraph与RAG核心实现.md` — 覆盖 LangGraph 多轮编排、RAG 混合检索管线、Agent 层设计、人工确认（HITL）全链路交互、6 个核心设计决策及面试金句、3 组面试官追问预案、14 道模拟面试题。

**面试模拟练习**：`docs/面试模拟记录.md` — 每日角色扮演练习日志。用户与 AI 互相切换面试官/面试者身份进行模拟面试，纯文字叙述（不打图不用代码块），每次练习后复盘。触发词包括"面试"、"考我"、"模拟面试"等。

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI 0.115 + SQLAlchemy 2.x (async) + Alembic |
| 业务数据库 | PostgreSQL 16（结构化数据 + JSONB） |
| 向量数据库 | **pgvector**（PostgreSQL 扩展，`pgvector/pgvector:pg16` 镜像），HNSW L2 索引，embedding 维度 1024 |
| 缓存/队列 | Redis 7 + Celery 5.5（异步任务、Beat 调度，内嵌在 app 容器） |
| 反向代理 | Nginx（端口 80，反向代理 + SPA 静态文件托管） |
| AI 模型 | DeepSeek（OpenAI 兼容 SDK 2.x）、DashScope Embedding (text-embedding-v3, 已归一化) |
| Agent 框架 | LangChain 1.3 + **LangGraph 1.2.8**（Postgres checkpoint 3.1.0 已装） |
| 测试 | pytest 8.3 + pytest-asyncio + httpx |
| 前端 | Vue 3.4 + Vite 5.4 + Pinia 2.1 + Vue Router 4.3 |
| 部署 | Docker Compose（4 容器 lite 栈），外部端口 80 |

## 目录结构

```
ai-interview-agent/
├── ai-interview-backend/          # FastAPI 后端（容器化）
│   ├── app/
│   │   ├── api/                   # 接口模块（client/backoffice 拆 v1）
│   │   ├── route/                 # 路由注册中心（router_registry + create_app）
│   │   ├── core/                  # config / celery_app / security / log_config
│   │   ├── configs/               # 双 Swagger 应用拆分
│   │   ├── db/                    # SQLAlchemy async session
│   │   ├── models/                # SQLAlchemy 模型（**embedding 列用 pgvector Vector(1024)**）
│   │   ├── schemas/               # Pydantic 请求/响应模型
│   │   ├── services/              # 业务逻辑（auth/email/redis_verification 等）
│   │   ├── llm/                   # 🆕 LangChain 原子能力（LLM/Embedding/Prompt 工厂）
│   │   ├── prompts/               # 🆕 PromptTemplate YAML 集中管理（8 个文件）
│   │   ├── repositories/          # 🆕 Repository Pattern 数据访问层
│   │   ├── retrieval/             # 🆕 Phase 3 RAG 混合检索管线
│   │   │   ├── vector.py          # pgvector HNSW L2 向量检索（AsyncSession）
│   │   │   ├── bm25.py            # BM25 倒排索引（character bigrams）
│   │   │   ├── bm25_lifecycle.py  # BM25 索引生命周期管理
│   │   │   ├── rrf.py             # RRF 倒数秩融合
│   │   │   ├── rerank.py          # Cross-Encoder 重排序（qwen3-rerank）
│   │   │   └── pipeline.py        # RetrievalPipeline 统一入口
│   │   ├── workflows/             # 🆕 LangGraph 编排层
│   │   │   ├── _shared/           # 共享基础设施（checkpointer/state/tracing/llm/tools/sse）
│   │   │   │   └── sse.py         # astream_to_sse() LangGraph → SSE 封装
│   │   │   ├── interview/         # ✅ Phase 2 面试评估（HITL StateGraph）
│   │   │   │   ├── nodes/         # fetch_context / retrieve_knowledge / evaluate / check_finished / ask_question / generate_report
│   │   │   │   ├── state.py       # InterviewState + ScoreResult
│   │   │   │   ├── graph.py       # build_interview_graph() + get_compiled_graph()
│   │   │   │   └── service.py     # submit_answer() 单一入口（模块级函数）
│   │   │   └── retrieval_check/   # 🆕 Phase 3 检索自检循环（retrieve → check → rewrite query）
│   │   ├── deps.py                # 🆕 Phase 4 统一 Depends 工厂（service）
│   │   ├── vector_db/             # ✅ Phase 5: pgvector 向量库（AsyncSession 替代 MilvusClient）
│   │   │   ├── client.py          # health_check_vector() pg 扩展检测
│   │   │   ├── index.py           # L2 distance → similarity 转换 + EMBEDDING_DIM=1024
│   │   │   └── collections/
│   │   │       ├── knowledge.py   # upsert/search/delete（pgvector AsyncSession）
│   │   │       └── question_bank.py # search/insert（含 is_active + position_tag 过滤）
│   │   ├── common/                # language / release / json_utils
│   │   ├── exceptions/            # APIException / 错误码
│   │   ├── utils/                 # 通用工具
│   │   └── schedule/              # Celery 定时任务
│   ├── tests/                     # pytest 测试
│   ├── eval/                       # 🆕 Phase 3 RAGAS 评估框架
│   │   ├── golden_set.json         # 手工标注评测集（10 条，标注待补全）
│   │   ├── reports/                # baseline-{date}.json 评估报告
│   │   └── scripts/                # upload_golden_set.py / eval_ragas.py
│   ├── migrations/                # Alembic（含 a2b3c4d5e6f70001: add pgvector embedding columns + HNSW 索引）
│   ├── scripts/
│   │   ├── create_first_admin.py
│   │   ├── seed_position_templates.py
│   │   ├── seed_question_bank.py  # 🆕 题库种子数据（28 题，3 岗位）
│   │   ├── seed_knowledge.py      # 🆕 知识库种子数据（4 文档，Markdown）
│   │   └── deploy.sh
│   ├── start.sh                    # ✅ Phase 5: app + 内嵌 celery 启动脚本
│   ├── nginx/                      # ✅ Phase 5: Nginx 反代 + SPA 静态文件配置
│   │   └── nginx.conf
│   └── docker-compose.lite.yml     # ✅ Phase 5: 4 容器精简栈（app/pgvector/redis/nginx）
├── ai-interview-frontend/         # 用户端（dist/ 不上 git，本地 build → scp 上传）
└── ai-interview-admin/            # 管理端（base: '/admin/'，dist/ 不上 git）
├── ai-interview-frontend/         # 用户端 (localhost:3000 → :8006)
└── ai-interview-admin/            # 管理端 (localhost:3001 → :8006)
```

## 分支策略

| 分支 | 用途 | 谁推 |
|------|------|------|
| `main` | **发布版**（已发布、公开可见） | 仅在显式指定时推 |
| `dev` | **重构 / 新功能开发**（默认） | 日常开发默认推这里 |

**约定**：
- 重构 / Phase 1-4 实施 / bug 修复 → 全部推 `dev`
- 推 `main` 必须显式说"推 main"（或 `git push main` 显式命令）
- 默认 `git push`（无参数）只推当前分支

`.git/config` 已配置：
- `branch.dev.remote = github`（默认 push 目标）
- `push.default = current`（只推当前分支到同名远端分支）

**未公开的内部文档**（CLAUDE.md / `docs/` / 部署教程 / 各种规划 md）通过 `git add -f` 强制提交到 dev，但**永远不进 main**。



## 核心约束

- **必须** 有 DeepSeek API Key 和 DashScope API Key 才能运行
- **必须** 通过 Docker 启动后端（本地用 `docker-compose.yml` + `docker-compose.dev.yml`；部署用 `docker-compose.lite.yml` 4 容器栈）
- 本地开发：后端跑在 Docker 容器中，前端跑在 Windows 本地；部署：Nginx 托管前端 dist
- 本地开发两个前端 `vite.config.js` proxy 都指向 `http://localhost:8006`
- **embedding 维度 1024 不可随意修改**（与 pgvector HNSW 索引绑定）
- **embedding 存 PostgreSQL pgvector**（Phase 5 迁移完成；旧 Milvus/MinIO 依赖已移除）
- **JWT 区分 scope**：client / backoffice 两套 token
- **Swagger 双套**：`/client/docs`（无需认证）、`/backoffice/docs`（需 JWT）
- **路由统一注册**：所有路由通过 `app/route/router_registry.py` 集中管理
- **测试在容器内跑**：dev compose 把 `./tests` 和 `./pytest.ini` 挂进 `shilian-app`

## LangChain / LangGraph 分工（硬性规则）

| 层级 | 职责 | 位置 |
|------|------|------|
| **LangChain**（原子能力） | LLM / Embedding / @tool / Message / PromptTemplate | `app/llm/`, `app/prompts/` |
| **LangGraph**（编排） | 多步骤流程、StateGraph、Agent 决策、条件分支、Checkpointer | `app/workflows/` |
| **Node 纯函数** | 业务逻辑，可独立测试，调用 repo + llm | `app/workflows/<name>/nodes/` |
| **Repository** | 纯 DB CRUD，不调 LLM / 向量库 / HTTPException | `app/repositories/` |

**禁止**：
- ❌ 业务代码里直接 `from openai import OpenAI` — 用 `app.llm.get_chat_llm()`
- ❌ Prompt 写在 Python 代码里 — 用 `app.prompts/*.yaml` + `load_prompt()`
- ❌ 业务 service 里手动管 LangGraph state — 让 `app/workflows/_shared/` 接管
- ❌ 节点函数里直接写 SQL — 用 `app/repositories/`

**新功能必须**写在 `app/workflows/<name>/`（graph + nodes + service），不写在 `app/services/`（除 `auth/`、`email/` 等非 AI 业务）。

**Phase 2 重构硬性规则**：
- ❌ 禁止新增 `@staticmethod` — 新 service 用实例方法 + FastAPI Depends 注入
- ❌ 禁止手写 `yield f"data: {json.dumps(...)}\n\n"` — SSE 用 `sse-starlette` 或 `StreamingResponse` 封装
- ❌ 禁止 `re.search(r'\{.*"score".*\}', text)` 从 LLM 输出抠 JSON — 用 `with_structured_output(PydanticModel)` 拿强类型结果
- ❌ 禁止 `submit_answer(stream=False)` 和 `submit_answer_stream` 分两个方法 — 合并为 `submit_answer(stream: bool = False)`
- ❌ 禁止手写 `yield f"data: {json.dumps(...)}\n\n"` — SSE 用 `app/workflows/_shared/sse.py:astream_to_sse()` 封装
- ❌ 禁止 `isinstance(llm_output, AIMessage)` 后取 `.content` 再 `json.loads()` — 评分节点用 `with_structured_output(PydanticModel)` 拿强类型结果
- ❌ 禁止 `_extract_json` 抛 `ValueError` — 已改为返回带 `parse_failed: True` 的 fallback dict

## pgvector 向量库使用规则

- **唯一入口**：`app/vector_db/collections/{knowledge, question_bank}.py`（所有函数接收 `session: AsyncSession`，不再用 `MilvusClient`）
- **距离度量：L2**（pgvector HNSW `vector_l2_ops`）
- **DashScope embedding 已归一化**：L2 distance 对归一化向量等价于 cosine 距离
  - `similarity = 1 - distance/√2`（distance=0 → sim=1，distance=√2 → sim=0）
  - 实现见 `app/vector_db/index.py:l2_distance_to_similarity()`
- **查询语法**：`KnowledgeChunk.embedding.l2_distance(query_vector).label("distance")` → `.order_by(KnowledgeChunk.embedding.l2_distance(query_vector))`
- **不需要 flush/load**：PostgreSQL 事务保证写入立即可见，不像 Milvus 需要显式 flush
- **ID 与 PostgreSQL 同源**：`knowledge_chunks.id` / `question_bank.id` 由 PG auto-increment 生成；召回结果可直接 JOIN 到 PG 元数据表
- **health_check**：`SELECT 1 FROM pg_extension WHERE extname='vector'` 验证 pgvector 扩展已加载

## 常用命令

> ⚠️ 容器名从 `ai-interview-*` 重命名为 `shilian-*`（2026-07-10 品牌升级）

```bash
# === 本地开发（Windows + Docker Desktop，完整 7 容器）===
cd ai-interview-backend
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build

# === 部署（2GB ECS，4 容器 lite 栈）===
cd ai-interview-backend
docker compose -f docker-compose.lite.yml up -d --build

# 数据库初始化（开发和生产通用）
docker exec shilian-app alembic upgrade head
docker exec shilian-app python scripts/create_first_admin.py
docker exec shilian-app python scripts/seed_position_templates.py

# 种子数据（题库 28 题 + 知识库 4 文档，向量化写入 pgvector）
docker exec shilian-app python scripts/seed_question_bank.py
docker exec shilian-app python scripts/seed_knowledge.py

# 验证 pgvector 扩展
docker exec shilian-postgres psql -U demo -d ai_interview -c "SELECT extname FROM pg_extension WHERE extname='vector';"

# 验证后端健康
curl http://localhost:8006/api/v1/config/health    # 本地开发
curl http://localhost/api/v1/config/health          # 部署（Nginx → 80）

# Swagger 文档
# 用户端：http://localhost:8006/client/docs
# 管理端：http://localhost:8006/backoffice/docs

# 测试（容器内）
docker exec shilian-app pytest -m "unit"          # 仅单元测试
docker exec shilian-app pytest -m "smoke"         # 冒烟测试
docker exec shilian-app pytest tests/test_xxx.py  # 单文件
docker exec shilian-app pytest -m "integration"   # 集成测试
RUN_E2E=1 docker exec -e RUN_E2E=1 shilian-app pytest -m "e2e"

# 前端（本地开发）
cd ai-interview-frontend && npm install && npm run dev   # → localhost:3000
cd ai-interview-admin && npm install && npm run dev       # → localhost:3001

# 前端（部署 build）
cd ai-interview-frontend && npm run build   # → dist/
cd ai-interview-admin && npm run build       # → dist/

# 管理端登录：admin@ai-interview.com / LY1234567890
```

## 当前状态

### 已完成
- 项目可在 Windows + Docker Desktop 上完整运行（完整 7 容器栈）
- **部署栈：4 容器 lite**（`shilian-app` + `shilian-postgres`/pgvector + `shilian-redis` + `shilian-nginx`），目标 2GB ECS
- 数据库迁移完成，管理员和 8 个岗位模板已初始化
- **pgvector 已上线**：`knowledge_chunks` + `question_bank` embedding 列（Vector(1024)），HNSW L2 索引就绪
- **Phase 5（2026-07-22）：Milvus→pgvector 迁移完成**（20 commits，pymilvus 移除，MilvusClient→AsyncSession，pgvector HNSW 索引）
- **Service 层全部迁 pgvector**（knowledge_service / question_bank_service / interview_service / eval_ragas.py）
- 端到端验证：创建题目 → pgvector 向量召回命中 ✅
- 两套 RAG：题库 RAG + 知识库 RAG，混合检索（vector + BM25 → RRF → rerank）已在 lifespan 启动时自动构建 BM25 索引并激活（**2026-08-01 P0-2 已修复**，Knowledge 32 文档 / QuestionBank 84 文档）
- 岗位匹配 Agent 5 个工具链：`get_parsed_resume → build_candidate_profile → match_positions → get_position_interview_focus → start_mock_interview`
- LangChain 升级完成 (0.3.x → 1.x)：`create_agent()` 重写，`openai` 升级 2.x
- **LangGraph 1.2.8 + langgraph-checkpoint-postgres 3.1.0 已装**（PostgresSaver 持久化 LangGraph state）
- 路由重构为注册中心：`router_registry.py` 集中管理所有 RouteConfig
- Swagger 双套：client/backoffice 独立，OpenAPI JSON 可导出
- 测试体系：pytest 8.3 + 5 个测试文件，unit/smoke/integration/e2e/slow 五类 marker
- 生产 compose 已含 Nginx（lite 栈反向代理 + 静态文件托管）
- 验证码服务 (`redis_verification.py`) + 候补名单 (`waiting_list`)，客户端/后台均挂路由
- 面试会话消息独立建模（`interview_message`），支撑多轮上下文
- 品牌升级："AI Interview" → "试炼 (MockPilot)"，容器名 / PROJECT_NAME / 前端 title 全部更新
- **Phase 1 基础设施完成**：LLM 工厂 / Prompt YAML / Repository 骨架 / workflows/_shared 全部就位（详见下方"Phase 1 实施记录"）
- **Phase 2 实施完成**（2026-07-10）：核心面试 LangGraph 化 + 4 个 P0 bug 修复 + YAML prompt 激活 + JSON 解析兜底
- **Phase 3 完成**（2026-07-11）：RAG 管线升级 Spec + 16 任务全部实施完成。含 app/retrieval/ 模块（vector + BM25 + RRF + rerank + pipeline）、app/workflows/retrieval_check/ 自检循环、eval/ 评估框架（golden set + RAGAS）
- **过度封装清理**（2026-07-11）：代码审计，删除/简化 6 项过封装类 + 1 项死代码。净删 ~140 行，测试 51/51 pass
- **技术债清理**（2026-07-11）：一次性解决 12 项已知技术债，10 项已修 / 2 项跳过，净删 ~500 行，测试 32/32 pass
- **代码文档化**（2026-07-11）：核心模块 53 个文件全部补全 Google 风格中文 docstring（Args:/Returns:），~49 处新增
- **项目文档更新**（2026-07-11）：工程化能力.md / 面试要点.md / 项目RAG实现原理.md / AI面试项目问答清单.md / AI应用工程化能力.md / 部署教程.md 6 份文档同步到 Phase 4 状态
- **P0 Critical Bug 修复**（2026-08-01）：P0-1（报告评分写回 DB）+ P0-2（BM25 索引构建 + PG id 修复），6 commits / 6 files / +263 -86，35/35 unit tests pass，smoke test 验证 BM25 Knowledge 32 + QuestionBank 84 文档已构建、search 返回 PG 主键。详见 `docs/superpowers/specs/2026-08-01-p0-critical-bugs-design.md`
- **部署文档**（2026-07-22）：`docs/零基础部署教程.md`（1018 行覆盖 Part 0 概念 → Part 12 日常运维，含 8 个实战坑 + 加功能改代码完整流程）+ `docs/部署runbook-2gb单机.md`（运维 runbook），均为本地 gitignore 保护
- **首次云端部署完成**（2026-07-22）：阿里云 ECS（Ubuntu 22.04 / 2 vCPU / 2 GiB / 40 GiB / 3 Mbps）跑通完整 4 容器 lite 栈，`http://123.56.239.7/` 已可访问；域名 `shilian.asia` 已解析但因未 ICP 备案被阿里云拦截入口（**临时方案**：用 IP 访问；备案下来后改回域名）
- **部署实战 6 项修复**（2026-07-22）：
  - `app/models/admin.py` — `SAEnum(UserRole, create_type=True)` 改为 `+ values_callable=lambda x: [e.value for e in x]`，修复 Enum name/value 不匹配数据库 enum（`superadmin` vs `SUPERADMIN`）导致 `create_first_admin.py` 报 `invalid input value for enum userrole` 的 P0 bug
  - `main.py` — `workers=4` → `workers=1`（2GB ECS 4 worker 必 OOM，已验证；升级内存后再调回）
  - `docker-compose.lite.yml` — 需配合宿主机先 `mkdir -p ./logs ./uploads/avatars && chmod 777`（容器内 `os.makedirs` 在挂载卷上写不进去，log handler 报 `Unable to configure handler 'file'`）
  - `.env` 模板 — 生产模式必须配置 `CORS_ORIGINS=http://shilian.asia,...`，否则启动直接 `RuntimeError` 退出（已有，但需在部署 runbook 中明确）
  - `app/api/client/v1/auth.py` 等 — 创建上传目录时增加 `exist_ok=True`（已），但宿主目录权限也得对（操作侧修复）
  - 部署教程 — 新增 Part 10 8 个实战坑 + Part 11 加功能改代码全流程，覆盖二次开发的所有场景
- **GitHub 仓库**：`https://github.com/LY67198/shilian_mini.git`（`origin`，默认推 `dev` 分支）
- **面试准备文档**（2026-07-11）：`面试准备-LangGraph与RAG核心实现.md` — 9 大章节，覆盖 LangGraph 多轮编排 + RAG 混合检索管线 + Agent 层 + Repository Pattern + 6 个核心设计决策及面试话术 + 简历流程与代码对照 + 人工确认全链路详解 + 3 组面试官追问预案 + 14 道模拟面试题 + 关键代码速查表
- **前端 SSE 流式解析修复**（2026-07-11）：`interview.js:submitAnswerStream()` 只认 `data.type` 字段，但后端用 SSE `event:` 头。改为解析 `event:` 行 + 按后端事件类型路由（chunk → onChunk, score/next_question → 累积, done → onDone 合并）。修复后面试提交回答后 AI 反馈能实时流式显示，不再卡住。
- **Milvus KnowledgeChunkPayload.metadata 修复**（2026-07-11）：`metadata` 字段默认值从 `None` 改为 `Field(default_factory=dict)`，解决 Milvus JSON 字段拒绝 `None` 值的问题。
- **种子数据补齐**（2026-07-11）：题库从空的 0 题扩充到 28 题（python_backend 10 / java_backend 9 / vue_frontend 9），知识库从空的 0 文档扩充到 4 篇 Markdown 文档（16 chunks），全部已向量化写入 pgvector。面试出题不再每次走纯 AI 生成兜底。
- **管理员密码重置**（2026-07-11）：`admin@ai-interview.com` 密码从 `ai-interview&admin` 改为 `LY1234567890`，旧密码有 `&` 符号易在 shell 中被误解析。
### 重构路线（4 个 Phase）

> 路线图于 2026-07-10 重排，详见 `docs/superpowers/specs/2026-07-10-phase-2-4-roadmap-redesign.md`

- [x] **Phase 0** — Milvus 迁 service + 导出名修正（P0-1/P0-7 已完成；P0-2~P0-6 4 个 bug 遗留至 Phase 2，见下方"P0 遗留"）
- [x] **Phase 1** — 基础设施：prompt 外置 yaml + LLM 工厂 + repository 拆分 + workflows/_shared（已完成）
- [x] **Phase 2** — 核心面试 LangGraph 化（**已完成 2026-07-10**）：HITL StateGraph + 去重 submit_answer + 去正则 score + 基于结构化输出 + SSE 标准化 + ai_service 切 YAML + JSON 解析兜底 + P0-2/3/4/6 已修
- [x] **Phase 3** — RAG 管线升级：Golden set + RAGAS baseline → BM25 + RRF + qwen3-rerank + 自检循环 LangGraph（**已完成 2026-07-11**）
  - ✅ 设计 Spec：`docs/superpowers/specs/2026-07-10-phase-3-rag-upgrade-design.md`
  - ✅ 实施计划：`docs/superpowers/plans/2026-07-10-phase-3-rag-upgrade-implementation.md`（16 tasks，20 new files + 4 modified + 2 test files）
  - 技术选型：rank-bm25 (character bigrams) + DashScope gte-rerank + RAGAS 5 metrics
  - ✅ Tasks 1-7 — `app/retrieval/` 模块完成（SearchResult + vector + BM25 + RRF + rerank + pipeline）
  - ✅ Task 8 — Self-check YAML prompts（2 个）
  - ✅ Task 9 — RetrievalCheckState
  - [x] Tasks 10-16 — self-check nodes + graph + 集成 + eval + 测试（完成 2026-07-11）
- [x] **Phase 4** — 多 Agent + 可观测性（**已完成 2026-07-11**）：Agent 类提取 + Repository 补全 + RAG trace + RAGAS LangSmith + service 去 static
- [x] **Phase 5** — Milvus→pgvector 迁移 + 4 容器 lite 部署栈（**已完成 2026-07-22**）：
  - ✅ `requirements.txt`：`pymilvus==2.4.10` → `pgvector==0.3.6`
  - ✅ `models/`：knowledge_chunks + question_bank 添加 `Vector(1024)` embedding 列
  - ✅ Alembic 迁移：`CREATE EXTENSION vector` + `add_column embedding Vector(1024)` + HNSW `vector_l2_ops` 索引
  - ✅ `vector_db/collections/`：`MilvusClient` → `AsyncSession`，全部函数改为 async
  - ✅ `vector_db/client.py`：`get_milvus_client()` → `health_check_vector(session)` pg 扩展检测
  - ✅ `vector_db/index.py`：精简为 `EMBEDDING_DIM=1024` + `l2_distance_to_similarity()`
  - ✅ `retrieval/vector.py` + `pipeline.py`：`MilvusClient` → `AsyncSession`
  - ✅ 4 个 service 文件：移除 `get_milvus_client()`，改用现有 session
  - ✅ `eval/scripts/eval_ragas.py`：同步改为 AsyncSession
  - ✅ `Dockerfile` → `start.sh`：celery worker+beat 内嵌启动
  - ✅ `docker-compose.lite.yml`：4 容器（app 1200m / postgres 400m / redis 160m / nginx 64m）+ healthcheck + 日志轮转
  - ✅ 3 个 legacy compose 文件：清除 milvus/minio/celery-worker/celery-beat/flower 服务定义
  - ✅ 10 个文件 docstring："Milvus"→"pgvector"
  - ✅ 前端：admin `base: '/admin/'`，两个前端 `dist/` gitignore
  - ✅ `nginx/nginx.conf`：反向代理 `/api/` + SPA 静态托管 + SSE `proxy_buffering off`
  - ✅ `scripts/deploy.sh`：默认 `docker-compose.lite.yml`
  - ✅ `docs/零基础部署教程.md`：~1100 行零基础手把手教程 + runbook
  - ✅ `README.md`：同步更新为 pgvector 4 容器栈
  - ✅ 20 个 commit 推送到 `dev` on `shilian_mini`
- [x] **Phase 6** — 首次云端部署实战（**已完成 2026-07-22**）：
  - ✅ 阿里云 ECS（Ubuntu 22.04 / 2 vCPU / 2 GiB / `123.56.239.7`）+ 域名 `shilian.asia` 解析到位
  - ✅ 4 容器 lite 栈跑通，`http://123.56.239.7/api/v1/config/health` 返回 `200 OK / all services up`
  - ✅ 数据库 8 岗位模板 + 28 题 + 4 文档全部种子化（pgvector embedding 落库）
  - ✅ 部署实战发现并修复 6 项问题（见上方"部署实战 6 项修复"）
  - ⚠️ **域名未 ICP 备案，临时用 IP 访问；正式上线需走 `beian.aliyun.com` 备案（约 1-20 工作日）**
  - ✅ 重写 `docs/零基础部署教程.md`（1018 行 / 32 KB），从 Docker 反向代理概念 → 加功能改代码全链路，小白可读

### Phase 1 实施记录

**新增目录**：
- `app/llm/` — LangChain 原子能力层
  - `client.py` — `get_chat_llm()` 工厂 + `chat_completion()` 带重试
  - `embedding.py` — DashScope embedding（从 services/common 迁来）
  - `prompts.py` — YAML PromptTemplate 加载器
- `app/prompts/*.yaml` — 8 个 prompt 外置（position_agent_system / resume_parse / resume_analyze / question_generate / question_select / question_seed / evaluate_answer / generate_report）
- `app/repositories/` — Repository Pattern 骨架
  - `base.py` — `BaseRepository[T]` 泛型基类（仅提供 `get_by_id`，其余 CRUD 方法已移除）
  - `interview_repo.py` — 首个 repo（list_by_user / list_messages / get_active_question）
- `app/workflows/_shared/` — LangGraph 编排共享设施
  - `llm.py` / `checkpointer.py`（AsyncPostgresSaver 单例）/ `state_base.py` / `tracing.py` / `format_exception.py` / `tools.py`

**修改**：
- `position_agent_service.get_llm()` 委托 `app.llm.get_chat_llm`
- `ai_service._chat / _chat_stream` 委托 `app.llm.chat_completion`
- `services/common/embedding.py` 改为 deprecation stub
- CLAUDE.md 新增"LangChain / LangGraph 分工（硬性规则）"段

### Phase 2 实施记录

**P0 Bug 修复**：
| Bug | 修改 |
|-----|------|
| P0-2 | `core/security.py` — token 哈希 bcrypt → SHA-256 + hmac.compare_digest；新增 `get_password_hash` / `verify_password` 方法；Alembic 清空旧 token |
| P0-3 | `models/admin.py` — `Admin.role` String(20) → `SAEnum(UserRole)` + Alembic 迁移 |
| P0-4 | `admin.py`/`user.py`/`security.py` — 三份 CryptContext 合并到 `security.py` 全局 `pwd_context` 单例 |
| P0-6 | `schedule/celery_job.py` — 顶部标记 DEPRECATED |

**核心重构**：
- `_extract_json` — 解析失败不抛 ValueError，返回 `{"score": 5.0, "parse_failed": True}` 兜底
- `app/workflows/_shared/sse.py` — `astream_to_sse()` 封装，映射 LangGraph `astream_events` → SSE 标准事件
- `app/workflows/interview/` — 6 个 nodes（fetch_context / retrieve_knowledge / evaluate / check_finished / ask_question / generate_report）+ StateGraph HITL 模式（`interrupt_after=["ask_question"]`）+ `submit_answer(stream=True/False)` 单一入口
- `app/api/client/v1/interview.py` — `/answer` 和 `/answer/stream` 端点全部走 graph service
- `ai_service.py` — 5 个出题方法（parse_resume / analyze_resume / generate_questions / select_and_adapt / generate_with_seeds）切 YAML `load_prompt()`；删除 evaluate_answer / evaluate_answer_stream / generate_report（已迁到 graph nodes）
- `interview_service.py` — 删除 submit_answer / submit_answer_stream（~320 行），保留 start / get_report / get_messages / get_interviews / delete

**代码量**：`ai_service.py` 485→230 行，`interview_service.py` 663→320 行，新增 `workflows/interview/` ~350 行。

**P1 遗留更新**（增量改进，不阻塞）：
- 5 个 repo 只建了 1 个示例（interview_repo）— 其他 4 个（user / admin / question_bank / knowledge）— **Phase 4 做**

### Phase 4 实施记录

**Agent 类提取**（2026-08-01 已回退）：
- ~~`app/agents/` — BaseAgent + QuestionAgent + EvaluatorAgent + ReportAgent~~
- ~~3 个新 YAML prompt：question_agent / evaluator_agent / report_agent~~
- ~~evaluate_node / generate_report_node 委托 Agent 类，不再直接调 LLM~~
- ~~submit_answer() 通过 state.custom 注入 agent 实例~~
- **2026-08-01 回退**：`app/agents/` 目录已删除。这些类不是真 Agent（无工具调用/ReAct 循环），只是 `prompt|llm` 薄封装。逻辑已内联到 evaluate_node / generate_report_node，直接用 `load_prompt() + get_chat_llm() + with_structured_output()`。QuestionAgent 是死代码已删。ReportAgent 的 `extract_json` 顺手改为 `with_structured_output(ReportResult)`。

**Repository 补全**：
- `question_bank_repo.py` — search_by_position / list_by_position / increment_use_count
- `knowledge_repo.py` — list_by_document / get_by_chunk_id / full_text_search
- `interview_repo.py` — get_by_id_with_messages / delete_cascade

**RAG 链路 Tracing**：
- `tracing.py` — trace_span context manager（LangSmith RunTree 子 span）
- `pipeline.py` — 4 步检索步骤各包一层 trace_span

**RAGAS + LangSmith**：
- `eval/scripts/upload_golden_set.py` — 同步 golden_set.json 到 LangSmith dataset
- `eval/scripts/eval_ragas.py` — 支持 --upload 和 --experiment-name 参数
- ✅ **首个 baseline 报告已生成**（2026-07-11, `eval/reports/baseline-20260711.json`）
  - 评估 LLM 用 DeepSeek（LangchainLLMWrapper → ChatOpenAI(model="deepseek-chat")）
  - 3/5 指标正常出分：faithfulness 0.55 / context_precision 0.30 / context_recall 0.30
  - 2/5 指标 NaN：answer_relevancy、answer_correctness — 依赖 `/v1/embeddings`，DeepSeek 不支持
  - 低 precision/recall 是因为 golden set 的 `relevant_chunk_ids` 未标注

**Service 去 @staticmethod**：
- 14 个 service 文件全部改为实例方法 + Depends 注入
- `app/deps.py` — 统一 factory 函数（agent + service）

### 已知技术债（2026-08-01 更新）

**P0（2026-08-01 架构评审发现，**已于 2026-08-01 修复**）**：
- [x] **P0-1 面试报告评分事实性错误**：evaluate 节点从不把 score/feedback 写回 `InterviewMessage` → `generate_report` 的 `get_scored_messages()` 恒为空 → 报告里每道题分数都是最后一题分数、除最后一题外答案全是"未回答"。**修复**：evaluate_node 评分后将 score/feedback/question_index 写回最新未评分的 candidate 消息（6d63ce6..9175eb7，6 commits）。
- [x] **P0-2 BM25 索引从未构建，混合检索生产静默关闭**：`build_bm25_indices` 全仓库只有定义、**零调用**，lifespan 未挂 → 题库永远走 vector-only 兜底，retrieve_knowledge 节点注入空知识。BM25 结果用语料下标当 id（非 PG 主键），RRF 按 id 融合必然失效。**修复**：lifespan 挂构建 + BM25Index 存 `(pg_id, text)` 对 + search 返回 PG 主键（6d63ce6..9175eb7，6 commits）。

**其余已知**：
- [ ] 自定义分页器 Paginator — 跳过（替换 fastapi-pagination 会破坏 API 格式）
- [ ] golden set `relevant_chunk_ids` 未标注 — 导致 RAGAS context_precision/recall 偏低（0.30），属数据标注任务而非代码技术债
- [ ] **域名 ICP 备案** — `shilian.asia` 当前未备案，**生产入口临时用 IP `123.56.239.7`**；备案下来后改回域名（约 1-20 工作日）

> 其余 17 项技术债已全部修复，详见上方"已完成"列表。

### Phase 5 pgvector 迁移关键变更
- ✅ `pymilvus==2.4.10` 依赖移除，替换为 `pgvector==0.3.6`
- ✅ embedding 列从 Milvus 迁回 PostgreSQL（`Vector(1024)` + HNSW `vector_l2_ops` 索引）
- ✅ 所有 `MilvusClient` → `AsyncSession`（无需 flush/load，事务保证可见性）
- ✅ `scripts/init_milvus.py` 删除（pgvector 扩展在 Alembic 迁移中自动创建）
- ✅ 3 个 legacy compose 文件清除 milvus/minio/celery 独立容器
- ✅ 容器数 7→4：app(含celery) + postgres/pgvector + redis + nginx

### Phase 6 首次云端部署实战

**目标**：把 Phase 5 的 4 容器 lite 栈跑通阿里云 2GB ECS（Ubuntu 22.04 / `123.56.239.7`），域名 `shilian.asia`。

**流程时间轴**（2026-07-22 全天）：
1. 09:00 阿里云 ECS 选型 + 安全组放行 22/80
2. 09:15 域名解析 + 安装 Docker + 配 2GB swap
3. 09:30 拉代码 + 配 `.env`（折腾 terminal 换行问题、改用 VS Code SSH）
4. 10:00 本地 build 两个前端 + scp 上传 dist
5. 10:30 `docker compose up` → CORS 报错 → 修 `.env`
6. 11:00 uploads/avatars 权限错 → mkdir + chmod 777
7. 11:15 logs 目录权限错（`Unable to configure handler 'file'`）→ mkdir + chmod 777
8. 11:30 UserRole enum 大小写不匹配（`SUPERADMIN` vs `superadmin`）→ 修 `values_callable`
9. 11:45 Alembic migration + 4 个 seed 脚本顺序跑完
10. 12:00 workers=4 OOM（2GB 机器）→ 改 `workers=1`
11. 12:30 健康端点 200 OK / 外部 502 → 阿里云安全组补 80 端口
12. 13:00 全栈运行 ✅（用 IP 访问，域名因未备案拦截）

**6 项修复**（详见 Part 10 实战坑）：
| # | 位置 | 修复 |
|---|------|------|
| 1 | `app/models/admin.py` | `SAEnum` 加 `values_callable=lambda x: [e.value for e in x]`，修复 Python enum name → DB enum value 的传递失败 |
| 2 | `main.py` | `workers=4` → `workers=1`（2GB ECS 限制） |
| 3 | 部署 runbook（操作侧） | 创建 `logs/` 和 `uploads/` 挂载目录时显式 `chmod 777` |
| 4 | `.env` 模板（操作侧）| 部署必须填 `CORS_ORIGINS`，否则启动 `RuntimeError` |
| 5 | 安全组（操作侧）| 80 端口必须手动添加放行 |
| 6 | 部署教程 | 新增 Part 0-12 完整流程，含所有坑 + 加功能改代码全链路 |

**新建/更新文档**：
- `docs/零基础部署教程.md`（替换原 33KB → 新 32KB / 1018 行，含 Part 0 概念 → Part 12 运维全流程）
- 当前可访问入口：`http://123.56.239.7/`（用户端）/ `http://123.56.239.7/admin/`（管理后台）
- 备案状态：**待 ICP 备案**，期间无法用域名访问

### 架构评审记录（2026-08-01，5 路并行代码审计）

**一句话结论**：分层大方向正确（面试用 LangGraph 工作流、RAG 用普通管线都对），但"Agent"名不副实 —— 全仓库真正用 Agent 机制（`create_agent` 工具循环）的只有岗位匹配 1 处，且被 system prompt 锁死顺序；`app/agents/` 3 个类全是单步 LLM 调用包装（QuestionAgent 是死代码）。

**Agent / Workflow / 普通函数 名实对照**：
| 模块 | 代码事实 | 判定 |
|---|---|---|
| 面试 StateGraph（`workflows/interview/`） | 6 节点 + `interrupt_after=["ask_question"]` HITL + AsyncPostgresSaver + SSE | ✅ 用对（P0-1 评分写回 DB 已于 2026-08-01 修复） |
| RAG 管线（`retrieval/pipeline.py`） | 确定性四阶段（vector+BM25+RRF+rerank），普通 Python | ✅ 用对（不该套 graph） |
| 岗位匹配 `create_agent`（`position_agent_service.py:52`） | 唯一真·工具调用 Agent，但 prompt 锁死"严格按此顺序调用工具/不要跳过步骤"（position_agent_system.yaml:7,17） | ⚠️ 固定 DAG 伪装成 Agent |
| retrieval_check StateGraph（`workflows/retrieval_check/`） | "最多重写 2 次"的有界循环，无 checkpointer/无流式/无 HITL；由 retrieve_knowledge_node 以 `graph.ainvoke()` 命令式嵌套触发，断 trace | ⚠️ 过度设计（~20 行 while 即可），唯一价值是 retry_reason 反馈闭环 |
| `app/agents/` 3 个类（Question/Evaluator/Report） | 单步 `prompt\|llm` / `with_structured_output`，**无 bind_tools、无工具循环** | ❌ 名不副实；QuestionAgent + deps 3 工厂为死代码 |

**死代码 / 过度封装清单**（2026-08-01 部分已清理）：
- [x] `app/agents/` 整目录已删除（question_agent 死代码 + evaluator/report 内联到 node）
- [x] `app/deps.py:9-21` 3 个 agent 工厂已删除
- [x] `ReportAgent` 违反 Phase 2 硬性规则 → generate_report_node 改为 `with_structured_output(ReportResult)`
- [x] `EvaluatorAgent` LLM 失败静默返回 → evaluate_node 改为 try/except 含错误信息兜底
- [ ] `get_workflow_llm`（`workflows/_shared/llm.py`）空壳死代码
- [ ] `workflows/_shared/tools.py` `DEFAULT_TOOLS` 空占位
- [ ] `app/prompts/evaluate_answer.yaml` / `generate_report.yaml` 孤儿文件（与 evaluator_agent/report_agent.yaml 功能重复）
- [ ] `embedding.py:108` `aembed_documents` 未 await（笔误；两个 sync 变体全无调用者）

**其他问题**：
- `rerank.py:39` 用 DashScope 同步 SDK 阻塞事件循环（async 函数内未 `asyncio.to_thread`，对比 BM25 在 pipeline.py:85 已卸线程）
- position_agent 的 `start_mock_interview` 是写 DB 的副作用工具却挂在工具集里，LLM 可在任意时机重复调用（重复建面试风险）
- 岗位匹配改造二选一：**路线 A** 删掉 prompt 顺序约束、让 LLM 真决策（工具加前置校验）；**路线 B** 拆成显式边 LangGraph StateGraph 或普通 async 顺序调用（最诚实，面试话术见"项目介绍"相关章节）

**修复路线（按优先级）**：~~P0-1 报告评分 → P0-2 BM25 构建~~ ✅ 已完成（2026-08-01）→ 岗位匹配拆 Agent 壳 → retrieval_check 降级纯函数 → 清剩余死代码（get_workflow_llm / DEFAULT_TOOLS / 孤儿 prompts / aembed_documents）。

**2026-08-01 已修复**：`app/agents/` 删除 + 逻辑内联（EvaluatorAgent → evaluate_node, ReportAgent → generate_report_node）+ `extract_json` 改 `with_structured_output(ReportResult)`。

**2026-08-01 P0 Bug 修复 — 已完成**（6 commits / 6 files / +263 -86）：
- 设计文档：`docs/superpowers/specs/2026-08-01-p0-critical-bugs-design.md`
- 实施计划：`docs/superpowers/plans/2026-08-01-p0-critical-bugs-implementation.md`（7 Tasks / 4 files）
- 执行方式：Subagent-Driven Development
- 验证：35/35 unit tests pass + smoke test（BM25 32+84 文档，search 返回 PG 主键）
