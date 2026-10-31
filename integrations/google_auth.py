"""Authentification OAuth2 Google (Calendar, Docs, Drive).

Premier lancement : un navigateur s'ouvre pour consentir (ton compte doit
être déclaré comme « utilisateur test » dans l'écran de consentement Google
Cloud — voir SETUP.md §3.2). Le jeton est ensuite mis en cache dans
token.json et rafraîchi automatiquement aux lancements suivants.
"""

from __future__ import annotations

from pathlib import Path

from config.settings import Settings
from utils.logger import get_logger

log = get_logger("google")

# Portées (scopes) nécessaires aux trois modules Google de Perseus
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
]


def get_credentials(settings: Settings):
    """Renvoie des identifiants Google valides (cache + rafraîchissement auto)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path: Path = settings.google_token_path
    credentials = None

    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if credentials and credentials.valid:
        return credentials

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        token_path.write_text(credentials.to_json(), encoding="utf-8")
        return credentials

    credentials_file = settings.google_credentials_path
    if not credentials_file.exists():
        raise RuntimeError(
            f"Fichier {credentials_file.name} introuvable : télécharge-le depuis "
            "Google Cloud Console → Identifiants → OAuth (voir SETUP.md §3.2)."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
    log.info("Première connexion Google : un navigateur s'ouvre pour le consentement…")
    credentials = flow.run_local_server(port=0, prompt="consent")
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    log.info("Jeton Google enregistré dans %s.", token_path.name)
    return credentials


def get_service(settings: Settings, api: str, version: str):
    """Construit un client de service Google (calendar, docs, drive…)."""
    from googleapiclient.discovery import build

    return build(api, version, credentials=get_credentials(settings), cache_discovery=False)
