"""Vision de l'écran : capture (mss) + analyse par modèle vision d'OpenRouter.

Plutôt qu'un OCR local fragile, Perseus envoie la capture d'écran à un modèle
multimodal : il « comprend » la mise en page, lit le texte d'une vidéo
(sous-titres), décrit une interface, etc. Un OCR Tesseract optionnel est
disponible en secours (fonction `ocr()`).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image

from config.settings import Settings
from core.brain import Brain
from utils.logger import get_logger

log = get_logger("screen")


class ScreenVision:
    """Capture et analyse de l'écran de l'utilisateur."""

    def __init__(self, settings: Settings, brain: Brain) -> None:
        """Nécessite le cerveau (modèle vision) pour analyser les captures."""
        self.settings = settings
        self.brain = brain

    def capture(self, monitor: int | None = None) -> Image.Image:
        """Capture un écran (mss) et renvoie une image PIL."""
        import mss  # import tardif (léger gain de démarrage)

        with mss.mss() as sct:
            index = (monitor or self.settings.screen_monitor) - 1
            # sct.monitors[0] = bureau virtuel complet, [1..] = écrans physiques
            region = sct.monitors[max(0, min(index, len(sct.monitors) - 1))]
            shot = sct.grab(region)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def analyze(self, question: str = "") -> str:
        """Capture l'écran puis demande sa description au modèle vision."""
        image = self.capture()
        question = question.strip() or (
            "Décris précisément ce qui est affiché à l'écran : application "
            "utilisée, textes visibles, éléments importants. Si du texte "
            "défile (vidéo, sous-titres), transcris-le fidèlement."
        )
        self.save_debug(image, "screen_analyze")
        return self.brain.describe_image(image, question)

    def save_debug(self, image: Image.Image, name: str) -> Path:
        """Sauvegarde une capture horodatée dans data/debug (traçabilité)."""
        path = self.settings.debug_dir / f"{name}_{datetime.now():%Y%m%d_%H%M%S}.png"
        image.save(path)
        log.debug("Capture enregistrée : %s", path)
        return path

    def ocr(self, image: Image.Image) -> str:
        """OCR local optionnel — nécessite pytesseract + Tesseract-OCR."""
        try:
            import pytesseract
        except ImportError as exc:
            raise RuntimeError(
                "pytesseract non installé (voir SETUP.md §8, section OCR)."
            ) from exc
        return pytesseract.image_to_string(image, lang="fra+eng")
