"""Connexion au portail EduConnect (education.gouv.fr).

Les sélecteurs exacts varient selon les départements et les ENT : Perseus
essaie une liste de sélecteurs standards, surchargeables via .env
(PORTAL_USER_SELECTOR, PORTAL_PASSWORD_SELECTOR, PORTAL_SUBMIT_SELECTOR).
En cas d'échec, une capture d'écran est déposée dans data/debug pour
calibrer facilement les sélecteurs.
"""

from __future__ import annotations

from automation.browser import AutomationError, debug_dump, find_first_visible
from config.settings import Settings
from utils.logger import get_logger

log = get_logger("educonnect")

# Sélecteurs candidats (le premier visible gagne)
USER_SELECTORS = [
    "#username", 'input[name="username"]', 'input[type="email"]',
    'input[name="login"]', "#user", "#login",
]
PWD_SELECTORS = ['input[type="password"]']
SUBMIT_SELECTORS = [
    'button[type="submit"]', 'input[type="submit"]',
    'button:has-text("Connexion")', 'button:has-text("Se connecter")',
    "#connexion", "#authenticate",
]


class EduConnectFlow:
    """Flux d'authentification EduConnect (formulaire standard)."""

    def __init__(self, settings: Settings) -> None:
        """Stocke la configuration (identifiants + sélecteurs surchargés)."""
        self.settings = settings

    def login(self, page) -> None:
        """Authentifie la session EduConnect dans la page donnée."""
        if not self.settings.portal_username or not self.settings.portal_password:
            raise AutomationError(
                "PORTAL_USERNAME / PORTAL_PASSWORD manquants dans .env."
            )
        user_selectors = (
            [self.settings.portal_user_selector]
            if self.settings.portal_user_selector else USER_SELECTORS
        )
        pwd_selectors = (
            [self.settings.portal_password_selector]
            if self.settings.portal_password_selector else PWD_SELECTORS
        )
        submit_selectors = (
            [self.settings.portal_submit_selector]
            if self.settings.portal_submit_selector else SUBMIT_SELECTORS
        )

        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)

        username_field = find_first_visible(page, user_selectors, timeout_ms=15000)
        password_field = find_first_visible(page, pwd_selectors, timeout_ms=5000)
        if not username_field or not password_field:
            debug_dump(page, "educonnect_login", self.settings)
            raise AutomationError(
                "Formulaire EduConnect introuvable (capture dans data/debug). "
                "Ajuste EDUCONNECT_URL ou les sélecteurs PORTAL_* si besoin."
            )

        username_field.fill(self.settings.portal_username)
        password_field.fill(self.settings.portal_password)
        submit_button = find_first_visible(page, submit_selectors, timeout_ms=5000)
        if submit_button:
            submit_button.click()
        else:
            password_field.press("Enter")

        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2500)

        # Vérification : si le champ mot de passe est toujours visible → échec
        if find_first_visible(page, pwd_selectors, timeout_ms=2000):
            debug_dump(page, "educonnect_failed", self.settings)
            raise AutomationError(
                "Connexion EduConnect refusée (identifiants invalides ? MFA "
                "activé ?). Capture dans data/debug."
            )
        log.info("Connexion EduConnect réussie.")
