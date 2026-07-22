#!/usr/bin/env bash
set -e

# 在后台起 celery worker（内嵌 beat 调度，低并发省内存）
celery -A app.core.celery_app worker --beat --loglevel=info --concurrency=1 &

# 前台起 API（容器主进程）
exec python main.py