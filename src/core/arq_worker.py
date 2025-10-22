# src/core/arq_worker.py
from arq.connections import RedisSettings
from src.core.config import Config
from src.jobs.tasks import process_agent_creation
from src.logger.logger import logger


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(Config.REDIS_URL)
    functions = [process_agent_creation]  # <-- FIXED HERE
    max_jobs = 5
    keep_result = 3600
    job_timeout = 1800

    @staticmethod
    async def on_startup(ctx):
        logger.info("🔹 Arq worker starting up")
        logger.debug("Redis URL: %s", Config.REDIS_URL)
        logger.debug(
            "Worker settings - Max jobs: %d, Keep result: %d, Timeout: %d",
            WorkerSettings.max_jobs,
            WorkerSettings.keep_result,
            WorkerSettings.job_timeout,
        )
        ctx["start_time"] = "now"

    @staticmethod
    async def on_shutdown(ctx):
        logger.info("🔹 Arq worker shutting down")
        logger.debug("Worker uptime: %s", ctx.get("start_time"))
