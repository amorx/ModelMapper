import re
import time
from typing import Any, Protocol
from urllib.parse import urljoin

import httpx

from src.schemas import ModelRunRequest, ModelRunResult, RunStatus
from src.services.http_client import SafeHttpClient
from src.services.secrets import SecretsResolver

HEADER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,80}$")


class ProviderDiscoveryError(ValueError):
    pass


class ModelProvider(Protocol):
    async def generate(self, request: ModelRunRequest) -> ModelRunResult:
        ...

    async def test_connection(self, provider: dict[str, Any]) -> bool:
        ...


class ConfigurableHttpProvider:
    def __init__(self, secrets: SecretsResolver) -> None:
        self._secrets = secrets

    async def generate(self, request: ModelRunRequest) -> ModelRunResult:
        provider = request.provider
        style = str(provider["endpoint_style"])
        headers = self._headers(provider)
        client = SafeHttpClient(
            timeout_seconds=int(provider["timeout_seconds"]),
            allow_local_network=bool(provider["allow_local_network"]),
        )
        started = time.perf_counter()
        try:
            if style == "ollama_generate":
                payload = {
                    "model": request.model["model_name"],
                    "prompt": request.prompt["prompt_text"],
                    "stream": False,
                    "options": request.params,
                }
                response = await client.post_json(urljoin(str(provider["base_url"]), "/api/generate"), payload, headers)
                return self._ollama_result(response, started)
            if style == "openai_chat":
                payload = {
                    "model": request.model["model_name"],
                    "messages": [{"role": "user", "content": request.prompt["prompt_text"]}],
                    **request.params,
                }
                response = await client.post_json(
                    urljoin(str(provider["base_url"]), "/v1/chat/completions"),
                    payload,
                    headers,
                )
                return self._openai_chat_result(response, started)
            if style == "openai_completions":
                payload = {"model": request.model["model_name"], "prompt": request.prompt["prompt_text"], **request.params}
                response = await client.post_json(urljoin(str(provider["base_url"]), "/v1/completions"), payload, headers)
                return self._openai_completion_result(response, started)
            return ModelRunResult(status=RunStatus.FAILED, error_text="Unsupported provider endpoint style")
        except httpx.TimeoutException as error:
            return ModelRunResult(status=RunStatus.TIMEOUT, error_text=safe_error(error))
        except (httpx.HTTPError, ValueError) as error:
            return ModelRunResult(status=RunStatus.FAILED, error_text=safe_error(error))

    async def test_connection(self, provider: dict[str, Any]) -> bool:
        client = SafeHttpClient(
            timeout_seconds=int(provider["timeout_seconds"]),
            allow_local_network=bool(provider["allow_local_network"]),
        )
        headers = self._headers(provider)
        try:
            if provider["endpoint_style"] == "ollama_generate":
                response = await client.get_json(urljoin(str(provider["base_url"]), "/api/tags"), headers)
            else:
                response = await client.get_json(urljoin(str(provider["base_url"]), "/v1/models"), headers)
        except (httpx.HTTPError, ValueError):
            return False
        return response.status_code < 500

    async def list_ollama_models(self, provider: dict[str, Any]) -> list[dict[str, Any]]:
        if provider["endpoint_style"] != "ollama_generate":
            raise ProviderDiscoveryError("Provider is not configured for Ollama model discovery")
        client = SafeHttpClient(
            timeout_seconds=int(provider["timeout_seconds"]),
            allow_local_network=bool(provider["allow_local_network"]),
        )
        headers = self._headers(provider)
        try:
            response = await client.get_json(urljoin(str(provider["base_url"]), "/api/tags"), headers)
        except (httpx.HTTPError, ValueError) as error:
            raise ProviderDiscoveryError(safe_error(error)) from error
        if response.status_code >= 400:
            raise ProviderDiscoveryError(f"Ollama returned HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as error:
            raise ProviderDiscoveryError("Ollama returned invalid JSON") from error
        if not isinstance(data, dict):
            raise ProviderDiscoveryError("Ollama returned an unexpected response")
        models = data.get("models") or []
        seen: dict[str, dict[str, Any]] = {}
        for item in models:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or name in seen:
                continue
            try:
                size_bytes = int(item.get("size") or 0)
            except (TypeError, ValueError):
                size_bytes = 0
            seen[name] = {"name": name, "size_bytes": max(size_bytes, 0)}
        return sorted(seen.values(), key=lambda entry: entry["name"])

    def _headers(self, provider: dict[str, Any]) -> dict[str, str]:
        scheme = str(provider.get("auth_scheme") or "none")
        if scheme == "none":
            return {}
        secret = self._secrets.resolve(provider)
        if secret is None:
            return {}
        if scheme == "bearer":
            return {"Authorization": f"Bearer {secret}"}
        if scheme == "header":
            header_name = str(provider.get("header_name") or "")
            if not HEADER_NAME_PATTERN.fullmatch(header_name):
                raise ValueError("Invalid provider auth header name")
            return {header_name: secret}
        raise ValueError("Unsupported provider auth scheme")

    @staticmethod
    def _ollama_result(response: httpx.Response, started: float) -> ModelRunResult:
        if response.status_code >= 400:
            return ModelRunResult(status=RunStatus.FAILED, error_text=f"Provider returned HTTP {response.status_code}")
        data = response.json()
        total_ms = elapsed_ms(started)
        eval_count = int(data.get("eval_count") or 0)
        prompt_count = int(data.get("prompt_eval_count") or 0)
        return ModelRunResult(
            status=RunStatus.SUCCEEDED,
            response_text=str(data.get("response") or ""),
            total_latency_ms=total_ms,
            time_to_first_token_ms=total_ms,
            tokens_in=prompt_count,
            tokens_out=eval_count,
            raw_metadata=data,
        )

    @staticmethod
    def _openai_chat_result(response: httpx.Response, started: float) -> ModelRunResult:
        if response.status_code >= 400:
            return ModelRunResult(status=RunStatus.FAILED, error_text=f"Provider returned HTTP {response.status_code}")
        data = response.json()
        usage = data.get("usage") or {}
        choices = data.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        return ModelRunResult(
            status=RunStatus.SUCCEEDED,
            response_text=str(message.get("content") or ""),
            total_latency_ms=elapsed_ms(started),
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            raw_metadata=data,
        )

    @staticmethod
    def _openai_completion_result(response: httpx.Response, started: float) -> ModelRunResult:
        if response.status_code >= 400:
            return ModelRunResult(status=RunStatus.FAILED, error_text=f"Provider returned HTTP {response.status_code}")
        data = response.json()
        usage = data.get("usage") or {}
        choices = data.get("choices") or []
        text = choices[0].get("text", "") if choices else ""
        return ModelRunResult(
            status=RunStatus.SUCCEEDED,
            response_text=str(text),
            total_latency_ms=elapsed_ms(started),
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            raw_metadata=data,
        )


def elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def safe_error(error: Exception) -> str:
    return str(error).replace("\n", " ")[:300]
