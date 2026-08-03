# Webhook de devolucoes - deploy no Railway.
# O Railway detecta este Dockerfile automaticamente; PORT e injetada por ele.
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /srv

# Camada de dependencias (cacheavel): instala a partir do lockfile, sem o projeto.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Codigo. O .dockerignore corta build/ (127 MB de imagens) e afins.
COPY . .

# uvicorn le a PORT do ambiente; exec form nao suporta expansao, dai o sh -c.
CMD ["sh", "-c", "uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
