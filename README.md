# SecurePipeline Template

Minimal FastAPI starter template with security-minded defaults, test coverage, and Docker support.

## What This Template Includes

- FastAPI app with common security headers and scanner-noise endpoints.
- Input validation with Pydantic models.
- A starter vault service that encrypts data using `cryptography` (Fernet).
- CI pipeline for linting, static security checks, tests, secrets scanning, infra checks, and DAST.

## Quick Start

### 1) Install dependencies

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2) Set environment

`ENCRYPTION_KEY` is required for the vault service. Generate one:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Then export it:

```bash
export ENCRYPTION_KEY="paste-generated-key"
```

### 3) Run locally

```bash
python -m uvicorn src.app:app --host 0.0.0.0 --port 8080 --reload
```

### 4) Run tests

```bash
pytest --cov=src --cov-report=term-missing
```

### 5) Build container

```bash
docker build -t secure-pipeline-template .
docker run --rm -p 8080:8080 -e ENCRYPTION_KEY="$ENCRYPTION_KEY" secure-pipeline-template
```

## Common Commands

- `ruff check .` - lint and style checks
- `bandit -r src/ -ll` - static security scan
- `pytest --cov=src --cov-report=term-missing --cov-fail-under=100` - tests with coverage gate
- `docker build -t secure-pipeline-template .` - image build smoke check

## Customize For A New App

1. Rename the project and update endpoint payload/version values in `src/app.py`.
2. Replace the starter vault service in `src/services/vault.py` with your domain logic.
3. Move app settings to environment-based configuration as new features are added.
4. Adjust CI thresholds and enabled scanners as your app requirements evolve.
5. Add integration tests in `tests/` for app-specific workflows.

## Notes

- This template uses a local environment variable key for simplicity. In production, use a managed secret store and key rotation policy.
- Keep optional security workflows (for example, Safety) enabled only after required secrets are configured.
