"""Entrée/sortie audio : microphone (détection d'énergie) et lecture de sons.

Conçu pour Windows 11 (PortAudio/WASAPI via `sounddevice`). La capture
renvoie des tableaux NumPy float32 mono, rééchantillonnés à 16 kHz
(format attendu par Whisper).
"""

from __future__ import annotations

import queue
import threading
import time
import wave
from collections import deque
from pathlib import Path

import numpy as np

from config.settings import Settings
from utils.logger import get_logger

log = get_logger("audio")

TARGET_RATE = 16000  # Fréquence d'échantillonnage cible (Whisper = 16 kHz)


def rms(frame: np.ndarray) -> float:
    """Calcule le niveau RMS d'une trame (intensité moyenne du son)."""
    samples = frame.astype(np.float32)
    return float(np.sqrt(np.mean(np.square(samples))))


def resample_to_16k(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    """Rééchantillonne grossièrement (interpolation linéaire) vers 16 kHz."""
    if sample_rate == TARGET_RATE or audio.size == 0:
        return audio.astype(np.float32)
    duration = audio.shape[0] / sample_rate
    target_length = max(1, int(duration * TARGET_RATE))
    x_old = np.linspace(0.0, duration, num=audio.shape[0], endpoint=False)
    x_new = np.linspace(0.0, duration, num=target_length, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


class AudioInput:
    """Flux continu du microphone avec file d'attente de trames."""

    def __init__(self, settings: Settings) -> None:
        """Prépare le flux (sans l'ouvrir : voir `start()`)."""
        self.settings = settings
        self.sample_rate = TARGET_RATE
        self._frames: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=200)
        self._stream = None
        self._stopped = threading.Event()

    def start(self) -> None:
        """Ouvre le flux d'entrée (gère les micros qui refusent 16 kHz)."""
        import sounddevice as sd  # import tardif : évite de charger PortAudio inutilement

        device = self.settings.audio_input_device or None
        try:
            self._stream = sd.InputStream(
                samplerate=TARGET_RATE, channels=1, dtype="int16",
                device=device, blocksize=1600, callback=self._callback,
            )
        except Exception as exc:
            # Certains micros n'acceptent pas 16 kHz : on retombe sur leur
            # fréquence native et on rééchantillonnera après coup.
            log.warning("Ouverture à 16 kHz impossible (%s) → fréquence native du micro.", exc)
            self.sample_rate = int(sd.query_devices(device or sd.default.device[0])["default_samplerate"])
            self._stream = sd.InputStream(
                samplerate=self.sample_rate, channels=1, dtype="int16",
                device=device, blocksize=int(self.sample_rate * 0.1), callback=self._callback,
            )
        self._stream.start()
        log.info("Micro ouvert (%s Hz, périphérique : %s).",
                 self.sample_rate, sd.query_devices(device or sd.default.device[0])["name"])

    def _callback(self, indata, frames, time_info, status) -> None:
        """Callback PortAudio : pousse chaque trame dans la file d'attente."""
        if status:
            log.debug("Statut audio : %s", status)
        try:
            self._frames.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass  # On sacrifie la trame plutôt que de bloquer le driver audio.

    def stop(self) -> None:
        """Ferme proprement le flux audio."""
        self._stopped.set()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:  # noqa: BLE001
                log.debug("Fermeture du micro : %s", exc)
            self._stream = None

    def read_frame(self, timeout: float = 0.5) -> np.ndarray | None:
        """Lit une trame depuis la file (None si délai dépassé)."""
        try:
            return self._frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def record_utterance(self, *, max_s: float | None = None,
                         silence_s: float | None = None,
                         min_s: float = 0.35,
                         threshold: float | None = None,
                         idle_timeout_s: float | None = None) -> np.ndarray:
        """Enregistre une prise de parole : démarre au son, s'arrête au silence.

        Renvoie un tableau float32 16 kHz (vide si aucune parole détectée).
        """
        max_s = max_s or self.settings.utterance_max_s
        silence_s = silence_s or self.settings.utterance_silence_s
        threshold = threshold if threshold is not None else self.settings.wake_energy_threshold
        idle_timeout_s = idle_timeout_s or self.settings.utterance_idle_timeout_s

        pre_roll: deque[np.ndarray] = deque(maxlen=16)  # ~1,6 s de mémoire glissante
        speech_frames: list[np.ndarray] = []
        started = False
        started_at = time.time()
        last_voice = time.time()

        while not self._stopped.is_set():
            frame = self.read_frame(timeout=0.5)
            if frame is None:
                continue
            level = rms(frame)

            if not started:
                pre_roll.append(frame)
                if level >= threshold:
                    started = True
                    last_voice = time.time()
                    speech_frames = list(pre_roll)  # inclut l'attaque de la voix
                    log.debug("Parole détectée (rms=%.0f).", level)
                elif time.time() - started_at > idle_timeout_s:
                    return np.zeros(0, dtype=np.float32)
            else:
                speech_frames.append(frame)
                if level >= threshold * 0.6:  # hystérésis : fin de parole plus tolérante
                    last_voice = time.time()
                elapsed = sum(f.shape[0] for f in speech_frames) / self.sample_rate
                if (time.time() - last_voice) >= silence_s or elapsed >= max_s:
                    break

        if not speech_frames:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(speech_frames).astype(np.float32) / 32768.0
        duration = audio.shape[0] / self.sample_rate
        if duration < min_s:
            return np.zeros(0, dtype=np.float32)
        return resample_to_16k(audio, self.sample_rate)


# ---------------------------------------------------------------------- #
# Lecture de sons (bips, fichiers WAV générés)                           #
# ---------------------------------------------------------------------- #
def play_wav(path: Path, blocking: bool = True) -> None:
    """Joue un fichier WAV via l'API Windows native (winsound)."""
    import winsound

    # SND_SYNC n'existe pas dans le module : c'est le comportement par défaut.
    flags = winsound.SND_FILENAME
    if not blocking:
        flags |= winsound.SND_ASYNC
    winsound.PlaySound(str(path), flags)


def ensure_beep(path: Path) -> None:
    """Crée un court bip de confirmation (880 Hz) s'il n'existe pas encore."""
    if path.exists():
        return
    rate = 16000
    duration = 0.12
    t = np.linspace(0, duration, int(rate * duration), endpoint=False)
    tone = (np.sin(2 * np.pi * 880 * t) * 0.4 * 32767).astype(np.int16)
    fade = int(rate * 0.02)
    tone[:fade] = (tone[:fade] * np.linspace(0, 1, fade)).astype(np.int16)
    tone[-fade:] = (tone[-fade:] * np.linspace(1, 0, fade)).astype(np.int16)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(tone.tobytes())


def play_beep(settings: Settings) -> None:
    """Joue le bip de confirmation (crée le fichier au premier appel)."""
    try:
        ensure_beep(settings.sounds_dir / "beep.wav")
        play_wav(settings.sounds_dir / "beep.wav")
    except Exception as exc:  # noqa: BLE001 — le bip ne doit jamais bloquer
        log.debug("Bip impossible : %s", exc)
