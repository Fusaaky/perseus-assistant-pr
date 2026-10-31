"""Perseus — assistant personnel local (point d'entrée principal).

Utilisation :
    python main.py                  # assistant vocal complet (mot d'éveil « Perseus »)
    python main.py --no-wake        # vocal, sans mot d'éveil (1re phrase = commande)
    python main.py --cli            # mode texte (test sans micro)
    python main.py --ask "..."      # une seule question, réponse affichée + parlée
    python main.py --test brain     # test d'un module (brain|tts|stt|vision|
                                    #            calendar|playwright|phone|all)
    python main.py --serve-webhook  # serveur de webhooks téléphonie seul
    python main.py --debug          # logs détaillés
"""

from __future__ import annotations

import argparse
import threading

from config.settings import Settings
from core.state import SharedState
from utils.logger import get_logger, setup_logging

log = get_logger("main")


class PerseusApp:
    """Assemble tous les modules et fait tourner l'assistant."""

    def __init__(self, settings: Settings) -> None:
        """Initialise l'état partagé ; les services se créent à la demande."""
        self.settings = settings
        settings.ensure_dirs()
        self.state = SharedState()
        self.state.set_service("settings", settings)

        self._brain = None
        self._gate = None
        self._screen = None
        self._pc = None
        self._worker = None
        self._automation = None
        self._calendar = None
        self._workspace = None
        self._orchestrator = None
        self._webhook_thread = None

    # ------------------------------------------------------------------ #
    # Services (construction paresseuse, enregistrés dans SharedState)   #
    # ------------------------------------------------------------------ #
    @property
    def brain(self):
        """Cerveau LLM (OpenRouter)."""
        if self._brain is None:
            from core.brain import Brain

            self._brain = Brain(self.settings)
            self.state.set_service("brain", self._brain)
        return self._brain

    @property
    def gate(self):
        """Barrière d'autorisation pour les actions critiques."""
        if self._gate is None:
            from core.gate import PermissionGate

            self._gate = PermissionGate(self.settings, self.state)
            self.state.set_service("gate", self._gate)
        return self._gate

    @property
    def screen(self):
        """Vision d'écran (capture + modèle vision)."""
        if self._screen is None:
            from vision.screen import ScreenVision

            self._screen = ScreenVision(self.settings, self.brain)
            self.state.set_service("screen", self._screen)
        return self._screen

    @property
    def pc(self):
        """Contrôle clavier/souris/applications."""
        if self._pc is None:
            from vision.pc_control import PCControl

            self._pc = PCControl(self.settings)
            self.state.set_service("pc", self._pc)
        return self._pc

    @property
    def worker(self):
        """Worker Playwright (navigateur persistant)."""
        if self._worker is None:
            from automation.browser import PlaywrightWorker

            self._worker = PlaywrightWorker(self.settings)
            self.state.set_service("worker", self._worker)
        return self._worker

    @property
    def automation(self):
        """Portail scolaire Pronote/EduConnect."""
        if self._automation is None:
            from automation.pronote import PronotePortal

            self._automation = PronotePortal(self.settings)
            self.state.set_service("automation", self._automation)
        return self._automation

    @property
    def calendar(self):
        """Google Calendar."""
        if self._calendar is None:
            from integrations.google_calendar import GoogleCalendar

            self._calendar = GoogleCalendar(self.settings)
            self.state.set_service("calendar", self._calendar)
        return self._calendar

    @property
    def workspace(self):
        """Google Docs / Drive."""
        if self._workspace is None:
            from integrations.google_workspace import GoogleWorkspace

            self._workspace = GoogleWorkspace(self.settings)
            self.state.set_service("workspace", self._workspace)
        return self._workspace

    @property
    def orchestrator(self):
        """Orchestrateur (cerveau + registre d'outils)."""
        if self._orchestrator is None:
            from core.orchestrator import Orchestrator
            from tools.registry import build_default_registry

            self._orchestrator = Orchestrator(
                self.settings, self.state, self.brain,
                build_default_registry(self.settings, self.state),
            )
        return self._orchestrator

    # ------------------------------------------------------------------ #
    # Serveur webhook (téléphonie)                                       #
    # ------------------------------------------------------------------ #
    def start_webhook_server(self) -> None:
        """Démarre le serveur de webhooks après avoir pré-câblé ses services."""
        if self._webhook_thread is not None:
            return
        # Pré-câblage des services utilisés par les outils téléphoniques
        for name in ("brain", "gate", "calendar", "workspace", "worker", "automation"):
            getattr(self, name)
        from telephony.webhook_server import start_webhook_server

        self._webhook_thread = start_webhook_server(self.settings, self.state)

    # ------------------------------------------------------------------ #
    # Modes de fonctionnement                                            #
    # ------------------------------------------------------------------ #
    def run_voice(self, wake_required: bool | None = None) -> None:
        """Boucle principale vocale : écoute → réflexion → réponse parlée."""
        from voice.audio import AudioInput
        from voice.stt import Transcriber
        from voice.tts import create_tts
        from voice.wake_word import VoicePipeline

        settings = self.settings
        orchestrator = self.orchestrator  # précharge le cerveau
        if settings.telephony_enabled:
            self.start_webhook_server()

        tts = create_tts(settings)
        microphone = AudioInput(settings)
        microphone.start()
        pipeline = VoicePipeline(settings, microphone, Transcriber(settings))
        try:
            tts.speak(f"{settings.assistant_name} en ligne. Dis « {settings.wake_word} » pour m'appeler.")
        except Exception:  # noqa: BLE001 — la voix ne doit jamais bloquer le démarrage
            log.exception("Message d'accueil impossible à prononcer.")

        try:
            while True:
                command = pipeline.wait_for_command(wake_required=wake_required)
                if not command:
                    continue
                log.info("Commande retenue : %s", command)
                lowered = command.lower()
                if any(word in lowered for word in ("stop", "au revoir", "mise en veille", "bonne nuit")):
                    tts.speak("À bientôt !")
                    break
                try:
                    answer = orchestrator.process_command(command, source="voix")
                    tts.speak(answer)
                except Exception:  # noqa: BLE001 — une erreur isolée ne tue pas la boucle
                    log.exception("Erreur pendant le traitement de la commande.")
        except KeyboardInterrupt:
            log.info("Interruption clavier (Ctrl+C).")
        finally:
            microphone.stop()
            if self._worker is not None:
                self._worker.shutdown()

    def run_cli(self) -> None:
        """Mode texte : tape tes questions, Perseus répond à l'écrit ET à l'oral."""
        if self.settings.telephony_enabled:
            self.start_webhook_server()
        print(f"{self.settings.assistant_name} CLI — tape 'quit' pour sortir.")
        while True:
            try:
                text = input("Toi > ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue
            if text.lower() in {"quit", "exit", "q"}:
                break
            print(f"{self.settings.assistant_name} >", self.orchestrator.process_command(text, source="cli"))

    def one_shot(self, question: str) -> None:
        """Répond à une seule question (affichage + synthèse vocale)."""
        answer = self.orchestrator.process_command(question, source="one-shot")
        print(f"{self.settings.assistant_name} >", answer)
        try:
            from voice.tts import create_tts

            create_tts(self.settings).speak(answer)
        except Exception as exc:  # noqa: BLE001 — la voix est optionnelle ici
            log.warning("TTS indisponible : %s", exc)


def main() -> None:
    """Parse les arguments, configure les logs, lance le mode demandé."""
    parser = argparse.ArgumentParser(description="Perseus — assistant personnel local")
    parser.add_argument("--cli", action="store_true", help="mode texte sans micro")
    parser.add_argument("--ask", metavar="QUESTION", help="une seule question")
    parser.add_argument("--test", metavar="MODULE",
                        help="brain|tts|stt|vision|calendar|playwright|phone|all")
    parser.add_argument("--no-wake", action="store_true", help="désactive le mot d'éveil")
    parser.add_argument("--serve-webhook", action="store_true", help="serveur de webhooks seul")
    parser.add_argument("--debug", action="store_true", help="logs détaillés")
    arguments = parser.parse_args()

    settings = Settings()
    setup_logging(debug=arguments.debug, logs_dir=settings.logs_dir)
    for warning in settings.validate():
        log.warning(warning)

    if arguments.test:
        from diagnostics import run_test

        run_test(arguments.test, settings)
        return

    app = PerseusApp(settings)

    if arguments.serve_webhook:
        app.start_webhook_server()
        log.info("Serveur webhook en service — Ctrl+C pour quitter.")
        threading.Event().wait()  # dort indéfiniment
        return

    if arguments.ask:
        app.one_shot(arguments.ask)
        return

    if arguments.cli:
        app.run_cli()
        return

    app.run_voice(wake_required=False if arguments.no_wake else None)


if __name__ == "__main__":
    main()
