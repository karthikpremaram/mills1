"""Production logging configuration for Millis agent creation"""

import logging
import logging.handlers
import os
from datetime import datetime
import json
from typing import Any

# Create logs directory if it doesn't exist
os.makedirs("logs", exist_ok=True)


class AgentLogFormatter(logging.Formatter):
    """Custom formatter for agent creation logs"""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with additional fields"""
        # Basic log data
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add extra fields if available
        if hasattr(record, "task_id"):
            log_data["task_id"] = record.task_id
        if hasattr(record, "step"):
            log_data["step"] = record.step
        if hasattr(record, "progress"):
            log_data["progress"] = record.progress
        if hasattr(record, "duration_ms"):
            log_data["duration_ms"] = record.duration_ms

        # Add error details if present
        if record.exc_info:
            log_data["error"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
            }

        return json.dumps(log_data)


def setup_logger() -> logging.Logger:
    """Setup production logger configuration"""
    # Create logger
    logger = logging.getLogger("millis_agent")
    logger.setLevel(logging.INFO)

    # File handler with rotation
    file_handler = logging.handlers.RotatingFileHandler(
        "logs/millis_agent.log",
        maxBytes=10_000_000,  # 10MB
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setFormatter(AgentLogFormatter())

    # Console handler for critical errors
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.ERROR)
    console_handler.setFormatter(AgentLogFormatter())

    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


# Initialize the logger
logger = setup_logger()


class LogContext:
    """Context manager for logging operations with duration"""

    def __init__(
        self, logger: logging.Logger, operation: str, task_id: str = None, **extra: Any
    ):
        self.logger = logger
        self.operation = operation
        self.task_id = task_id
        self.extra = extra
        self.start_time = None

    def __enter__(self):
        self.start_time = datetime.now()
        self.logger.info(
            f"Starting {self.operation}",
            extra={"task_id": self.task_id, "step": self.operation, **self.extra},
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = (datetime.now() - self.start_time).total_seconds() * 1000
        if exc_type:
            self.logger.error(
                f"Error in {self.operation}: {str(exc_val)}",
                extra={
                    "task_id": self.task_id,
                    "step": self.operation,
                    "duration_ms": duration,
                    **self.extra,
                },
                exc_info=(exc_type, exc_val, exc_tb),
            )
        else:
            self.logger.info(
                f"Completed {self.operation}",
                extra={
                    "task_id": self.task_id,
                    "step": self.operation,
                    "duration_ms": duration,
                    **self.extra,
                },
            )


def log_step(
    logger: logging.Logger, task_id: str, step: str, progress: int, **extra: Any
):
    """Log a pipeline step with progress"""
    logger.info(
        f"Pipeline step: {step}",
        extra={"task_id": task_id, "step": step, "progress": progress, **extra},
    )


logger = logging.getLogger(__name__)
