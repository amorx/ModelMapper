# ModelMapper

ModelMapper is a local-first FastAPI app for comparing AI models with consistent prompts, tracking response quality, and building a personal guide for when to use each model.

## What It Includes

- Server-rendered dashboard, provider/model registry, prompt sets, run history, comparison tables, guide, and decision view.
- SQLite persistence with versioned migrations and seeded Ollama models.
- Configurable providers for Ollama and OpenAI-compatible APIs.
- API key management by environment-variable reference by default, with optional Fernet-encrypted local storage.
- SSRF-safe provider URL validation, strict CSP (`default-src 'self'`), rate limiting, structured secret redaction, and 100% test coverage.
- CI pipeline for linting, static security checks, tests, secrets scanning, infra checks, Docker linting, and DAST.

## Quick Start

### 1) Install dependencies

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2) Configure Local Storage

By default, ModelMapper writes SQLite data to `./data/modelmapper.db`. Override this if desired:

```bash
export MODELMAPPER_DB_PATH="./data/modelmapper.db"
```

### 3) Start Ollama And Pull Models

Ollama is seeded as a local provider at `http://localhost:11434`. Start Ollama, then pull at least five local models:

```bash
ollama pull gemma4:26b
ollama pull gpt-oss:20b
ollama pull mistral:7b
ollama pull llava:7b
ollama pull llama3:latest
```

Check that Ollama is reachable:

```bash
curl http://localhost:11434/api/tags
```

The app seeds matching model records for `gemma4:26b`, `gpt-oss:20b`, `mistral:7b`, `llava:7b`, and `llama3:latest`. In `/models`, disable or delete any records for models you have not pulled.

### 4) Local Secrets

For hosted providers, prefer environment-variable references such as `OPENAI_API_KEY` or `OPENROUTER_API_KEY`; the key value itself is not stored in SQLite.

Optional encrypted local key storage requires a Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
export MODELMAPPER_ENCRYPTION_KEY="paste-generated-key"
```

Do not put API keys in `.env` files that might be committed. This repository ignores `.env*`, but shell environment variables or your password manager are safer for personal use.

### 5) Run Locally

```bash
python -m uvicorn src.app:app --host 127.0.0.1 --port 8080 --reload
```

Then open `http://localhost:8080`.

Smoke check:

```bash
curl http://localhost:8080/healthz
```

### 6) Run Tests

```bash
pytest --cov=src --cov-report=term-missing --cov-fail-under=100
```

### 7) Build Container

```bash
docker build -t modelmapper .
docker run --rm -p 8080:8080 -v "$PWD/data:/home/modelmapper/app/data" modelmapper
```

When the app runs inside Docker and Ollama runs on your Mac host, update the local provider base URL in `/providers` to:

```text
http://host.docker.internal:11434
```

For native Linux Docker, use host networking or the host bridge IP instead.

## Common Commands

- `ruff check .` - lint and style checks
- `bandit -r src/ -v` - static security scan
- `pytest --cov=src --cov-report=term-missing --cov-fail-under=100` - tests with coverage gate
- `pip-audit -r requirements.txt` - dependency vulnerability audit
- `docker build -t modelmapper .` - image build smoke check

## Core Workflow

1. Register providers and models in `/providers` and `/models`.
2. Create prompt sets in `/prompts` with rubrics and optional deterministic checks.
3. Run selected prompts against multiple models in `/runs/new`.
4. Compare latency, token usage, cost, status, scores, and response style in `/compare`.
5. Capture strengths, weaknesses, and use-case notes for the personal guide.
6. Use `/decision` to apply priority-ordered selection rules and get a transparent model recommendation.

## Security Notes

- The intended v1 deployment is local only. Do not expose the app publicly with port forwarding or tunnels until authentication and cloud persistence are designed.
- Keep API keys in environment variables unless you explicitly enable encrypted local key storage.
- Non-local provider URLs must use HTTPS. HTTP is only allowed for localhost-style local runtimes.
- Private/link-local networks are blocked unless a provider explicitly enables `allow_local_network`.
- HTML views use same-origin assets only; do not add inline scripts/styles or third-party CDNs without revisiting CSP.
- CI should mock provider calls. Tests must not require Ollama, hosted APIs, or real secrets.
