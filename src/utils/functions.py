import json
from io import BytesIO
import httpx
from src.logging.logger import logger


async def _safe_json(response: httpx.Response) -> dict:
    """Safely parse JSON; return {} if empty or invalid."""
    try:
        if not response.text.strip():
            logger.warning("Empty response body from %s", response.url)
            return {}
        return response.json()
    except json.JSONDecodeError:
        logger.warning("Non-JSON response from %s: %s", response.url, response.text)
        return {}


async def create_millis_assistant(payload, api_key):
    url = "https://api-west.millis.ai/agents"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            url,
            headers={"Content-Type": "application/json", "Authorization": api_key},
            json=payload,
        )
        response.raise_for_status()
        return await _safe_json(response)


async def generate_presigned_url(api_key, filename):
    url = "https://api-west.millis.ai/knowledge/generate_presigned_url"
    payload = {"filename": filename}
    headers = {"Authorization": api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return await _safe_json(response)


async def upload_text_to_s3(s3_url, fields, text, file_name="data.txt"):
    """Upload text or bytes to S3 using presigned URL."""
    file_bytes = text.encode("utf-8") if isinstance(text, str) else text
    file_obj = BytesIO(file_bytes)
    files = {"file": (file_name, file_obj)}

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(s3_url, data=fields, files=files)
        response.raise_for_status()
        return response


async def create_file_in_millis(params):
    url = "https://api-west.millis.ai/knowledge/create_file"
    headers = {
        "Authorization": params["API_KEY"],
        "Content-Type": "application/json",
    }
    payload = {
        "agent_id": params["assistant_id"],
        "object_key": params["s3_key"],
        "description": params["kb_description"],
        "name": params["file_name"],
        "file_type": "text/plain",
        "size": params["file_size"],
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return await _safe_json(response)


async def set_knowledge_base(api_key, assistant_id, file_id, messages):
    """Assign uploaded files as KB for the assistant."""
    url = "https://api-eu-west.millis.ai/knowledge/set_agent_files"
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "agent_id": assistant_id,
        "files": [file_id],
        "messages": [messages],
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, headers=headers, json=payload)

        # Even if the API returns no JSON, don’t crash
        if response.status_code >= 400:
            logger.error(
                "Failed to set KB (status %s): %s",
                response.status_code,
                response.text,
            )
            response.raise_for_status()

        return await _safe_json(response)
