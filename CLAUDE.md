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
├── start.sh                       # 一键启动（本地开发）：Docker + DB 幂等初始化 + 前端 dev 窗口 + --stop
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
- ❌ 禁止 `re.search(r'\{.*"score".*\}', text)` 从 LLM 输出抠 JSON — 非流式链路用 `with_structured_output(PydanticModel)` 拿强类型结果；流式链路（见下）用 `chain.astream() 累积 + extract_json`
- ❌ 禁止 `submit_answer(stream=False)` 和 `submit_answer_stream` 分两个方法 — 合并为 `submit_answer(stream: bool = False)`
- ❌ 禁止 `isinstance(llm_output, AIMessage)` 后取 `.content` 再 `json.loads()` — 评分节点非流式链路用 `with_structured_output`，流式链路用 `chain.astream() 累积 + extract_json`（见下）
- ❌ 禁止 `_extract_json` 抛 `ValueError` — 已改为返回带 `parse_failed: True` 的 fallback dict
- ❌ 禁止 `with_structured_output` + `ainvoke` 阻塞反馈流式 —— **流式链路**（evaluate / generate_report / 出题）用 `chain.astream() 累积 + extract_json` 最后解析（与出题链路一致），结构化结果仍由 Pydantic 校验保证；**非流式链路**仍强制 `with_structured_output`

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
# === 一键启动（本地开发，Git Bash 根目录一条命令）===
./start.sh          # 后端容器 + DB 幂等初始化 + 两个前端 dev 窗口（详见 一键启动指南.md）
./start.sh --stop   # 停止：杀前端窗口 + docker compose down

# === 本地开发（Windows + Docker Desktop，手动分步）===
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

**2026-08-03（最新）**：项目完整可运行。`pytest -m "unit"` 全绿（142 passed），前端/管理端 build 通过。最新完成：
- **任意简历可用——岗位匹配 LLM 兜底**（2026-08-03）：`match_positions` 加 `MIN_MATCH_SCORE`（0.25）阈值，最佳分低于阈值时不再硬塞 IT 模板，改由 `ai_service.synthesize_positions` LLM 从画像合成 1-3 个岗位并落库为 `category="custom"` 模板行（sha1 幂等 tag，ValueError/IntegrityError 竞态兜底，custom 行不参与后续匹配）；画像 prompt `position_hints` 放宽为自由填写；6 个出题/评分 prompt 去技术向措辞；backoffice category 枚举扩 `custom`；Agent 输出 schema 补 `category`，前端 PositionMatch 对 custom 岗位显示"AI 定制"标签、上传页加"不确定做什么？试试 AI 岗位匹配"入口。验证：`pytest -m "unit"` 142 passed、前端/管理端 build 通过。spec：`docs/superpowers/specs/2026-08-03-arbitrary-resume-fallback-design.md`；计划：`docs/superpowers/plans/2026-08-03-arbitrary-resume-fallback.md`（9 任务 TDD 全落地，11 commit）
- **出题/反馈"非流式"根因修复**（2026-08-03）：定位为 `get_chat_llm()` 默认 `streaming=False`——`langchain-openai` 的 `ChatOpenAI(streaming=False)` 会把 `.astream()` 响应**缓冲成单块一次性 yield**（底层虽是 stream=true 请求）。容器内实测：False→1 chunk/9.7s，True→121 chunk/2.5s。修复：5 处流式调用点补 `streaming=True`（`evaluate.py` / `generate_report.py` / `ai_service.py` 的 `select_and_adapt_questions_stream` + `generate_next_question_stream` 两分支）。验证：真实路径产出 176 token chunk；`pytest -m "unit"` 115 passed；uvicorn StatReload 自动重启，**dev 环境无需重建镜像**（bind mount + reload）。教训：**任何新增 `.astream()` 链路必须传 `streaming=True`**
- **面试中逐题实时生成**（2026-08-03）：岗位匹配入口快建（`generate_questions=False`）→
  新 SSE 端点 `POST /interviews/{id}/next-question` 挂载生成第 1 题 →
  `ask_question_node` 双模现场生成第 N 题 → `check_finished` 按 `total_questions` 判断 →
  `sse.py` 按 `langgraph_node` 区分 `question_chunk`/`chunk`，题目逐字流式。
  上传页预生成路径零改动。spec：`docs/superpowers/specs/2026-08-02-progressive-question-streaming-design.md`；
  计划：`docs/superpowers/plans/2026-08-03-progressive-question-streaming.md`
- **面试反馈真流式化**（2026-08-02）：evaluate/generate_report 改 `astream + extract_json`（复用出题流式范式），SSE `chunk` 逐字流；前端 `Interview.vue` 改对象增量 JSON 解析，feedback/报告摘要逐字显示，移除 JSON 正则过滤。spec：`docs/superpowers/specs/2026-08-02-feedback-streaming-design.md`

### 一键启动（本地开发）（✅ 已完成）

项目根目录单条命令 `./start.sh`（Git Bash）拉起整套本地环境：Docker 检测 → dev 栈容器（`-f docker-compose.yml -f docker-compose.dev.yml up -d --build`）→ 120s 健康轮询 → **DB 幂等初始化**（alembic + admin + 3 seed，其中 question_bank/knowledge 用 psql count 守卫防重复）→ 两个前端 `cmd` 独立窗口跑 `npm run dev`；`./start.sh --stop` 停止。`一键启动指南.md` 已重构为一条命令主流程（手动步骤降级为故障排查用）。设计：`docs/superpowers/specs/2026-08-02-one-click-start-design.md`；计划：`docs/superpowers/plans/2026-08-02-one-click-start.md`。

- **验证**：干净环境（`down -v`）端到端通过（8006/3000/3001 可访问、种子落库 28/16/1）、二次运行幂等跳过、`--stop` 容器归零；最终整体 code review **Ready to merge**。
- **顺带修复 3 个 bug**（干净环境暴露）：① `Admin.role` 枚举加 `values_callable` 持久化小写值（否则全新库建管理员 DataError）；② `create_first_admin.py` 密码同步为 `LY1234567890`；③ `start.sh` 前端窗口子 shell 后台化防非交互阻塞。
- **待人工确认**：前端窗口内 npm 实际服务（无头环境无法闭环）；本机 Clash 代理下 curl localhost 需 `--noproxy '*'`。

### 待办（不阻塞）

1. **stream/非流式校验+落库段重复**（约 45 行，spec 明示的设计取舍）——后续可抽取 `_validate_resume()` + `_persist_interview()`
2. **`startInterviewStream` 未接 AbortController**——中途离开页面 done 仍会跳转（流仅 ~16s，影响小）
3. **前端 `startInterview` 导出已无引用**——保留作回退
4. **部署/生产 DB 大概率缺 embedding 列**（全真链路验证暴露）——上线前必须执行 `alembic upgrade head` + `scripts/rebuild_embeddings.py`（question_bank 84/84 + knowledge_chunks 32/32）
5. **两个手动 E2E 未做**：出题 SSE（`?stream=true` 需真实 token + 已完成简历）；一键启动前端窗口内 npm 服务（需交互终端跑 `./start.sh` 人工确认）
6. **【已完成 2026-08-03】任意简历可用——岗位匹配 LLM 兜底**：方案 A 已实施（模板优先 + `MIN_MATCH_SCORE` 阈值 + LLM 合成岗位落库 `category="custom"`，Agent 5 工具链不拆壳）。完成记录见上方"当前状态"。**未做**：非技术简历人工 E2E（需真实 DeepSeek token + 市场营销类简历，验证"AI 定制"标签实际渲染 + 非技术逐题出题）

### 里程碑（2026-08-02 及之前，详见 `docs/PROJECT_HISTORY.md`）

- **出题 SSE 流式化**（2026-08-01）：`/interviews/start?stream=true` 全程 SSE（status→chunk→done）+ 前端增量 JSON 解析，题目逐条浮现消除假进度条等待（总耗时不变）；7 任务 + 收尾 commit 详见 PROJECT_HISTORY
- **Phase 0-6 完成**：pgvector 迁移、LangGraph 面试 HITL、RAG 混合检索（vector+BM25→RRF→rerank，lifespan 自动构建 BM25 索引）、RAGAS 评估、双 Swagger、首次云端部署（`123.56.239.7`，域名 `shilian.asia` 待 ICP 备案）
- **全真链路验证**（`docker exec -e RUN_FULL_CHAIN=1 shilian-app pytest tests/test_full_chain_integration.py -v`）：RAG 混合检索 + RetrievalCheck 自检 + PositionAgent 5 工具全链，4 测试通过。暴露并修复两个真实 bug：① **RAG 向量检索完全失效（生产级）**——DB 停留在 Milvus 期缺 embedding 列，向量检索静默降级 BM25-only；修复 `alembic upgrade head` + 新增 `rebuild_embeddings.py`；② **rerank 模型失效 + 崩溃**——`DASHSCOPE_RERANK_MODEL` 改 `gte-rerank-v2` + `app/retrieval/rerank.py` 加 status_code 检查。agent 全链偶发 API 不稳定（`Connection error`/幻觉）属 DeepSeek API 层，重试即过
- **出题链路提速 4x**（63s→16s）：瘦身 `question_select` prompt + `select_and_adapt_questions` v2（LLM 只选题面 -83%，参考答案按 bank_id 补齐）+ 前端 `startInterview` 超时 60s→180s
- **审计待办 10 项完成**（P2-1~4 + info-1~6）：结束判断 `len(questions)`、提交失败 `persist_failed`、报告用 DB feedback、未评分回退 0.0、lifespan 关 checkpointer、系统 prompt 第 5 工具 + `interview_result`、画像 prompt 移 YAML、出题结果归一化、历史去重当前答案、BM25Index 带 metadata
