from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.templating import Jinja2Templates

from src.dependencies import get_storage
from src.schemas import DecisionCriteria, DecisionRuleCreate
from src.services.recommendations import recommend_model
from src.services.storage import Storage

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


@router.get("/decision", response_class=HTMLResponse)
def decision_page(request: Request, storage: Storage = Depends(get_storage)) -> HTMLResponse:
    criteria = DecisionCriteria()
    recommendation = recommend_model(
        criteria,
        storage.list_rows("decision_rules"),
        storage.list_rows("models"),
        storage.comparison_rows(),
    )
    return templates.TemplateResponse(
        request,
        "decision.html",
        {
            "rules": storage.list_rows("decision_rules"),
            "models": storage.list_rows("models"),
            "recommendation": recommendation,
        },
    )


@router.post("/decision")
def decision_form(
    task_type: str = Form(""),
    privacy_required: bool = Form(False),
    max_cost_per_1k: float | None = Form(None),
    speed_priority: int = Form(3),
    modality: str = Form("text"),
    storage: Storage = Depends(get_storage),
) -> dict[str, Any]:
    criteria = DecisionCriteria(
        task_type=task_type,
        privacy_required=privacy_required,
        max_cost_per_1k=max_cost_per_1k,
        speed_priority=speed_priority,
        modality=modality,
    )
    recommendation = recommend_model(criteria, storage.list_rows("decision_rules"), storage.list_rows("models"), storage.comparison_rows())
    return {
        "model": recommendation.model,
        "rule": recommendation.rule,
        "rationale": recommendation.rationale,
    }


@router.post("/api/decision-rules")
def create_decision_rule(rule: DecisionRuleCreate, storage: Storage = Depends(get_storage)) -> dict[str, int]:
    return {"id": storage.create_decision_rule(rule)}


@router.post("/decision-rules")
def create_decision_rule_form(
    priority: int = Form(...),
    task_type: str = Form(""),
    privacy_required: bool = Form(False),
    max_cost_per_1k: float | None = Form(None),
    speed_priority: int = Form(3),
    modality: str = Form("text"),
    recommended_model_id: int = Form(...),
    rationale: str = Form(...),
    storage: Storage = Depends(get_storage),
) -> RedirectResponse:
    storage.create_decision_rule(
        DecisionRuleCreate(
            priority=priority,
            task_type=task_type,
            privacy_required=privacy_required,
            max_cost_per_1k=max_cost_per_1k,
            speed_priority=speed_priority,
            modality=modality,
            recommended_model_id=recommended_model_id,
            rationale=rationale,
        )
    )
    return RedirectResponse("/decision", status_code=303)
