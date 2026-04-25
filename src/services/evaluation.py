import json
import re
from typing import Any

from src.schemas import AutoCheckType


def run_auto_check(check_type: str, check_value: str, response_text: str) -> bool | None:
    if check_type in {"", AutoCheckType.NONE.value}:
        return None
    if check_type == AutoCheckType.EXACT.value:
        return response_text.strip() == check_value.strip()
    if check_type == AutoCheckType.CONTAINS.value:
        return check_value.lower() in response_text.lower()
    if check_type == AutoCheckType.REGEX.value:
        return re.search(check_value, response_text) is not None
    if check_type == AutoCheckType.JSON_SCHEMA.value:
        return is_json_object(response_text)
    raise ValueError("Unsupported auto-check type")


def comparison_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in rows if row.get("status") == "succeeded"]
    average_latency = average([row.get("total_latency_ms") for row in completed])
    average_accuracy = average([row.get("accuracy_score") for row in completed])
    return {
        "total_runs": len(rows),
        "completed_runs": len(completed),
        "average_latency_ms": average_latency,
        "average_accuracy_score": average_accuracy,
    }


def average(values: list[Any]) -> float:
    numeric = [float(value) for value in values if value is not None]
    if not numeric:
        return 0.0
    return round(sum(numeric) / len(numeric), 2)


def is_json_object(value: str) -> bool:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict)
