import json
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from starlette.templating import Jinja2Templates

from src.dependencies import get_storage
from src.schemas import BenchmarkSessionCreate
from src.services.benchmarks import (
    compute_benchmark_summary,
    create_benchmark_runs_and_session,
    execute_benchmark_runs,
    max_benchmark_runs,
)
from src.services.runner import build_runner
from src.services.storage import Storage

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


@router.get("/benchmarks", response_class=HTMLResponse)
def benchmarks_list(request: Request, storage: Storage = Depends(get_storage)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "benchmarks_list.html",
        {
            "sessions": storage.list_benchmark_sessions(),
            "prompt_sets": storage.list_rows("prompt_sets"),
            "models": storage.list_rows("models"),
            "max_runs": max_benchmark_runs(),
        },
    )


@router.get("/benchmarks/{session_id}", response_class=HTMLResponse)
def benchmark_detail_page(
    request: Request,
    session_id: int,
    storage: Storage = Depends(get_storage),
) -> HTMLResponse:
    try:
        summary = compute_benchmark_summary(storage, session_id)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    return templates.TemplateResponse(
        request,
        "benchmark_detail.html",
        {"summary": summary},
    )


@router.get("/api/benchmarks")
def api_list_benchmarks(storage: Storage = Depends(get_storage)) -> list[dict[str, Any]]:
    return storage.list_benchmark_sessions(100)


@router.get("/api/benchmarks/{session_id}")
def api_benchmark_detail(session_id: int, storage: Storage = Depends(get_storage)) -> dict[str, Any]:
    try:
        return compute_benchmark_summary(storage, session_id)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.post("/api/benchmarks")
async def api_start_benchmark(
    body: BenchmarkSessionCreate,
    storage: Storage = Depends(get_storage),
) -> dict[str, Any]:
    try:
        session_id, run_ids, total = create_benchmark_runs_and_session(storage, body)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    runner = build_runner(storage)
    try:
        await execute_benchmark_runs(storage, runner, session_id, run_ids)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(err)) from err
    return {"session_id": session_id, "scheduled_run_count": total, "run_ids": run_ids}


@router.post("/benchmarks")
async def form_start_benchmark(
    request: Request,
    name: str = Form("Benchmark"),
    prompt_set_id: int = Form(...),
    model_ids: list[int] = Form(...),
    sweep_json: str = Form(...),
    repeat_count: int = Form(1),
    notes: str = Form(""),
    storage: Storage = Depends(get_storage),
) -> RedirectResponse:
    if not model_ids:
        raise HTTPException(status_code=400, detail="Select at least one model")
    try:
        sweep = json.loads(sweep_json)
    except json.JSONDecodeError as err:
        raise HTTPException(status_code=400, detail="sweep_json must be valid JSON array") from err
    if not isinstance(sweep, list) or not sweep:
        raise HTTPException(status_code=400, detail="sweep must be a non-empty JSON array of objects")
    try:
        body = BenchmarkSessionCreate(
            name=name,
            prompt_set_id=prompt_set_id,
            model_ids=model_ids,
            sweep=[dict(item) for item in sweep if isinstance(item, dict)],
            repeat_count=repeat_count,
            notes=notes,
        )
    except ValidationError as err:
        raise HTTPException(status_code=422, detail=err.errors()) from err
    try:
        session_id, run_ids, _total = create_benchmark_runs_and_session(storage, body)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    runner = build_runner(storage)
    await execute_benchmark_runs(storage, runner, session_id, run_ids)
    return RedirectResponse(f"/benchmarks/{session_id}", status_code=303)
