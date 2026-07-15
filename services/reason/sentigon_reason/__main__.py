"""Run the reason service: python -m sentigon_reason"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "sentigon_reason.app:app",
        host="0.0.0.0",
        port=int(os.environ.get("REASON_HTTP_PORT", "8050")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
