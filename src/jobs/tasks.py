import uuid
from typing import Optional, List, Tuple
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
from src.jobs.redis_state import (
    create_task_record,
    set_task_state,
    get_task_state,
    append_log,
)
from src.core.config import Config

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
        await append_log(redis, task_id, "Task cancelled by user.")
        return True
    return False


async def update_progress(redis, task_id, percent: float, step: str = None, details: Optional[str] = None):
    """Update task progress and log."""
    await set_task_state(redis, task_id, {"percent": int(percent), "current_step": step, "details": details})
    if step:
        await append_log(redis, task_id, f"{step} → {percent:.1f}% ({details or ''})")


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

    # Create task record in Redis
    await create_task_record(redis, task_id, {
        "task_id": task_id,
        "state": "RUNNING",
        "percent": 0,
        "current_step": None,
        "details": None,
        "agent_id": None,
        "error_message": None,
    })
    await append_log(redis, task_id, f"Task started for URLs: {urls}")

    try:
        # ---------------- Step 1: Create System Prompt ----------------
        step_name, weight = STEPS[0]
        await set_task_state(redis, task_id, {"current_step": step_name})
        await append_log(redis, task_id, "Creating system prompt and scraping links...")
        system_prompt, important_links = await create_system_prompt_important_links(urls)

        percent += weight
        await update_progress(redis, task_id, percent, step_name, "System prompt generated")

        if await check_cancel(ctx, task_id):
            await set_task_state(redis, task_id, {"state": "CANCELLED"})
            return {"status": "CANCELLED"}

        # ---------------- Step 2: Create Knowledge Base ----------------
        step_name, weight = STEPS[1]
        await set_task_state(redis, task_id, {"current_step": step_name})
        await append_log(redis, task_id, "Generating knowledge base...")
        company_name = COMPANY_NAMES[0] if COMPANY_NAMES else "Default Assistant"
        kb = await get_knowledge_base(company_name, important_links, redis=redis, task_id=task_id)
        kb_description = get_kb_description(important_links, output_dir=f"agent_content/{company_name}")

        percent += weight
        await update_progress(redis, task_id, percent, step_name, "Knowledge base generated")

        if await check_cancel(ctx, task_id):
            await set_task_state(redis, task_id, {"state": "CANCELLED"})
            return {"status": "CANCELLED"}

        # ---------------- Step 3: Create Millis Assistant ----------------
        step_name, weight = STEPS[2]
        await set_task_state(redis, task_id, {"current_step": step_name})
        await append_log(redis, task_id, "Creating Millis assistant...")

        payload = Payload(
            agent_name=assistant_name or company_name,
            prompt=system_prompt,
            greeting_message=f"Hi! Welcome to {assistant_name or company_name}. How can I help?"
        ).get_payload()
        assistant = await create_millis_assistant(payload, API_KEY)
        assistant_id = assistant["id"]

        percent += weight
        await update_progress(redis, task_id, percent, step_name, f"assistant_id={assistant_id}")

        if await check_cancel(ctx, task_id):
            await set_task_state(redis, task_id, {"state": "CANCELLED"})
            return {"status": "CANCELLED"}

        # ---------------- Step 4: Upload KB & Register File ----------------
        step_name, weight = STEPS[3]
        await set_task_state(redis, task_id, {"current_step": step_name})
        await append_log(redis, task_id, "Uploading KB and registering file...")

        file_name = f"{assistant_name or company_name}.txt"
        presigned_data = await generate_presigned_url(API_KEY, file_name)
        s3_upload_url, s3_fields, s3_key = presigned_data["url"], presigned_data["fields"], presigned_data["fields"]["key"]
        await upload_text_to_s3(s3_upload_url, s3_fields, kb, file_name)

        file_data = await create_file_in_millis({
            "API_KEY": API_KEY,
            "assistant_id": assistant_id,
            "file_name": file_name,
            "s3_key": s3_key,
            "kb_description": kb_description,
            "file_size": len(kb),
        })

        file_id = file_data.get("id") if isinstance(file_data, dict) else str(file_data).strip()
        if not file_id:
            raise RuntimeError("File registration failed")

        percent += weight
        await update_progress(redis, task_id, percent, step_name, f"file_id={file_id}")

        # ---------------- Step 5: Assign Knowledge Base ----------------
        step_name, weight = STEPS[4]
        await set_task_state(redis, task_id, {"current_step": step_name})
        await append_log(redis, task_id, "Assigning KB to assistant...")

        await set_knowledge_base(API_KEY, assistant_id, file_id, "Let me check")

        percent += weight
        await update_progress(redis, task_id, percent, step_name, "KB assigned")

        # ---------------- Step 6: Finalize and Cleanup ----------------
        step_name, weight = STEPS[5]
        await set_task_state(redis, task_id, {"current_step": step_name, "agent_id": assistant_id})
        await append_log(redis, task_id, "Finalizing and cleaning up...")

        percent += weight
        await update_progress(redis, task_id, percent, step_name, "Cleanup complete")

        # ---------------- Mark task as SUCCESS ----------------
        await set_task_state(redis, task_id, {"state": "SUCCESS", "percent": 100})
        await append_log(redis, task_id, "Agent creation completed successfully!")

        return {
            "assistant_name": assistant_name,
            "assistant_id": assistant_id,
            "file_name": file_name,
            "file_id": file_id
        }

    except Exception as exc:
        await set_task_state(redis, task_id, {"state": "FAILED", "error_message": str(exc)})
        await append_log(redis, task_id, f"Task failed: {exc}")
        raise
