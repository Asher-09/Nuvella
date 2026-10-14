from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_SECRET_RE = re.compile(
    r"(?i)(token\s*[=:]\s*|postgres(?:ql)?://|bot\s+)([^\s\"']{12,})"
)


def _redact(value: str) -> str:
    return _SECRET_RE.sub(lambda match: match.group(1) + "[REDACTED]", value)


class RedactionFilter(logging.Filter):
    """Redact common bot-token and database-URL patterns from log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
            redacted = _redact(rendered)
            if redacted != rendered:
                record.msg = redacted
                record.args = ()
            # Exception text is formatted after the filter in normal logging;
            # render and redact it here so secrets in driver exception messages
            # cannot bypass the message redactor in errors.log.
            if record.exc_info:
                record.exc_text = _redact(logging.Formatter().formatException(record.exc_info))
                record.exc_info = None
            elif record.exc_text:
                record.exc_text = _redact(record.exc_text)
            if record.stack_info:
                record.stack_info = _redact(record.stack_info)
        except Exception:
            pass
        return True


class ErrorOnlyFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.ERROR


def setup_logging() -> Path:
    """Configure console + rotating all-events, errors, and command audit logs."""
    log_dir = Path(os.getenv("NUVELLA_LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Make setup safe when the bot is restarted in-process or tests import main.py.
    owned = [h for h in root.handlers if getattr(h, "_nuvella_handler", False)]
    if owned:
        return log_dir

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    redactor = RedactionFilter()

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    console.addFilter(redactor)
    console._nuvella_handler = True  # type: ignore[attr-defined]
    root.addHandler(console)

    full_log = RotatingFileHandler(
        log_dir / "nuvella.log", maxBytes=10 * 1024 * 1024, backupCount=7, encoding="utf-8"
    )
    full_log.setLevel(logging.INFO)
    full_log.setFormatter(formatter)
    full_log.addFilter(redactor)
    full_log._nuvella_handler = True  # type: ignore[attr-defined]
    root.addHandler(full_log)

    errors = RotatingFileHandler(
        log_dir / "errors.log", maxBytes=5 * 1024 * 1024, backupCount=10, encoding="utf-8"
    )
    errors.setLevel(logging.ERROR)
    errors.setFormatter(formatter)
    errors.addFilter(redactor)
    errors.addFilter(ErrorOnlyFilter())
    errors._nuvella_handler = True  # type: ignore[attr-defined]
    root.addHandler(errors)

    commands = RotatingFileHandler(
        log_dir / "commands.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    commands.setLevel(logging.INFO)
    commands.setFormatter(formatter)
    commands.addFilter(redactor)
    commands._nuvella_commands = True  # type: ignore[attr-defined]
    commands._nuvella_handler = True  # type: ignore[attr-defined]
    command_logger = logging.getLogger("nuvella.commands")
    if not any(getattr(handler, "_nuvella_commands", False) for handler in command_logger.handlers):
        command_logger.addHandler(commands)
    command_logger.setLevel(logging.INFO)
    command_logger.propagate = False
    return log_dir


def get_logger(component: str) -> logging.Logger:
    """Return a logger routed through the central Nuvella handlers."""
    name = component if component.startswith("nuvella") else f"nuvella.{component}"
    return logging.getLogger(name)
