"""Run the dispatch service: python -m sentigon_dispatch"""

from __future__ import annotations

import os

import uvicorn
from sentigon_common.config import settings


def main() -> None:
    uvicorn.run(
        "sentigon_dispatch.app:app",
        host=settings.service_bind_host,
        port=int(os.environ.get("DISPATCH_HTTP_PORT", "8081")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
