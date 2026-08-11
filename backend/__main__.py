"""Run the trusted local demo with one same-origin Uvicorn worker."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "backend.app:app",
        host="127.0.0.1",
        port=int(os.environ.get("TEXT_TO_CAD_PORT", "8000")),
        workers=1,
    )


if __name__ == "__main__":
    main()
