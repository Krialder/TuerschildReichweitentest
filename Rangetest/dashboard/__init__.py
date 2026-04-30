"""Rangetest-Dashboard Paket.

Stellt nur die Factory `create_app` bereit. Das eigentliche Routing wird
ueber die Untermodule ``runs_api``, ``flash``, ``topology`` und
``analysis`` zusammengebaut.
"""
from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
