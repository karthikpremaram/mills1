"""Millis Agent Creation Service (Production-Ready)"""

from typing import Optional
import httpx
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.core.config import Config
from src.agent import create_system_prompt_important_links, get_knowledge_base
from src.agent_config.agent_tools import COMPANY_NAMES
from src.logger.logger import logger
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
    logger.info("Starting agent actions for URL: %s", main_url)

    try:
        system_prompt, important_links = await create_system_prompt_important_links(
            main_url
        )
        if system_prompt.startswith("-1"):
            logger.error("Failed to create system prompt for %s", main_url)
            return "-1"

        logger.info("System prompt created successfully")
        logger.debug(
            "Number of important links found: %d", len(important_links.get("links", []))
        )

        company_name = COMPANY_NAMES[0] if COMPANY_NAMES else "Default Assistant"
        logger.info("Processing knowledge base for company: %s", company_name)
        kb = await get_knowledge_base(company_name, important_links)
        logger.debug("Knowledge base size: %d characters", len(kb))

        kb_description = get_kb_description(
            important_links, output_dir=f"agent_content/{company_name}"
        )
        logger.debug("KB description size: %d characters", len(kb_description))

        logger.info(
            "Successfully created knowledge base and description for %s", company_name
        )
        return system_prompt, kb, kb_description
    except Exception as e:
        logger.error("Agent actions failed for %s: %s", main_url, str(e), exc_info=True)
        raise


# -------------------------------------------------------------------------
# Helper: Create Millis Assistant
# -------------------------------------------------------------------------
@async_retry(retries=3, delay=0.5, exceptions=(httpx.HTTPError, TimeoutError))
async def millis_actions(
    system_prompt: str, kb: str, kb_description: str, assistant_name: Optional[str]
):
    """Create Millis assistant, upload file, register KB, and assign it."""

    try:
        assistant_name = assistant_name or (
            COMPANY_NAMES[0] if COMPANY_NAMES else "Default Assistant"
        )
        logger.info("Starting Millis assistant creation for: %s", assistant_name)

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
        logger.debug("Creating Millis assistant with name: %s", assistant_name)
        assistant = await create_millis_assistant(payload, API_KEY)
        if not assistant or "id" not in assistant:
            logger.error("Failed to create assistant; empty or invalid response")
            return "-1"

        assistant_id = assistant["id"]
        logger.info("Millis assistant created successfully - ID: %s", assistant_id)

        # ----------------- Generate Presigned URL -----------------
        file_name = f"{assistant_name}.txt"
        logger.info("Generating presigned URL for file: %s", file_name)
        presigned_data = await generate_presigned_url(API_KEY, file_name)

        s3_upload_url = presigned_data["url"]
        s3_fields = presigned_data["fields"]
        s3_key = s3_fields["key"]
        logger.debug("Obtained presigned URL with key: %s", s3_key)

        # ----------------- Upload to S3 -----------------
        logger.debug("Uploading knowledge base to S3 (%d bytes)", len(kb))
        upload_response = await upload_text_to_s3(
            s3_upload_url, s3_fields, kb, file_name
        )
        logger.info(
            "KB uploaded to S3 successfully - Status: %d", upload_response.status_code
        )

        # ----------------- Register File -----------------
        logger.debug("Registering file in Millis system")
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
            logger.error("File registration failed - no response data")
            return "-1"

        # handle both dict and plain string responses safely
        file_id = None
        if isinstance(file_data, dict):
            file_id = file_data.get("id")
        elif isinstance(file_data, str):
            file_id = file_data.strip()

        if not file_id:
            logger.error(
                "Invalid file_id returned from create_file_in_millis: %s", file_data
            )
            return "-1"

        logger.info("File registered successfully with ID: %s", file_id)

        # ----------------- Assign Knowledge Base -----------------
        logger.debug("Assigning knowledge base to assistant %s", assistant_id)
        response = await set_knowledge_base(
            API_KEY, assistant_id, file_id, "Let me check"
        )
        if not getattr(response, "ok", True):
            error_msg = getattr(response, "text", str(response))
            logger.error("Failed to assign KB: %s", error_msg)
            return "-1"

        logger.info("Knowledge base successfully assigned to %s", assistant_name)

        result = {
            "assistant_name": assistant_name,
            "assistant_id": assistant_id,
            "file_name": file_name,
            "file_id": file_id,
        }
        logger.debug("Millis actions completed successfully: %s", result)
        return result

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
        logger.error("Invalid URL format received: %s", request.main_url)
        raise HTTPException(
            status_code=400,
            detail="Invalid URL format. Must start with http:// or https://",
        )

    logger.info(
        "Received agent creation request - URL: %s, Name: %s",
        request.main_url,
        request.assistant_name or "not specified",
    )

    try:
        # Step 1: Generate content
        logger.info("Starting agent content generation")
        result = await agent_actions(request.main_url)
        if result == "-1":
            logger.error("Agent content creation failed for %s", request.main_url)
            raise HTTPException(
                status_code=500, detail="Failed to create agent content"
            )

        system_prompt, kb, kb_description = result
        logger.debug(
            "Content generated - Prompt: %d chars, KB: %d chars",
            len(system_prompt),
            len(kb),
        )

        # Step 2: Create Millis assistant
        logger.info("Starting Millis assistant setup")
        assistant = await millis_actions(
            system_prompt, kb, kb_description, request.assistant_name
        )
        if assistant == "-1":
            logger.error("Millis setup failed for %s", request.main_url)
            raise HTTPException(
                status_code=500, detail="Failed to complete Millis setup"
            )

        logger.info(
            "Successfully created assistant '%s' with ID %s",
            assistant["assistant_name"],
            assistant["assistant_id"],
        )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "success", "message": assistant},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error in create_agent_endpoint: %s", str(e))
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
