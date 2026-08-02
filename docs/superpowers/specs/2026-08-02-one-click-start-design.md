# 一键启动（本地开发）设计文档

- 日期：2026-08-02
- 范围：本地开发环境一条命令拉起整个项目（后端 Docker + DB 初始化 + 两个前端 dev）
- 场景：**本地开发**（Windows + Docker Desktop + Git Bash）
- 形式：**单条命令** `./start.sh`

## 背景与问题

当前启动 `试炼（MockPilot）` 需要手工执行多步：

1. `cd ai-interview-backend && docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build`
2. 5 条 `docker exec` 命令（alembic + 管理员 + 3 个 seed）
3. `curl` 健康检查
4. 两个前端各开终端 `npm install && npm run dev`

步骤多、易漏，`一键启动指南.md` 虽有文档但仍是手工分步。目标是提供一条命令完成以上全部，指南同步重构。

## 交付物

1. `D:\shilian_ai\start.sh` — 单命令启动脚本（Git Bash / bash 语法，Windows 兼容）
2. 重写 `D:\shilian_ai\一键启动指南.md` — 顶部一条命令为主流程，原 5 步手工流程降级为「故障排查用」

## start.sh 接口

```
./start.sh          # 启动（默认）
./start.sh --stop   # 停止：杀前端窗口 + docker compose down
./start.sh --help   # 用法说明
```

## 启动流程

任一步失败即停止并输出修复指引，不静默。

1. **参数解析**：`--stop` / `--help` / 空（默认启动）
2. **Docker 检测**：`docker info` 失败 → 输出「请先启动 Docker Desktop」并退出（非零码）
3. **后端容器**：
   ```bash
   cd ai-interview-backend
   docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
   ```
4. **等待健康**：轮询 `curl -sf http://localhost:8006/api/v1/config/health`，120s 超时 → 输出「健康检查超时，请 `docker logs shilian-app` 排查」并退出
5. **DB 幂等初始化**（见下节）
6. **前端依赖**：`ai-interview-frontend/node_modules` 或 `ai-interview-admin/node_modules` 缺失 → 对应目录 `npm install`
7. **前端独立窗口**：
   ```bash
   cmd //c start "shilian-frontend" cmd //k "cd /d D:\shilian_ai\ai-interview-frontend && npm run dev"
   cmd //c start "shilian-admin" cmd //k "cd /d D:\shilian_ai\ai-interview-admin && npm run dev"
   ```
   （窗口标题 `shilian-frontend` / `shilian-admin` 供 `--stop` 按标题定位）
8. **打印访问地址 + 管理端账号**

## DB 初始化幂等策略

| 步骤 | 幂等性 | 守卫 |
|------|--------|------|
| `docker exec shilian-app alembic upgrade head` | 天然幂等 | 无 |
| `docker exec shilian-app python scripts/create_first_admin.py` | 自带存在检查（存在即返回） | 无 |
| `docker exec shilian-app python scripts/seed_position_templates.py` | 自带跳过（`[跳过] xxx 已存在`） | 无 |
| `docker exec shilian-app python scripts/seed_question_bank.py` | **无去重** | psql count>0 则跳过 |
| `docker exec shilian-app python scripts/seed_knowledge.py` | **无去重** | psql count>0 则跳过 |

无去重的两个 seed，脚本侧守卫：

```bash
count=$(docker exec shilian-postgres psql -U "$PG_USER" -d "$PG_DB" -tAc "SELECT count(*) FROM question_bank")
if [ "$count" -eq 0 ]; then
  docker exec shilian-app python scripts/seed_question_bank.py
else
  echo "[跳过] question_bank 已有 $count 条"
fi
```

- `knowledge_chunks` 同理
- PG 凭据读 `ai-interview-backend/.env`（`POSTGRES_USER` / `POSTGRES_DB`），缺失时缺省 `demo` / `ai_interview`（与 dev compose 默认一致）

## 停止 `--stop`

- 前端窗口：`taskkill //F //FI "WINDOWTITLE eq shilian-*"`（按窗口标题 `shilian-frontend` / `shilian-admin` 定位）
- 容器：
  ```bash
  cd ai-interview-backend
  docker compose -f docker-compose.yml -f docker-compose.dev.yml down
  ```

## 一键启动指南.md 调整

- **顶部「一键启动（本地开发）」区**：`./start.sh` + `--stop`，一句「start.sh 自动完成：Docker 检测 → 后端容器 → DB 幂等初始化 → 弹出两个前端窗口」
- **原 5 步手工流程** → 「手动分步（故障排查用）」小节保留，标注「自动脚本失败时按此排查」
- **部署（lite 栈）**：保留独立小节省，标注「部署专用，本地开发勿用」
- **访问地址表**改为本地端口：用户端 3000 / 管理端 3001 / 后端 API 8006
- **前置要求 / 账号 / 容器一览**保留

## 错误处理

- 每个步骤失败：红色提示 + 具体修复指引，进程退出非零码
- 健康检查超时：提示 `docker logs shilian-app` 看后端启动日志
- seed 失败：提示先手动跑对应 `docker exec` 命令排查

## 验证

1. **干净环境**：`docker compose down -v` 清卷 → `./start.sh` → 验证 3000/3001/8006 三端口可访问、DB 有种子数据
2. **二次运行**：再次 `./start.sh` → 验证 seed 全部跳过、无重复数据
3. **`--stop`**：容器 down、前端窗口被关闭
4. **文档一致性**：指南顶部命令与实际脚本行为一致
