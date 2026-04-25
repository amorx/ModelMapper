import sqlite3
import socket
import uuid
from unittest.mock import Mock

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.app import app, lifespan, process_modelmapper_entry
from src.dependencies import get_storage
from src.schemas import (
    DecisionCriteria,
    ModelCreate,
    ModelRunRequest,
    ModelRunResult,
    ProviderCreate,
    RunCreate,
    RunStatus,
)
from src.services.crypto import EncryptionService
from src.services.evaluation import comparison_summary, run_auto_check
from src.services.http_client import SafeHttpClient, UnsafeProviderUrlError
from src.services.logging import RedactionFilter, redact_mapping, redact_text
from src.services.providers import ConfigurableHttpProvider, safe_error
from src.services.rate_limit import InMemoryRateLimiter
from src.services.recommendations import recommend_model, rule_matches
from src.services.runner import ComparisonRunner, build_runner
from src.services.secrets import SecretsResolver, fingerprint_secret
from src.services.storage import Storage, normalize_db_value


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def storage(tmp_path: object) -> Storage:
    db_path = getattr(tmp_path, "joinpath")("modelmapper.db")
    item = Storage(str(db_path))
    item.init_db()
    return item


def test_encryption_service_round_trip_and_errors() -> None:
    key = Fernet.generate_key().decode("utf-8")
    service = EncryptionService(key)
    encrypted = service.encrypt("secret")
    assert encrypted != "secret"
    assert service.decrypt(encrypted) == "secret"
    with pytest.raises(ValueError, match="Unable to decrypt"):
        service.decrypt("not-a-token")
    with pytest.raises(ValueError, match="Missing CUSTOM_KEY"):
        EncryptionService(env_var="CUSTOM_KEY")
    with pytest.raises(ValueError, match="valid Fernet key"):
        EncryptionService("short")
    with pytest.raises(ValueError, match="valid Fernet key"):
        EncryptionService("x" * 44)


def test_process_modelmapper_entry() -> None:
    assert process_modelmapper_entry({"name": "Provider"}) is True
    assert process_modelmapper_entry({"name": ""}) is False


def test_schema_validation() -> None:
    provider = ProviderCreate(display_name="OpenAI", base_url="https://api.openai.com")
    assert provider.endpoint_style == "ollama_generate"
    assert ProviderCreate(display_name="OpenAI", base_url="https://api.openai.com", api_key_env_var="").api_key_env_var is None
    assert ProviderCreate(display_name="OpenAI", base_url="https://api.openai.com", api_key_env_var=None).api_key_env_var is None
    assert ProviderCreate(display_name="OpenAI", base_url="https://api.openai.com", api_key_env_var="OPENAI_API_KEY").api_key_env_var == "OPENAI_API_KEY"
    with pytest.raises(ValidationError):
        ProviderCreate(display_name="Bad", base_url="https://api.example.com", api_key_env_var="1 bad")
    with pytest.raises(ValidationError):
        ModelCreate(provider_id=1, model_name="", display_name="bad")


@pytest.mark.anyio
async def test_lifespan_initializes_storage() -> None:
    async with lifespan(app):
        assert app.state.storage.list_rows("models")


def test_get_storage_creates_missing_state() -> None:
    fake_app = Mock()
    fake_app.state = Mock()
    del fake_app.state.storage
    request = Mock(app=fake_app)
    storage = get_storage(request)
    assert isinstance(storage, Storage)


def test_not_found_handler_includes_security_headers() -> None:
    """Ensure custom 404 handler responds with hardened headers."""
    client = TestClient(app)
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert response.headers["Content-Security-Policy"] == "default-src 'self'"


def test_dashboard_and_healthz() -> None:
    client = TestClient(app)
    health = client.get("/healthz")
    dashboard = client.get("/")
    status = client.get("/api/status")
    assert health.json() == {"status": "ok"}
    assert "ModelMapper" in dashboard.text
    assert status.json()["status"] == "ModelMapper Online"


def test_robots_and_sitemap_endpoints() -> None:
    """Ensure scanner-noise endpoints exist and return expected content types."""
    client = TestClient(app)

    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert "User-agent" in robots.text
    assert robots.headers["content-type"].startswith("text/plain")

    sitemap = client.get("/sitemap.xml")
    assert sitemap.status_code == 200
    assert sitemap.text.startswith("<?xml")
    assert sitemap.headers["content-type"].startswith("application/xml")


def test_storage_seed_and_run_lifecycle(storage: Storage) -> None:
    assert len(storage.list_rows("models")) >= 5
    prompt = storage.list_rows("prompts")[0]
    model = storage.list_rows("models")[0]
    run_id = storage.create_run(RunCreate(prompt_id=prompt["id"], model_id=model["id"], params_json={"temperature": 0}))
    assert storage.get_by_id("runs", run_id)["status"] == "pending"
    storage.update_run_status(run_id, RunStatus.RUNNING)
    storage.update_run_status(
        run_id,
        RunStatus.SUCCEEDED,
        response_text="ok",
        tokens_in=1000,
        tokens_out=2000,
        raw_metadata={"provider": "test"},
    )
    run = storage.get_by_id("runs", run_id)
    assert run["status"] == "succeeded"
    assert run["estimated_cost_usd"] == 0.0
    storage.update_model(
        model["id"],
        ModelCreate(provider_id=model["provider_id"], model_name="updated-local", display_name="Updated Local", enabled=False),
    )
    updated_model = storage.get_by_id("models", model["id"])
    assert updated_model["model_name"] == "updated-local"
    assert updated_model["enabled"] == 0
    storage.purge_run_text(run_id)
    assert storage.get_by_id("runs", run_id)["response_text"] == ""
    assert "runs" in storage.export_data()


def test_storage_errors_and_secret_store(storage: Storage) -> None:
    with pytest.raises(ValueError, match="Prompt and model"):
        storage.create_run(RunCreate(prompt_id=999, model_id=999))
    with pytest.raises(ValueError, match="Unknown storage table"):
        storage.list_rows("bad")
    storage.store_secret_for_provider(1, "encrypted", "finger")
    assert storage.get_secret_for_provider(1) == "encrypted"
    assert storage.get_secret_for_provider(999) is None
    assert normalize_db_value({"a": 1}) == '{"a": 1}'


def test_http_client_url_validation() -> None:
    local = SafeHttpClient(timeout_seconds=1, allow_local_network=True)
    local.validate_url("http://localhost:11434/api/tags")
    with pytest.raises(UnsafeProviderUrlError, match="http or https"):
        local.validate_url("file:///tmp/key")
    with pytest.raises(UnsafeProviderUrlError, match="only allowed"):
        local.validate_url("http://example.com")
    with pytest.raises(UnsafeProviderUrlError, match="private network"):
        SafeHttpClient(timeout_seconds=1).validate_url("http://127.0.0.1:11434")
    with pytest.raises(UnsafeProviderUrlError, match="hostname"):
        local.validate_url("https:///missing")
    with patch_socket_failure():
        assert SafeHttpClient._is_private_host("unresolvable.invalid") is False
    assert SafeHttpClient._is_private_host("8.8.8.8") is False


@pytest.mark.anyio
async def test_provider_success_and_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, timeout: int, follow_redirects: bool) -> None:
            self.timeout = timeout
            self.follow_redirects = follow_redirects

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, json: dict[str, object], headers: dict[str, str] | None = None) -> object:
            return Mock(
                status_code=200,
                json=lambda: {"response": "hello", "eval_count": 2, "prompt_eval_count": 1},
            )

        async def get(self, url: str, headers: dict[str, str] | None = None) -> object:
            return Mock(
                status_code=200,
                json=lambda: {
                    "models": [
                        {"name": "llama3:latest", "size": 4_700_000_000},
                        {"name": "mistral:7b", "size": "not-a-number"},
                        {"name": ""},
                    ]
                },
            )

    monkeypatch.setattr("src.services.http_client.httpx.AsyncClient", FakeClient)
    provider = ConfigurableHttpProvider(SecretsResolver())
    request = ModelRunRequest(
        provider={
            "id": 1,
            "base_url": "http://localhost:11434",
            "endpoint_style": "ollama_generate",
            "auth_scheme": "none",
            "timeout_seconds": 1,
            "allow_local_network": True,
        },
        model={"model_name": "llama3.1"},
        prompt={"prompt_text": "hello"},
        params={},
    )
    result = await provider.generate(request)
    assert result.status == RunStatus.SUCCEEDED
    assert result.response_text == "hello"
    assert await provider.test_connection(request.provider) is True
    assert await provider.list_ollama_models(request.provider) == [
        {"name": "llama3:latest", "size_bytes": 4_700_000_000},
        {"name": "mistral:7b", "size_bytes": 0},
    ]
    assert safe_error(Exception("bad\nerror")) == "bad error"
    unsupported = request.model_copy(deep=True)
    unsupported.provider["endpoint_style"] = "unknown"
    assert (await provider.generate(unsupported)).status == RunStatus.FAILED
    with pytest.raises(ValueError, match="Ollama"):
        await provider.list_ollama_models(unsupported.provider)


@pytest.mark.anyio
async def test_provider_openai_and_header_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, timeout: int, follow_redirects: bool) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, json: dict[str, object], headers: dict[str, str] | None = None) -> object:
            return Mock(
                status_code=200,
                json=lambda: {"choices": [{"message": {"content": "chat"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 4}},
            )

    monkeypatch.setattr("src.services.http_client.httpx.AsyncClient", FakeClient)
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    provider = ConfigurableHttpProvider(SecretsResolver())
    request = ModelRunRequest(
        provider={
            "id": 2,
            "base_url": "https://api.example.com",
            "endpoint_style": "openai_chat",
            "auth_scheme": "bearer",
            "timeout_seconds": 1,
            "allow_local_network": False,
            "api_key_source": "env",
            "api_key_env_var": "OPENAI_API_KEY",
        },
        model={"model_name": "gpt"},
        prompt={"prompt_text": "hello"},
        params={},
    )
    result = await provider.generate(request)
    assert result.response_text == "chat"
    assert result.tokens_out == 4


@pytest.mark.anyio
async def test_provider_completion_errors_and_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    class ErrorClient:
        def __init__(self, timeout: int, follow_redirects: bool) -> None:
            pass

        async def __aenter__(self) -> "ErrorClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, json: dict[str, object], headers: dict[str, str] | None = None) -> object:
            if "timeout" in json:
                raise httpx.TimeoutException("timeout")
            if "broken" in json:
                raise httpx.HTTPError("api_key: leaked")
            return Mock(status_code=500, json=lambda: {})

        async def get(self, url: str, headers: dict[str, str] | None = None) -> object:
            raise httpx.HTTPError("down")

    monkeypatch.setattr("src.services.http_client.httpx.AsyncClient", ErrorClient)
    monkeypatch.setenv("CUSTOM_KEY", "secret-value")
    provider = ConfigurableHttpProvider(SecretsResolver())
    base = {
        "id": 3,
        "base_url": "https://api.example.com",
        "endpoint_style": "openai_completions",
        "auth_scheme": "header",
        "header_name": "X-API-Key",
        "timeout_seconds": 1,
        "allow_local_network": False,
        "api_key_source": "env",
        "api_key_env_var": "CUSTOM_KEY",
    }
    request = ModelRunRequest(provider=base, model={"model_name": "gpt"}, prompt={"prompt_text": "hello"}, params={})
    assert (await provider.generate(request)).status == RunStatus.FAILED
    timeout_request = request.model_copy(deep=True)
    timeout_request.params["timeout"] = True
    assert (await provider.generate(timeout_request)).status == RunStatus.TIMEOUT
    broken_request = request.model_copy(deep=True)
    broken_request.params["broken"] = True
    assert (await provider.generate(broken_request)).status == RunStatus.FAILED
    assert await provider.test_connection(base) is False
    bad_header = {**base, "header_name": "bad header"}
    assert provider._headers({**base, "api_key_env_var": "MISSING", "api_key_source": "none"}) == {}
    with pytest.raises(ValueError, match="Invalid"):
        provider._headers(bad_header)
    with pytest.raises(ValueError, match="Unsupported"):
        provider._headers({**base, "auth_scheme": "basic"})
    assert ConfigurableHttpProvider._ollama_result(Mock(status_code=500), 0).status == RunStatus.FAILED
    assert ConfigurableHttpProvider._openai_chat_result(Mock(status_code=500), 0).status == RunStatus.FAILED
    completion = ConfigurableHttpProvider._openai_completion_result(
        Mock(status_code=200, json=lambda: {"choices": [{"text": "done"}], "usage": {"prompt_tokens": 1, "completion_tokens": 2}}),
        0,
    )
    assert completion.response_text == "done"
    assert ConfigurableHttpProvider._openai_completion_result(Mock(status_code=500), 0).status == RunStatus.FAILED


def test_secrets_resolver_modes(storage: Storage, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    resolver = SecretsResolver(store=storage, encryption=EncryptionService(Fernet.generate_key().decode("utf-8")))
    assert resolver.resolve({"api_key_source": "none"}) is None
    assert resolver.resolve({"api_key_source": "env", "api_key_env_var": "OPENAI_API_KEY"}) == "secret-value"
    fingerprint = resolver.store_encrypted(1, "encrypted-secret")
    assert fingerprint == fingerprint_secret("encrypted-secret")
    assert resolver.resolve({"id": 1, "api_key_source": "encrypted"}) == "encrypted-secret"
    with pytest.raises(ValueError, match="not set"):
        resolver.resolve({"api_key_source": "env", "api_key_env_var": "MISSING"})
    with pytest.raises(ValueError, match="Unsupported"):
        resolver.resolve({"api_key_source": "unknown"})
    with pytest.raises(ValueError, match="missing"):
        resolver.resolve({"api_key_source": "env"})
    with pytest.raises(ValueError, match="not configured"):
        SecretsResolver().resolve({"id": 1, "api_key_source": "encrypted"})
    with pytest.raises(ValueError, match="missing"):
        resolver.resolve({"id": 999, "api_key_source": "encrypted"})
    with pytest.raises(ValueError, match="not configured"):
        SecretsResolver().store_encrypted(1, "secret")


def test_evaluation_and_recommendations(storage: Storage) -> None:
    assert run_auto_check("none", "", "x") is None
    assert run_auto_check("exact", "Hello", " Hello ") is True
    assert run_auto_check("contains", "ell", "Hello") is True
    assert run_auto_check("regex", "H.llo", "Hello") is True
    assert run_auto_check("json_schema", "", '{"ok": true}') is True
    assert run_auto_check("json_schema", "", "[]") is False
    assert run_auto_check("json_schema", "", "not json") is False
    with pytest.raises(ValueError, match="Unsupported"):
        run_auto_check("bad", "", "")
    summary = comparison_summary([{"status": "succeeded", "total_latency_ms": 10, "accuracy_score": 5}])
    assert summary["average_accuracy_score"] == 5.0
    recommendation = recommend_model(
        DecisionCriteria(privacy_required=True),
        storage.list_rows("decision_rules"),
        storage.list_rows("models"),
        storage.comparison_rows(),
    )
    assert recommendation.model is not None
    assert recommend_model(DecisionCriteria(), [], [], []).model is None
    assert recommend_model(
        DecisionCriteria(task_type="coding", speed_priority=1),
        [{"priority": 1, "task_type": "writing", "recommended_model_id": 1, "rationale": "no"}],
        storage.list_rows("models"),
        [],
    ).model is not None
    assert recommend_model(
        DecisionCriteria(max_cost_per_1k=1, speed_priority=1, modality="vision"),
        [{"priority": 1, "max_cost_per_1k": 2, "speed_priority": 3, "modality": "text", "recommended_model_id": 1, "rationale": "no"}],
        storage.list_rows("models"),
        [{"model_id": storage.list_rows("models")[0]["id"], "accuracy_score": 4}],
    ).model is not None
    assert rule_matches(DecisionCriteria(speed_priority=1), {"speed_priority": 2}) is False
    assert rule_matches(DecisionCriteria(modality="vision"), {"speed_priority": 1, "modality": "text"}) is False
    assert recommend_model(
        DecisionCriteria(privacy_required=True),
        [],
        [{"id": 1, "enabled": True, "privacy_notes": "local model"}],
        [],
    ).model["id"] == 1


def test_redaction_and_rate_limit() -> None:
    assert "[REDACTED]" in redact_text("api_key: abc123")
    assert redact_mapping({"Authorization": "Bearer x"})["Authorization"] == "[REDACTED]"
    record = Mock(msg="token=abc", args=("secret: value",))
    assert RedactionFilter().filter(record) is True
    dict_record = Mock(msg="safe", args={"api_key": "abc", "plain": "token=x"})
    assert RedactionFilter().filter(dict_record) is True
    assert redact_mapping({"plain": "token=x"})["plain"] == "token= [REDACTED]"
    assert redact_mapping({"count": 1})["count"] == 1
    limiter = InMemoryRateLimiter(limit=1, window_seconds=60)
    assert limiter.allow("a") is True
    assert limiter.allow("a") is False
    limiter._events["expired"].append(0)
    assert limiter.allow("expired") is True


@pytest.mark.anyio
async def test_rate_limit_middleware_response() -> None:
    limiter = InMemoryRateLimiter(limit=0, window_seconds=60)
    request = Mock()
    request.url.path = "/runs"
    request.client.host = "test"
    response = await limiter(request, Mock())
    assert response.status_code == 429
    allowed_limiter = InMemoryRateLimiter(limit=1, window_seconds=60)
    ok_response = Mock(status_code=204)

    async def call_next(_request: object) -> object:
        return ok_response

    assert await allowed_limiter(request, call_next) is ok_response


def test_pages_and_api_endpoints() -> None:
    client = TestClient(app)
    for path in ["/providers", "/models", "/prompts", "/runs/new", "/runs", "/compare", "/benchmarks", "/guide", "/decision", "/exports"]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["Content-Security-Policy"] == "default-src 'self'"
    assert client.get("/api/providers").status_code == 200
    assert client.get("/api/models").status_code == 200
    assert client.get("/api/prompts").status_code == 200
    assert client.get("/api/runs").status_code == 200
    assert client.get("/api/exports").status_code == 200
    assert client.get("/api/benchmarks").status_code == 200


def test_local_only_rejects_public_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODELMAPPER_LOCAL_ONLY", "1")
    client = TestClient(app)
    response = client.post("/api/providers", json={"display_name": "Cloud", "base_url": "https://api.openai.com/v1"})
    assert response.status_code == 400
    assert "MODELMAPPER_LOCAL_ONLY" in response.json()["detail"]


def test_form_and_json_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODELMAPPER_LOCAL_ONLY", "0")
    client = TestClient(app)
    assert client.post("/api/providers", json={"display_name": "API Provider", "base_url": "https://api.example.com"}).json()["id"] > 0
    assert client.post(
        "/providers",
        data={
            "display_name": "Local Gateway",
            "provider_kind": "gateway",
            "base_url": "https://gateway.example.com",
            "endpoint_style": "openai_chat",
            "auth_scheme": "none",
            "api_key_source": "none",
        },
        follow_redirects=False,
    ).status_code == 303
    providers = client.get("/api/providers").json()
    provider_id = next(provider["id"] for provider in providers if provider["endpoint_style"] == "ollama_generate")
    assert client.post(
        "/api/models",
        json={"provider_id": provider_id, "model_name": "api-model", "display_name": "API Model"},
    ).json()["id"] > 0
    api_model_id = client.get("/api/models").json()[0]["id"]
    assert client.put(
        f"/api/models/{api_model_id}",
        json={
            "provider_id": provider_id,
            "model_name": "api-updated",
            "display_name": "API Updated",
            "context_window": 4096,
            "default_params": {"temperature": 0.1},
            "enabled": False,
        },
    ).json() == {"ok": True}
    assert client.post(
        "/models",
        data={
            "provider_id": provider_id,
            "model_name": "test-model",
            "display_name": "Test Model",
            "context_window": 1024,
            "default_params": "{}",
        },
        follow_redirects=False,
    ).status_code == 303
    assert client.post(
        f"/models/{api_model_id}",
        data={
            "provider_id": provider_id,
            "model_name": "form-updated",
            "display_name": "Form Updated",
            "context_window": 2048,
            "default_params": "{}",
            "supports_chat": "on",
            "enabled": "on",
        },
        follow_redirects=False,
    ).status_code == 303
    assert client.post("/prompt-sets", data={"name": "Set", "description": "Desc"}, follow_redirects=False).status_code == 303
    prompt_set_id = client.get("/api/prompt-sets").json()[0]["id"]
    assert client.post("/api/prompt-sets", json={"name": "API Set", "description": ""}).json()["id"] > 0
    assert client.post(
        "/prompts",
        data={"prompt_set_id": prompt_set_id, "category": "coding", "task_goal": "goal", "prompt_text": "prompt"},
        follow_redirects=False,
    ).status_code == 303
    assert client.post(
        "/api/prompts",
        json={"prompt_set_id": prompt_set_id, "category": "api", "task_goal": "goal", "prompt_text": "prompt"},
    ).json()["id"] > 0
    model_id = client.get("/api/models").json()[0]["id"]
    assert client.post(
        "/decision-rules",
        data={"priority": 2, "recommended_model_id": model_id, "rationale": "Use it."},
        follow_redirects=False,
    ).status_code == 303
    assert client.post("/decision", data={"task_type": "", "speed_priority": 3, "modality": "text"}).status_code == 200
    assert client.post(
        "/api/decision-rules",
        json={"priority": 3, "recommended_model_id": model_id, "rationale": "API rule."},
    ).json()["id"] > 0
    assert client.post("/api/providers/999/test").json() == {"ok": False}
    async def fake_test_connection(self: object, provider: dict[str, object]) -> bool:
        return True

    monkeypatch.setattr("src.routers.providers.ConfigurableHttpProvider.test_connection", fake_test_connection)
    assert client.post(f"/api/providers/{provider_id}/test").json() == {"ok": True}
    monkeypatch.setenv("MODELMAPPER_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
    assert "fingerprint" in client.post(
        f"/api/providers/{provider_id}/secrets",
        json={"provider_id": provider_id, "api_key": "secret-value"},
    ).json()
    with pytest.raises(ValueError, match="Provider id mismatch"):
        client.post(f"/api/providers/{provider_id}/secrets", json={"provider_id": provider_id + 1, "api_key": "secret-value"})

    # Unique tag names so re-runs on a persistent ./data/modelmapper.db do not treat models as already synced.
    sync_uid = uuid.uuid4().hex[:12]
    name_a = f"sync-fresh-a-{sync_uid}:latest"
    name_b = f"sync-fresh-b-{sync_uid}:7b"

    async def fake_list_ollama_models(self: object, provider: dict[str, object]) -> list[dict[str, object]]:
        return [
            {"name": name_a, "size_bytes": 4_700_000_000},
            {"name": name_b, "size_bytes": 4_100_000_000},
            {"name": "form-updated", "size_bytes": 1_000_000_000},
        ]

    monkeypatch.setattr("src.routers.models.ConfigurableHttpProvider.list_ollama_models", fake_list_ollama_models)
    ollama_models = client.get(f"/api/providers/{provider_id}/ollama-models").json()
    assert {entry["name"] for entry in ollama_models["models"]} == {name_a, name_b, "form-updated"}
    assert ollama_models["models"][0]["size_bytes"] >= 0
    assert "form-updated" in ollama_models["saved"]

    sync_payload = client.post(f"/api/providers/{provider_id}/ollama-sync").json()
    assert set(sync_payload["created"]) == {name_a, name_b}
    assert sync_payload["skipped"] == ["form-updated"]
    refreshed = client.get(f"/api/providers/{provider_id}/ollama-models").json()
    assert {name_a, name_b, "form-updated"}.issubset(set(refreshed["saved"]))
    repeat_sync = client.post(f"/api/providers/{provider_id}/ollama-sync").json()
    assert repeat_sync["created"] == []
    assert set(repeat_sync["skipped"]) >= {name_a, name_b, "form-updated"}
    assert client.post("/api/providers/999/ollama-sync").status_code == 404
    openai_provider_id = next(
        provider["id"] for provider in client.get("/api/providers").json() if provider["endpoint_style"] != "ollama_generate"
    )
    assert client.post(f"/api/providers/{openai_provider_id}/ollama-sync").status_code == 400
    assert client.get(f"/api/providers/{openai_provider_id}/ollama-models").status_code == 400


@pytest.mark.anyio
async def test_run_routes_and_runner(monkeypatch: pytest.MonkeyPatch, storage: Storage) -> None:
    class FakeProvider:
        async def generate(self, request: ModelRunRequest) -> object:
            return Mock(
                status=RunStatus.SUCCEEDED,
                response_text="ok",
                error_text="",
                time_to_first_token_ms=1,
                total_latency_ms=2,
                tokens_in=3,
                tokens_out=4,
                raw_metadata={"ok": True},
            )

    prompt_id = storage.list_rows("prompts")[0]["id"]
    model_id = storage.list_rows("models")[0]["id"]
    runner = ComparisonRunner(storage=storage, provider=FakeProvider(), per_run_timeout_seconds=1)  # type: ignore[arg-type]
    run_ids = await runner.run_many(prompt_id, [model_id], {})
    assert storage.get_by_id("runs", run_ids[0])["status"] == "succeeded"
    await runner._run_one(999, {})
    model_missing_provider = storage.create_model(ModelCreate(provider_id=999, model_name="orphan", display_name="Orphan"))
    with pytest.raises(ValueError, match="Model provider"):
        storage.create_run(RunCreate(prompt_id=prompt_id, model_id=model_missing_provider))
    with pytest.raises(ValueError, match="missing provider"):
        runner._build_request({"provider_id": 999, "model_id": model_id, "prompt_id": prompt_id, "params_json": "{}"})
    assert isinstance(build_runner(storage), ComparisonRunner)

    class FakeRouteRunner:
        async def run_many(self, prompt_id: int, model_ids: list[int], params: dict[str, object]) -> list[int]:
            return [11, 12]

    monkeypatch.setattr("src.routers.runs.build_runner", lambda _storage: FakeRouteRunner())
    client = TestClient(app)
    assert client.post(
        "/runs",
        data={"prompt_id": prompt_id, "model_ids": [model_id], "params_json": "{}"},
        follow_redirects=False,
    ).status_code == 303
    assert client.post("/api/runs/compare", json={"prompt_id": prompt_id, "model_ids": [model_id]}).json() == {"run_ids": [11, 12]}
    run_id = client.post("/api/runs", json={"prompt_id": prompt_id, "model_id": model_id, "params_json": {}}).json()["id"]
    assert client.post(
        "/api/evaluations",
        json={"run_id": run_id, "accuracy_score": 5, "style_score": 4},
    ).json()["id"] > 0
    assert client.post(f"/api/runs/{run_id}/purge-text").json() == {"ok": True}


@pytest.mark.anyio
async def test_runner_timeout(storage: Storage) -> None:
    class SlowProvider:
        async def generate(self, request: ModelRunRequest) -> object:
            import asyncio

            await asyncio.sleep(0.05)
            return Mock(status=RunStatus.SUCCEEDED)

    prompt_id = storage.list_rows("prompts")[0]["id"]
    model_id = storage.list_rows("models")[0]["id"]
    runner = ComparisonRunner(storage=storage, provider=SlowProvider(), per_run_timeout_seconds=0)  # type: ignore[arg-type]
    run_ids = await runner.run_many(prompt_id, [model_id], {})
    assert storage.get_by_id("runs", run_ids[0])["status"] == "timeout"


def test_storage_estimate_cost_missing_model(storage: Storage) -> None:
    assert storage.estimate_cost(999, tokens_in=1, tokens_out=1) == 0.0
    prompt_id = storage.list_rows("prompts")[0]["id"]
    model_id = storage.list_rows("models")[0]["id"]
    run_id = storage.create_run(RunCreate(prompt_id=prompt_id, model_id=model_id))
    with storage.connect() as connection:
        connection.execute("UPDATE runs SET model_id = 999 WHERE id = ?", (run_id,))
    assert storage.estimate_cost(run_id, tokens_in=1, tokens_out=1) == 0.0


def test_storage_connection_closes_on_error(tmp_path: object) -> None:
    db_path = getattr(tmp_path, "joinpath")("broken.db")
    storage = Storage(str(db_path))
    with pytest.raises(sqlite3.OperationalError):
        with storage.connect() as connection:
            connection.execute("SELECT * FROM missing")


class patch_socket_failure:
    def __enter__(self) -> None:
        self._patcher = pytest.MonkeyPatch()
        self._patcher.setattr("src.services.http_client.socket.getaddrinfo", self._fail)

    def __exit__(self, *_args: object) -> None:
        self._patcher.undo()

    @staticmethod
    def _fail(*_args: object) -> object:
        raise socket.gaierror


@pytest.mark.anyio
async def test_benchmark_api_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODELMAPPER_LOCAL_ONLY", "0")

    async def fake_gen(self: object, request: object) -> ModelRunResult:
        return ModelRunResult(
            status=RunStatus.SUCCEEDED,
            response_text="range(1, n+1)",
            time_to_first_token_ms=1,
            total_latency_ms=2,
            tokens_in=3,
            tokens_out=4,
            raw_metadata={},
        )

    monkeypatch.setattr("src.services.runner.ConfigurableHttpProvider.generate", fake_gen)
    client = TestClient(app)
    model_id = client.get("/api/models").json()[0]["id"]
    set_id = client.post("/api/prompt-sets", json={"name": "BenchTestSet", "description": "unit"}).json()["id"]
    assert (
        client.post(
            "/api/prompts",
            json={
                "prompt_set_id": set_id,
                "category": "coding",
                "task_goal": "sample",
                "prompt_text": "return code only",
            },
        ).json()["id"]
        > 0
    )
    response = client.post(
        "/api/benchmarks",
        json={
            "name": "Unit benchmark",
            "prompt_set_id": set_id,
            "model_ids": [model_id],
            "sweep": [{"temperature": 0.0, "max_tokens": 128}],
            "repeat_count": 1,
        },
    )
    assert response.status_code == 200
    session_id = response.json()["session_id"]
    assert response.json()["scheduled_run_count"] == 1
    detail = client.get(f"/api/benchmarks/{session_id}").json()
    assert len(detail["per_model"]) == 1
    assert len(detail["rows"]) == 1
    assert detail["rows"][0]["status"] == "succeeded"
