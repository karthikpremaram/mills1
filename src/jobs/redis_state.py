"""Utility helpers to read/write the small Task State record that the plan requires."""

import json
from typing import Any, Dict, Optional
from redis.asyncio import Redis


TASK_PREFIX = "task:"  # final key: task:{task_id}
IDEMP_PREFIX = "idemp:"  # idemp:{idempotency_key} -> task_id
LOGS_PREFIX = "tasklogs:"  # tasklogs:{task_id}


async def set_task_state(
    redis: Redis, task_id: str, state_update: Dict[str, Any]
) -> None:
    """set the task state"""
    key = TASK_PREFIX + task_id
    # Use GET/SET to merge partial updates atomically (Lua would be better for strict atomicity).
    data = await redis.get(key)
    if data:
        current = json.loads(data)
    else:
        current = {
            "task_id": task_id,
            "state": "QUEUED",
            "percent": 0,
            "current_step": None,
            "details": None,
        }
    # merge updates
    current.update(state_update)
    await redis.set(key, json.dumps(current), nx=False)


async def get_task_state(redis: Redis, task_id: str) -> Optional[Dict[str, Any]]:
    """get the task state"""
    key = TASK_PREFIX + task_id
    data = await redis.get(key)
    if not data:
        return None
    return json.loads(data)


async def create_task_state(
    redis: Redis, task_id: str, initial: Dict[str, any]
) -> None:
    """create the task state"""
    key = TASK_PREFIX + task_id
    await redis.set(key, json.dumps(initial))

async def create_task_record(redis: Redis, task_id: str, initial: Dict[str, Any]) -> None:
    """ create task record """
    key = TASK_PREFIX + task_id
    await redis.set(key, json.dumps(initial))

async def set_idempotency(redis: Redis, idempotency_key: str, task_id: str) -> None:
    """set idempotency"""
    key = IDEMP_PREFIX + idempotency_key
    await redis.set(key, task_id)


async def get_idempotency(redis: Redis, idempotency_key: str) -> Optional[str]:
    """ get idempotency"""
    key = IDEMP_PREFIX + idempotency_key
    val = await redis.get(key)
    if not val:
        return None
    return val.decode() if isinstance(val, bytes) else val


async def append_log(redis: Redis, task_id: str, message: str) -> None:
    """ append logs of the tasks"""
    key = LOGS_PREFIX + task_id
    await redis.rpush(key, message)


async def get_logs(redis: Redis, task_id: str, limit: int = 100):
    """ get the logs """
    key = LOGS_PREFIX + task_id
    items = await redis.lrange(key, -limit, -1)
    return [i.decode() if isinstance(i, bytes) else i for i in items]
