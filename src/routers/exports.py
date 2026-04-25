from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from src.dependencies import get_storage
from src.services.storage import Storage

router = APIRouter()


@router.get("/exports")
def export_page(storage: Storage = Depends(get_storage)) -> JSONResponse:
    return JSONResponse(storage.export_data())


@router.get("/api/exports")
def export_data(storage: Storage = Depends(get_storage)) -> dict[str, list[dict[str, Any]]]:
    return storage.export_data()


@router.post("/api/runs/{run_id}/purge-text")
def purge_run_text(run_id: int, storage: Storage = Depends(get_storage)) -> dict[str, bool]:
    storage.purge_run_text(run_id)
    return {"ok": True}
