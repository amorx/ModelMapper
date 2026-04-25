import logging
import re
from collections.abc import Mapping
from typing import Any

SECRET_PATTERN = re.compile(r"(?i)(authorization|api[_-]?key|token|secret)(=|:)\s*[^,\s]+")


def redact_text(value: str) -> str:
    return SECRET_PATTERN.sub(r"\1\2 [REDACTED]", value)


def redact_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in values.items():
        if re.search(r"(?i)(authorization|api[_-]?key|token|secret)", key):
            redacted[key] = "[REDACTED]"
        elif isinstance(value, str):
            redacted[key] = redact_text(value)
        else:
            redacted[key] = value
    return redacted


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(str(record.msg))
        if isinstance(record.args, dict):
            record.args = redact_mapping(record.args)
        elif isinstance(record.args, tuple):
            record.args = tuple(redact_text(item) if isinstance(item, str) else item for item in record.args)
        return True


def configure_logging() -> None:
    root = logging.getLogger()
    if not any(isinstance(item, RedactionFilter) for item in root.filters):
        root.addFilter(RedactionFilter())
