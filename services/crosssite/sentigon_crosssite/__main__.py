"""Run the crosssite service: python -m sentigon_crosssite"""
from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "sentigon_crosssite.app:app",
        host="0.0.0.0",
        port=int(os.environ.get("CROSSSITE_HTTP_PORT", "8086")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
