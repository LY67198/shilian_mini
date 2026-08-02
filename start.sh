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
