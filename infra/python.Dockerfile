FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
COPY . /app
EXPOSE 8080
CMD ["uv", "run", "python", "services/api/server.py"]
