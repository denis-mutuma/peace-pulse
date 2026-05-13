FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
RUN apt-get update \
  && apt-get install -y --no-install-recommends sqlite3 \
  && rm -rf /var/lib/apt/lists/*
COPY . /app
EXPOSE 8080
CMD ["uv", "run", "python", "-m", "services.api_prod.main"]
