import logging
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from src.schemas import VaultAccessRequest

app = FastAPI()
LOGGER = logging.getLogger(__name__)

# 1. Force headers onto EVERY response, including internal errors
@app.exception_handler(404)
async def custom_404_handler(request: Request, _: Exception) -> JSONResponse:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Cross-Origin-Resource-Policy": "same-origin",
    }
    LOGGER.info("Not found path=%s", request.url.path)
    return JSONResponse(status_code=404, content={"detail": "Not Found"}, headers=headers)

# 2. Explicitly define these so they return 200 OK with correct headers
# Explicitly handle spider targets to prevent 'Non-Storable' 404s
@app.get("/robots.txt", include_in_schema=False)
def robots() -> Response:
    return Response(content="User-agent: *\nDisallow: /", media_type="text/plain")

@app.get("/sitemap.xml", include_in_schema=False)
def sitemap() -> Response:
    return Response(content='<?xml version="1.0" encoding="UTF-8"?><urlset></urlset>', media_type="application/xml")

@app.middleware("http")
async def add_security_headers(request: Any, call_next: Any) -> Any:  # pragma: no cover
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'"
    # Hide the fact that we are using Python/Uvicorn
    response.headers["Server"] = "Hidden"
    # NEW: Fix for WARN-NEW: Non-Storable Content [10049]
    # Tells browsers and proxies "Do not store this in cache ever"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    # NEW: Fix for WARN-NEW: Cross-Origin-Resource-Policy [90004]
    # Prevents other domains from reading the response (Anti-Spectre/Meltdown defense)
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"

    return response

@app.get("/")
async def read_root() -> dict[str, str]:
    return {"status": "Secure Vault Online", "version": "1.0.0"}

def process_vault_entry(data: dict[str, Any]) -> bool:
    try:
        request = VaultAccessRequest(**data)
        LOGGER.info("Access granted for username=%s", request.username)
        return True
    except ValidationError as error:
        LOGGER.warning("Invalid input rejected: %s", error.errors())
        return False
