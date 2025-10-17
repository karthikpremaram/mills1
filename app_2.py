"""Millis Agent Creation Service"""

import asyncio
from typing import Optional
import httpx
from redis.asyncio import Redis
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
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
    set_knowledge_base
)
from src.utils.retry import async_retry
from src.logging.logger import logger, LogContext, log_step

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


# -------------------
# Agent Creation Endpoint
# -------------------
@app.post("/agents")
async def create_agent_endpoint(
    request: CreateAgentRequest, background_tasks: BackgroundTasks
):
    validate_task_manager()

    if not request.main_url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="Invalid URL format. Must start with http:// or https://",
        )
    result = agent_actions(request.main_url)
    
    if isinstance(result, str):
        System_prompt, kb, kb_description = result
    
        task_id = await task_manager.create_task()
    
        background_tasks.add_task(millis_actions(System_prompt, kb, kb_description,request.assistant_name)
, task_id, request)

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


# -------------------
# Task Status & SSE
# -------------------
@app.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    validate_task_manager()
    state = await task_manager.get_task_state(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="Task not found")
    return state


@app.delete("/tasks/{task_id}")
async def cancel_task(task_id: str):
    validate_task_manager()
    state = await task_manager.get_task_state(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="Task not found")
    await task_manager.cancel_task(task_id)
    return JSONResponse({"status": "cancelled", "task_id": task_id})


@app.get("/tasks/{task_id}/events")
async def task_events(task_id: str):
    validate_task_manager()
    state = await task_manager.get_task_state(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="Task not found")

    async def event_generator():
        try:
            while True:
                state = await task_manager.get_task_state(task_id)
                if not state or state["state"] in ["SUCCESS", "FAILED", "CANCELLED"]:
                    yield f"data: {state}\n\n"
                    break
                yield f"data: {state}\n\n"
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"SSE stream error: {str(e)}")
            yield "data: {'error': 'Stream terminated unexpectedly'}\n\n"

    return EventSourceResponse(event_generator())


# -------------------
# Agent Creation Pipeline
# -------------------
# @async_retry(retries=3, delay=1.0, exceptions=(httpx.HTTPError, TimeoutError))
# async def process_agent_creation(task_id: str, request: CreateAgentRequest):
#     validate_task_manager()
#     try:
#         with LogContext(logger, "agent_creation", task_id, url=request.main_url):
#             # Step 0: Initialize
#             log_step(logger, task_id, "initialize", 0)
#             await task_manager.update_progress(task_id, "initialize", 0)

#             # Step 1: Agent Actions
#             log_step(logger, task_id, "agent_actions", 10)
#             await task_manager.update_progress(task_id, "agent_actions", 10)
#             system_prompt, important_links = await agent_action(request.main_url)
#             if system_prompt == "-1":
#                 raise ValueError("Failed to create system prompt")

#             # Step 2: Knowledge Base
#             log_step(logger, task_id, "knowledge_base", 20)
#             await task_manager.update_progress(task_id, "knowledge_base", 20)
#             kb = await get_knowledge_base(COMPANY_NAMES[0], important_links)

#             log_step(logger, task_id, "kb_description", 30)
#             await task_manager.update_progress(task_id, "kb_description", 30)
#             kb_description = await get_kb_description(
#                 important_links, output_dir=f"agent_content/{COMPANY_NAMES[0]}"
#             )
#             if not kb_description:
#                 raise ValueError("Failed to generate knowledge base description")

#             # Step 3: Create Millis Assistant
#             assistant_name = request.assistant_name or COMPANY_NAMES[0]
#             log_step(logger, task_id, "creating_assistant", 40)
#             await task_manager.update_progress(task_id, "creating_assistant", 40)

#             greeting_message = f"Hi, welcome to {assistant_name}! May I know your name and what brings you here today?"
#             payload = Payload(
#                 agent_name=assistant_name,
#                 prompt=system_prompt,
#                 greeting_message=greeting_message,
#             )
#             payload_json = payload.get_payload()

#             log_step(logger, task_id, "creating_millis_agent", 50)
#             await task_manager.update_progress(task_id, "creating_millis_agent", 50)
#             assistant = await create_millis_assistant(payload_json, API_KEY)
#             assistant_id = assistant["id"]

#             # Step 4: Upload KB to S3
#             log_step(logger, task_id, "generating_presigned_url", 60)
#             await task_manager.update_progress(task_id, "generating_presigned_url", 60)
#             file_name = f"{assistant_name}.txt"
#             presigned_data = await generate_presigned_url(API_KEY, file_name)
#             s3_url = presigned_data["url"]
#             s3_fields = presigned_data["fields"]

#             log_step(logger, task_id, "uploading_to_s3", 80)
#             await task_manager.update_progress(task_id, "uploading_to_s3", 80)
#             upload_resp = await upload_text_to_s3(s3_url, s3_fields, kb, file_name)
#             if upload_resp.status_code != 200:
#                 raise RuntimeError(f"S3 upload failed: {upload_resp.status_code}")

#             # Step 5: Set Knowledge Base
#             log_step(logger, task_id, "setting_knowledge_base", 90)
#             await task_manager.update_progress(task_id, "setting_knowledge_base", 90)
#             file_id = s3_fields.get("key", "").split("/")[-1]
#             messages = [{"role": "system", "content": kb_description}]
#             await set_knowledge_base(API_KEY, assistant_id, file_id, messages)

#             # Complete
#             log_step(logger, task_id, "complete", 100)
#             await task_manager.update_progress(task_id, "complete", 100)
#             return assistant_id

#     except asyncio.TimeoutError:
#         await task_manager.set_error(task_id, "Operation timed out")
#         logger.error(f"Agent creation timed out for task {task_id}")
#         raise
#     except Exception as e:
#         await task_manager.set_error(task_id, str(e))
#         logger.exception(f"Agent creation failed for task {task_id}")
#         raise


def agent_actions(main_url):
    """Create system prompt, knowledge base, knowledge base description"""

    # get System prompt and important links
    System_prompt, important_links = agent_action(main_url)
    if System_prompt == "-1":
        return "-1"
    print("System prompt created")

    # ---------------------------------------------------------------
    # scrape data from important links and refine with llm for knowledge base

    kb = get_knowledge_base(COMPANY_NAMES[0], important_links)
    kb_description = get_kb_description(
        important_links, output_dir=f"agent_content/{COMPANY_NAMES[0]}"
    )
    return (System_prompt, kb, kb_description)



@async_retry(retries=3, delay=1.0, exceptions=(httpx.HTTPError, TimeoutError))
def millis_actions(System_prompt, kb, kb_description, assistant_name):
    """create millis assistant, upload file, assaign knowledge base"""

    try:
        if assistant_name is None:
            assistant_name = COMPANY_NAMES[0]
            print("*" * 20)
            print("assistant name set")
            print("*" * 20)
        greeting_message = f"Hi, welcome to {assistant_name}! May I know your name and what brings you here today?"
        paypload = Payload(
            agent_name=assistant_name,
            prompt=System_prompt,
            greeting_message=greeting_message,
        )
        paypload_json = paypload.get_payload()

        # # ----------------------------------------------------------------
        # create millis agent
        assistant = create_millis_assistant(paypload_json, API_KEY)
        assistant_id = assistant["id"]

        # -----------------------------------------------------------------
        # generate_presigned_url
        file_name = assistant_name + ".txt"
        print(f"Generating presigned URL for {file_name}...")
        presigned_data = generate_presigned_url(API_KEY, file_name)
        s3_upload_url = presigned_data["url"]
        s3_fields = presigned_data["fields"]
        s3_key_from_fields = s3_fields["key"]
        print(
            f"Presigned URL obtained. S3 URL: {s3_upload_url}, S3 Key: {s3_key_from_fields}"
        )

        # ----------------------------------------------------------------------
        # upload_text_to_s3
        upload_response = upload_text_to_s3(s3_upload_url, s3_fields, kb, file_name)
        # upload_response = upload_file_to_s3(s3_upload_url, s3_fields, FILE_PATH, FILE_NAME)
        print(f"File uploaded to S3. Status Code: {upload_response.status_code}")

        # -------------------------------------------------------------------------
        # upload file to millis

        file_size = len(kb)
        # file_size = os.path.getsize(FILE_PATH)
        print(f"Registering {file_name} with Millis AI...")
        params = {
            "API_KEY": API_KEY,
            "assistant_id": assistant_id,
            "file_name": file_name,
            "s3_key": s3_key_from_fields,
            "kb_description": kb_description,
            "file_size": file_size,
        }
        file_id = create_file_in_millis(params)
        print(f"File registered with Millis AI. Response: {file_id}")

        # ---------------------------------------------------------------------------------
        # set knowledge base
        messages = "Let me check"
        response = set_knowledge_base(API_KEY, assistant_id, file_id, messages)
        if response.ok:
            print(f"knowledge base created for {assistant_name}")
        else:
            print(response.content)
            print("knowledge base not created")

        return {
            "assistant_name": assistant_name,
            "assistant_id": assistant_id,
            "file_name": file_name,
            "file_id": file_id,
        }
    except Exception:
        return "-1"