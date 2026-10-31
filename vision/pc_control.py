"""Contrôle du PC : clavier, souris, applications, commandes système.

⚠️ Ces fonctions sont volontairement bas niveau : les décisions sont prises
par le cerveau (LLM) via les outils, et toute commande système passe d'abord
par la barrière d'autorisation (core/gate.py). L'arrêt d'urgence PyAutoGUI
est activé : promener la souris dans le coin supérieur gauche interrompt tout.
"""

from __future__ import annotations

import re
import shutil
import subprocess

from config.settings import Settings
from utils.logger import get_logger

log = get_logger("pc")

# Alias courants : nom parlé → exécutable Windows
APP_ALIASES = {
    "chrome": "chrome", "google chrome": "chrome",
    "edge": "msedge", "microsoft edge": "msedge",
    "firefox": "firefox",
    "calculatrice": "calc", "calculator": "calc",
    "bloc-notes": "notepad", "notepad": "notepad",
    "explorateur": "explorer", "explorer": "explorer",
    "paint": "mspaint",
    "terminal": "wt", "powershell": "powershell",
    "spotify": "spotify", "discord": "discord",
    "vscode": "code", "code": "code",
    "word": "winword", "excel": "excel",
    "paramètres": "ms-settings:", "settings": "ms-settings:",
}

# Caractères autorisés pour un nom d'application (anti-injection shell)
_SAFE_APP_NAME = re.compile(r"[A-Za-z0-9 _:.\\/-]+")


class PCControl:
    """Actions de bas niveau sur Windows 11."""

    def __init__(self, settings: Settings) -> None:
        """Stocke la configuration ; PyAutoGUI est importé au premier usage."""
        self.settings = settings
        self._gui = None

    @property
    def gui(self):
        """Accès paresseux à PyAutoGUI, configuré en mode sûr."""
        if self._gui is None:
            import pyautogui

            pyautogui.FAILSAFE = True  # coin haut-gauche = arrêt d'urgence
            pyautogui.PAUSE = 0.1
            self._gui = pyautogui
        return self._gui

    # ------------------------------------------------------------------ #
    # Applications & web                                                 #
    # ------------------------------------------------------------------ #
    def open_app(self, name: str) -> str:
        """Ouvre une application par son nom courant (chrome, calculatrice…)."""
        target = APP_ALIASES.get(name.lower().strip(), name.strip())
        if not _SAFE_APP_NAME.fullmatch(target):
            return f"ERREUR : nom d'application invalide « {name} »."
        executable = shutil.which(target)
        target = executable or target
        subprocess.Popen(["cmd", "/c", "start", "", target])
        log.info("Application lancée : %s", target)
        return f"Application « {name} » lancée."

    def open_url(self, url: str) -> str:
        """Ouvre une URL dans le navigateur par défaut."""
        import webbrowser

        webbrowser.open(url)
        return f"Site ouvert : {url}"

    # ------------------------------------------------------------------ #
    # Clavier & souris                                                   #
    # ------------------------------------------------------------------ #
    def type_text(self, text: str) -> str:
        """Tape du texte dans la fenêtre active (presse-papiers pour les accents)."""
        import pyperclip

        pyperclip.copy(text)
        self.gui.hotkey("ctrl", "v")
        return "Texte tapé."

    def press_hotkey(self, keys: list[str]) -> str:
        """Envoie un raccourci clavier, ex. ['ctrl', 's']."""
        self.gui.hotkey(*[key.lower() for key in keys])
        return f"Raccourci {'+'.join(keys)} envoyé."

    def click(self, x: int, y: int, button: str = "left") -> str:
        """Clique à une position absolue de l'écran (en pixels)."""
        self.gui.click(x=int(x), y=int(y), button=button)
        return f"Clic {button} en ({x}, {y})."

    def scroll(self, amount: int) -> str:
        """Fait défiler la fenêtre active (positif = vers le haut)."""
        self.gui.scroll(int(amount))
        return f"Défilement de {amount} effectué."

    def screen_size(self) -> tuple[int, int]:
        """Renvoie la résolution de l'écran principal."""
        return self.gui.size()

    def active_window_title(self) -> str:
        """Renvoie le titre de la fenêtre active (contexte utile pour le LLM)."""
        try:
            import pygetwindow as gw

            window = gw.getActiveWindow()
            return window.title if window else ""
        except Exception:  # noqa: BLE001
            return ""

    # ------------------------------------------------------------------ #
    # Commandes système (réservées aux actions autorisées)               #
    # ------------------------------------------------------------------ #
    def run_command(self, command: str, timeout_s: int = 30) -> str:
        """Exécute une commande système — appelée UNIQUEMENT après autorisation."""
        if not self.settings.allow_shell:
            return "ERREUR : ALLOW_SHELL=false dans .env — commandes système désactivées."
        try:
            process = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=timeout_s,
            )
            output = process.stdout or ""
            if process.stderr:
                output += f"\n[stderr] {process.stderr}"
            return (output.strip() or "(aucune sortie)")[:2000]
        except subprocess.TimeoutExpired:
            return f"ERREUR : commande trop longue (> {timeout_s}s)."
