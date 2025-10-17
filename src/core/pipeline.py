"""Pipeline management for async task processing"""

import asyncio
from enum import Enum
from typing import Dict, Optional
import httpx
from redis import Redis
import json


class TaskState(Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PipelineStep:
    def __init__(self, name: str, weight: int):
        self.name = name
        self.weight = weight


PIPELINE_STEPS = [
    PipelineStep("validate_inputs", 5),
    PipelineStep("fetch_pages", 35),
    PipelineStep("extract_knowledge", 15),
    PipelineStep("generate_descriptions", 15),
    PipelineStep("create_agents", 10),
    PipelineStep("upload_knowledge", 15),
    PipelineStep("finalize", 5),
]


class TaskManager:
    def __init__(self, redis_client: Redis):
        self.redis = redis_client

    async def create_task(self, idempotency_key: Optional[str] = None) -> str:
        """Create a new task and return its ID"""
        if idempotency_key:
            existing_task = self.redis.get(f"idempotency:{idempotency_key}")
            if existing_task:
                return existing_task  # Already decoded due to decode_responses=True

        task_id = f"task_{asyncio.current_task().get_name()}"
        task_state = {
            "task_id": task_id,
            "state": TaskState.QUEUED.value,
            "percent": 0,
            "current_step": None,
            "agent_id": None,
            "error_message": None,
        }

        self.redis.set(f"task:{task_id}", json.dumps(task_state))
        if idempotency_key:
            self.redis.set(f"idempotency:{idempotency_key}", task_id)

        return task_id

    async def update_progress(self, task_id: str, step: str, progress: float):
        """Update task progress"""
        task_state = json.loads(self.redis.get(f"task:{task_id}"))
        task_state["current_step"] = step
        task_state["percent"] = min(100, progress)

        if progress >= 100:
            task_state["state"] = TaskState.SUCCESS.value

        self.redis.set(f"task:{task_id}", json.dumps(task_state))

    async def set_error(self, task_id: str, error_message: str):
        """Mark task as failed with error message"""
        task_state = json.loads(self.redis.get(f"task:{task_id}"))
        task_state["state"] = TaskState.FAILED.value
        task_state["error_message"] = error_message
        self.redis.set(f"task:{task_id}", json.dumps(task_state))

    async def get_task_state(self, task_id: str) -> Dict:
        """Get current task state"""
        state = self.redis.get(f"task:{task_id}")
        return json.loads(state) if state else None

    async def cancel_task(self, task_id: str):
        """Cancel a task"""
        task_state = json.loads(self.redis.get(f"task:{task_id}"))
        task_state["state"] = TaskState.CANCELLED.value
        self.redis.set(f"task:{task_id}", json.dumps(task_state))


class AsyncPipeline:
    def __init__(self, task_manager: TaskManager):
        self.task_manager = task_manager

    async def validate_inputs(self, task_id: str, data: Dict) -> bool:
        """Validate input data"""
        # Add validation logic here
        await self.task_manager.update_progress(task_id, "validate_inputs", 5)
        return True

    async def fetch_pages(self, task_id: str, urls: list) -> list:
        """Fetch pages concurrently"""
        async with httpx.AsyncClient() as client:
            tasks = []
            for i, url in enumerate(urls):
                tasks.append(client.get(url))
                progress = 5 + (35 * (i + 1) / len(urls))
                await self.task_manager.update_progress(
                    task_id, "fetch_pages", progress
                )

            responses = await asyncio.gather(*tasks, return_exceptions=True)
            return [r for r in responses if not isinstance(r, Exception)]

    async def process_task(self, task_id: str, data: Dict) -> str:
        """Process a task through the pipeline"""
        try:
            # Validate inputs
            if not await self.validate_inputs(task_id, data):
                raise ValueError("Invalid input data")

            # Fetch pages
            pages = await self.fetch_pages(task_id, data.get("urls", []))

            # Continue with other steps...
            # Each step updates progress through task_manager

            agent_id = "generated_agent_id"  # Replace with actual agent creation

            await self.task_manager.update_progress(task_id, "finalize", 100)
            return agent_id

        except Exception as e:
            await self.task_manager.set_error(task_id, str(e))
            raise
