from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class AutoCheckType(StrEnum):
    NONE = "none"
    EXACT = "exact"
    CONTAINS = "contains"
    REGEX = "regex"
    JSON_SCHEMA = "json_schema"


class ProviderCreate(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=80)
    provider_kind: str = Field("ollama", min_length=1, max_length=40)
    base_url: str = Field(..., min_length=8, max_length=400)
    endpoint_style: Literal["ollama_generate", "openai_chat", "openai_completions"] = "ollama_generate"
    auth_scheme: Literal["none", "bearer", "header"] = "none"
    header_name: str | None = Field(default=None, max_length=80)
    timeout_seconds: int = Field(default=60, ge=1, le=300)
    api_key_source: Literal["none", "env", "encrypted"] = "none"
    api_key_env_var: str | None = Field(default=None, max_length=80)
    allow_local_network: bool = False
    enabled: bool = True

    @field_validator("api_key_env_var")
    @classmethod
    def validate_env_var(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if not value.replace("_", "").isalnum() or not value[0].isalpha():
            raise ValueError("API key environment variable name must be shell-safe")
        return value


class SecretCreate(BaseModel):
    provider_id: int = Field(..., ge=1)
    api_key: str = Field(..., min_length=8, max_length=4096)


class ModelCreate(BaseModel):
    provider_id: int = Field(..., ge=1)
    model_name: str = Field(..., min_length=1, max_length=120)
    display_name: str = Field(..., min_length=1, max_length=120)
    context_window: int = Field(default=0, ge=0)
    supports_chat: bool = True
    supports_tools: bool = False
    supports_vision: bool = False
    default_params: dict[str, Any] = Field(default_factory=dict)
    input_cost_per_1k: float = Field(default=0.0, ge=0)
    output_cost_per_1k: float = Field(default=0.0, ge=0)
    privacy_notes: str = Field(default="", max_length=500)
    enabled: bool = True


class PromptSetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)


class PromptCreate(BaseModel):
    prompt_set_id: int = Field(..., ge=1)
    category: str = Field(..., min_length=1, max_length=80)
    task_goal: str = Field(..., min_length=1, max_length=250)
    prompt_text: str = Field(..., min_length=1, max_length=10000)
    expected_answer: str = Field(default="", max_length=10000)
    rubric: str = Field(default="", max_length=2000)
    auto_check_type: AutoCheckType = AutoCheckType.NONE
    auto_check_value: str = Field(default="", max_length=5000)
    tags: str = Field(default="", max_length=500)


class RunCreate(BaseModel):
    prompt_id: int = Field(..., ge=1)
    model_id: int = Field(..., ge=1)
    params_json: dict[str, Any] = Field(default_factory=dict)
    benchmark_session_id: int | None = None
    sweep_name: str = Field(default="", max_length=120)
    sweep_index: int = Field(default=0, ge=0)
    repeat_index: int = Field(default=0, ge=0)


class BenchmarkSessionCreate(BaseModel):
    name: str = Field(default="Benchmark", min_length=1, max_length=200)
    prompt_set_id: int = Field(..., ge=1)
    model_ids: list[int] = Field(..., min_length=1)
    """Each item is an Ollama `options` dict (temperature, max_tokens, etc.)."""
    sweep: list[dict[str, Any]] = Field(..., min_length=1)
    repeat_count: int = Field(default=1, ge=1, le=20)
    notes: str = Field(default="", max_length=2000)


class EvaluationCreate(BaseModel):
    run_id: int = Field(..., ge=1)
    accuracy_score: int = Field(..., ge=1, le=5)
    style_score: int = Field(..., ge=1, le=5)
    strengths: str = Field(default="", max_length=2000)
    weaknesses: str = Field(default="", max_length=2000)
    notes: str = Field(default="", max_length=4000)
    recommended_use_cases: str = Field(default="", max_length=2000)
    auto_check_passed: bool | None = None


class DecisionRuleCreate(BaseModel):
    priority: int = Field(..., ge=1, le=10000)
    task_type: str = Field(default="", max_length=80)
    privacy_required: bool = False
    max_cost_per_1k: float | None = Field(default=None, ge=0)
    speed_priority: int = Field(default=3, ge=1, le=5)
    modality: str = Field(default="text", max_length=40)
    recommended_model_id: int = Field(..., ge=1)
    rationale: str = Field(..., min_length=1, max_length=500)


class DecisionCriteria(BaseModel):
    task_type: str = Field(default="", max_length=80)
    privacy_required: bool = False
    max_cost_per_1k: float | None = Field(default=None, ge=0)
    speed_priority: int = Field(default=3, ge=1, le=5)
    modality: str = Field(default="text", max_length=40)


class ModelRunRequest(BaseModel):
    provider: dict[str, Any]
    model: dict[str, Any]
    prompt: dict[str, Any]
    params: dict[str, Any] = Field(default_factory=dict)


class ModelRunResult(BaseModel):
    status: RunStatus
    response_text: str = ""
    error_text: str = ""
    time_to_first_token_ms: int | None = None
    total_latency_ms: int | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class HealthPayload(BaseModel):
    status: str


class ExternalUrl(BaseModel):
    url: HttpUrl
