"""Journalisation centralisée pour Perseus.

- Console : format court et lisible (heures:minutes:secondes).
- Fichier : logs/perseus.log avec rotation automatique (2 Mo, 5 fichiers).
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False


def _configure_console_encoding() -> None:
    """Force UTF-8 sur stdout/stderr pour éviter les crashs cp1252 sous Windows."""
    import sys

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001 — flux non reconfigurable
                pass
    if sys.platform == "win32":
        try:
            import os

            os.system("chcp 65001 > nul 2>&1")  # console Windows en UTF-8
        except Exception:  # noqa: BLE001
            pass


def setup_logging(debug: bool = False, logs_dir: Path | None = None) -> logging.Logger:
    """Configure le logging une seule fois et renvoie le logger racine `perseus`."""
    global _CONFIGURED
    _configure_console_encoding()
    logger = logging.getLogger("perseus")
    if _CONFIGURED:
        return logger

    level = logging.DEBUG if debug else logging.INFO
    logger.setLevel(level)

    console_format = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", datefmt="%H:%M:%S"
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_format)
    console_handler.setLevel(level)
    logger.addHandler(console_handler)

    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            logs_dir / "perseus.log",
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
        )
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)

    # Réduction du bruit des bibliothèques tierces
    for noisy in ("httpx", "httpcore", "urllib3", "openai", "faster_whisper",
                  "uvicorn", "asyncio", "googleapiclient"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logger.propagate = False
    _CONFIGURED = True
    return logger


def get_logger(name: str) -> logging.Logger:
    """Renvoie un logger enfant nommé `perseus.<name>`."""
    return logging.getLogger(f"perseus.{name}")
