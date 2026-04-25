from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.templating import Jinja2Templates

from src.dependencies import get_storage
from src.schemas import ProviderCreate, SecretCreate
from src.services.crypto import EncryptionService
from src.services.local_config import is_allowed_base_url_in_local_mode, is_local_only_mode
from src.services.providers import ConfigurableHttpProvider
from src.services.secrets import SecretsResolver
from src.services.storage import Storage

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


@router.get("/providers", response_class=HTMLResponse)
def providers_page(request: Request, storage: Storage = Depends(get_storage)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "providers.html",
        {"providers": redacted_providers(storage.list_rows("providers"))},
    )


@router.get("/api/providers")
def list_providers(storage: Storage = Depends(get_storage)) -> list[dict[str, object]]:
    return redacted_providers(storage.list_rows("providers"))


@router.post("/api/providers")
def create_provider(provider: ProviderCreate, storage: Storage = Depends(get_storage)) -> dict[str, int]:
    if is_local_only_mode() and not is_allowed_base_url_in_local_mode(provider.base_url):
        raise HTTPException(
            status_code=400,
            detail="MODELMAPPER_LOCAL_ONLY is enabled: base_url must be localhost, loopback, or a private/LAN address. Set MODELMAPPER_LOCAL_ONLY=0 to allow public API URLs.",
        )
    return {"id": storage.create_provider(provider)}


@router.post("/providers")
def create_provider_form(
    display_name: str = Form(...),
    provider_kind: str = Form("openai-compatible"),
    base_url: str = Form(...),
    endpoint_style: str = Form("openai_chat"),
    auth_scheme: str = Form("bearer"),
    api_key_source: str = Form("env"),
    api_key_env_var: str = Form(""),
    allow_local_network: bool = Form(False),
    storage: Storage = Depends(get_storage),
) -> RedirectResponse:
    if is_local_only_mode() and not is_allowed_base_url_in_local_mode(base_url):
        raise HTTPException(
            status_code=400,
            detail="MODELMAPPER_LOCAL_ONLY is enabled: base_url must be localhost, loopback, or a private/LAN address. Set MODELMAPPER_LOCAL_ONLY=0 to allow public API URLs.",
        )
    storage.create_provider(
        ProviderCreate(
            display_name=display_name,
            provider_kind=provider_kind,
            base_url=base_url,
            endpoint_style=endpoint_style,  # type: ignore[arg-type]
            auth_scheme=auth_scheme,  # type: ignore[arg-type]
            api_key_source=api_key_source,  # type: ignore[arg-type]
            api_key_env_var=api_key_env_var or None,
            allow_local_network=allow_local_network,
        )
    )
    return RedirectResponse("/providers", status_code=303)


@router.post("/api/providers/{provider_id}/secrets")
def store_provider_secret(provider_id: int, secret: SecretCreate, storage: Storage = Depends(get_storage)) -> dict[str, str]:
    if provider_id != secret.provider_id:
        raise ValueError("Provider id mismatch")
    fingerprint = SecretsResolver(store=storage, encryption=EncryptionService()).store_encrypted(
        provider_id,
        secret.api_key,
    )
    return {"fingerprint": fingerprint}


@router.post("/api/providers/{provider_id}/test")
async def test_provider(provider_id: int, storage: Storage = Depends(get_storage)) -> dict[str, bool]:
    provider = storage.get_by_id("providers", provider_id)
    if provider is None:
        return {"ok": False}
    ok = await ConfigurableHttpProvider(SecretsResolver(store=storage)).test_connection(provider)
    return {"ok": ok}


def redacted_providers(providers: list[dict[str, object]]) -> list[dict[str, object]]:
    return [{**provider, "api_key_env_var": provider.get("api_key_env_var") or ""} for provider in providers]
