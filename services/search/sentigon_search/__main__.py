"""Run the search service: python -m sentigon_search"""

from __future__ import annotations

import os

import uvicorn
from sentigon_common.config import settings


def main() -> None:
    uvicorn.run(
        "sentigon_search.app:app",
        host=settings.service_bind_host,
        port=int(os.environ.get("SEARCH_HTTP_PORT", "8060")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
