import json
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.templating import Jinja2Templates

from src.dependencies import get_storage
from src.schemas import ModelCreate
from src.services.providers import ConfigurableHttpProvider, ProviderDiscoveryError
from src.services.secrets import SecretsResolver
from src.services.storage import Storage

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


@router.get("/models", response_class=HTMLResponse)
def models_page(request: Request, storage: Storage = Depends(get_storage)) -> HTMLResponse:
    providers = storage.list_rows("providers")
    provider_names = {int(provider["id"]): provider["display_name"] for provider in providers}
    return templates.TemplateResponse(
        request,
        "models.html",
        {"models": storage.list_rows("models"), "providers": providers, "provider_names": provider_names},
    )


@router.get("/api/models")
def list_models(storage: Storage = Depends(get_storage)) -> list[dict[str, Any]]:
    return storage.list_rows("models")


@router.post("/api/models")
def create_model(model: ModelCreate, storage: Storage = Depends(get_storage)) -> dict[str, int]:
    return {"id": storage.create_model(model)}


@router.put("/api/models/{model_id}")
def update_model(model_id: int, model: ModelCreate, storage: Storage = Depends(get_storage)) -> dict[str, bool]:
    if storage.get_by_id("models", model_id) is None:
        raise HTTPException(status_code=404, detail="Model not found")
    storage.update_model(model_id, model)
    return {"ok": True}


@router.get("/api/providers/{provider_id}/ollama-models")
async def list_ollama_provider_models(provider_id: int, storage: Storage = Depends(get_storage)) -> dict[str, Any]:
    provider = _require_ollama_provider(storage, provider_id)
    try:
        models = await ConfigurableHttpProvider(SecretsResolver(store=storage)).list_ollama_models(provider)
    except ProviderDiscoveryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"models": models, "saved": _saved_model_names(storage, provider_id)}


@router.post("/api/providers/{provider_id}/ollama-sync")
async def sync_ollama_provider_models(provider_id: int, storage: Storage = Depends(get_storage)) -> dict[str, list[str]]:
    provider = _require_ollama_provider(storage, provider_id)
    try:
        models = await ConfigurableHttpProvider(SecretsResolver(store=storage)).list_ollama_models(provider)
    except ProviderDiscoveryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    existing = set(_saved_model_names(storage, provider_id))
    created: list[str] = []
    skipped: list[str] = []
    for entry in models:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        if name in existing:
            skipped.append(name)
            continue
        storage.create_model(
            ModelCreate(
                provider_id=provider_id,
                model_name=name,
                display_name=name,
                context_window=8192,
                default_params={"temperature": 0.2, "top_p": 0.9, "max_tokens": 1024},
                privacy_notes="Local Ollama model; prompts stay on this machine.",
            )
        )
        created.append(name)
        existing.add(name)
    return {"created": created, "skipped": skipped}


def _require_ollama_provider(storage: Storage, provider_id: int) -> dict[str, Any]:
    provider = storage.get_by_id("providers", provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    if provider["endpoint_style"] != "ollama_generate":
        raise HTTPException(status_code=400, detail="Provider is not configured for Ollama")
    return provider


def _saved_model_names(storage: Storage, provider_id: int) -> list[str]:
    return sorted(
        {
            str(model["model_name"])
            for model in storage.list_rows("models")
            if int(model["provider_id"]) == provider_id
        }
    )


@router.post("/models")
def create_model_form(
    provider_id: int = Form(...),
    model_name: str = Form(...),
    display_name: str = Form(...),
    context_window: int = Form(0),
    supports_chat: bool = Form(True),
    supports_tools: bool = Form(False),
    supports_vision: bool = Form(False),
    default_params: str = Form("{}"),
    input_cost_per_1k: float = Form(0.0),
    output_cost_per_1k: float = Form(0.0),
    privacy_notes: str = Form(""),
    enabled: bool = Form(True),
    storage: Storage = Depends(get_storage),
) -> RedirectResponse:
    storage.create_model(
        build_model_create(
            provider_id=provider_id,
            model_name=model_name,
            display_name=display_name,
            context_window=context_window,
            supports_chat=supports_chat,
            supports_tools=supports_tools,
            supports_vision=supports_vision,
            default_params=default_params,
            input_cost_per_1k=input_cost_per_1k,
            output_cost_per_1k=output_cost_per_1k,
            privacy_notes=privacy_notes,
            enabled=enabled,
        )
    )
    return RedirectResponse("/models", status_code=303)


@router.post("/models/{model_id}")
def update_model_form(
    model_id: int,
    provider_id: int = Form(...),
    model_name: str = Form(...),
    display_name: str = Form(...),
    context_window: int = Form(0),
    supports_chat: bool = Form(False),
    supports_tools: bool = Form(False),
    supports_vision: bool = Form(False),
    default_params: str = Form("{}"),
    input_cost_per_1k: float = Form(0.0),
    output_cost_per_1k: float = Form(0.0),
    privacy_notes: str = Form(""),
    enabled: bool = Form(False),
    storage: Storage = Depends(get_storage),
) -> RedirectResponse:
    if storage.get_by_id("models", model_id) is None:
        raise HTTPException(status_code=404, detail="Model not found")
    storage.update_model(
        model_id,
        build_model_create(
            provider_id=provider_id,
            model_name=model_name,
            display_name=display_name,
            context_window=context_window,
            supports_chat=supports_chat,
            supports_tools=supports_tools,
            supports_vision=supports_vision,
            default_params=default_params,
            input_cost_per_1k=input_cost_per_1k,
            output_cost_per_1k=output_cost_per_1k,
            privacy_notes=privacy_notes,
            enabled=enabled,
        ),
    )
    return RedirectResponse("/models", status_code=303)


def build_model_create(
    *,
    provider_id: int,
    model_name: str,
    display_name: str,
    context_window: int,
    supports_chat: bool,
    supports_tools: bool,
    supports_vision: bool,
    default_params: str,
    input_cost_per_1k: float,
    output_cost_per_1k: float,
    privacy_notes: str,
    enabled: bool,
) -> ModelCreate:
    return ModelCreate(
        provider_id=provider_id,
        model_name=model_name,
        display_name=display_name,
        context_window=context_window,
        supports_chat=supports_chat,
        supports_tools=supports_tools,
        supports_vision=supports_vision,
        default_params=json.loads(default_params),
        input_cost_per_1k=input_cost_per_1k,
        output_cost_per_1k=output_cost_per_1k,
        privacy_notes=privacy_notes,
        enabled=enabled,
    )
