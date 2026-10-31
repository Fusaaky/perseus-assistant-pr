"""Speech-to-Text 100 % local avec faster-whisper (CTranslate2).

Le modèle est chargé une seule fois puis réutilisé pour toutes les
transcriptions. `vad_filter=True` supprime les silences avant transcription,
ce qui réduit fortement les « hallucinations » de Whisper sur le bruit.
"""

from __future__ import annotations

import numpy as np

from config.settings import Settings
from utils.logger import get_logger

log = get_logger("stt")


class Transcriber:
    """Wrapper autour de faster-whisper, chargé paresseusement."""

    def __init__(self, settings: Settings) -> None:
        """Stocke la configuration ; le modèle est chargé au premier usage."""
        self.settings = settings
        self._model = None

    def _load(self):
        """Charge le modèle Whisper (une seule fois, device auto-détecté)."""
        if self._model is not None:
            return self._model

        from faster_whisper import WhisperModel  # import tardif (chargement lourd)

        device = self.settings.stt_device
        compute = self.settings.stt_compute_type
        if device == "auto":
            try:
                import ctranslate2
                device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:  # noqa: BLE001
                device = "cpu"
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"

        log.info("Chargement de Whisper « %s » (device=%s, compute=%s)…",
                 self.settings.stt_model_size, device, compute)
        self._model = WhisperModel(
            self.settings.stt_model_size, device=device, compute_type=compute,
        )
        return self._model

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> str:
        """Transcrit un tableau float32 16 kHz en texte (français par défaut)."""
        if audio.size == 0:
            return ""
        model = self._load()
        segments, _info = model.transcribe(
            audio,
            language=language or self.settings.language,
            beam_size=1,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
        )
        text = " ".join(segment.text for segment in segments).strip()
        log.debug("Transcription : %r", text)
        return text
