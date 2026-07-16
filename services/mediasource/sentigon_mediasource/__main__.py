"""Run the media-source service: python -m sentigon_mediasource"""

from __future__ import annotations

import os

import uvicorn
from sentigon_common.config import settings


def main() -> None:
    uvicorn.run(
        "sentigon_mediasource.app:app",
        host=settings.service_bind_host,
        port=int(os.environ.get("MEDIASOURCE_HTTP_PORT", "8055")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
