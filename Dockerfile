# STAGE 1: Builder
FROM python:3.11-slim-bookworm AS builder
ENV PIP_ROOT_USER_ACTION=ignore
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# STAGE 2: Runtime
FROM python:3.11-slim-bookworm AS runtime

RUN groupadd -g 1000 vaultgroup && \
    useradd -r -u 1000 -g vaultgroup vaultuser

WORKDIR /home/vaultuser/app

COPY --from=builder --chown=vaultuser:vaultgroup /root/.local /home/vaultuser/.local
COPY --chown=vaultuser:vaultgroup src/ ./src/

ENV PATH="/home/vaultuser/.local/bin:${PATH}"
ENV PYTHONPATH="/home/vaultuser/app"

USER vaultuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/', timeout=3)"

CMD ["python", "-m", "uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8080"]
