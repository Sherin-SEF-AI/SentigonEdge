"""Run the core API: python -m sentigon_api"""

from __future__ import annotations

import os

import uvicorn
from sentigon_common.config import settings


def main() -> None:
    uvicorn.run(
        "sentigon_api.app:app",
        host=settings.service_bind_host,
        port=int(os.environ.get("API_HTTP_PORT", "8010")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
