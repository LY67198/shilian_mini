"""Celery worker 入口

通过 `python celery_worker.py` 启动 Celery worker 进程，
处理 app/core/celery_app.py 中 include 的所有任务。
"""

from app.core.celery_app import celery_app

if __name__ == '__main__':
    # This file is used to start Celery workers
    celery_app.start(['celery', 'worker', '--loglevel=info'])