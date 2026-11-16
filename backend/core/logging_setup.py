"""Central logging setup for JARVIS X (spec §7.7 Observability).

Plain text to stdout/stderr with ISO timestamps. Kept dependency-free and
idempotent so tests, uvicorn and scripts can all call configure().
"""
from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def configure(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Quiet the noisier third-party loggers unless debugging.
    if level != "DEBUG":
        for noisy in ("uvicorn.access", "httpx", "httpcore"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"jarvis.{name}")
