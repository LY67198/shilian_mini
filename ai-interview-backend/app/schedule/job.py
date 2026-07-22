"""异步定时任务函数集合（APScheduler 调用入口）

每个函数为标准 async 协程，由 app/schedule/schedule.py 的 async_task_wrapper
包装后接入 AsyncIOScheduler；任务内部创建独立的数据库引擎与会话工厂，
避免与 FastAPI 异步循环冲突。
"""

import logging
from app.db.base import create_scheduler_engine, create_scheduler_session_factory
logger = logging.getLogger(__name__)

async def demo():
    logger.info("Running scheduled task: demo")
    
    # 创建此任务专用的数据库引擎和会话工厂
    scheduler_engine = create_scheduler_engine()
    SchedulerSessionLocal = create_scheduler_session_factory(scheduler_engine)
    
    # 使用新创建的会话工厂
    async with SchedulerSessionLocal() as db:
        try:
            pass
        
        except Exception as e:
            logger.error(f"Error in demo: {e}", exc_info=True)
        finally:
            # 关闭数据库引擎
            await scheduler_engine.dispose()

