"""线程池服务 — 异步发邮件 / CPU-bound 任务派发

FastAPI 是 async 生态，但同步阻塞调用（如 SMTP）不能直接 await，
统一通过 `thread_pool_service.submit(fn, *args)` 派发到独立线程。
"""
from concurrent.futures import ThreadPoolExecutor


class ThreadPoolService:
    """线程池服务 — 将同步阻塞操作（如 SMTP 发送）提交到线程池执行，避免阻塞事件循环。"""

    def __init__(self, max_workers: int = 4):
        """初始化线程池。

        Args:
            max_workers: 线程池最大工作线程数，默认 4。
        """
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    def get_executor(self):
        """获取底层 ThreadPoolExecutor 实例，供 loop.run_in_executor 提交任务。"""
        return self.executor

    def shutdown(self):
        """关闭线程池（不等待未完成任务）。"""
        self.executor.shutdown(wait=False)


# 全局单例实例
thread_pool_service = ThreadPoolService()
