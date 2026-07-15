"""Run the notify service: python -m sentigon_notify"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "sentigon_notify.app:app",
        host="0.0.0.0",
        port=int(os.environ.get("NOTIFY_HTTP_PORT", "8070")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
