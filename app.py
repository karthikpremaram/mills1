"""Millis Agent Creation Service (Production-Ready)"""

import asyncio
from typing import Optional
import httpx
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.core.config import Config
from src.agent import create_system_prompt_important_links, get_knowledge_base
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

# -------------------------------------------------------------------------
# Config
# -------------------------------------------------------------------------
API_KEY = Config.MILLIS_API_KEY
app = FastAPI(title="Millis Voice Assistant")


# -------------------------------------------------------------------------
# Request Schema
# -------------------------------------------------------------------------
class CreateAgentRequest(BaseModel):
    main_url: str
    assistant_name: Optional[str] = None


# -------------------------------------------------------------------------
# Helper: Create System Prompt and KB
# -------------------------------------------------------------------------
@async_retry(retries=3, delay=0.5, exceptions=(ValueError, TimeoutError))
async def agent_actions(main_url: str):
    """Create system prompt, knowledge base, and KB description."""

    system_prompt, important_links = await create_system_prompt_important_links(main_url)
    if system_prompt.startswith("-1"):
        return "-1"

    logger.info("System prompt created successfully.")

    company_name = COMPANY_NAMES[0] if COMPANY_NAMES else "Default Assistant"
    kb = await get_knowledge_base(company_name, important_links)
    kb_description = get_kb_description(
        important_links, output_dir=f"agent_content/{company_name}"
    )

    logger.info("Knowledge base and description created for %s", company_name)
    return system_prompt, kb, kb_description


# -------------------------------------------------------------------------
# Helper: Create Millis Assistant
# -------------------------------------------------------------------------
@async_retry(retries=3, delay=0.5, exceptions=(httpx.HTTPError, TimeoutError))
async def millis_actions(system_prompt: str, kb: str, kb_description: str, assistant_name: Optional[str]):
    """Create Millis assistant, upload file, register KB, and assign it."""

    try:
        assistant_name = assistant_name or (COMPANY_NAMES[0] if COMPANY_NAMES else "Default Assistant")
        print("Starting Millis assistant creation for: %s", assistant_name)

        greeting_message = (
            f"Hi, welcome to {assistant_name}! "
            "May I know your name and what brings you here today?"
        )

        payload = Payload(
            agent_name=assistant_name,
            prompt=system_prompt,
            greeting_message=greeting_message,
        ).get_payload()

        # ----------------- Create Assistant -----------------
        assistant = await create_millis_assistant(payload, API_KEY)
        if not assistant or "id" not in assistant:
            logger.error("Failed to create assistant; empty or invalid response.")
            return "-1"

        assistant_id = assistant["id"]
        logger.info("Millis assistant created successfully. ID: %s", assistant_id)

        # ----------------- Generate Presigned URL -----------------
        file_name = f"{assistant_name}.txt"
        logger.info("Generating presigned URL for %s", file_name)
        presigned_data = await generate_presigned_url(API_KEY, file_name)

        s3_upload_url = presigned_data["url"]
        s3_fields = presigned_data["fields"]
        s3_key = s3_fields["key"]
        logger.info("Presigned URL obtained successfully.")

        # ----------------- Upload to S3 -----------------
        upload_response = await upload_text_to_s3(s3_upload_url, s3_fields, kb, file_name)
        print("📤 KB uploaded to S3. Status code: %s", upload_response.status_code)

        # ----------------- Register File -----------------
        params = {
            "API_KEY": API_KEY,
            "assistant_id": assistant_id,
            "file_name": file_name,
            "s3_key": s3_key,
            "kb_description": kb_description,
            "file_size": len(kb),
        }

        file_data = await create_file_in_millis(params)
        if not file_data:
            logger.error("Millis file registration returned no data.")
            return "-1"

        # handle both dict and plain string responses safely
        file_id = None
        if isinstance(file_data, dict):
            file_id = file_data.get("id")
        elif isinstance(file_data, str):
            file_id = file_data.strip()

        if not file_id:
            logger.error("Invalid file_id from create_file_in_millis: %s", file_data)
            return "-1"

        print("File registered successfully with ID: %s", file_id)

        # ----------------- Assign Knowledge Base -----------------
        response = await set_knowledge_base(API_KEY, assistant_id, file_id, "Let me check")
        if not getattr(response, "ok", True):
            logger.error("Failed to assign KB: %s", getattr(response, "text", response))
            return "-1"

        print("Knowledge base assigned successfully for %s", assistant_name)

        return {
            "assistant_name": assistant_name,
            "assistant_id": assistant_id,
            "file_name": file_name,
            "file_id": file_id,
        }

    except Exception as exc:
        logger.exception("Millis actions failed: %s", exc)
        return "-1"


# -------------------------------------------------------------------------
# FastAPI Endpoint
# -------------------------------------------------------------------------
@app.post("/agents")
async def create_agent_endpoint(request: CreateAgentRequest):
    """Endpoint to create a Millis voice assistant agent."""

    if not request.main_url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="Invalid URL format. Must start with http:// or https://",
        )

    logger.info("Agent creation request received for URL: %s", request.main_url)

    result = await agent_actions(request.main_url)
    if result == "-1":
        raise HTTPException(status_code=500, detail="Failed to create agent content")

    system_prompt, kb, kb_description = result

    logger.info("Proceeding with Millis assistant creation.")
    assistant = await millis_actions(system_prompt, kb, kb_description, request.assistant_name)
    if assistant == "-1":
        raise HTTPException(status_code=500, detail="Failed to complete Millis setup")

    logger.info("Assistant creation completed successfully for %s", assistant["assistant_name"])

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "success", "message": assistant},
    )
