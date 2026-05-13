from __future__ import annotations

import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "services.api_prod.app:app",
        host="0.0.0.0",
        port=8080,
        reload=settings.env == "development",
    )


if __name__ == "__main__":
    main()
