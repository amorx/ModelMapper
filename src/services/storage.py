import json
import os
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.schemas import (
    BenchmarkSessionCreate,
    DecisionRuleCreate,
    EvaluationCreate,
    ModelCreate,
    PromptCreate,
    PromptSetCreate,
    ProviderCreate,
    RunCreate,
    RunStatus,
)

DEFAULT_DB_PATH = "./data/modelmapper.db"
MIGRATION_VERSION = "001_initial_modelmapper"
MIGRATION_BENCHMARKS = "002_benchmarks"


class Storage:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or os.getenv("MODELMAPPER_DB_PATH", DEFAULT_DB_PATH)

    @contextmanager
    def connect(self) -> Iterable[sqlite3.Connection]:
        path = Path(self.db_path)
        if path != Path(":memory:"):
            path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def init_db(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)
            applied = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?",
                (MIGRATION_VERSION,),
            ).fetchone()
            if applied is None:
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (MIGRATION_VERSION, utc_now()),
                )
            self._apply_migrations(connection)
            self.seed_defaults(connection)
            self._seed_benchmark_v1(connection)

    def _apply_migrations(self, connection: sqlite3.Connection) -> None:
        applied = connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (MIGRATION_BENCHMARKS,),
        ).fetchone()
        if applied is not None:
            return
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS benchmark_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                prompt_set_id INTEGER NOT NULL,
                model_ids_json TEXT NOT NULL,
                sweep_json TEXT NOT NULL,
                repeat_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                notes TEXT NOT NULL,
                total_scheduled INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                finished_at TEXT,
                FOREIGN KEY(prompt_set_id) REFERENCES prompt_sets(id)
            );
            """
        )
        for column, ddl in (
            ("benchmark_session_id", "INTEGER"),
            ("sweep_name", "TEXT NOT NULL DEFAULT ''"),
            ("sweep_index", "INTEGER NOT NULL DEFAULT 0"),
            ("repeat_index", "INTEGER NOT NULL DEFAULT 0"),
            ("auto_check_passed", "INTEGER"),
        ):
            if not _table_has_column(connection, "runs", column):
                connection.execute(f"ALTER TABLE runs ADD COLUMN {column} {ddl}")  # nosec B608
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (MIGRATION_BENCHMARKS, utc_now()),
        )

    def _seed_benchmark_v1(self, connection: sqlite3.Connection) -> None:
        from src.benchmarks.default_prompts import BENCHMARK_V1_DESCRIPTION, BENCHMARK_V1_NAME, BENCHMARK_V1_PROMPTS

        found = connection.execute("SELECT 1 FROM prompt_sets WHERE name = ?", (BENCHMARK_V1_NAME,)).fetchone()
        if found is not None:
            return
        ps_id = self.create_prompt_set(
            PromptSetCreate(name=BENCHMARK_V1_NAME, description=BENCHMARK_V1_DESCRIPTION),
            connection=connection,
        )
        for entry in BENCHMARK_V1_PROMPTS:
            self.create_prompt(
                PromptCreate(prompt_set_id=ps_id, **entry),
                connection=connection,
            )

    def seed_defaults(self, connection: sqlite3.Connection) -> None:
        existing = connection.execute("SELECT id FROM providers LIMIT 1").fetchone()
        if existing is not None:
            return
        provider_id = self.create_provider(
            ProviderCreate(
                display_name="Local Ollama",
                provider_kind="ollama",
                base_url="http://localhost:11434",
                endpoint_style="ollama_generate",
                auth_scheme="none",
                api_key_source="none",
                allow_local_network=True,
            ),
            connection=connection,
        )
        for name in (
            "gemma4:26b",
            "gpt-oss:20b",
            "mistral:7b",
            "llava:7b",
            "llama3:latest",
        ):
            self.create_model(
                ModelCreate(
                    provider_id=provider_id,
                    model_name=name,
                    display_name=name,
                    context_window=8192,
                    default_params={"temperature": 0.2, "top_p": 0.9, "max_tokens": 1024},
                    privacy_notes="Local Ollama model; prompts stay on this machine.",
                ),
                connection=connection,
            )
        prompt_set_id = self.create_prompt_set(
            PromptSetCreate(name="Starter Benchmark", description="Consistent prompts for comparing model behavior."),
            connection=connection,
        )
        self.create_prompt(
            PromptCreate(
                prompt_set_id=prompt_set_id,
                category="reasoning",
                task_goal="Check concise reasoning and answer quality.",
                prompt_text="Explain the trade-offs between local and hosted AI models in five bullets.",
                rubric="5 = accurate, balanced, concise; 3 = useful but misses nuance; 1 = inaccurate.",
                tags="reasoning,comparison",
            ),
            connection=connection,
        )
        first_model = connection.execute("SELECT id FROM models ORDER BY id LIMIT 1").fetchone()
        if first_model is not None:
            self.create_decision_rule(
                DecisionRuleCreate(
                    priority=1,
                    task_type="privacy",
                    privacy_required=True,
                    speed_priority=3,
                    modality="text",
                    recommended_model_id=int(first_model["id"]),
                    rationale="Use a local Ollama model when privacy is the strongest requirement.",
                ),
                connection=connection,
            )

    def create_provider(self, provider: ProviderCreate, *, connection: sqlite3.Connection | None = None) -> int:
        values = provider.model_dump()
        return self._insert(
            "providers",
            {
                **values,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            },
            connection=connection,
        )

    def create_model(self, model: ModelCreate, *, connection: sqlite3.Connection | None = None) -> int:
        values = model.model_dump()
        values["default_params"] = json.dumps(values["default_params"], sort_keys=True)
        return self._insert("models", values, connection=connection)

    def update_model(self, model_id: int, model: ModelCreate) -> None:
        values = model.model_dump()
        values["default_params"] = json.dumps(values["default_params"], sort_keys=True)
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE models
                SET provider_id = ?, model_name = ?, display_name = ?, context_window = ?,
                    supports_chat = ?, supports_tools = ?, supports_vision = ?, default_params = ?,
                    input_cost_per_1k = ?, output_cost_per_1k = ?, privacy_notes = ?, enabled = ?
                WHERE id = ?
                """,
                (
                    values["provider_id"],
                    values["model_name"],
                    values["display_name"],
                    values["context_window"],
                    normalize_db_value(values["supports_chat"]),
                    normalize_db_value(values["supports_tools"]),
                    normalize_db_value(values["supports_vision"]),
                    values["default_params"],
                    values["input_cost_per_1k"],
                    values["output_cost_per_1k"],
                    values["privacy_notes"],
                    normalize_db_value(values["enabled"]),
                    model_id,
                ),
            )

    def create_prompt_set(self, prompt_set: PromptSetCreate, *, connection: sqlite3.Connection | None = None) -> int:
        return self._insert("prompt_sets", prompt_set.model_dump(), connection=connection)

    def create_prompt(self, prompt: PromptCreate, *, connection: sqlite3.Connection | None = None) -> int:
        values = prompt.model_dump()
        values["auto_check_type"] = str(values["auto_check_type"])
        return self._insert("prompts", values, connection=connection)

    def create_run(self, run: RunCreate, *, connection: sqlite3.Connection | None = None) -> int:
        prompt = self.get_by_id("prompts", run.prompt_id)
        model = self.get_by_id("models", run.model_id)
        if prompt is None or model is None:
            raise ValueError("Prompt and model must exist before creating a run")
        provider = self.get_by_id("providers", int(model["provider_id"]))
        if provider is None:
            raise ValueError("Model provider must exist before creating a run")
        data = run.model_dump()
        values: dict[str, Any] = {
            "prompt_id": run.prompt_id,
            "model_id": run.model_id,
            "provider_id": int(model["provider_id"]),
            "status": RunStatus.PENDING.value,
            "started_at": None,
            "finished_at": None,
            # Metric field name; no secret material is stored here.
            "time_to_first_token_ms": None,  # nosec B105
            "total_latency_ms": None,
            "tokens_in": 0,
            "tokens_out": 0,
            "estimated_cost_usd": 0.0,
            "response_text": "",
            "error_text": "",
            "params_json": json.dumps(run.params_json, sort_keys=True),
            "raw_metadata": "{}",
            "created_at": utc_now(),
            "benchmark_session_id": data.get("benchmark_session_id"),
            "sweep_name": data.get("sweep_name") or "",
            "sweep_index": int(data.get("sweep_index") or 0),
            "repeat_index": int(data.get("repeat_index") or 0),
            "auto_check_passed": None,
        }
        return self._insert("runs", values, connection=connection)

    def update_run_status(
        self,
        run_id: int,
        status: RunStatus,
        *,
        response_text: str = "",
        error_text: str = "",
        time_to_first_token_ms: int | None = None,
        total_latency_ms: int | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        raw_metadata: dict[str, Any] | None = None,
    ) -> None:
        finished_at = utc_now() if status not in {RunStatus.PENDING, RunStatus.RUNNING} else None
        run_row = self.get_by_id("runs", run_id)
        if run_row is None:
            return
        started_at = utc_now() if status == RunStatus.RUNNING else run_row.get("started_at")
        estimated_cost = self.estimate_cost(run_id, tokens_in=tokens_in, tokens_out=tokens_out)
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET status = ?, started_at = COALESCE(?, started_at), finished_at = COALESCE(?, finished_at),
                    response_text = ?, error_text = ?, time_to_first_token_ms = ?,
                    total_latency_ms = ?, tokens_in = ?, tokens_out = ?, estimated_cost_usd = ?,
                    raw_metadata = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    started_at,
                    finished_at,
                    response_text,
                    error_text,
                    time_to_first_token_ms,
                    total_latency_ms,
                    tokens_in,
                    tokens_out,
                    estimated_cost,
                    json.dumps(raw_metadata or {}, sort_keys=True),
                    run_id,
                ),
            )

    def set_run_autocheck(self, run_id: int, auto_check_passed: bool | None) -> None:
        value: int | None = None if auto_check_passed is None else (1 if auto_check_passed else 0)
        with self.connect() as connection:
            connection.execute("UPDATE runs SET auto_check_passed = ? WHERE id = ?", (value, run_id))

    def create_evaluation(self, evaluation: EvaluationCreate) -> int:
        return self._insert("evaluations", evaluation.model_dump())

    def create_benchmark_session(self, session: BenchmarkSessionCreate, total_scheduled: int) -> int:
        values: dict[str, Any] = {
            "name": session.name,
            "prompt_set_id": session.prompt_set_id,
            "model_ids_json": json.dumps(list(session.model_ids), sort_keys=True),
            "sweep_json": json.dumps(session.sweep, sort_keys=True),
            "repeat_count": session.repeat_count,
            "status": "pending",
            "notes": session.notes,
            "total_scheduled": total_scheduled,
            "created_at": utc_now(),
            "finished_at": None,
        }
        return self._insert("benchmark_sessions", values)

    def update_benchmark_session(
        self,
        session_id: int,
        status: str,
        *,
        set_finished: bool = False,
    ) -> None:
        with self.connect() as connection:
            if set_finished:
                connection.execute(
                    "UPDATE benchmark_sessions SET status = ?, finished_at = ? WHERE id = ?",
                    (status, utc_now(), session_id),
                )
            else:
                connection.execute(
                    "UPDATE benchmark_sessions SET status = ? WHERE id = ?",
                    (status, session_id),
                )

    def list_prompts_for_set(self, prompt_set_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM prompts WHERE prompt_set_id = ? ORDER BY id",
                (prompt_set_id,),
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def list_benchmark_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM benchmark_sessions ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def list_runs_for_benchmark(self, session_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT runs.*, prompts.task_goal, prompts.category, prompts.auto_check_type,
                       prompts.auto_check_value, models.display_name AS model_display_name,
                       providers.display_name AS provider_display_name
                FROM runs
                JOIN prompts ON prompts.id = runs.prompt_id
                JOIN models ON models.id = runs.model_id
                JOIN providers ON providers.id = runs.provider_id
                WHERE runs.benchmark_session_id = ?
                ORDER BY runs.prompt_id, runs.model_id, runs.sweep_index, runs.repeat_index, runs.id
                """,
                (session_id,),
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def create_decision_rule(
        self,
        rule: DecisionRuleCreate,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        return self._insert("decision_rules", rule.model_dump(), connection=connection)

    def store_secret_for_provider(self, provider_id: int, encrypted_secret: str, fingerprint: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO provider_secrets(provider_id, encrypted_secret, fingerprint, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET
                    encrypted_secret = excluded.encrypted_secret,
                    fingerprint = excluded.fingerprint,
                    updated_at = excluded.updated_at
                """,
                (provider_id, encrypted_secret, fingerprint, utc_now(), utc_now()),
            )

    def get_secret_for_provider(self, provider_id: int) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT encrypted_secret FROM provider_secrets WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
        return None if row is None else str(row["encrypted_secret"])

    def list_rows(self, table: str) -> list[dict[str, Any]]:
        ensure_table(table)
        with self.connect() as connection:
            rows = connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()  # nosec B608
        return [row_to_dict(row) for row in rows]

    def get_by_id(self, table: str, row_id: int) -> dict[str, Any] | None:
        ensure_table(table)
        with self.connect() as connection:
            row = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()  # nosec B608
        return None if row is None else row_to_dict(row)

    def comparison_rows(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT runs.*, prompts.task_goal, prompts.category, models.display_name AS model_display_name,
                       providers.display_name AS provider_display_name,
                       evaluations.accuracy_score, evaluations.style_score
                FROM runs
                JOIN prompts ON prompts.id = runs.prompt_id
                JOIN models ON models.id = runs.model_id
                JOIN providers ON providers.id = runs.provider_id
                LEFT JOIN evaluations ON evaluations.run_id = runs.id
                ORDER BY runs.id DESC
                """
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def export_data(self) -> dict[str, list[dict[str, Any]]]:
        tables = [
            "providers",
            "models",
            "prompt_sets",
            "prompts",
            "runs",
            "evaluations",
            "decision_rules",
            "benchmark_sessions",
        ]
        return {table: self.list_rows(table) for table in tables}

    def purge_run_text(self, run_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE runs SET response_text = '', error_text = '', raw_metadata = '{}' WHERE id = ?",
                (run_id,),
            )

    def estimate_cost(self, run_id: int, *, tokens_in: int, tokens_out: int) -> float:
        run = self.get_by_id("runs", run_id)
        if run is None:
            return 0.0
        model = self.get_by_id("models", int(run["model_id"]))
        if model is None:
            return 0.0
        input_cost = float(model["input_cost_per_1k"])
        output_cost = float(model["output_cost_per_1k"])
        return round((tokens_in / 1000 * input_cost) + (tokens_out / 1000 * output_cost), 8)

    def _insert(
        self,
        table: str,
        values: dict[str, Any],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        ensure_table(table)
        keys = list(values.keys())
        placeholders = ", ".join("?" for _ in keys)
        columns = ", ".join(keys)
        # Table names are validated by ensure_table before SQL construction.
        sql = f"INSERT INTO {table}({columns}) VALUES ({placeholders})"  # nosec B608
        params = tuple(normalize_db_value(values[key]) for key in keys)
        if connection is not None:
            cursor = connection.execute(sql, params)
            return int(cursor.lastrowid)
        with self.connect() as managed:
            cursor = managed.execute(sql, params)
            return int(cursor.lastrowid)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def normalize_db_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, dict | list):
        return json.dumps(value, sort_keys=True)
    return value


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _table_has_column(connection: sqlite3.Connection, table: str, column: str) -> bool:
    allowed = {"runs", "benchmark_sessions"}
    if table not in allowed:
        raise ValueError("PRAGMA table check only for known tables")
    if table == "runs":
        rows = connection.execute("PRAGMA table_info(runs)").fetchall()
    else:
        rows = connection.execute("PRAGMA table_info(benchmark_sessions)").fetchall()
    return any(str(row[1]) == column for row in rows)


def ensure_table(table: str) -> None:
    allowed = {
        "providers",
        "provider_secrets",
        "models",
        "prompt_sets",
        "prompts",
        "runs",
        "evaluations",
        "decision_rules",
        "benchmark_sessions",
        "schema_migrations",
    }
    if table not in allowed:
        raise ValueError("Unknown storage table")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL,
    provider_kind TEXT NOT NULL,
    base_url TEXT NOT NULL,
    endpoint_style TEXT NOT NULL,
    auth_scheme TEXT NOT NULL,
    header_name TEXT,
    timeout_seconds INTEGER NOT NULL,
    api_key_source TEXT NOT NULL,
    api_key_env_var TEXT,
    allow_local_network INTEGER NOT NULL,
    enabled INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_secrets (
    provider_id INTEGER PRIMARY KEY,
    encrypted_secret TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(provider_id) REFERENCES providers(id)
);

CREATE TABLE IF NOT EXISTS models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER NOT NULL,
    model_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    context_window INTEGER NOT NULL,
    supports_chat INTEGER NOT NULL,
    supports_tools INTEGER NOT NULL,
    supports_vision INTEGER NOT NULL,
    default_params TEXT NOT NULL,
    input_cost_per_1k REAL NOT NULL,
    output_cost_per_1k REAL NOT NULL,
    privacy_notes TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    FOREIGN KEY(provider_id) REFERENCES providers(id)
);

CREATE TABLE IF NOT EXISTS prompt_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_set_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    task_goal TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    expected_answer TEXT NOT NULL,
    rubric TEXT NOT NULL,
    auto_check_type TEXT NOT NULL,
    auto_check_value TEXT NOT NULL,
    tags TEXT NOT NULL,
    FOREIGN KEY(prompt_set_id) REFERENCES prompt_sets(id)
);

CREATE TABLE IF NOT EXISTS benchmark_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    prompt_set_id INTEGER NOT NULL,
    model_ids_json TEXT NOT NULL,
    sweep_json TEXT NOT NULL,
    repeat_count INTEGER NOT NULL,
    status TEXT NOT NULL,
    notes TEXT NOT NULL,
    total_scheduled INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    FOREIGN KEY(prompt_set_id) REFERENCES prompt_sets(id)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_id INTEGER NOT NULL,
    model_id INTEGER NOT NULL,
    provider_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    time_to_first_token_ms INTEGER,
    total_latency_ms INTEGER,
    tokens_in INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    estimated_cost_usd REAL NOT NULL,
    response_text TEXT NOT NULL,
    error_text TEXT NOT NULL,
    params_json TEXT NOT NULL,
    raw_metadata TEXT NOT NULL,
    created_at TEXT NOT NULL,
    benchmark_session_id INTEGER,
    sweep_name TEXT NOT NULL DEFAULT '',
    sweep_index INTEGER NOT NULL DEFAULT 0,
    repeat_index INTEGER NOT NULL DEFAULT 0,
    auto_check_passed INTEGER,
    FOREIGN KEY(prompt_id) REFERENCES prompts(id),
    FOREIGN KEY(model_id) REFERENCES models(id),
    FOREIGN KEY(provider_id) REFERENCES providers(id),
    FOREIGN KEY(benchmark_session_id) REFERENCES benchmark_sessions(id)
);

CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    accuracy_score INTEGER NOT NULL,
    style_score INTEGER NOT NULL,
    strengths TEXT NOT NULL,
    weaknesses TEXT NOT NULL,
    notes TEXT NOT NULL,
    recommended_use_cases TEXT NOT NULL,
    auto_check_passed INTEGER,
    FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS decision_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    priority INTEGER NOT NULL,
    task_type TEXT NOT NULL,
    privacy_required INTEGER NOT NULL,
    max_cost_per_1k REAL,
    speed_priority INTEGER NOT NULL,
    modality TEXT NOT NULL,
    recommended_model_id INTEGER NOT NULL,
    rationale TEXT NOT NULL,
    FOREIGN KEY(recommended_model_id) REFERENCES models(id)
);
"""
