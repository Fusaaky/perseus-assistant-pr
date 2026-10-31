"""Configuration centrale de Perseus.

Toutes les valeurs sensibles (clés API, identifiants scolaires…) proviennent
du fichier `.env` situé à la racine du projet. Ce fichier ne doit JAMAIS être
versionné dans Git (voir .gitignore).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Dossier racine du projet (.../Perseus)
BASE_DIR = Path(__file__).resolve().parent.parent

# Charge les variables du fichier .env (silencieux si le fichier n'existe pas)
load_dotenv(BASE_DIR / ".env")


def _str(key: str, default: str = "") -> str:
    """Lit une variable d'environnement sous forme de chaîne nettoyée."""
    return os.getenv(key, default).strip()


def _int(key: str, default: int) -> int:
    """Lit une variable d'environnement sous forme d'entier (défaut si invalide)."""
    try:
        return int(os.getenv(key, "").strip())
    except (TypeError, ValueError):
        return default


def _float(key: str, default: float) -> float:
    """Lit une variable d'environnement sous forme de flottant (défaut si invalide)."""
    try:
        return float(os.getenv(key, "").strip())
    except (TypeError, ValueError):
        return default


def _bool(key: str, default: bool) -> bool:
    """Lit une variable d'environnement sous forme de booléen (1/true/oui)."""
    raw = os.getenv(key, "").strip().lower()
    if raw == "":
        return default
    return raw in {"1", "true", "vrai", "oui", "y", "yes", "on"}


def _resolve(path_str: str) -> Path:
    """Renvoie un chemin absolu (résolu par rapport au dossier du projet)."""
    path = Path(path_str)
    return path if path.is_absolute() else (BASE_DIR / path)


@dataclass
class Settings:
    """Regroupe toute la configuration de l'assistant Perseus."""

    # ------------------------------------------------------------------ #
    # Identité                                                           #
    # ------------------------------------------------------------------ #
    user_name: str = _str("USER_NAME", "Eliot")
    assistant_name: str = _str("ASSISTANT_NAME", "Perseus")

    # ------------------------------------------------------------------ #
    # Cerveau (OpenRouter)                                               #
    # ------------------------------------------------------------------ #
    openrouter_api_key: str = _str("OPENROUTER_API_KEY")
    openrouter_base_url: str = _str("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    llm_model: str = _str("LLM_MODEL", "openai/gpt-4o-mini")
    vision_model: str = _str("VISION_MODEL", "openai/gpt-4o-mini")
    llm_temperature: float = _float("LLM_TEMPERATURE", 0.4)
    llm_max_tokens: int = _int("LLM_MAX_TOKENS", 900)

    # ------------------------------------------------------------------ #
    # Voix : Speech-to-Text (Whisper local)                              #
    # ------------------------------------------------------------------ #
    stt_model_size: str = _str("STT_MODEL_SIZE", "small")
    stt_device: str = _str("STT_DEVICE", "auto")
    stt_compute_type: str = _str("STT_COMPUTE_TYPE", "auto")
    language: str = _str("LANGUAGE", "fr")

    # ------------------------------------------------------------------ #
    # Voix : mot d'éveil ("Perseus") et capture d'énoncés                #
    # ------------------------------------------------------------------ #
    wake_enabled: bool = _bool("WAKE_WORD_ENABLED", True)
    wake_word: str = _str("WAKE_WORD", "perseus")
    wake_variants: list[str] = field(default_factory=lambda: [
        v.strip() for v in _str("WAKE_VARIANTS", "perseus,perse,persée,percy").split(",")
        if v.strip()
    ])
    wake_energy_threshold: float = _float("WAKE_ENERGY_THRESHOLD", 800.0)
    wake_beep: bool = _bool("WAKE_BEEP", True)
    utterance_max_s: float = _float("UTTERANCE_MAX_S", 15.0)
    utterance_silence_s: float = _float("UTTERANCE_SILENCE_S", 1.0)
    utterance_idle_timeout_s: float = _float("UTTERANCE_IDLE_TIMEOUT_S", 30.0)
    audio_input_device: str = _str("AUDIO_INPUT_DEVICE", "")

    # ------------------------------------------------------------------ #
    # Voix : Text-to-Speech                                              #
    # ------------------------------------------------------------------ #
    # auto = Piper si dispo (local) → Edge-TTS (cloud gratuit) → SAPI (local)
    tts_provider: str = _str("TTS_PROVIDER", "auto")
    piper_voice: str = _str("PIPER_VOICE", "siwis")
    piper_quality: str = _str("PIPER_QUALITY", "medium")
    edge_tts_voice: str = _str("EDGE_TTS_VOICE", "fr-FR-DeniseNeural")
    kokoro_voice: str = _str("KOKORO_VOICE", "ff_sara")
    elevenlabs_api_key: str = _str("ELEVENLABS_API_KEY")
    elevenlabs_voice_id: str = _str("ELEVENLABS_VOICE_ID", "pFZP5JQG7iQjIQuC4Bku")

    # ------------------------------------------------------------------ #
    # Vision & contrôle du PC                                            #
    # ------------------------------------------------------------------ #
    screen_monitor: int = _int("SCREEN_MONITOR", 1)
    allow_pc_control: bool = _bool("ALLOW_PC_CONTROL", True)
    allow_shell: bool = _bool("ALLOW_SHELL", False)

    # ------------------------------------------------------------------ #
    # Portails scolaires (Playwright)                                    #
    # ------------------------------------------------------------------ #
    pronote_url: str = _str("PRONOTE_URL")
    educonnect_url: str = _str("EDUCONNECT_URL")
    portal_username: str = _str("PORTAL_USERNAME")
    portal_password: str = _str("PORTAL_PASSWORD")
    automation_headless: bool = _bool("AUTOMATION_HEADLESS", False)
    portal_user_selector: str = _str("PORTAL_USER_SELECTOR")
    portal_password_selector: str = _str("PORTAL_PASSWORD_SELECTOR")
    portal_submit_selector: str = _str("PORTAL_SUBMIT_SELECTOR")

    # ------------------------------------------------------------------ #
    # Google Workspace                                                   #
    # ------------------------------------------------------------------ #
    google_credentials_file: str = _str("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    google_token_file: str = _str("GOOGLE_TOKEN_FILE", "token.json")
    google_timezone: str = _str("GOOGLE_TIMEZONE", "Europe/Paris")
    google_calendar_id: str = _str("GOOGLE_CALENDAR_ID", "primary")

    # ------------------------------------------------------------------ #
    # Téléphonie (Twilio + Vapi)                                         #
    # ------------------------------------------------------------------ #
    telephony_enabled: bool = _bool("TELEPHONY_ENABLED", False)
    twilio_account_sid: str = _str("TWILIO_ACCOUNT_SID")
    twilio_auth_token: str = _str("TWILIO_AUTH_TOKEN")
    twilio_phone_number: str = _str("TWILIO_PHONE_NUMBER")
    vapi_api_key: str = _str("VAPI_API_KEY")
    vapi_assistant_id: str = _str("VAPI_ASSISTANT_ID")
    vapi_phone_number_id: str = _str("VAPI_PHONE_NUMBER_ID")
    user_phone_number: str = _str("USER_PHONE_NUMBER")
    webhook_host: str = _str("WEBHOOK_HOST", "127.0.0.1")
    webhook_port: int = _int("WEBHOOK_PORT", 8629)
    webhook_secret: str = _str("WEBHOOK_SECRET")
    public_webhook_url: str = _str("PUBLIC_WEBHOOK_URL")
    call_timeout_s: int = _int("CALL_TIMEOUT_S", 120)

    # ------------------------------------------------------------------ #
    # Dossiers de données                                                #
    # ------------------------------------------------------------------ #
    data_dir: Path = field(default_factory=lambda: _resolve(_str("PERSEUS_DATA_DIR", "data")))
    logs_dir: Path = field(default_factory=lambda: _resolve(_str("PERSEUS_LOGS_DIR", "logs")))

    def __post_init__(self) -> None:
        """Dérive les sous-dossiers et chemins de fichiers après création."""
        self.voices_dir = self.data_dir / "voices"
        self.sounds_dir = self.data_dir / "sounds"
        self.downloads_dir = self.data_dir / "downloads"
        self.debug_dir = self.data_dir / "debug"
        self.browser_profile_dir = self.data_dir / "browser_profile"
        self.google_credentials_path = _resolve(self.google_credentials_file)
        self.google_token_path = _resolve(self.google_token_file)

    def ensure_dirs(self) -> None:
        """Crée l'arborescence de dossiers de données si nécessaire."""
        for folder in (
            self.data_dir,
            self.voices_dir,
            self.sounds_dir,
            self.downloads_dir,
            self.debug_dir,
            self.browser_profile_dir,
            self.logs_dir,
        ):
            folder.mkdir(parents=True, exist_ok=True)

    def validate(self) -> list[str]:
        """Renvoie la liste des problèmes de configuration détectés."""
        problems: list[str] = []
        if not self.openrouter_api_key:
            problems.append("OPENROUTER_API_KEY manquante — le cerveau ne pourra pas répondre (SETUP.md §3.1).")
        if not self.google_credentials_path.exists():
            problems.append("credentials.json absent — modules Google inutilisables (SETUP.md §3.2).")
        if not self.pronote_url:
            problems.append("PRONOTE_URL vide — le module « devoirs » est inactif (SETUP.md §3.7).")
        if self.telephony_enabled:
            missing = [
                name for name, value in {
                    "VAPI_API_KEY": self.vapi_api_key,
                    "VAPI_ASSISTANT_ID": self.vapi_assistant_id,
                    "VAPI_PHONE_NUMBER_ID": self.vapi_phone_number_id,
                    "USER_PHONE_NUMBER": self.user_phone_number,
                    "PUBLIC_WEBHOOK_URL": self.public_webhook_url,
                }.items() if not value
            ]
            if missing:
                problems.append(f"Téléphonie activée mais variables manquantes : {', '.join(missing)}.")
        return problems
