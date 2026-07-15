"""Run the fleet service: python -m sentigon_fleet"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "sentigon_fleet.app:app",
        host="0.0.0.0",
        port=int(os.environ.get("FLEET_HTTP_PORT", "8082")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
