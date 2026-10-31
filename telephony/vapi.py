"""Client REST Vapi.ai — appels sortants et suivi d'appels.

Architecture téléphonie de Perseus :

    📱 Ton portable  ←─ PSTN ─  numéro Twilio  ←─ Vapi.ai (agent vocal)  ←─ API REST (ce fichier)
                                              ←─ webhooks  →  FastAPI local (webhook_server.py)

Vapi héberge l'agent conversationnel (STT → LLM → TTS) dans le cloud, branché
sur un numéro Twilio. Perseus déclenche les appels ici (POST /call) et reçoit
les résultats sur son serveur de webhooks local (outils serveur, fin d'appel).
"""

from __future__ import annotations

import time

import requests

from config.settings import Settings
from utils.logger import get_logger

log = get_logger("vapi")


class TelephonyError(RuntimeError):
    """Erreur lors d'une opération téléphonique (Vapi/Twilio)."""


class VapiClient:
    """Client HTTP minimaliste pour l'API Vapi.ai."""

    BASE_URL = "https://api.vapi.ai"

    def __init__(self, settings: Settings) -> None:
        """Prépare la session HTTP avec la clé privée Vapi."""
        self.settings = settings
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {settings.vapi_api_key}",
            "Content-Type": "application/json",
        })

    # ------------------------------------------------------------------ #
    # Appels sortants                                                    #
    # ------------------------------------------------------------------ #
    def place_call(self, *, customer_number: str, assistant_id: str,
                   phone_number_id: str, first_message: str = "",
                   variable_values: dict | None = None,
                   max_duration_s: int = 120) -> dict:
        """Déclenche un appel sortant vers `customer_number` via un assistant Vapi."""
        if not (self.settings.vapi_api_key and assistant_id and phone_number_id):
            raise TelephonyError(
                "Configuration Vapi incomplète : vérifie VAPI_API_KEY, "
                "VAPI_ASSISTANT_ID et VAPI_PHONE_NUMBER_ID dans .env."
            )
        overrides = {
            "firstMessage": first_message or None,
            "variableValues": variable_values or {},
            "maxDurationSeconds": int(max_duration_s),
        }
        body = {
            "assistantId": assistant_id,
            "phoneNumberId": phone_number_id,
            "customer": {"number": customer_number},
            "assistantOverrides": {key: value for key, value in overrides.items() if value},
        }
        try:
            response = self.session.post(f"{self.BASE_URL}/call", json=body, timeout=30)
            response.raise_for_status()
            call = response.json()
            log.info("Appel Vapi créé : id=%s → %s", call.get("id"), customer_number)
            return call
        except requests.RequestException as exc:
            detail = (getattr(getattr(exc, "response", None), "text", "") or "")[:300]
            raise TelephonyError(f"Échec de l'appel Vapi : {exc} {detail}") from exc

    # ------------------------------------------------------------------ #
    # Suivi d'appels                                                     #
    # ------------------------------------------------------------------ #
    def get_call(self, call_id: str) -> dict:
        """Récupère l'état d'un appel (status, endedReason, analysis…)."""
        try:
            response = self.session.get(f"{self.BASE_URL}/call/{call_id}", timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            raise TelephonyError(f"Lecture de l'appel {call_id} impossible : {exc}") from exc

    def wait_for_call_end(self, call_id: str, timeout_s: int = 180, poll_s: int = 5) -> dict:
        """Sondage jusqu'à la fin de l'appel (fallback si aucun webhook actif)."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            call = self.get_call(call_id)
            if call.get("status") == "ended":
                return call
            time.sleep(poll_s)
        raise TelephonyError("Délai dépassé en attendant la fin de l'appel.")

    @staticmethod
    def call_summary(call: dict) -> str:
        """Extrait un résumé du dialogue (analyse Vapi ou transcription)."""
        analysis = call.get("analysis") or {}
        if analysis.get("summary"):
            return str(analysis["summary"])
        artifact = call.get("artifact") or {}
        messages = artifact.get("messages") or call.get("messages") or []
        parts = [str(m.get("message", "")) for m in messages if isinstance(m, dict)]
        return " ".join(part for part in parts if part).strip()
