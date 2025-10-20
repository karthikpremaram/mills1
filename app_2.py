# app.py
import json
import uuid
import asyncio
from typing import Optional, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from arq import create_pool
from arq.connections import RedisSettings
from redis.asyncio import Redis

from src.core.config import Config
from src.jobs.redis_state import (
    set_idempotency,
    get_idempotency,
    create_task_record,
    set_task_state,
    get_task_state,
    append_log,
    get_logs,
)

from src.logger.logger import logger

# ------------------------------------------------------------
# Lifespan context — handles startup and shutdown
# ------------------------------------------------------------
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup
    redis = Redis.from_url(Config.REDIS_URL, decode_responses=False)
    app.state.redis = redis
    logger.info("Connected to Redis")
    # You can lazily create an Arq pool when enqueuing jobs, so we don’t hold it globally.
    yield
    # Shutdown
    await redis.close()
    logger.info("Redis connection closed")


app = FastAPI(title="Millis Agent Queue (Arq + FastAPI)", lifespan=lifespan)


# ------------------------------------------------------------
# Request schema
# ------------------------------------------------------------
class CreateAgentRequest(BaseModel):
    main_url: str
    assistant_name: Optional[str] = None
    idempotency_key: Optional[str] = None


# ------------------------------------------------------------
# POST /agents  → enqueue async job
# ------------------------------------------------------------
@app.post("/agents")
async def create_agent(request: CreateAgentRequest):
    logger.info("Received agent creation request for URL: %s", request.main_url)

    if not request.main_url.startswith(("http://", "https://")):
        logger.error("Invalid URL format: %s", request.main_url)
        raise HTTPException(
            status_code=400,
            detail="Invalid URL format. Must start with http:// or https://",
        )

    redis: Redis = app.state.redis

    # Check idempotency
    if request.idempotency_key:
        logger.debug("Checking idempotency key: %s", request.idempotency_key)
        existing = await get_idempotency(redis, request.idempotency_key)
        if existing:
            logger.info(
                "Found existing task %s for idempotency key %s",
                existing,
                request.idempotency_key,
            )
            state = await get_task_state(redis, existing)
            if state:
                return JSONResponse(
                    status_code=200,
                    content={
                        "task_id": existing,
                        "state": state.get("state"),
                        "percent": state.get("percent"),
                    },
                )

    # Enqueue ARQ job
    logger.debug("Creating ARQ pool")
    pool = await create_pool(RedisSettings.from_dsn(Config.REDIS_URL))
    job_id = str(uuid.uuid4())
    logger.info("Enqueueing job %s", job_id)

    await pool.enqueue_job(
        "process_agent_creation",
        request.main_url,
        request.assistant_name,
        request.idempotency_key,
        job_id=job_id,
    )
    await pool.close()
    logger.debug("ARQ pool closed")

    # Create initial Redis task record
    initial = {
        "task_id": job_id,
        "state": "QUEUED",
        "percent": 0,
        "current_step": None,
        "details": None,
        "agent_id": None,
        "error_message": None,
    }
    logger.debug("Creating initial task record for %s", job_id)
    await create_task_record(redis, job_id, initial)

    # Store idempotency mapping
    if request.idempotency_key:
        logger.debug(
            "Setting idempotency mapping: %s -> %s", request.idempotency_key, job_id
        )
        await set_idempotency(redis, request.idempotency_key, job_id)

    logger.info("Job %s successfully queued", job_id)
    return JSONResponse(
        status_code=202, content={"task_id": job_id, "state": "QUEUED", "percent": 0}
    )


# ------------------------------------------------------------
# GET /tasks/{task_id}  → fetch task status
# ------------------------------------------------------------
@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    redis: Redis = app.state.redis
    state = await get_task_state(redis, task_id)
    if not state:
        raise HTTPException(status_code=404, detail="Task not found")

    logs = await get_logs(redis, task_id, limit=20)
    state["logs"] = logs
    return JSONResponse(status_code=200, content=state)


# ------------------------------------------------------------
# DELETE /tasks/{task_id}  → cancel running task
# ------------------------------------------------------------
@app.delete("/tasks/{task_id}")
async def cancel_task(task_id: str):
    redis: Redis = app.state.redis
    state = await get_task_state(redis, task_id)
    if not state:
        raise HTTPException(status_code=404, detail="Task not found")

    await set_task_state(redis, task_id, {"state": "CANCELLED"})
    await append_log(redis, task_id, "Task cancellation requested via API.")
    return JSONResponse(
        status_code=200, content={"task_id": task_id, "state": "CANCELLED"}
    )


# ------------------------------------------------------------
# GET /tasks/{task_id}/events  → stream real-time updates (SSE)
# ------------------------------------------------------------
@app.get("/tasks/{task_id}/events")
async def task_events(task_id: str):
    redis: Redis = app.state.redis

    async def event_stream():
        last_data = None
        while True:
            state = await get_task_state(redis, task_id)
            if not state:
                yield "event: error\ndata: task_not_found\n\n"
                return

            payload = json.dumps(state)
            if payload != last_data:
                last_data = payload
                yield f"data: {payload}\n\n"
                # stop streaming when task finishes
                if state.get("state") in ("SUCCESS", "FAILED", "CANCELLED"):
                    return

            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")