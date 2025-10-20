# src/core/arq_worker.py
from arq.connections import RedisSettings
from src.core.config import Config
from src.jobs.tasks import process_agent_creation

class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(Config.REDIS_URL)
    functions = [process_agent_creation]  # <-- FIXED HERE
    max_jobs = 5
    keep_result = 3600
    job_timeout = 600

    @staticmethod
    async def on_startup(ctx):
        print("🔹 Arq worker starting up")
        ctx['start_time'] = 'now'

    @staticmethod
    async def on_shutdown(ctx):
        print("🔹 Arq worker shutting down")
