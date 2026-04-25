import os
from typing import Any

from src.schemas import BenchmarkSessionCreate, RunCreate, RunStatus
from src.services.runner import ComparisonRunner
from src.services.storage import Storage

DEFAULT_MAX_BENCHMARK_RUNS = 200


def max_benchmark_runs() -> int:
    raw = os.getenv("MODELMAPPER_BENCHMARK_MAX_RUNS", str(DEFAULT_MAX_BENCHMARK_RUNS))
    try:
        return max(1, min(50_000, int(raw, 10)))
    except ValueError:
        return DEFAULT_MAX_BENCHMARK_RUNS


def format_sweep_name(sweep_index: int, params: dict[str, Any]) -> str:
    if not params:
        return f"s{sweep_index}"
    inner = ",".join(f"{key}={params[key]!s}" for key in sorted(params.keys()))
    label = f"s{sweep_index}:{inner}"
    return label if len(label) <= 120 else f"{label[:117]}..."


def total_scheduled_runs(session: BenchmarkSessionCreate, prompt_count: int) -> int:
    return prompt_count * len(session.model_ids) * len(session.sweep) * session.repeat_count


def create_benchmark_runs_and_session(storage: Storage, body: BenchmarkSessionCreate) -> tuple[int, list[int], int]:
    prompts = storage.list_prompts_for_set(body.prompt_set_id)
    if not prompts:
        raise ValueError("Prompt set has no prompts")
    total = total_scheduled_runs(body, len(prompts))
    cap = max_benchmark_runs()
    if total > cap:
        raise ValueError(f"Benchmark would schedule {total} runs; limit is {cap} (set MODELMAPPER_BENCHMARK_MAX_RUNS)")
    session_id = storage.create_benchmark_session(body, total)
    run_ids: list[int] = []
    for sweep_index, sweep_params in enumerate(body.sweep):
        params_copy = dict(sweep_params)
        for repeat_index in range(body.repeat_count):
            for prompt in prompts:
                prompt_id = int(prompt["id"])
                for model_id in body.model_ids:
                    label = format_sweep_name(sweep_index, params_copy)
                    rid = storage.create_run(
                        RunCreate(
                            prompt_id=prompt_id,
                            model_id=model_id,
                            params_json=params_copy,
                            benchmark_session_id=session_id,
                            sweep_name=label,
                            sweep_index=sweep_index,
                            repeat_index=repeat_index,
                        )
                    )
                    run_ids.append(rid)
    return session_id, run_ids, total


async def execute_benchmark_runs(
    storage: Storage,
    runner: ComparisonRunner,
    session_id: int,
    run_ids: list[int],
) -> None:
    storage.update_benchmark_session(session_id, "running")
    try:
        await runner.run_many_ids(run_ids)
    except Exception:  # noqa: BLE001 — surface as failed session
        storage.update_benchmark_session(session_id, "failed", set_finished=True)
        raise
    all_ok = _all_runs_succeeded_or_skipped(storage, run_ids)
    storage.update_benchmark_session(
        session_id,
        "completed" if all_ok else "completed_with_errors",
        set_finished=True,
    )


def _all_runs_succeeded_or_skipped(storage: Storage, run_ids: list[int]) -> bool:
    for rid in run_ids:
        run = storage.get_by_id("runs", rid)
        if run is None:
            return False
        if str(run.get("status")) != RunStatus.SUCCEEDED.value:
            return False
    return True


def compute_benchmark_summary(storage: Storage, session_id: int) -> dict[str, Any]:
    session = storage.get_by_id("benchmark_sessions", session_id)
    if session is None:
        raise ValueError("Benchmark session not found")
    rows = storage.list_runs_for_benchmark(session_id)
    by_model: dict[int, dict[str, Any]] = {}
    for row in rows:
        mid = int(row["model_id"])
        bucket = by_model.setdefault(
            mid,
            {
                "model_id": mid,
                "model_display_name": row.get("model_display_name", ""),
                "runs": 0,
                "succeeded": 0,
                "with_autocheck": 0,
                "autocheck_passed": 0,
                "latency_sum_ms": 0.0,
                "latency_count": 0,
                "cost_sum": 0.0,
                "tokens_in": 0,
                "tokens_out": 0,
            },
        )
        bucket["runs"] += 1
        if str(row.get("status")) == RunStatus.SUCCEEDED.value:
            bucket["succeeded"] += 1
        lat = row.get("total_latency_ms")
        if lat is not None:
            bucket["latency_sum_ms"] += float(lat)
            bucket["latency_count"] += 1
        bucket["cost_sum"] += float(row.get("estimated_cost_usd") or 0.0)
        bucket["tokens_in"] += int(row.get("tokens_in") or 0)
        bucket["tokens_out"] += int(row.get("tokens_out") or 0)
        ac = row.get("auto_check_passed")
        if ac is not None:
            bucket["with_autocheck"] += 1
            if int(ac) == 1:
                bucket["autocheck_passed"] += 1
    by_model_list = []
    for mid, data in sorted(by_model.items(), key=lambda x: x[0]):
        lat_avg = 0.0
        if data["latency_count"] > 0:
            lat_avg = round(data["latency_sum_ms"] / data["latency_count"], 2)
        pass_rate = None
        if data["with_autocheck"] > 0:
            pass_rate = round(data["autocheck_passed"] / data["with_autocheck"] * 100.0, 1)
        by_model_list.append(
            {
                "model_id": mid,
                "model_display_name": data["model_display_name"],
                "runs": data["runs"],
                "succeeded": data["succeeded"],
                "with_autocheck": data["with_autocheck"],
                "autocheck_pass_rate_percent": pass_rate,
                "average_latency_ms": lat_avg,
                "total_estimated_cost_usd": round(data["cost_sum"], 8),
                "tokens_in": data["tokens_in"],
                "tokens_out": data["tokens_out"],
            }
        )
    return {
        "session": session,
        "rows": rows,
        "per_model": by_model_list,
    }
