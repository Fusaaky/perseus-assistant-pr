"""Extraction des devoirs et notes depuis Pronote (via Playwright).

Stratégie volontairement robuste : plutôt que de dépendre de sélecteurs HTML
instables pour le *contenu*, Perseus navigue jusqu'à la bonne page puis
récupère le **texte brut**, que le cerveau (LLM) structure ensuite. Chaque
étape produit un dump de débogage dans data/debug pour calibrer les
sélecteurs si ton ENT diffère.
"""

from __future__ import annotations

import re

from automation.browser import AutomationError, debug_dump, find_first_visible
from automation.educonnect import EduConnectFlow
from config.settings import Settings
from utils.logger import get_logger

log = get_logger("pronote")

# Entrées de menu Pronote (plusieurs libellés/variantes possibles)
MENU_SELECTORS = {
    "devoirs": [
        'span:has-text("Cahier de texte")', '[title*="Cahier de texte"]',
        'text=Cahier de texte', 'text=Travail à faire',
    ],
    "notes": [
        'span:has-text("Notes")', '[title*="Notes"]', 'text=Notes',
    ],
}


class PronotePortal:
    """Connexion + extraction de contenu sur le portail Pronote."""

    def __init__(self, settings: Settings) -> None:
        """Stocke la configuration et prépare le flux EduConnect (SSO)."""
        self.settings = settings
        self._educonnect = EduConnectFlow(settings)

    # ------------------------------------------------------------------ #
    # API publique (appelées par les outils / le webhook)                #
    # ------------------------------------------------------------------ #
    def get_homework(self, worker) -> str:
        """Renvoie le texte brut de la page « Cahier de texte » de Pronote."""
        def job(context) -> str:
            """Ouvre Pronote, navigue vers le cahier de texte et extrait le texte."""
            page = context.new_page()
            try:
                self._open_logged(page)
                self._open_menu(page, "devoirs")
                page.wait_for_timeout(3000)  # laisse l'interface SPA se rendre
                text = page.inner_text("body")
                debug_dump(page, "pronote_homework", self.settings)
                return self._clean(text)
            finally:
                page.close()
        return worker.submit(job, timeout_s=240)

    def get_grades(self, worker) -> str:
        """Renvoie le texte brut de la page « Notes » de Pronote."""
        def job(context) -> str:
            """Ouvre Pronote, navigue vers les notes et extrait le texte."""
            page = context.new_page()
            try:
                self._open_logged(page)
                self._open_menu(page, "notes")
                page.wait_for_timeout(3000)
                text = page.inner_text("body")
                debug_dump(page, "pronote_grades", self.settings)
                return self._clean(text)
            finally:
                page.close()
        return worker.submit(job, timeout_s=240)

    # ------------------------------------------------------------------ #
    # Navigation interne                                                 #
    # ------------------------------------------------------------------ #
    def _open_logged(self, page) -> None:
        """Ouvre Pronote et garantit une session authentifiée."""
        if not self.settings.pronote_url:
            raise AutomationError("PRONOTE_URL manquante dans .env.")
        page.goto(self.settings.pronote_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)

        # Page d'entrée de Pronote : choix de l'ENT (EduConnect, etc.)
        ent_button = find_first_visible(
            page,
            ['text=EduConnect', 'a:has-text("EduConnect")', 'button:has-text("EduConnect")'],
            timeout_ms=4000,
        )
        if ent_button:
            log.info("Page d'entrée Pronote → passage par EduConnect.")
            ent_button.click()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(2500)

        if self._already_logged(page):
            log.info("Session Pronote déjà active (profil persistant).")
            return

        self._login(page)
        if not self._already_logged(page):
            debug_dump(page, "pronote_not_logged", self.settings)
            raise AutomationError("Session Pronote non établie après connexion (voir data/debug).")

    def _already_logged(self, page) -> bool:
        """Détecte une session active (menu de navigation Pronote présent)."""
        return find_first_visible(
            page, MENU_SELECTORS["devoirs"] + MENU_SELECTORS["notes"], timeout_ms=3000,
        ) is not None

    def _login(self, page) -> None:
        """Authentifie : soit formulaire Pronote direct, soit via EduConnect."""
        if "educonnect" in (page.url or "").lower():
            self._educonnect.login(page)
            page.wait_for_timeout(3000)
            return

        password_field = find_first_visible(page, ['input[type="password"]'], timeout_ms=8000)
        if not password_field:
            # Pas de formulaire direct → probablement une redirection ENT
            ent_button = find_first_visible(
                page, ['text=EduConnect', 'a:has-text("EduConnect")'], timeout_ms=4000,
            )
            if ent_button:
                ent_button.click()
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(2500)
            self._educonnect.login(page)
            page.wait_for_timeout(3000)
            return

        # Formulaire de connexion Pronote direct (identifiant + mot de passe)
        user_candidates = [
            'input[type="text"]', 'input[type="email"]',
            'input[name="username"]', 'input[placeholder*="dentifiant"]',
        ]
        if self.settings.portal_user_selector:
            user_candidates.insert(0, self.settings.portal_user_selector)
        username_field = find_first_visible(page, user_candidates, timeout_ms=5000)
        if not username_field or not self.settings.portal_username:
            debug_dump(page, "pronote_login_form", self.settings)
            raise AutomationError(
                "Champ identifiant introuvable ou PORTAL_USERNAME vide "
                "(capture dans data/debug)."
            )
        username_field.fill(self.settings.portal_username)
        password_field.fill(self.settings.portal_password)
        submit_button = find_first_visible(
            page,
            ['button:has-text("Se connecter")', 'button:has-text("Connexion")',
             'input[type="submit"]', 'button[type="submit"]'],
            timeout_ms=4000,
        )
        if submit_button:
            submit_button.click()
        else:
            password_field.press("Enter")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)

    def _open_menu(self, page, key: str) -> None:
        """Clique sur l'entrée de menu demandée (« Cahier de texte » / « Notes »)."""
        menu_item = find_first_visible(page, MENU_SELECTORS[key], timeout_ms=12000)
        if not menu_item:
            debug_dump(page, f"pronote_menu_{key}", self.settings)
            raise AutomationError(
                f"Menu « {key} » introuvable dans Pronote (capture dans data/debug)."
            )
        menu_item.click()
        page.wait_for_timeout(2500)

    @staticmethod
    def _clean(text: str) -> str:
        """Nettoie le texte extrait (espaces multiples, lignes vides)."""
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
        cleaned = "\n".join(line for line in lines if line)
        return cleaned[:8000]
