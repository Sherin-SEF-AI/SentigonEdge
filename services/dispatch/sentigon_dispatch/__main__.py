"""Run the dispatch service: python -m sentigon_dispatch"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "sentigon_dispatch.app:app",
        host="0.0.0.0",
        port=int(os.environ.get("DISPATCH_HTTP_PORT", "8081")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
