import sys
import json
from loguru import logger
from app.core.config import settings


def serialize(record):
    subset = {
        "timestamp": record["time"].strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "level": record["level"].name,
        "message": record["message"],
        "file": record["file"].name,
        "line": record["line"],
        "module": record["name"],
        "function": record["function"],
    }
    if record["extra"]:
        subset["extra"] = record["extra"]
    return json.dumps(subset)


def patching(record):
    record["extra"]["serialized"] = serialize(record)


def setup_logging():
    # Clear default logger
    logger.remove()

    log_level = settings.LOG_LEVEL.upper()

    if settings.APP_ENV == "production":
        logger.configure(patcher=patching)
        logger.add(
            sys.stdout,
            format="{extra[serialized]}",
            level=log_level,
        )
    else:
        # Clean console formatting for local development
        logger.add(
            sys.stdout,
            format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            level=log_level,
            colorize=True,
        )

    logger.info(f"Logging initialized | level={log_level} | env={settings.APP_ENV}")
