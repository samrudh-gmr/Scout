"""Scout's own log file: what was asked of which model, how long it took, and what failed.

One size-capped file, ``~/.video-renamer/logs/scout.log``. It holds clip file
names, model names, timings, token counts and the provider's own error text.
It never holds prompts, image bytes or API keys; key-shaped text is scrubbed
on the way to the file as a backstop.

Setting the log up is best effort: a folder that cannot be created must never
stop the app, so ``setup_logging`` returns ``None`` instead of raising.
"""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "video_renamer"
LOG_FILE_NAME = "scout.log"
REDACTED = "[redacted]"
_MAX_BYTES = 2_000_000
_BACKUPS = 5
_FORMAT = "%(asctime)s %(levelname)s %(message)s"

_KEY_SHAPES = re.compile(
    r"AIza[0-9A-Za-z_\-]{20,}"  # Google API keys
    r"|sk-[0-9A-Za-z_\-]{16,}"  # OpenAI and Anthropic keys
    r"|(?<=Bearer )[0-9A-Za-z._\-]{16,}"
)

logger = logging.getLogger(LOGGER_NAME)


def default_log_dir() -> Path:
    return Path.home() / ".video-renamer" / "logs"


def redact(text: str, secrets: tuple[str | None, ...] = ()) -> str:
    """Remove the given secret values and anything shaped like an API key."""
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, REDACTED)
    return _KEY_SHAPES.sub(REDACTED, text)


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = None
        except Exception:  # noqa: BLE001 - a bad format string must not lose the line
            pass
        return True


def setup_logging(log_dir: Path | None = None) -> Path | None:
    """Attach the rotating log file once. Returns its path, or None if it cannot be used."""
    try:
        directory = Path(log_dir) if log_dir is not None else default_log_dir()
        path = directory / LOG_FILE_NAME
        for handler in logger.handlers:
            if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == path.resolve():
                return path
        directory.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8")
        handler.setFormatter(logging.Formatter(_FORMAT))
        handler.addFilter(_RedactingFilter())
        logger.addHandler(handler)
        if logger.level == logging.NOTSET or logger.level > logging.INFO:
            logger.setLevel(logging.INFO)
        return path
    except Exception:  # noqa: BLE001 - logging is never a reason to fail to start
        return None


def fields(**values: object) -> str:
    """``key=value`` pairs in the given order, skipping values that are None."""
    return " ".join(f"{name}={value}" for name, value in values.items() if value is not None)
