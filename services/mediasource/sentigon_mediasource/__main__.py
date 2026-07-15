"""Run the media-source service: python -m sentigon_mediasource"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "sentigon_mediasource.app:app",
        host="0.0.0.0",
        port=int(os.environ.get("MEDIASOURCE_HTTP_PORT", "8055")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
