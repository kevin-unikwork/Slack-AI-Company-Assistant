import time
import json
import redis.asyncio as aioredis
from redis.exceptions import RedisError
from typing import Any, Optional

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

class StateManager:
    """
    Centralized state manager providing Redis storage with a shared in-memory fallback.
    Ensures consistency across modules even when Redis is down.
    """
    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self._redis_client: Optional[aioredis.Redis] = None
        self._memory_store: dict[str, tuple[str, float]] = {}

    def get_redis(self) -> aioredis.Redis:
        if self._redis_client is None:
            self._redis_client = aioredis.from_url(self.redis_url, decode_responses=True)
        return self._redis_client

    async def set_state(self, key: str, value: Any, ttl: int) -> None:
        """Set state with TTL (seconds). Supports JSON-serializable values."""
        val_str = json.dumps(value) if not isinstance(value, str) else value
        
        try:
            r = self.get_redis()
            await r.setex(key, ttl, val_str)
        except Exception:
            logger.warning("Redis unavailable, using in-memory state fallback", extra={"key": key})
            self._memory_setex(key, ttl, val_str)

    async def set_if_not_exists(self, key: str, value: Any, ttl: int) -> bool:
        """
        Set state ONLY if key does not exist.
        Returns True if set, False if already exists or on error.
        """
        val_str = json.dumps(value) if not isinstance(value, str) else value
        try:
            r = self.get_redis()
            # result is True if set, None if not set
            result = await r.set(key, val_str, ex=ttl, nx=True)
            return result is True
        except Exception:
            logger.warning("Redis unavailable for NX check, using in-memory fallback", extra={"key": key})
            if self._memory_get(key) is None:
                self._memory_setex(key, ttl, val_str)
                return True
            return False

    async def get_state(self, key: str) -> Optional[str]:
        """Retrieve state. Returns None if not found or expired."""
        try:
            r = self.get_redis()
            return await r.get(key)
        except RedisError:
            return self._memory_get(key)

    async def delete_state(self, key: str) -> None:
        """Remove state from both Redis and memory."""
        try:
            r = self.get_redis()
            await r.delete(key)
        except RedisError:
            pass
        self._memory_delete(key)

    def _memory_setex(self, key: str, ttl: int, value: str) -> None:
        self._memory_store[key] = (value, time.time() + ttl)

    def _memory_get(self, key: str) -> Optional[str]:
        data = self._memory_store.get(key)
        if not data:
            return None
        value, expires_at = data
        if time.time() > expires_at:
            self._memory_store.pop(key, None)
            return None
        return value

    def _memory_delete(self, key: str) -> None:
        self._memory_store.pop(key, None)

# Global singleton instance
state_manager = StateManager(settings.redis_url)
