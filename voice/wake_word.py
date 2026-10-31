"""Détection du mot d'éveil « Perseus » + capture de la commande.

Choix d'architecture : plutôt qu'un modèle de wake-word dédié (openWakeWord /
Porcupine, qui exigent d'entraîner un modèle personnalisé pour « Perseus »),
Perseus réutilise Whisper lui-même :

- au repos, seules les prises de parole (au-dessus du seuil d'énergie) sont
  transcrites — le CPU reste au repos sinon ;
- si la transcription contient le mot d'éveil, la suite de la phrase devient
  la commande ; si la phrase ne contient QUE le mot d'éveil, un bip confirme
  l'éveil puis une nouvelle écoute capture la commande.

C'est robuste, gratuit et raisonnable en CPU avec `base`/`small` en int8.
"""

from __future__ import annotations

import difflib
import re
import unicodedata

from config.settings import Settings
from utils.logger import get_logger
from voice.audio import AudioInput, play_beep
from voice.stt import Transcriber

log = get_logger("wakeword")


def strip_accents(text: str) -> str:
    """Supprime les accents (é → e) pour une comparaison tolérante."""
    return "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


class VoicePipeline:
    """Boucle complète : veille → mot d'éveil → commande texte."""

    def __init__(self, settings: Settings, audio: AudioInput,
                 transcriber: Transcriber) -> None:
        """Assemble le micro et le transcripteur Whisper."""
        self.settings = settings
        self.audio = audio
        self.stt = transcriber
        self.variants = [strip_accents(v.lower()) for v in settings.wake_variants]

    def wait_for_command(self, wake_required: bool | None = None) -> str:
        """Bloque jusqu'à une commande valide. Renvoie '' si rien d'exploitable."""
        wake_required = self.settings.wake_enabled if wake_required is None else wake_required

        while True:
            audio = self.audio.record_utterance()
            if audio.size == 0:
                continue
            text = self.stt.transcribe(audio)
            if not text:
                continue
            log.info("Entendu : %s", text)

            if not wake_required:
                return text

            if not self._contains_wake(text):
                continue  # parole ambiante ignorée silencieusement

            command = self._strip_wake(text)
            if len(command.split()) >= 2:
                return command

            # L'utilisateur a dit « Perseus » seul : bip, puis écoute de la suite.
            if self.settings.wake_beep:
                play_beep(self.settings)
            follow_up = self.stt.transcribe(
                self.audio.record_utterance(max_s=12, silence_s=1.2)
            )
            if follow_up:
                return follow_up
            log.info("Éveil sans commande ; retour en veille.")

    # ------------------------------------------------------------------ #
    # Reconnaissance du mot d'éveil                                      #
    # ------------------------------------------------------------------ #
    def _contains_wake(self, text: str) -> bool:
        """Détecte le mot d'éveil (accents tolérés + tolérance de transcription)."""
        normalized = strip_accents(text.lower())
        tokens = re.findall(r"[a-z']+", normalized)
        for token in tokens:
            if token in self.variants:
                return True
            if any(
                difflib.SequenceMatcher(None, token, variant).ratio() >= 0.8
                for variant in self.variants
            ):
                return True
        return False

    def _strip_wake(self, text: str) -> str:
        """Retire le mot d'éveil (et la ponctuation qui suit) de la phrase."""
        pattern = rf"\s*{self._wake_pattern()}[\s,:!?.-]*"
        cleaned = re.sub(pattern, " ", text, count=1, flags=re.IGNORECASE)
        return cleaned.strip(" ,.!?")

    def _wake_pattern(self) -> str:
        """Construit l'expression régulière couvrant les variantes du mot d'éveil."""
        alternatives = "|".join(
            sorted((re.escape(v) for v in self.variants), key=len, reverse=True)
        )
        return f"(?:{alternatives})"
