from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.templating import Jinja2Templates

from src.dependencies import get_storage
from src.schemas import AutoCheckType, PromptCreate, PromptSetCreate
from src.services.storage import Storage

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


@router.get("/prompts", response_class=HTMLResponse)
def prompts_page(request: Request, storage: Storage = Depends(get_storage)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "prompts.html",
        {"prompt_sets": storage.list_rows("prompt_sets"), "prompts": storage.list_rows("prompts")},
    )


@router.get("/api/prompt-sets")
def list_prompt_sets(storage: Storage = Depends(get_storage)) -> list[dict[str, object]]:
    return storage.list_rows("prompt_sets")


@router.post("/api/prompt-sets")
def create_prompt_set(prompt_set: PromptSetCreate, storage: Storage = Depends(get_storage)) -> dict[str, int]:
    return {"id": storage.create_prompt_set(prompt_set)}


@router.post("/prompt-sets")
def create_prompt_set_form(
    name: str = Form(...),
    description: str = Form(""),
    storage: Storage = Depends(get_storage),
) -> RedirectResponse:
    storage.create_prompt_set(PromptSetCreate(name=name, description=description))
    return RedirectResponse("/prompts", status_code=303)


@router.get("/api/prompts")
def list_prompts(storage: Storage = Depends(get_storage)) -> list[dict[str, object]]:
    return storage.list_rows("prompts")


@router.post("/api/prompts")
def create_prompt(prompt: PromptCreate, storage: Storage = Depends(get_storage)) -> dict[str, int]:
    return {"id": storage.create_prompt(prompt)}


@router.post("/prompts")
def create_prompt_form(
    prompt_set_id: int = Form(...),
    category: str = Form(...),
    task_goal: str = Form(...),
    prompt_text: str = Form(...),
    expected_answer: str = Form(""),
    rubric: str = Form(""),
    auto_check_type: str = Form("none"),
    auto_check_value: str = Form(""),
    tags: str = Form(""),
    storage: Storage = Depends(get_storage),
) -> RedirectResponse:
    storage.create_prompt(
        PromptCreate(
            prompt_set_id=prompt_set_id,
            category=category,
            task_goal=task_goal,
            prompt_text=prompt_text,
            expected_answer=expected_answer,
            rubric=rubric,
            auto_check_type=AutoCheckType(auto_check_type),
            auto_check_value=auto_check_value,
            tags=tags,
        )
    )
    return RedirectResponse("/prompts", status_code=303)
