"""Run the ingest service: python -m sentigon_ingest"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "sentigon_ingest.app:app",
        host="0.0.0.0",
        port=int(os.environ.get("INGEST_HTTP_PORT", "8020")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
