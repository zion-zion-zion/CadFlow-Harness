"""Run the trusted local demo with one same-origin Uvicorn worker."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv


def load_backend_environment(dotenv_path: str | Path | None = None) -> None:
    """Load repository-local configuration without overriding exported values."""

    path = (
        Path(dotenv_path).expanduser().resolve()
        if dotenv_path is not None
        else Path(__file__).resolve().parents[1] / ".env"
    )
    load_dotenv(dotenv_path=path, override=False)


def main() -> None:
    load_backend_environment()
    uvicorn.run(
        "backend.app:app",
        host=os.environ.get("TEXT_TO_CAD_HOST", "127.0.0.1"),
        port=int(os.environ.get("TEXT_TO_CAD_PORT", "8000")),
        workers=1,
    )


if __name__ == "__main__":
    main()
