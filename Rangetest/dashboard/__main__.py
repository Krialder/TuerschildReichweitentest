"""Erlaubt ``python -m dashboard``.

Liest die Settings aus ``config.toml``, baut die FastAPI-App und startet
sie ueber Uvicorn.
"""
from __future__ import annotations

import uvicorn

from .app import create_app
from .config import load_settings


def main() -> None:
    settings = load_settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
