"""Run the perception service: python -m sentigon_perception"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "sentigon_perception.app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PERCEPTION_HTTP_PORT", "8030")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
