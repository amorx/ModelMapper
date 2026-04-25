import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from src.routers import benchmarks, decision, exports, models, prompts, providers, runs
from src.services.logging import configure_logging
from src.services.rate_limit import InMemoryRateLimiter
from src.services.storage import Storage

configure_logging()


@asynccontextmanager
async def lifespan(application: FastAPI) -> Any:
    application.state.storage.init_db()
    yield


app = FastAPI(title="ModelMapper", version="1.0.0", lifespan=lifespan)
LOGGER = logging.getLogger(__name__)
templates = Jinja2Templates(directory="src/templates")
app.mount("/static", StaticFiles(directory="src/static"), name="static")
app.state.storage = Storage()
app.state.storage.init_db()
app.middleware("http")(InMemoryRateLimiter(limit=60, window_seconds=60))


@app.exception_handler(404)
async def custom_404_handler(request: Request, _: Exception) -> JSONResponse:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": "default-src 'self'",
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Cross-Origin-Resource-Policy": "same-origin",
    }
    LOGGER.info("Not found path=%s", request.url.path)
    return JSONResponse(status_code=404, content={"detail": "Not Found"}, headers=headers)


@app.get("/robots.txt", include_in_schema=False)
def robots() -> Response:
    return Response(content="User-agent: *\nDisallow: /", media_type="text/plain")

@app.get("/sitemap.xml", include_in_schema=False)
def sitemap() -> Response:
    return Response(content='<?xml version="1.0" encoding="UTF-8"?><urlset></urlset>', media_type="application/xml")


@app.middleware("http")
async def add_security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'"
    response.headers["Server"] = "Hidden"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/")
async def dashboard(request: Request) -> Response:
    storage: Storage = request.app.state.storage
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "providers": storage.list_rows("providers"),
            "models": storage.list_rows("models"),
            "runs": storage.comparison_rows()[:10],
        },
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status")
async def api_status() -> dict[str, str]:
    return {"status": "ModelMapper Online", "version": "1.0.0"}


def process_modelmapper_entry(data: dict[str, Any]) -> bool:
    name = str(data.get("name", ""))
    valid = 1 <= len(name) <= 120
    if valid:
        LOGGER.info("ModelMapper entry accepted")
    else:
        LOGGER.warning("Invalid ModelMapper entry rejected")
    return valid


app.include_router(providers.router)
app.include_router(models.router)
app.include_router(prompts.router)
app.include_router(runs.router)
app.include_router(benchmarks.router)
app.include_router(decision.router)
app.include_router(exports.router)
