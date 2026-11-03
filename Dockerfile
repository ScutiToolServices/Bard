# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
ARG BARD_ENGINE=piper
ENV BARD_ENGINE=${BARD_ENGINE} HF_HOME=/data/hf PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" BARD_DATA_DIR=/data BARD_HOST=0.0.0.0
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project $([ "$BARD_ENGINE" = paradee ] && echo --extra paradee)
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable $([ "$BARD_ENGINE" = paradee ] && echo --extra paradee)

EXPOSE 8090
# Piper: the voice is downloaded into the /data volume on first start; later starts reuse it (and still boot offline).
# Paradee: its model downloads into /data/hf on first use.
CMD ["sh", "-c", "if [ \"$BARD_ENGINE\" != paradee ]; then python -m piper.download_voices \"${BARD_VOICE:-en_US-lessac-medium}\" --download-dir /data/voices; fi; exec python -m src.run"]
