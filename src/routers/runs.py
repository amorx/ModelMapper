import json
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.templating import Jinja2Templates

from src.dependencies import get_storage
from src.schemas import EvaluationCreate, RunCreate
from src.services.evaluation import comparison_summary, run_auto_check
from src.services.runner import build_runner
from src.services.storage import Storage

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


@router.get("/runs/new", response_class=HTMLResponse)
def new_run_page(request: Request, storage: Storage = Depends(get_storage)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "run_new.html",
        {"prompts": storage.list_rows("prompts"), "models": storage.list_rows("models")},
    )


@router.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request, storage: Storage = Depends(get_storage)) -> HTMLResponse:
    rows = storage.comparison_rows()
    return templates.TemplateResponse(request, "runs.html", {"runs": rows, "summary": comparison_summary(rows)})


@router.get("/compare", response_class=HTMLResponse)
def compare_page(request: Request, storage: Storage = Depends(get_storage)) -> HTMLResponse:
    rows = storage.comparison_rows()
    return templates.TemplateResponse(request, "compare.html", {"runs": rows, "summary": comparison_summary(rows)})


@router.get("/guide", response_class=HTMLResponse)
def guide_page(request: Request, storage: Storage = Depends(get_storage)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "guide.html",
        {"models": storage.list_rows("models"), "runs": storage.comparison_rows()},
    )


@router.get("/api/runs")
def list_runs(storage: Storage = Depends(get_storage)) -> list[dict[str, Any]]:
    return storage.comparison_rows()


@router.post("/api/runs")
def create_run(run: RunCreate, storage: Storage = Depends(get_storage)) -> dict[str, int]:
    return {"id": storage.create_run(run)}


@router.post("/runs")
async def launch_run_form(
    prompt_id: int = Form(...),
    model_ids: list[int] = Form(...),
    params_json: str = Form("{}"),
    storage: Storage = Depends(get_storage),
) -> RedirectResponse:
    await build_runner(storage).run_many(prompt_id, model_ids, json.loads(params_json))
    return RedirectResponse("/runs", status_code=303)


@router.post("/api/runs/compare")
async def launch_comparison(payload: dict[str, Any], storage: Storage = Depends(get_storage)) -> dict[str, list[int]]:
    runner = build_runner(storage)
    run_ids = await runner.run_many(
        int(payload["prompt_id"]),
        [int(item) for item in payload["model_ids"]],
        dict(payload.get("params_json") or {}),
    )
    return {"run_ids": run_ids}


@router.post("/api/evaluations")
def create_evaluation(evaluation: EvaluationCreate, storage: Storage = Depends(get_storage)) -> dict[str, int]:
    run = storage.get_by_id("runs", evaluation.run_id)
    if run is not None:
        prompt = storage.get_by_id("prompts", int(run["prompt_id"]))
        if prompt is not None and evaluation.auto_check_passed is None:
            evaluation.auto_check_passed = run_auto_check(
                str(prompt["auto_check_type"]),
                str(prompt["auto_check_value"]),
                str(run["response_text"]),
            )
    return {"id": storage.create_evaluation(evaluation)}
