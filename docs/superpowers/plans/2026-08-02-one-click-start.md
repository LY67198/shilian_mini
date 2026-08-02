# 一键启动（本地开发）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 本地开发环境用一条命令 `./start.sh` 拉起整个项目（后端 Docker + DB 幂等初始化 + 两个前端 dev 窗口），并重写 `一键启动指南.md` 使其以一条命令为主流程。

**Architecture:** 项目根目录新增单个 bash 脚本 `start.sh`，按顺序执行：Docker 检测 → `docker compose up -d --build`（base+dev 栈）→ 健康轮询 → 幂等 DB 初始化（两个无去重 seed 用 psql count 守卫）→ 前端 `npm install`（缺依赖时）→ `cmd //c start` 各开独立窗口跑 `npm run dev`。`--stop` 用 `taskkill` 按窗口标题杀前端 + `docker compose down`。指南顶部改为一条命令，原手工流程降级为「故障排查用」。

**Tech Stack:** bash（Git Bash，Windows）、docker compose、psql、npm/vite、markdown

**前置条件（验证时）**：Docker Desktop 已启动；`ai-interview-backend/.env` 已配 DeepSeek/DashScope Key（seed 嵌入用）。脚本本身不校验 Key，缺 Key 时 seed 会失败并打印错误。

**设计文档**：`docs/superpowers/specs/2026-08-02-one-click-start-design.md`

---

### Task 1: 编写 `start.sh`（完整脚本）

**Files:**
- Create: `D:\shilian_ai\start.sh`

- [ ] **Step 1: 编写完整脚本**

```bash
#!/usr/bin/env bash
#
# 试炼 (MockPilot) 一键启动脚本（本地开发）
# 用法: ./start.sh [--stop|--help]
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/ai-interview-backend"
FRONTEND_DIR="$ROOT_DIR/ai-interview-frontend"
ADMIN_DIR="$ROOT_DIR/ai-interview-admin"
COMPOSE_ARGS=(-f docker-compose.yml -f docker-compose.dev.yml)
HEALTH_URL="http://localhost:8006/api/v1/config/health"
HEALTH_TIMEOUT=120

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; NC=$'\033[0m'
info()  { echo -e "${GREEN}[一键启动]${NC} $*"; }
warn()  { echo -e "${YELLOW}[警告]${NC} $*"; }
error() { echo -e "${RED}[错误]${NC} $*"; }

show_usage() {
  cat <<'EOF'
试炼 (MockPilot) 一键启动脚本（本地开发）

用法:
  ./start.sh          启动：后端 Docker + DB 初始化 + 两个前端 dev 窗口
  ./start.sh --stop   停止：关闭前端窗口 + 后端容器
  ./start.sh --help   显示本帮助
EOF
}

# 读取 ai-interview-backend/.env 中的 PG 凭据（compose 格式，无 export）
read_pg_env() {
  local env_file="$BACKEND_DIR/.env"
  PG_USER="demo"
  PG_DB="ai_interview"
  [ -f "$env_file" ] || return 0
  PG_USER="$(grep -E '^POSTGRES_USER=' "$env_file" 2>/dev/null | head -1 | cut -d= -f2- || true)"
  PG_DB="$(grep -E '^POSTGRES_DB=' "$env_file" 2>/dev/null | head -1 | cut -d= -f2- || true)"
  PG_USER="${PG_USER:-demo}"
  PG_DB="${PG_DB:-ai_interview}"
}

check_docker() {
  if ! docker info >/dev/null 2>&1; then
    error "Docker 未运行，请先启动 Docker Desktop 再执行本脚本"
    exit 1
  fi
  info "Docker 检测通过"
}

start_backend() {
  info "启动后端容器（首次会 build，可能需要几分钟）..."
  cd "$BACKEND_DIR"
  docker compose "${COMPOSE_ARGS[@]}" up -d --build
}

wait_health() {
  info "等待后端健康检查（${HEALTH_TIMEOUT}s 超时）..."
  local waited=0
  until curl -sf "$HEALTH_URL" >/dev/null 2>&1; do
    if [ "$waited" -ge "$HEALTH_TIMEOUT" ]; then
      error "健康检查超时，请排查: docker logs shilian-app"
      exit 1
    fi
    sleep 2
    waited=$((waited + 2))
  done
  info "后端健康检查通过"
}

# 无去重的 seed 用 psql count 守卫；查询失败则跳过并提示手动执行
seed_if_empty() {
  local table="$1" module="$2"
  local count
  count="$(docker exec shilian-postgres psql -U "$PG_USER" -d "$PG_DB" -tAc "SELECT count(*) FROM $table" 2>/dev/null | tr -d ' ' || true)"
  if [ -z "$count" ]; then
    warn "无法查询 $table 数量，跳过 seed（可手动执行: docker exec shilian-app python scripts/$module.py）"
  elif [ "$count" -eq 0 ]; then
    docker exec shilian-app python "scripts/$module.py"
  else
    info "[跳过] $table 已有 $count 条数据"
  fi
}

init_db() {
  info "DB 初始化（幂等）..."
  docker exec shilian-app alembic upgrade head
  docker exec shilian-app python scripts/create_first_admin.py
  docker exec shilian-app python scripts/seed_position_templates.py
  read_pg_env
  seed_if_empty "question_bank" "seed_question_bank"
  seed_if_empty "knowledge_chunks" "seed_knowledge"
}

ensure_node_modules() {
  for dir in "$FRONTEND_DIR" "$ADMIN_DIR"; do
    if [ ! -d "$dir/node_modules" ]; then
      warn "$(basename "$dir") 缺少 node_modules，执行 npm install..."
      (cd "$dir" && npm install)
    fi
  done
}

start_frontends() {
  info "启动两个前端 dev 窗口..."
  cmd //c start "shilian-frontend" cmd //k "cd /d $(cygpath -w "$FRONTEND_DIR") && npm run dev" || warn "前端窗口启动失败"
  cmd //c start "shilian-admin" cmd //k "cd /d $(cygpath -w "$ADMIN_DIR") && npm run dev" || warn "管理端窗口启动失败"
}

print_urls() {
  cat <<EOF

${GREEN}✓ 一键启动完成${NC}
  用户端     http://localhost:3000
  管理端     http://localhost:3001
  后端 API   http://localhost:8006/api
  管理端账号 admin@ai-interview.com / LY1234567890
EOF
}

stop_all() {
  warn "关闭前端窗口..."
  taskkill //F //FI "WINDOWTITLE eq shilian-*" >/dev/null 2>&1 || true
  warn "停止后端容器..."
  cd "$BACKEND_DIR"
  docker compose "${COMPOSE_ARGS[@]}" down
  info "已停止"
}

case "${1:-}" in
  --help|-h) show_usage ;;
  --stop)    stop_all ;;
  "")
    check_docker
    start_backend
    wait_health
    init_db
    ensure_node_modules
    start_frontends
    print_urls
    ;;
  *) error "未知参数: $1"; show_usage; exit 1 ;;
esac
```

- [ ] **Step 2: 语法检查**

Run: `bash -n /d/shilian_ai/start.sh && echo OK`
Expected: `OK`（无语法错误）

- [ ] **Step 3: 验证 --help 分支（不触达 Docker）**

Run: `cd /d/shilian_ai && ./start.sh --help`
Expected: 打印用法说明，退出码 0（`echo $?` → `0`）。该分支不调用 Docker。

- [ ] **Step 4: 提交**

```bash
cd /d/shilian_ai
git add start.sh
git commit -m "feat: 一键启动脚本 start.sh（Docker + DB 幂等初始化 + 前端 dev 窗口 + --stop）"
```

---

### Task 2: 重写 `一键启动指南.md`

**Files:**
- Modify: `D:\shilian_ai\一键启动指南.md`（整文件重写，当前文件未被 git 跟踪，需 `git add -f`）

- [ ] **Step 1: 整文件重写为以下内容**

```markdown
# 试炼 (MockPilot) 一键启动指南

## 一键启动（本地开发）

在项目根目录执行一条命令（Git Bash）：

```bash
./start.sh
```

start.sh 自动完成：Docker 检测 → 后端容器启动 → DB 幂等初始化 → 弹出两个前端 dev 窗口。

停止：

```bash
./start.sh --stop
```

## 访问地址

| 服务 | 地址 |
|------|------|
| 用户端 | http://localhost:3000 |
| 管理端 | http://localhost:3001 |
| 后端 API | http://localhost:8006/api |
| Swagger (用户端) | http://localhost:8006/client/docs |
| Swagger (管理端) | http://localhost:8006/backoffice/docs |

## 账号信息

- **管理端登录**：`admin@ai-interview.com` / `LY1234567890`

## 前置要求

- Docker Desktop 已启动
- Git Bash（Windows）或任意 bash
- Node.js 18+
- DeepSeek API Key + DashScope API Key（已在 `ai-interview-backend/.env` 中配置）

## 手动分步（故障排查用）

自动脚本失败时，按此逐步排查。

### 1. 启动后端容器

```bash
cd ai-interview-backend
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

### 2. 初始化数据库

```bash
docker exec shilian-app alembic upgrade head
docker exec shilian-app python scripts/create_first_admin.py
docker exec shilian-app python scripts/seed_position_templates.py
docker exec shilian-app python scripts/seed_question_bank.py
docker exec shilian-app python scripts/seed_knowledge.py
```

### 3. 验证后端健康

```bash
curl http://localhost:8006/api/v1/config/health
# → {"code":200,"message":"Success","data":{"status":"healthy"}}
```

### 4. 启动前端

```bash
# 用户端（新终端）
cd ai-interview-frontend
npm install && npm run dev

# 管理端（新终端）
cd ai-interview-admin
npm install && npm run dev
```

## 部署（生产环境，专用）

> ⚠️ 本地开发勿用。云服务器上使用 lite 栈：

```bash
cd ai-interview-backend
docker compose -f docker-compose.lite.yml up -d --build
```

## 容器一览

| 容器名 | 用途 |
|--------|------|
| shilian-app | FastAPI 后端（含内嵌 Celery worker+beat） |
| shilian-postgres | 业务数据库（pgvector 扩展，存 embedding） |
| shilian-redis | 缓存/Celery 队列 |
| shilian-nginx | 反向代理 + 静态前端托管（仅部署启用） |
```

- [ ] **Step 2: 提交**

```bash
cd /d/shilian_ai
git add -f 一键启动指南.md
git commit -m "docs: 一键启动指南重构为 ./start.sh 一条命令主流程"
```

---

### Task 3: 干净环境端到端验证

> 需要 Docker Desktop 运行 + `.env` 已配两个 API Key。首次 build 可能耗时数分钟。

**Files:**
- Test: `D:\shilian_ai\start.sh`（无新文件）

- [ ] **Step 1: 清理现有容器与数据卷（干净基线）**

```bash
cd /d/shilian_ai/ai-interview-backend
docker compose -f docker-compose.yml -f docker-compose.dev.yml down -v
```
Expected: 容器与卷被移除。此步会清空本地 DB 数据（本地开发库，可接受）。

- [ ] **Step 2: 执行一键启动**

Run: `cd /d/shilian_ai && ./start.sh`
Expected 顺序输出：
1. `Docker 检测通过`
2. `启动后端容器...`（build + up 完成）
3. `等待后端健康检查` → `后端健康检查通过`
4. `DB 初始化（幂等）...` 且 seed 输出「新增」而非「跳过」（首次为空库）
5. 弹出两个终端窗口（标题 `shilian-frontend` / `shilian-admin`）
6. 结尾 `✓ 一键启动完成` + 地址表

- [ ] **Step 3: 验证三个端口可访问**

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8006/api/v1/config/health   # → 200
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000                        # → 200
curl -s -o /dev/null -w "%{http_code}" http://localhost:3001                        # → 200
```
Expected: 三行均为 `200`。

- [ ] **Step 4: 验证 DB 种子数据落库**

```bash
docker exec shilian-postgres psql -U demo -d ai_interview -tAc "SELECT count(*) FROM question_bank"
docker exec shilian-postgres psql -U demo -d ai_interview -tAc "SELECT count(*) FROM knowledge_chunks"
docker exec shilian-postgres psql -U demo -d ai_interview -tAc "SELECT count(*) FROM admin"
```
Expected: question_bank ≥ 1（约 30），knowledge_chunks ≥ 1（约 4×chunk），admin = 1。（若 `.env` 的 PG 凭据非 demo/ai_interview，改用 `.env` 实际值）

- [ ] **Step 5: 提交（如验证中发现脚本 bug，先修复再提交）**

```bash
cd /d/shilian_ai
git add start.sh
git commit -m "fix: 一键启动端到端验证修复"
```
（若未改动则跳过此提交）

---

### Task 4: 幂等与停止验证

**Files:**
- Test: `D:\shilian_ai\start.sh`（无新文件）

- [ ] **Step 1: 二次运行验证幂等（不重复插数据）**

Run: `cd /d/shilian_ai && ./start.sh`
Expected:
- seed 输出 `[跳过] question_bank 已有 N 条数据` 与 `[跳过] knowledge_chunks 已有 N 条数据`（而非新增）
- 不弹窗报错；注意会再弹出两个前端窗口（已知行为，后续 `--stop` 一并关闭）

- [ ] **Step 2: 验证无重复数据**

```bash
docker exec shilian-postgres psql -U demo -d ai_interview -tAc "SELECT count(*) FROM question_bank"
```
Expected: 数量与 Task 3 Step 4 完全一致（未翻倍）。

- [ ] **Step 3: 验证 --stop**

```bash
cd /d/shilian_ai && ./start.sh --stop
```
Expected:
1. 输出 `关闭前端窗口...`，前端两个窗口被关闭（标题含 `shilian-`）
2. 输出 `停止后端容器...` + `已停止`
3. 容器停止：

```bash
docker ps --filter "name=shilian-" --format "{{.Names}}"   # 无输出
```

- [ ] **Step 4: 提交（如验证中发现脚本 bug，先修复再提交）**

```bash
cd /d/shilian_ai
git add start.sh
git commit -m "fix: 一键启动幂等/停止验证修复"
```
（若未改动则跳过此提交）

---

## 计划自审

- **Spec 覆盖**：start.sh 全流程（Task 1）✓；指南重构为一条命令主流程 + 手动步骤降级 + 部署专用节省（Task 2）✓；干净环境验证（Task 3）✓；幂等 + `--stop` 验证（Task 4）✓。错误处理（健康超时/seed 失败/Docker 未启）均已内联在脚本。
- **占位符**：无 TBD/TODO；所有步骤含完整代码或精确命令与期望输出。
- **类型/命名一致性**：脚本内 `seed_if_empty` 收 module 名拼 `scripts/$module.py`，Task 3/4 验证用同一 `question_bank`/`knowledge_chunks` 表名，与 `app/models` 实测一致。
- **注意事项**：`git add -f` 用于 `一键启动指南.md`（`*.md` 被根 .gitignore 忽略，但按项目约定文档提交本地 dev、不 push 远程）。
