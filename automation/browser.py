"""Navigateur Playwright persistant, exécuté dans un thread dédié.

L'API synchrone de Playwright est incompatible avec la boucle asyncio du
serveur webhook : on l'isole donc dans un worker thread qui possède son
propre Chromium avec un **profil persistant** (les connexions aux portails
scolaires survivent aux redémarrages de Perseus).

Usage : `worker.submit(lambda context: ma_fonction(context))`
"""

from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

from config.settings import Settings
from utils.logger import get_logger

log = get_logger("browser")
T = TypeVar("T")

# Petit masquage d'automatisation (les portails scolaires détectent webdriver)
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || { runtime: {} };
"""


class AutomationError(RuntimeError):
    """Erreur d'automatisation web (navigation, connexion, extraction)."""


def debug_dump(page, name: str, settings: Settings) -> Path:
    """Sauvegarde une capture d'écran + le texte de la page pour le débogage."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = settings.debug_dir / f"{name}_{stamp}"
    try:
        page.screenshot(path=str(base) + ".png")
        Path(str(base) + ".txt").write_text(page.inner_text("body"), encoding="utf-8")
        log.info("Dump de débogage : %s(.png/.txt)", base)
    except Exception as exc:  # noqa: BLE001 — le dump ne doit jamais masquer l'erreur d'origine
        log.debug("Dump de débogage impossible : %s", exc)
    return base


def find_first_visible(page, selectors: list[str], timeout_ms: int = 8000):
    """Attend et renvoie le premier élément visible de la liste (None sinon).

    Indispensable pour les portails scolaires dont le HTML varie selon
    l'ENT et la version de Pronote.
    """
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.is_visible():
                    return locator
            except Exception:  # noqa: BLE001 — sélecteur invalide : on passe au suivant
                continue
        try:
            page.wait_for_timeout(300)
        except Exception:  # noqa: BLE001
            return None
    return None


class PlaywrightWorker:
    """Thread unique qui possède le navigateur et exécute les tâches reçues."""

    def __init__(self, settings: Settings) -> None:
        """Démarre le thread worker (le navigateur s'ouvre à la 1re tâche)."""
        self.settings = settings
        self._jobs: "queue.Queue[tuple[Callable[[Any], T], Future]]" = queue.Queue()
        self._context = None
        self._playwright = None
        self._thread = threading.Thread(target=self._main, name="playwright-worker", daemon=True)
        self._thread.start()

    def _main(self) -> None:
        """Boucle du worker : ouvre Chromium puis exécute les tâches reçues."""
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        profile = self.settings.browser_profile_dir
        while True:
            function, future = self._jobs.get()
            try:
                if self._context is None:
                    log.info("Lancement de Chromium (profil persistant : %s)…", profile)
                    self._context = self._playwright.chromium.launch_persistent_context(
                        user_data_dir=str(profile),
                        headless=self.settings.automation_headless,
                        locale="fr-FR",
                        viewport={"width": 1366, "height": 900},
                        args=["--disable-blink-features=AutomationControlled"],
                    )
                    self._context.add_init_script(_STEALTH_JS)
                future.set_result(function(self._context))
            except Exception as exc:  # noqa: BLE001 — on renvoie tout à l'appelant
                if self._is_fatal(exc):
                    log.warning("Navigateur fermé → il sera relancé à la prochaine tâche.")
                    self._context = None
                future.set_exception(exc)

    @staticmethod
    def _is_fatal(exc: Exception) -> bool:
        """Détecte une fermeture du navigateur nécessitant un redémarrage."""
        text = str(exc).lower()
        return "closed" in text or "target" in text

    def submit(self, function: Callable[[Any], T], timeout_s: int = 240) -> T:
        """Exécute `function(context)` dans le thread Playwright et attend le résultat."""
        future: Future = Future()
        self._jobs.put((function, future))
        return future.result(timeout=timeout_s)

    def shutdown(self) -> None:
        """Ferme le navigateur et stoppe Playwright (proprement, dans son thread)."""
        if self._playwright is None:
            return

        def _close(context) -> bool:
            """Ferme le contexte navigateur puis Playwright lui-même."""
            if context is not None:
                context.close()
            self._playwright.stop()
            return True

        try:
            self.submit(_close, timeout_s=20)
        except Exception as exc:  # noqa: BLE001
            log.debug("Arrêt du navigateur : %s", exc)
        self._context = None
        self._playwright = None
