"""Run the search service: python -m sentigon_search"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "sentigon_search.app:app",
        host="0.0.0.0",
        port=int(os.environ.get("SEARCH_HTTP_PORT", "8060")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
