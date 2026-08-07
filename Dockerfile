FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq5 \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[dev]"

COPY alembic.ini ./
COPY migrations ./migrations
COPY tests ./tests

# Paper mode by default. Live trading additionally requires an authorization
# file that is deliberately not part of any image.
ENV TRADEIT_TRADING_MODE=paper

ENTRYPOINT ["tradeit"]
CMD ["config"]
