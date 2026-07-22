"""Redis 异步客户端单例 — 全应用共享一个连接池

业务服务通过 `from app.services.common.redis import redis_client` 获取，
调用方用 `await redis_client.get/set/delete/...`。
"""
from redis.asyncio import Redis
from app.core.config import settings


class RedisClient:
    """Redis 异步客户端封装 — 键值存储、冷却检查和管道操作。"""

    def __init__(self):
        """初始化 Redis 连接，使用配置文件中的主机、端口和密码参数。"""
        # 构建Redis连接参数
        redis_params = {
            "host": settings.REDIS_HOST,
            "port": settings.REDIS_PORT,
            "decode_responses": True
        }

        # 只有当密码不为空时才添加密码参数
        if hasattr(settings, "REDIS_PASSWORD") and settings.REDIS_PASSWORD:
            redis_params["password"] = settings.REDIS_PASSWORD

        self.redis = Redis(**redis_params)

    async def set_with_ttl(self, key: str, value: str, ttl_seconds: int):
        """设置键值对，带过期时间。

    Args:
        key: Redis 键名。
        value: 要存储的值。
        ttl_seconds: 过期时间（秒）。
    """
        await self.redis.setex(key, ttl_seconds, value)

    async def get(self, key: str) -> str:
        """获取键对应的值；不存在返回 None。

    Args:
        key: Redis 键名。
    """
        return await self.redis.get(key)

    async def delete(self, key: str):
        """删除键。

    Args:
        key: Redis 键名。
    """
        await self.redis.delete(key)

    async def incr_with_ttl(self, key: str, ttl_seconds: int) -> int:
        """自增并在首次设置时带过期时间，返回自增后的值。

    Args:
        key: Redis 键名。
        ttl_seconds: 首次创建时的过期时间（秒）。

    Returns:
        自增后的整数值。
    """
        pipe = self.redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl_seconds, nx=True)
        results = await pipe.execute()
        return results[0]

    async def set_cooldown(self, key: str, ttl_seconds: int):
        """设置冷却时间标记。

    Args:
        key: 冷却键名。
        ttl_seconds: 冷却时长（秒）。
    """
        await self.redis.setex(key, ttl_seconds, "1")

    async def check_cooldown(self, key: str) -> bool:
        """检查是否在冷却中。

    Args:
        key: 冷却键名。

    Returns:
        存在则 True，否则 False。
    """
        return bool(await self.redis.exists(key))

    def pipeline(self, *args, **kwargs):
        """兼容原生 redis-py 的 pipeline 用法，直接转发到底层 redis 实例。
        注意：如果底层用的是 redis.asyncio.Redis，返回的是异步 pipeline。
        """
        return self.redis.pipeline(*args, **kwargs)

    async def brpop(self, key, timeout=1):
        """阻塞式从列表右侧弹出元素。

        Args:
            key: Redis 键名。
            timeout: 阻塞超时时间（秒），默认 1 秒。

        Returns:
            弹出元素组成的元组，超时返回 None。
        """
        return await self.redis.brpop(key, timeout=timeout)

    async def close(self):
        """关闭Redis连接"""
        await self.redis.close()


redis_client = RedisClient()
