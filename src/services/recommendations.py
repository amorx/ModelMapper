from dataclasses import dataclass
from typing import Any

from src.schemas import DecisionCriteria


@dataclass(frozen=True)
class Recommendation:
    model: dict[str, Any] | None
    rule: dict[str, Any] | None
    rationale: str


def recommend_model(
    criteria: DecisionCriteria,
    rules: list[dict[str, Any]],
    models: list[dict[str, Any]],
    runs: list[dict[str, Any]],
) -> Recommendation:
    enabled_models = [model for model in models if bool(model.get("enabled"))]
    model_by_id = {int(model["id"]): model for model in enabled_models}
    for rule in sorted(rules, key=lambda item: int(item["priority"])):
        if not rule_matches(criteria, rule):
            continue
        model = model_by_id.get(int(rule["recommended_model_id"]))
        if model is not None:
            return Recommendation(model=model, rule=rule, rationale=str(rule["rationale"]))
    fallback = tie_break(criteria, enabled_models, runs)
    if fallback is None:
        return Recommendation(model=None, rule=None, rationale="No enabled models are available.")
    return Recommendation(model=fallback, rule=None, rationale="Selected the best available model from recent evaluations.")


def rule_matches(criteria: DecisionCriteria, rule: dict[str, Any]) -> bool:
    if rule.get("task_type") and criteria.task_type and str(rule["task_type"]).lower() != criteria.task_type.lower():
        return False
    if bool(rule.get("privacy_required")) and not criteria.privacy_required:
        return False
    if rule.get("max_cost_per_1k") is not None and criteria.max_cost_per_1k is not None:
        if float(rule["max_cost_per_1k"]) > criteria.max_cost_per_1k:
            return False
    if int(rule.get("speed_priority") or 3) > criteria.speed_priority:
        return False
    if rule.get("modality") and str(rule["modality"]) != criteria.modality:
        return False
    return True


def tie_break(criteria: DecisionCriteria, models: list[dict[str, Any]], runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not models:
        return None
    if criteria.privacy_required:
        local = [model for model in models if "local" in str(model.get("privacy_notes", "")).lower()]
        if local:
            return local[0]
    scored = sorted(
        models,
        key=lambda model: latest_score(int(model["id"]), runs),
        reverse=True,
    )
    return scored[0]


def latest_score(model_id: int, runs: list[dict[str, Any]]) -> float:
    for run in runs:
        if int(run["model_id"]) == model_id and run.get("accuracy_score") is not None:
            return float(run["accuracy_score"])
    return 0.0
