"""Run the crosssite service: python -m sentigon_crosssite"""
from __future__ import annotations

import os

import uvicorn
from sentigon_common.config import settings


def main() -> None:
    uvicorn.run(
        "sentigon_crosssite.app:app",
        host=settings.service_bind_host,
        port=int(os.environ.get("CROSSSITE_HTTP_PORT", "8086")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
