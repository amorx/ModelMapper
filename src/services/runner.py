import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

from src.schemas import ModelRunRequest, RunCreate, RunStatus
from src.services.evaluation import run_auto_check
from src.services.providers import ConfigurableHttpProvider
from src.services.secrets import SecretsResolver
from src.services.storage import Storage

DEFAULT_BENCHMARK_CONCURRENCY = 4


def _max_global_concurrency() -> int:
    raw = os.getenv("MODELMAPPER_BENCHMARK_MAX_CONCURRENCY", str(DEFAULT_BENCHMARK_CONCURRENCY))
    try:
        return max(1, min(32, int(raw, 10)))
    except ValueError:
        return DEFAULT_BENCHMARK_CONCURRENCY


@dataclass(frozen=True)
class ComparisonRunner:
    storage: Storage
    provider: ConfigurableHttpProvider
    per_provider_limit: int = 2
    per_run_timeout_seconds: int = 60
    max_global_concurrency: int = DEFAULT_BENCHMARK_CONCURRENCY

    async def run_many(self, prompt_id: int, model_ids: list[int], params: dict[str, Any]) -> list[int]:
        run_ids = [
            self.storage.create_run(RunCreate(prompt_id=prompt_id, model_id=model_id, params_json=params))
            for model_id in model_ids
        ]
        await self.run_many_ids(run_ids)
        return run_ids

    async def run_many_ids(self, run_ids: list[int]) -> None:
        if not run_ids:
            return
        global_limit = _max_global_concurrency()
        global_sem = asyncio.Semaphore(max(1, min(global_limit, len(run_ids))))
        semaphores: dict[int, asyncio.Semaphore] = {}

        async def wrapped(run_id: int) -> None:
            async with global_sem:
                await self._run_one(run_id, semaphores)

        await asyncio.gather(*(wrapped(run_id) for run_id in run_ids))

    async def _run_one(self, run_id: int, semaphores: dict[int, asyncio.Semaphore]) -> None:
        run = self.storage.get_by_id("runs", run_id)
        if run is None:
            return
        provider_id = int(run["provider_id"])
        semaphore = semaphores.setdefault(provider_id, asyncio.Semaphore(self.per_provider_limit))
        async with semaphore:
            self.storage.update_run_status(run_id, RunStatus.RUNNING)
            request = self._build_request(run)
            try:
                result = await asyncio.wait_for(self.provider.generate(request), timeout=self.per_run_timeout_seconds)
            except TimeoutError:
                self.storage.update_run_status(run_id, RunStatus.TIMEOUT, error_text="Run timed out")
                return
            self.storage.update_run_status(
                run_id,
                result.status,
                response_text=result.response_text,
                error_text=result.error_text,
                time_to_first_token_ms=result.time_to_first_token_ms,
                total_latency_ms=result.total_latency_ms,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                raw_metadata=result.raw_metadata,
            )
            if result.status == RunStatus.SUCCEEDED:
                self._apply_autocheck_for_run(run_id, run, result.response_text)

    def _apply_autocheck_for_run(self, run_id: int, run: dict[str, Any], response_text: str) -> None:
        prompt = self.storage.get_by_id("prompts", int(run["prompt_id"]))
        if prompt is None:
            return
        check_type = str(prompt.get("auto_check_type") or "none")
        check_value = str(prompt.get("auto_check_value") or "")
        try:
            outcome = run_auto_check(check_type, check_value, response_text)
        except ValueError:
            self.storage.set_run_autocheck(run_id, None)
            return
        if outcome is None:
            self.storage.set_run_autocheck(run_id, None)
        else:
            self.storage.set_run_autocheck(run_id, bool(outcome))

    def _build_request(self, run: dict[str, Any]) -> ModelRunRequest:
        provider = self.storage.get_by_id("providers", int(run["provider_id"]))
        model = self.storage.get_by_id("models", int(run["model_id"]))
        prompt = self.storage.get_by_id("prompts", int(run["prompt_id"]))
        if provider is None or model is None or prompt is None:
            raise ValueError("Run references missing provider, model, or prompt")
        defaults = json.loads(str(model["default_params"] or "{}"))
        overrides = json.loads(str(run["params_json"] or "{}"))
        return ModelRunRequest(provider=provider, model=model, prompt=prompt, params={**defaults, **overrides})


def build_runner(storage: Storage) -> ComparisonRunner:
    return ComparisonRunner(
        storage=storage,
        provider=ConfigurableHttpProvider(SecretsResolver(store=storage)),
        max_global_concurrency=_max_global_concurrency(),
    )
