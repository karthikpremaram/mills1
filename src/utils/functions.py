import json
from io import BytesIO
import httpx


async def create_millis_assistant(payload, api_key):
    url = "https://api-west.millis.ai/agents"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            url,
            headers={"Content-Type": "application/json", "Authorization": api_key},
            json=payload,
        )
        response.raise_for_status()
        return response.json()


async def generate_presigned_url(api_key, filename):
    url = "https://api-west.millis.ai/knowledge/generate_presigned_url"
    payload = {"filename": filename}
    headers = {"Authorization": api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


async def upload_text_to_s3(s3_url, fields, text, file_name="data.txt"):
    if isinstance(text, str):
        file_bytes = text.encode("utf-8")
    else:
        file_bytes = text
    file_obj = BytesIO(file_bytes)
    files = {"file": (file_name, file_obj)}

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(s3_url, data=fields, files=files)
        response.raise_for_status()
        return response


async def set_knowledge_base(api_key, assistant_id, file_id, messages):
    url = "https://api-eu-west.millis.ai/knowledge/set_agent_files"
    payload = {"agent_id": assistant_id, "files": [file_id], "messages": [messages]}
    headers = {"Authorization": api_key, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response


async def create_file_in_millis(params):
    url = "https://api-west.millis.ai/knowledge/create_file"
    headers = {
        "Authorization": params["API_KEY"],
        "Content-Type": "application/json"
    }
    payload = {
        "agent_id": params["assistant_id"],
        "object_key": params["s3_key"],
        "description": params["kb_description"],
        "name": params["file_name"],
        "file_type": "text/plain",
        "size": params["file_size"],
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


async def set_knowledge_base(api_key, assistant_id, file_id, messages):
    url = "https://api-eu-west.millis.ai/knowledge/set_agent_files"
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json"
    }
    payload = {
        "agent_id": assistant_id,
        "files": [file_id],
        "messages": [messages]
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()