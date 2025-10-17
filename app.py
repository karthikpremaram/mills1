"""Millis Agent Creation Service"""

import asyncio
from typing import Optional
import httpx
from redis.asyncio import Redis
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.core.pipeline import TaskManager, AsyncPipeline
from src.core.config import Config
from src.agent import agent_action, get_knowledge_base
from src.agent_config.agent_tools import COMPANY_NAMES
from src.scrape.llm import get_kb_description
from src.utils.payloads import Payload
from src.utils.functions import (
    create_millis_assistant,
    generate_presigned_url,
    upload_text_to_s3,
    create_file_in_millis,
    set_knowledge_base,
)
from src.utils.retry import async_retry
from src.logging.logger import logger

API_KEY = Config.MILLIS_API_KEY

app = FastAPI(title="Millis Voice Assistant")

# Global connections
redis_client: Optional[Redis] = None
task_manager: Optional[TaskManager] = None
pipeline: Optional[AsyncPipeline] = None


class CreateAgentRequest(BaseModel):
    main_url: str
    assistant_name: Optional[str] = None


# -------------------
# Redis initialization
# -------------------
async def get_redis_connection() -> Redis:
    return Redis(
        host="localhost",
        port=6379,
        db=0,
        decode_responses=True,
        socket_timeout=5,
        retry_on_timeout=True,
        max_connections=10,
    )


@app.on_event("startup")
async def startup_event():
    global redis_client, task_manager, pipeline
    try:
        redis_client = await get_redis_connection()
        await redis_client.ping()
        task_manager = TaskManager(redis_client)
        pipeline = AsyncPipeline(task_manager)
        logger.info("Successfully connected to Redis")
    except Exception as e:
        logger.error(f"Redis startup failed: {str(e)}")
        raise RuntimeError("Redis connection failed. Ensure Redis is running.")


@app.on_event("shutdown")
async def shutdown_event():
    global redis_client
    if redis_client:
        try:
            await redis_client.close()
            logger.info("Redis connection closed")
        except Exception as e:
            logger.error(f"Error closing Redis: {str(e)}")


# -------------------
# Task Manager Check
# -------------------
def validate_task_manager():
    if task_manager is None:
        raise HTTPException(
            status_code=503,
            detail="Task management system not available. Please try again later.",
        )


async def agent_actions(main_url):
    """Create system prompt, knowledge base, knowledge base description"""

    # get System prompt and important links
    System_prompt, important_links = await agent_action(main_url)
    if System_prompt.startswith("-1"):
        return "-1"

    print("System prompt created")
    
    # ---------------------------------------------------------------
    # scrape data from important links and refine with llm for knowledge base
    kb = await get_knowledge_base(COMPANY_NAMES[0], important_links)
    kb_description = await get_kb_description(
        important_links, output_dir=f"agent_content/{COMPANY_NAMES[0]}"
    )

    return (System_prompt, kb, kb_description)


@async_retry(retries=3, delay=1.0, exceptions=(httpx.HTTPError, TimeoutError))
async def millis_actions(System_prompt, kb, kb_description, assistant_name):
    """Create Millis assistant, upload file, and assign KB (async)."""

    try:
        assistant_name = assistant_name or COMPANY_NAMES[0]
        greeting_message = f"Hi, welcome to {assistant_name}! May I know your name and what brings you here today?"

        payload = Payload(
            agent_name=assistant_name,
            prompt=System_prompt,
            greeting_message=greeting_message,
        ).get_payload()

        # Create Millis Assistant
        assistant = await create_millis_assistant(payload, API_KEY)
        assistant_id = assistant["id"]

        # Generate Presigned URL
        file_name = f"{assistant_name}.txt"
        presigned_data = await generate_presigned_url(API_KEY, file_name)
        s3_upload_url = presigned_data["url"]
        s3_fields = presigned_data["fields"]
        s3_key_from_fields = s3_fields["key"]

        # Upload Text to S3
        upload_response = await upload_text_to_s3(
            s3_upload_url, s3_fields, kb, file_name
        )
        logger.info(f"📤 Uploaded file to S3: {upload_response.status_code}")

        # Register file with Millis
        params = {
            "API_KEY": API_KEY,
            "assistant_id": assistant_id,
            "file_name": file_name,
            "s3_key": s3_key_from_fields,
            "kb_description": kb_description,
            "file_size": len(kb),
        }
        file_data = await create_file_in_millis(params)

        # Assign KB
        messages = "Let me check"
        await set_knowledge_base(API_KEY, assistant_id, file_data["id"], messages)

        logger.info(f"✅ Knowledge base created for {assistant_name}")

        return {
            "assistant_name": assistant_name,
            "assistant_id": assistant_id,
            "file_name": file_name,
            "file_id": file_data["id"],
        }

    except Exception as e:
        logger.error(f"Millis actions failed: {e}")
        return "-1"


# -------------------
# Agent Creation Endpoint
# -------------------
@app.post("/agents")
async def create_agent_endpoint(request: CreateAgentRequest):
    validate_task_manager()

    if not request.main_url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="Invalid URL format. Must start with http:// or https://",
        )

    result = await agent_actions(request.main_url)
    if result == "-1":
        raise HTTPException(status_code=500, detail="Failed to create agent content")

    System_prompt, kb, kb_description = result
    task_id = await task_manager.create_task()

    async def background_job():
        try:
            await millis_actions(
                System_prompt, kb, kb_description, request.assistant_name
            )
        except Exception as e:
            logger.error(f"Background task failed: {e}")

    asyncio.create_task(background_job())

    return JSONResponse(
        {
            "task_id": task_id,
            "state": "QUEUED",
            "percent": 0,
            "_links": {
                "status": f"/tasks/{task_id}",
                "events": f"/tasks/{task_id}/events",
            },
        }
    )
