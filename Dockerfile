# Webhook de devolucoes - deploy no Railway.
# O Railway detecta este Dockerfile automaticamente; PORT e injetada por ele.
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /srv

# Camada de dependencias (cacheavel): instala a partir do lockfile, sem o projeto.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Codigo. O .dockerignore corta build/ (127 MB de imagens) e afins.
COPY . .

# Invoca o binario do venv DIRETO, sem `uv run`. Com --no-install-project acima, o
# `uv run` tentaria sincronizar o projeto no boot - o que exige rede dentro do
# container e faria o deploy falhar (ou ficar lento) sem necessidade. `import app`
# e `import config` funcionam porque o codigo esta no WORKDIR.
# exec form nao expande ${PORT}, dai o sh -c.
CMD ["sh", "-c", ".venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
