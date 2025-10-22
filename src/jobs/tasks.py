from typing import Optional, List, Tuple

from src.agent import create_system_prompt_important_links, get_knowledge_base
from src.agent_config.agent_tools import COMPANY_NAMES
from src.core.config import Config
from src.jobs.redis_state import (
    create_task_record,
    set_task_state,
    get_task_state,
    append_log,
)
from src.logger.logger import logger
from src.scrape.llm import get_kb_description
from src.utils.functions import (
    create_millis_assistant,
    generate_presigned_url,
    upload_text_to_s3,
    create_file_in_millis,
    set_knowledge_base,
)
from src.utils.payloads import Payload

# ---------------------------------------------------
# Step definitions (weights sum to 100)
# ---------------------------------------------------
STEPS: List[Tuple[str, int]] = [
    ("create_system_prompt", 20),
    ("create_knowledge_base", 20),
    ("create_millis_assistant", 20),
    ("upload_kb_and_register", 20),
    ("assign_kb", 10),
    ("finalize_and_cleanup", 10),
]

assert sum(weight for _, weight in STEPS) == 100, "Step weights must sum to 100"

API_KEY = Config.MILLIS_API_KEY

# ---------------------------------------------------
# Helpers
# ---------------------------------------------------


async def check_cancel(ctx, task_id: str) -> bool:
    """Check if the task is cancelled."""
    redis = ctx["redis"]
    state = await get_task_state(redis, task_id)
    if state and state.get("state") == "CANCELLED":
        logger.info("Task %s cancelled by user", task_id)
        await append_log(redis, task_id, "Task cancelled by user.")
        return True
    return False


async def update_progress(
    redis, task_id, percent: float, step: str = None, details: Optional[str] = None
):
    """Update task progress and log."""
    logger.debug("Updating task %s progress: %s - %.1f%%", task_id, step, percent)
    await set_task_state(
        redis,
        task_id,
        {"percent": int(percent), "current_step": step, "details": details},
    )
    if step:
        progress_msg = f"{step} → {percent:.1f}% ({details or ''})"
        logger.info("Task %s: %s", task_id, progress_msg)
        await append_log(redis, task_id, progress_msg)


# ---------------------------------------------------
# Main Arq job
# ---------------------------------------------------
async def process_agent_creation(
    ctx,
    urls: List[str],
    assistant_name: Optional[str],
    idempotency_key: Optional[str],
    **kwargs,
):
    redis = ctx["redis"]
    task_id = ctx.get("job_id")
    percent = 0.0
    

    logger.info("Starting agent creation task %s for URLs: %s", task_id, urls)

    # Create task record in Redis
    initial_state = {
        "task_id": task_id,
        "state": "RUNNING",
        "percent": 0,
        "current_step": None,
        "details": None,
        "agent_id": None,
        "error_message": None,
    }

    logger.info("Initializing task record %s", task_id)
    await create_task_record(redis, task_id, initial_state)
    await append_log(redis, task_id, f"Task started for URLs: {urls}")

    try:
        # ---------------- Step 1: Create System Prompt ----------------
        step_name, weight = STEPS[0]
        logger.info("Starting step: %s", step_name)
        await set_task_state(redis, task_id, {"current_step": step_name})
        await append_log(redis, task_id, "Creating system prompt and scraping links...")
        system_prompt, important_links = await create_system_prompt_important_links(
            urls
        )

        percent += weight
        logger.info("System prompt generated successfully")
        await update_progress(
            redis, task_id, percent, step_name, "System prompt generated"
        )

        if await check_cancel(ctx, task_id):
            await set_task_state(redis, task_id, {"state": "CANCELLED"})
            return {"status": "CANCELLED"}

        # ---------------- Step 2: Create Knowledge Base ----------------
        step_name, weight = STEPS[1]
        logger.info("Starting step: %s", step_name)
        await set_task_state(redis, task_id, {"current_step": step_name})
        await append_log(redis, task_id, "Generating knowledge base...")
        company_name = COMPANY_NAMES[0] if COMPANY_NAMES else "Default Assistant"
        kb = await get_knowledge_base(
            company_name, important_links, redis=redis, task_id=task_id
        )
        kb_description = get_kb_description(
            important_links, output_dir=f"agent_content/{company_name}"
        )
        logger.debug("Knowledge base length: %d characters", len(kb))

        percent += weight
        await update_progress(
            redis, task_id, percent, step_name, "Knowledge base generated"
        )

        if await check_cancel(ctx, task_id):
            await set_task_state(redis, task_id, {"state": "CANCELLED"})
            return {"status": "CANCELLED"}

        # ---------------- Step 3: Create Millis Assistant ----------------
        step_name, weight = STEPS[2]
        logger.info("Starting step: %s", step_name)
        await set_task_state(redis, task_id, {"current_step": step_name})
        await append_log(redis, task_id, "Creating Millis assistant...")

        agent_name = assistant_name or company_name
        logger.info("Creating Millis assistant: %s", agent_name)
        payload = Payload(
            agent_name=agent_name,
            prompt=system_prompt,
            greeting_message=f"Hi! Welcome to {agent_name}. How can I help?",
        ).get_payload()
        assistant = await create_millis_assistant(payload, API_KEY)
        assistant_id = assistant["id"]
        logger.info("Assistant created with ID: %s", assistant_id)

        percent += weight
        await update_progress(
            redis, task_id, percent, step_name, f"assistant_id={assistant_id}"
        )

        if await check_cancel(ctx, task_id):
            await set_task_state(redis, task_id, {"state": "CANCELLED"})
            return {"status": "CANCELLED"}

        # ---------------- Step 4: Upload KB & Register File ----------------
        step_name, weight = STEPS[3]
        logger.info("Starting step: %s", step_name)
        await set_task_state(redis, task_id, {"current_step": step_name})
        await append_log(redis, task_id, "Uploading KB and registering file...")

        file_name = f"{agent_name}.txt"
        logger.debug("Generating presigned URL for %s", file_name)
        presigned_data = await generate_presigned_url(API_KEY, file_name)
        s3_upload_url = presigned_data["url"]
        s3_fields = presigned_data["fields"]
        s3_key = s3_fields["key"]
        logger.debug("Uploading knowledge base to S3")
        await upload_text_to_s3(s3_upload_url, s3_fields, kb, file_name)

        logger.debug("Registering file in Millis")
        file_data = await create_file_in_millis(
            {
                "API_KEY": API_KEY,
                "assistant_id": assistant_id,
                "file_name": file_name,
                "s3_key": s3_key,
                "kb_description": kb_description,
                "file_size": len(kb),
            }
        )

        file_id = (
            file_data.get("id")
            if isinstance(file_data, dict)
            else str(file_data).strip()
        )
        if not file_id:
            logger.error("File registration failed - no file ID returned")
            raise RuntimeError("File registration failed")

        logger.info("File registered with ID: %s", file_id)
        percent += weight
        await update_progress(redis, task_id, percent, step_name, f"file_id={file_id}")

        # ---------------- Step 5: Assign Knowledge Base ----------------
        step_name, weight = STEPS[4]
        logger.info("Starting step: %s", step_name)
        await set_task_state(redis, task_id, {"current_step": step_name})
        await append_log(redis, task_id, "Assigning KB to assistant...")

        logger.debug("Setting knowledge base for assistant %s", assistant_id)
        await set_knowledge_base(API_KEY, assistant_id, file_id, "Let me check")

        percent += weight
        await update_progress(redis, task_id, percent, step_name, "KB assigned")

        # ---------------- Step 6: Finalize and Cleanup ----------------
        step_name, weight = STEPS[5]
        logger.info("Starting step: %s", step_name)
        await set_task_state(
            redis, task_id, {"current_step": step_name, "agent_id": assistant_id}
        )
        await append_log(redis, task_id, "Finalizing and cleaning up...")

        percent += weight
        await update_progress(redis, task_id, percent, step_name, "Cleanup complete")

        # ---------------- Mark task as SUCCESS ----------------
        logger.info("Task %s completed successfully", task_id)
        await set_task_state(redis, task_id, {"state": "SUCCESS", "percent": 100})
        await append_log(redis, task_id, "Agent creation completed successfully!")

        response = {
            "assistant_name": agent_name,
            "assistant_id": assistant_id,
            "file_name": file_name,
            "file_id": file_id,
        }
        logger.debug("Task %s result: %s", task_id, response)
        return response
        
    except Exception as exc:
        logger.error("Task %s failed: %s", task_id, str(exc), exc_info=True)
        error_msg = str(exc)
        await set_task_state(
            redis, task_id, {"state": "FAILED", "error_message": error_msg}
        )
        await append_log(redis, task_id, f"Task failed: {error_msg}")
        raise
