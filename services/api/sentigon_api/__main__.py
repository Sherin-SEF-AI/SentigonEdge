"""Run the core API: python -m sentigon_api"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "sentigon_api.app:app",
        host="0.0.0.0",
        port=int(os.environ.get("API_HTTP_PORT", "8010")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
