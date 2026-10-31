"""Text-to-Speech de Perseus.

Trois moteurs interchangeables (TTS_PROVIDER dans .env) :
- `piper`      : 100 % local et gratuit, voix française « siwis » par défaut ;
- `elevenlabs` : qualité maximale via API cloud (payant) ;
- `kokoro`     : local, très naturel, nécessite PyTorch + espeak-ng (optionnel).
"""

from __future__ import annotations

import re
import tempfile
import time
import wave
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

from config.settings import Settings
from utils.logger import get_logger

log = get_logger("tts")

# Expressions régulières de nettoyage (le texte vient souvent du LLM)
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2190-\u2BFF\u2600-\u27BF]")
_URL_RE = re.compile(r"https?://\S+")


def clean_for_speech(text: str) -> str:
    """Nettoie un texte « écran » (markdown, emojis, URLs) pour l'oral."""
    text = _URL_RE.sub(" (un lien) ", text)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = text.replace("`", " ").replace("**", " ").replace("*", " ")
    text = re.sub(r"[#_>|~]", " ", text)
    text = _EMOJI_RE.sub(" ", text)
    text = text.replace("…", "...").replace("«", " ").replace("»", " ")
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str, max_len: int = 280) -> list[str]:
    """Découpe un long texte en blocs de phrases (playback plus réactif)."""
    blocks: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?;:])\s+", text):
        if len(current) + len(sentence) + 1 <= max_len:
            current = f"{current} {sentence}".strip()
        else:
            if current:
                blocks.append(current)
            current = sentence
    if current:
        blocks.append(current)
    return blocks or ([text] if text else [])


def _run_async(coroutine):
    """Exécute une coroutine, même si une boucle asyncio tourne déjà."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


class TTSProvider(ABC):
    """Interface commune de tous les moteurs de synthèse vocale."""

    @abstractmethod
    def synthesize(self, text: str, out_path: Path) -> Path:
        """Synthétise `text` dans un fichier WAV et renvoie son chemin."""

    def speak(self, text: str) -> None:
        """Nettoie, synthétise puis joue le texte (bloquant jusqu'à la fin)."""
        from voice.audio import play_wav  # import tardif (évite les cycles)

        for block in split_sentences(clean_for_speech(text)):
            if not block:
                continue
            out_path = Path(tempfile.gettempdir()) / f"perseus_tts_{time.time_ns()}.wav"
            self.synthesize(block, out_path)
            try:
                play_wav(out_path)
            finally:
                try:
                    out_path.unlink(missing_ok=True)
                except OSError:  # fichier encore verrouillé (antivirus) : tant pis
                    pass


class PiperProvider(TTSProvider):
    """Moteur Piper (rhasspy) : rapide, local, voix française de qualité.

    La voix est téléchargée automatiquement depuis Hugging Face au premier
    lancement puis mise en cache dans data/voices/.
    """

    REPO = "rhasspy/piper-voices"

    def __init__(self, settings: Settings) -> None:
        """Stocke la configuration ; le modèle est chargé au premier usage."""
        self.settings = settings
        self._voice = None

    def _ensure_model(self) -> tuple[Path, Path]:
        """Télécharge (une seule fois) les fichiers de la voix Piper."""
        from huggingface_hub import hf_hub_download

        base = f"fr/fr_FR/{self.settings.piper_voice}/{self.settings.piper_quality}"
        stem = f"fr_FR-{self.settings.piper_voice}-{self.settings.piper_quality}"
        model_path = self.settings.voices_dir / base / f"{stem}.onnx"
        config_path = model_path.with_suffix(".onnx.json")
        if not model_path.exists():
            log.info("Téléchargement de la voix Piper « %s »…", stem)
            hf_hub_download(self.REPO, f"{base}/{stem}.onnx",
                            local_dir=str(self.settings.voices_dir))
            hf_hub_download(self.REPO, f"{base}/{stem}.onnx.json",
                            local_dir=str(self.settings.voices_dir))
        return model_path, config_path

    def _load(self):
        """Charge le modèle Piper en mémoire (une seule fois)."""
        if self._voice is None:
            try:
                from piper import PiperVoice
            except ImportError as exc:
                raise RuntimeError(
                    "Piper n'est pas installé : lance `pip install -r requirements.txt` "
                    "dans le venv activé (piper-tts >= 1.3 requis). Sinon, utilise "
                    "TTS_PROVIDER=edge ou sapi."
                ) from exc

            model_path, config_path = self._ensure_model()
            log.info("Chargement de Piper (%s)…", model_path.name)
            self._voice = PiperVoice.load(
                str(model_path), config_path=str(config_path), use_cuda=False,
            )
        return self._voice

    def synthesize(self, text: str, out_path: Path) -> Path:
        """Synthétise `text` vers un fichier WAV mono 16 bits."""
        voice = self._load()
        try:
            rate = voice.config.sample_rate
        except AttributeError:
            rate = 22050
        with wave.open(str(out_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(rate)
            voice.synthesize_wav(text, wav_file)
        return out_path


class ElevenLabsProvider(TTSProvider):
    """Moteur ElevenLabs (cloud) : voix ultra-naturelles, multilingue."""

    API_URL = "https://api.elevenlabs.io/v1/text-to-speech"

    def __init__(self, settings: Settings) -> None:
        """Vérifie la présence de la clé API ElevenLabs."""
        if not settings.elevenlabs_api_key:
            raise RuntimeError("ELEVENLABS_API_KEY manquante dans .env.")
        self.settings = settings

    def synthesize(self, text: str, out_path: Path) -> Path:
        """Appelle l'API REST et écrit le PCM brut dans un conteneur WAV."""
        import requests

        headers = {"xi-api-key": self.settings.elevenlabs_api_key}
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        response = None
        sample_rate = 44100
        last_error = ""
        # Formats bruts (PCM) : on reconstruit nous-même l'en-tête WAV.
        for fmt, rate in (("pcm_44100", 44100), ("pcm_44100_128", 44100), ("pcm_24000", 24000)):
            trial = requests.post(
                f"{self.API_URL}/{self.settings.elevenlabs_voice_id}",
                json=payload, params={"output_format": fmt},
                headers=headers, timeout=60,
            )
            if trial.status_code == 200:
                response, sample_rate = trial, rate
                break
            last_error = trial.text[:200]
        if response is None:
            raise RuntimeError(f"ElevenLabs a refusé la requête : {last_error}")

        with wave.open(str(out_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(response.content)
        return out_path


class EdgeTTSProvider(TTSProvider):
    """Moteur Edge-TTS : voix neuronales Microsoft, gratuites, sans clé API.

    ⚠️ Nécessite une connexion Internet (le texte transite par le service
    Microsoft Edge « Read Aloud »). La qualité française est excellente et
    ce moteur fonctionne sur toutes les versions de Python (contrairement à
    Piper) — c'est le secours automatique recommandé.
    """

    def __init__(self, settings: Settings) -> None:
        """Vérifie que le paquet edge-tts est installé."""
        try:
            import edge_tts  # noqa: F401 — vérification d'import
        except ImportError as exc:
            raise RuntimeError(
                "edge-tts non installé : pip install edge-tts (SETUP.md §5)."
            ) from exc
        self.settings = settings

    def synthesize(self, text: str, out_path: Path) -> Path:
        """Génère un MP3 via Edge-TTS puis le convertit en WAV lisible."""
        import edge_tts
        import soundfile as sf

        async def _generate(mp3_path: Path) -> None:
            """Télécharge la synthèse MP3 auprès du service Edge."""
            communicate = edge_tts.Communicate(
                text, voice=self.settings.edge_tts_voice, rate="-4%",
            )
            await communicate.save(str(mp3_path))

        mp3_path = Path(tempfile.gettempdir()) / f"perseus_edge_{time.time_ns()}.mp3"
        _run_async(_generate(mp3_path))
        try:
            data, rate = sf.read(str(mp3_path), dtype="int16", always_2d=False)
        finally:
            mp3_path.unlink(missing_ok=True)

        with wave.open(str(out_path), "wb") as wav_file:
            wav_file.setnchannels(1 if data.ndim == 1 else int(data.shape[1]))
            wav_file.setsampwidth(2)
            wav_file.setframerate(int(rate))
            wav_file.writeframes(data.tobytes())
        return out_path


class SapiProvider(TTSProvider):
    """Moteur Windows SAPI (System.Speech) : 100 % local, zéro dépendance.

    Utilise PowerShell pour synthétiser vers un WAV. C'est la solution de
    secours ultime : elle fonctionne sur toute machine Windows avec n'importe
    quelle version de Python, sans Internet et sans paquet supplémentaire.
    La première voix française installée (Hortense, Julie…) est choisie
    automatiquement.
    """

    def __init__(self, settings: Settings) -> None:
        """Aucun paquet à vérifier : System.Speech est fourni par Windows."""
        self.settings = settings

    def synthesize(self, text: str, out_path: Path) -> Path:
        """Synthétise `text` en WAV via le moteur vocal intégré de Windows."""
        import subprocess

        target = str(out_path).replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            "$fr = $s.GetInstalledVoices() | Where-Object "
            "{ $_.VoiceInfo.Culture.Name -like 'fr-*' } | Select-Object -First 1;"
            "if ($fr) { $s.SelectVoice($fr.VoiceInfo.Name) };"
            f"$s.SetOutputToWaveFile('{target}');"
            "$s.Speak([Console]::In.ReadToEnd());"
            "$s.Dispose();"
        )
        try:
            process = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-Command", script],
                input=text, text=True, capture_output=True, timeout=60,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("La synthèse SAPI a dépassé 60 secondes.") from exc
        if process.returncode != 0 or not out_path.exists():
            raise RuntimeError(f"SAPI (Windows) a échoué : {(process.stderr or '')[:300]}")
        return out_path


class KokoroProvider(TTSProvider):
    """Moteur Kokoro-82M (local) : très naturel, requiert torch + espeak-ng."""

    SAMPLE_RATE = 24000

    def __init__(self, settings: Settings) -> None:
        """Vérifie que Kokoro est installé (sinon message d'aide clair)."""
        try:
            from kokoro import KPipeline  # noqa: F401 — vérification d'import
        except ImportError as exc:
            raise RuntimeError(
                "Kokoro non installé : `pip install kokoro torch` + espeak-ng "
                "(voir SETUP.md §8)."
            ) from exc
        self.settings = settings
        self._pipeline = None

    def _load(self):
        """Charge le pipeline Kokoro en français (lang_code 'f')."""
        if self._pipeline is None:
            from kokoro import KPipeline

            self._pipeline = KPipeline(lang_code="f")
        return self._pipeline

    def synthesize(self, text: str, out_path: Path) -> Path:
        """Synthétise via le pipeline Kokoro et écrit un WAV 24 kHz."""
        chunks = []
        for _graphemes, _phonemes, audio in self._load()(
            text, voice=self.settings.kokoro_voice, speed=1.0,
        ):
            chunks.append(np.asarray(audio, dtype=np.float32))
        audio = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
        with wave.open(str(out_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.SAMPLE_RATE)
            wav_file.writeframes(pcm.tobytes())
        return out_path


def create_tts(settings: Settings) -> TTSProvider:
    """Instancie le moteur TTS choisi (ou le meilleur disponible si « auto »).

    Ordre de sélection en mode « auto » :
    1. Piper   → 100 % local/hors ligne, si installé (Python ≤ 3.12) ;
    2. Edge-TTS → voix neuronales françaises gratuites (Internet requis) ;
    3. SAPI    → voix Windows intégrée, zéro dépendance (toujours dispo).
    """
    provider = settings.tts_provider.lower()

    if provider == "piper":
        return PiperProvider(settings)
    if provider in {"edge", "edge-tts", "edgetts"}:
        return EdgeTTSProvider(settings)
    if provider == "sapi":
        return SapiProvider(settings)
    if provider == "elevenlabs":
        return ElevenLabsProvider(settings)
    if provider == "kokoro":
        return KokoroProvider(settings)
    if provider == "auto":
        try:
            import piper  # noqa: F401
            log.info("TTS auto → Piper (100 % local).")
            return PiperProvider(settings)
        except ImportError:
            pass
        try:
            import edge_tts  # noqa: F401
            log.info("TTS auto → Edge-TTS (voix neuronales, Internet requis).")
            return EdgeTTSProvider(settings)
        except ImportError:
            log.warning("TTS auto → voix Windows SAPI (Piper et Edge-TTS indisponibles).")
            return SapiProvider(settings)

    raise ValueError(
        f"TTS_PROVIDER inconnu : {provider} "
        "(choix : auto | piper | edge | sapi | elevenlabs | kokoro)"
    )
