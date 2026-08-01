# CLAUDE.md

> ⚠️ **本仓库是 `D:\ai-interview-agent\ai-interview-agent` 的复刻版本，专用于云服务器部署。**
> 原仓库保留本地开发环境（完整 7 容器）。本复刻版针对阿里云 99 元基础版（2 vCPU / 2 GiB）精简：
>
> | 调整项 | 原版 | 复刻版 |
> |---|---|---|
> | 向量库 | Milvus standalone | **pgvector**（`pgvector/pgvector:pg16` 镜像，零额外进程） |
> | MinIO / Flower / Whisper | 独立容器/服务 | **删除** |
> | Nginx | 反向代理容器 | **保留**（反代 + SPA 托管，端口 80） |
> | Celery worker/beat | 独立容器 | **并入 app 容器** |
> | 目标内存预算 | ~3 GB（OOM on 2 GiB） | **~2 GB 紧贴上限** |
>
> **不要把原版的新功能直接 merge 过来**——Milvus 相关代码（`app/vector_db/` Milvus 部分、`app/retrieval/vector.py` 的 Milvus 实现）已被 pgvector 替代，merge 会导致启动缺依赖。复刻日期 2026-07-22，原仓库快照 `Phase 4`，Phase 5（Milvus→pgvector）已完成（详见 `docs/PROJECT_HISTORY.md`）。

## 项目概述

**试炼（MockPilot）** — 企业级 AI 模拟面试平台。围绕"简历解析 → 岗位匹配 → RAG 题库出题 → 多轮 LangGraph 面试 → AI 评分 → 结构化报告"构建的完整求职训练闭环。

> **Agent 正名（2026-08-01）**：`app/agents/` 已删除——原 4 个类并非真 Agent（无工具循环），已内联到 workflow node 用 `load_prompt() + get_chat_llm() + with_structured_output()`。全仓库唯一用 LangChain `create_agent()` 的是 `position_agent_service.py`（system prompt 锁死工具顺序，固定 DAG）。**保留该 Agent 不拆壳**（功能卖点 + 拆壳回归风险 > 收益），仅修的 bug 见待办。

技术栈：FastAPI + LangGraph + LangChain + pgvector + PostgreSQL + Redis + DeepSeek + DashScope。

面试准备文档：`面试准备-LangGraph与RAG核心实现.md`（LangGraph 编排 + RAG 管线 + 6 大设计决策面试话术 + 14 道模拟面试题）。面试模拟练习：`docs/面试模拟记录.md`（角色扮演练习日志，触发词"面试"/"考我"/"模拟面试"）。

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI 0.115 + SQLAlchemy 2.x (async) + Alembic |
| 业务数据库 | PostgreSQL 16（结构化数据 + JSONB） |
| 向量数据库 | **pgvector**（`pgvector/pgvector:pg16`），HNSW L2 索引，embedding 维度 1024 |
| 缓存/队列 | Redis 7 + Celery 5.5（内嵌在 app 容器） |
| 反向代理 | Nginx（端口 80，反向代理 + SPA 静态文件托管） |
| AI 模型 | DeepSeek（OpenAI 兼容 SDK 2.x）、DashScope Embedding (text-embedding-v3, 已归一化) |
| Agent 框架 | LangChain 1.3 + **LangGraph 1.2.8**（langgraph-checkpoint-postgres 3.1.0） |
| 测试 | pytest 8.3 + pytest-asyncio + httpx |
| 前端 | Vue 3.4 + Vite 5.4 + Pinia 2.1 + Vue Router 4.3 |
| 部署 | Docker Compose（4 容器 lite 栈），外部端口 80 |

## 目录结构

```
ai-interview-agent/
├── ai-interview-backend/          # FastAPI 后端（容器化）
│   ├── app/
│   │   ├── api/                   # 接口模块（client/backoffice 拆 v1）
│   │   ├── route/                 # 路由注册中心（router_registry + create_app + lifespan）
│   │   ├── core/                  # config / celery_app / security / log_config
│   │   ├── configs/               # 双 Swagger 应用拆分
│   │   ├── db/                    # SQLAlchemy async session / base
│   │   ├── models/                # SQLAlchemy 模型（embedding 列用 pgvector Vector(1024)）
│   │   ├── schemas/               # Pydantic 请求/响应模型
│   │   ├── services/              # 非 AI 业务（auth/email/redis_verification 等）+ position_agent 工具
│   │   ├── llm/                   # LangChain 原子能力（LLM/Embedding/Prompt 工厂）
│   │   ├── prompts/               # PromptTemplate YAML 集中管理
│   │   ├── repositories/          # Repository Pattern 数据访问层
│   │   ├── retrieval/             # RAG 混合检索管线（vector + bm25 + rrf + rerank + pipeline）
│   │   ├── workflows/             # LangGraph 编排层
│   │   │   ├── _shared/           # checkpointer / state / tracing / sse
│   │   │   ├── interview/         # 面试评估（HITL StateGraph：fetch_context/retrieve_knowledge/evaluate/check_finished/ask_question/generate_report）
│   │   │   └── retrieval_check/   # 检索自检循环（纯 async while 循环）
│   │   ├── deps.py                # 统一 Depends 工厂（service）
│   │   ├── vector_db/             # pgvector 向量库（AsyncSession）
│   │   ├── common/                # language / release / json_utils
│   │   ├── exceptions/            # APIException / 错误码
│   │   └── schedule/              # Celery 定时任务
│   ├── tests/                     # pytest 测试
│   ├── eval/                      # RAGAS 评估框架（golden_set + reports + scripts）
│   ├── migrations/                # Alembic（含 pgvector embedding 列 + HNSW 索引）
│   ├── scripts/                   # create_first_admin / seed_position_templates / seed_question_bank / seed_knowledge / rebuild_embeddings / deploy
│   ├── start.sh                   # app + 内嵌 celery 启动脚本
│   ├── nginx/nginx.conf           # 反代 + SPA 静态文件 + SSE proxy_buffering off
│   └── docker-compose.lite.yml    # 4 容器精简栈（app/pgvector/redis/nginx）
├── ai-interview-frontend/         # 用户端（dist/ 不上 git，本地 build → scp 上传）
└── ai-interview-admin/            # 管理端（base: '/admin/'，dist/ 不上 git）
```

## 分支策略

| 分支 | 用途 | 谁推 |
|------|------|------|
| `main` | **发布版**（已发布、公开可见） | 仅在显式指定时推 |
| `dev` | **重构 / 新功能开发**（默认） | 日常开发默认推这里 |

**约定**：
- 重构 / Phase 实施 / bug 修复 → 全部推 `dev`；推 `main` 必须显式说"推 main"
- 默认 `git push`（无参数）只推当前分支（`push.default = current`，`branch.dev.remote = github`）
- **未公开的内部文档**（CLAUDE.md / `docs/` / 部署教程 / 规划 md）`git add -f` 提交本地 dev，但**永远不进 main、不 push 远程**

## 核心约束

- **必须** 有 DeepSeek API Key 和 DashScope API Key 才能运行
- **必须** 通过 Docker 启动后端（本地 `docker-compose.yml` + `docker-compose.dev.yml`；部署 `docker-compose.lite.yml` 4 容器栈）
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
- ❌ 禁止手写 `yield f"data: {json.dumps(...)}\n\n"` — SSE 用 `app/workflows/_shared/sse.py:astream_to_sse()` 封装
- ❌ 禁止 `re.search(r'\{.*"score".*\}', text)` 从 LLM 输出抠 JSON — 用 `with_structured_output(PydanticModel)` 拿强类型结果
- ❌ 禁止 `submit_answer(stream=False)` 和 `submit_answer_stream` 分两个方法 — 合并为 `submit_answer(stream: bool = False)`
- ❌ 禁止 `isinstance(llm_output, AIMessage)` 后取 `.content` 再 `json.loads()` — 评分节点用 `with_structured_output`
- ❌ 禁止 `_extract_json` 抛 `ValueError` — 已改为返回带 `parse_failed: True` 的 fallback dict

## pgvector 向量库使用规则

- **唯一入口**：`app/vector_db/collections/{knowledge, question_bank}.py`（所有函数接收 `session: AsyncSession`）
- **距离度量：L2**（pgvector HNSW `vector_l2_ops`）；DashScope embedding 已归一化，L2 等价 cosine
  - `similarity = 1 - distance/√2`，实现见 `app/vector_db/index.py:l2_distance_to_similarity()`
- **查询语法**：`KnowledgeChunk.embedding.l2_distance(query_vector).label("distance")` → `.order_by(KnowledgeChunk.embedding.l2_distance(query_vector))`
- **不需要 flush/load**：PostgreSQL 事务保证写入立即可见，不像 Milvus 需要显式 flush
- **ID 与 PostgreSQL 同源**：`knowledge_chunks.id` / `question_bank.id` 由 PG auto-increment 生成；召回结果可直接 JOIN 到 PG 元数据表
- **health_check**：`SELECT 1 FROM pg_extension WHERE extname='vector'` 验证 pgvector 扩展已加载

## 常用命令

> ⚠️ 容器名从 `ai-interview-*` 重命名为 `shilian-*`（2026-07-10 品牌升级）

```bash
# === 本地开发（Windows + Docker Desktop）===
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

# Swagger：用户端 http://localhost:8006/client/docs / 管理端 http://localhost:8006/backoffice/docs

# 测试（容器内）
docker exec shilian-app pytest -m "unit"          # 仅单元测试
docker exec shilian-app pytest -m "smoke"         # 冒烟测试
docker exec shilian-app pytest tests/test_xxx.py  # 单文件
docker exec shilian-app pytest -m "integration"   # 集成测试
RUN_E2E=1 docker exec -e RUN_E2E=1 shilian-app pytest -m "e2e"

# 前端（本地开发 / 部署 build）
cd ai-interview-frontend && npm install && npm run dev    # 本地 → 3000；部署 npm run build → dist/ scp
cd ai-interview-admin && npm install && npm run dev        # 本地 → 3001；部署 npm run build → dist/

# 管理端登录：admin@ai-interview.com / LY1234567890
```

## 当前状态

**已完成**：项目完整可运行（本地 7 容器 / 部署 4 容器 lite 栈，目标 2GB ECS）。Phase 0-6 全部完成：pgvector 迁移、LangGraph 面试 HITL、RAG 混合检索（vector+BM25→RRF→rerank，lifespan 自动构建 BM25 索引）、RAGAS 评估、双 Swagger、首次云端部署（`123.56.239.7`，域名 `shilian.asia` 待 ICP 备案）。**2026-08-01**：P0-1/P0-2 + P1 越权 + Topics 1/2/3（岗位幂等 / retrieval_check 折叠 / 死代码清理）+ 过时测试修复 + 待办 10 项（P2-1~4 + info-1~6）全部完成，`pytest -m unit` 73 passed。详细实施记录见 `docs/PROJECT_HISTORY.md`。

**2026-08-01 全真链路验证**：新增 `tests/test_full_chain_integration.py`（`RUN_FULL_CHAIN=1` 门控，消耗真实 token），覆盖 RAG 混合检索（知识库 + 题库带过滤）、RetrievalCheck 自检循环、PositionAgent 5 工具全链。4 个全真测试全通过。**测试暴露并修复两个真实 bug**：

- **🚨 RAG 向量检索完全失效（生产级）**：DB 停留在 Milvus 期"已删 embedding 列"状态，`alembic upgrade head`（a2b3c4d5e6f70001）从未执行 → 向量检索静默降级为 BM25-only（`app/retrieval/vector.py` 吞异常返回 `[]`）。修复：`alembic upgrade head` + 新增 `scripts/rebuild_embeddings.py` 一次性重建两表 embedding（question_bank 84/84 + knowledge_chunks 32/32）。⚠️ **部署/生产 DB 大概率同样缺列，上线前需执行同样两步。**
- **rerank 模型失效 + 崩溃**：`DASHSCOPE_RERANK_MODEL` 改为 `gte-rerank-v2`（原 `gte-rerank` 无效名）；DashScope 非 200 响应（403/output=None）不抛异常，`app/retrieval/rerank.py` 已加显式 status_code 检查，失败回退原序不崩溃。

agent 全链曾出现偶发 API 不稳定（"Connection error." / interview_result 幻觉），重试即过，属 DeepSeek API 层非代码 bug。运行命令：`docker exec -e RUN_FULL_CHAIN=1 shilian-app pytest tests/test_full_chain_integration.py -v`。

**2026-08-01 出题链路提速（4x）**：`/interviews/start` 曾因 DeepSeek 选题单次调用 60~120s 而让前端 axios 默认 60s 超时（`timeout of 60000ms exceeded`），后端却继续跑完 → 用户重试产生重复面试。修复两处：
- **瘦身 `question_select` prompt + `select_and_adapt_questions` v2**：LLM 输入只传题面（id+question，-83%）、输出去掉参考答案（原始输出仅 704 chars）；新增 `_merge_selected_questions` 按 bank_id 从题库候选补齐 `reference_answer`/`key_points`（LLM 结果不可用时回退候选前 N 题）。实测 63s → **16s**。
- **前端 `startInterview` 超时 60s → 180s**（与 positionAgent 120/180s 覆盖模式一致）。

`pytest -m unit` 73 passed（新增 6 个 `TestSelectAndAdaptQuestions`）。提交 `5d2df5e`（本地 dev，不 push）。

**待办（2026-08-01 审计后 10 项已全部完成）**：

> ✅ **已执行（2026-08-01）**：`docs/superpowers/plans/2026-08-01-claude-todo-10-fixes.md`（TDD 10 Task）。P2-1 结束判断用 `len(questions)` + 越界先校验；P2-2 提交失败打 `persist_failed` 标记；P2-3 报告用 DB feedback；P2-4 未评分回退 0.0；info-1 lifespan 关闭 checkpointer；info-2 系统 prompt 补第 5 工具 + `interview_result`；info-3 画像 prompt 移 YAML；info-4 出题结果归一化 list；info-5 历史去重当前答案；info-6 BM25Index 携带 metadata（RRF 融合保留）。全部提交本地 dev。

**2026-08-01 出题 SSE 流式化（进行中）**：`/interviews/start` 的 16s 等待里 ~14s 是单次 DeepSeek 选题 LLM（`chain.ainvoke` 一次性阻塞），前端 `ResumeUpload.vue:handleStart` 只显示假进度条动画。改造为 `?stream=true` 全程 SSE（status → chunk token 逐字流出 → done），前端实时增量 JSON 解析让题目逐条浮现，消除假进度条等待。**总耗时不变，只改善感知**。设计：`docs/superpowers/specs/2026-08-01-question-streaming-sse-design.md`；计划：`docs/superpowers/plans/2026-08-01-question-streaming-sse.md`。

- **执行中（subagent-driven，7 任务 TDD）**：
  - ✅ Task 1 `try_parse_partial_array` 增量 JSON 数组解析（前端 tryParseQuestions 算法后端等价版，供单测）`b2c17e7`
  - ✅ Task 2 `select_and_adapt_questions_stream` 流式选题（`chain.astream` 逐 token + 异常兜底仍回退候选前 N 题）`36c773c`
  - ⏳ Task 3-7 待执行：`_prepare_questions` 抽取 / `start_interview_stream`（status→chunk→done）/ 路由 `?stream=true` / 前端 `startInterviewStream`+`tryParseQuestions` / `ResumeUpload.vue` 流式化
- 实现注记：Task 2 里 `chain.astream(input={...})` 用关键字形式（测试 mock 只收关键字参数；LangChain `Runnable.astream(self, input, ...)` 首参即 input，真实有效）。计划原文 `chain.astream({...})` 位置参数会 TypeError。
- **RAG 链路已全量验证生效**（本次调查确认）：双路检索（vector+BM25，DB 两表 embedding 84/84+32/32 全填充）、rerank（`gte-rerank-v2`，日志 200）、自检（retrieval_check 接入答题链路）。唯一告警是 DeepSeek 偶发 `Connection error`（有兜底不阻断）。
- 提交全在本地 dev，不 push。
