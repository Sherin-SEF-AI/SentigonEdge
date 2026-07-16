"""Run the reason service: python -m sentigon_reason"""

from __future__ import annotations

import os

import uvicorn
from sentigon_common.config import settings


def main() -> None:
    uvicorn.run(
        "sentigon_reason.app:app",
        host=settings.service_bind_host,
        port=int(os.environ.get("REASON_HTTP_PORT", "8050")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
