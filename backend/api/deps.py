"""Shared FastAPI dependencies: bearer-token auth for every /api/v1 route.

The same token authenticates the dashboard and the future Android app
(spec §14: authentication and device authorization).
"""
from __future__ import annotations

from fastapi import Header, HTTPException, status

from ..core import config


def require_auth(authorization: str = Header(default="")) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    token = authorization.removeprefix("Bearer ").strip()
    if token != config.settings.auth_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    return token
