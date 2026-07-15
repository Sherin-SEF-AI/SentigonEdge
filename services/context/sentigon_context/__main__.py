"""Run the context service: python -m sentigon_context"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "sentigon_context.app:app",
        host="0.0.0.0",
        port=int(os.environ.get("CONTEXT_HTTP_PORT", "8040")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
