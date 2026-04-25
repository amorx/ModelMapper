FROM python:3.11-slim-bookworm AS builder
ENV PIP_ROOT_USER_ACTION=ignore
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.11-slim-bookworm AS runtime

RUN groupadd -g 1000 modelmapper && \
    useradd -r -u 1000 -g modelmapper modelmapper

WORKDIR /home/modelmapper/app

COPY --from=builder --chown=modelmapper:modelmapper /root/.local /home/modelmapper/.local
COPY --chown=modelmapper:modelmapper src/ ./src/

RUN mkdir -p /home/modelmapper/app/data && \
    chown -R modelmapper:modelmapper /home/modelmapper/app/data

ENV PATH="/home/modelmapper/.local/bin:${PATH}"
ENV PYTHONPATH="/home/modelmapper/app"
ENV MODELMAPPER_DB_PATH="/home/modelmapper/app/data/modelmapper.db"

USER modelmapper

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"

CMD ["python", "-m", "uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8080"]
