"""Serveur de webhooks local pour la téléphonie (Vapi.ai + Twilio).

Rôles :
1. Répondre aux « server tools » de Vapi : quand quelqu'un parle au téléphone
   avec Perseus, l'agent vocal interroge ce serveur pour obtenir des données
   fraîches (agenda, devoirs…) ou enregistrer une autorisation.
2. Recevoir les rapports de fin d'appel (résumé, transcription) et clôturer
   les demandes d'autorisation en attente.

⚠️ Ce serveur écoute sur 127.0.0.1:PORT. Pour être joignable par Vapi, il
doit être exposé via un tunnel public (cloudflared/ngrok — voir SETUP.md §4).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request

from config.settings import Settings
from core.state import SharedState
from utils.logger import get_logger

log = get_logger("webhook")

# Types d'événements Vapi représentant un appel d'outil serveur
TOOL_CALL_TYPES = {"function-call", "tool-calls"}


def _parse_arguments(raw) -> dict:
    """Convertit les arguments d'outil (chaîne JSON ou dict) en dict."""
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def _extract_function(call_item: dict) -> tuple[str, dict]:
    """Extrait (nom, arguments) d'un appel d'outil Vapi (plusieurs formats supportés)."""
    function = call_item.get("function") or {}
    if not function and isinstance(call_item.get("toolCall"), dict):
        function = call_item["toolCall"].get("function") or {}
    name = function.get("name") or call_item.get("name") or ""
    raw = function.get("arguments", function.get("parameters", call_item.get("parameters", "{}")))
    return name, _parse_arguments(raw)


def execute_phone_tool(state: SharedState, name: str, args: dict) -> str:
    """Exécute un outil demandé par l'agent téléphonique Vapi.

    C'est par ici que les appels ENTRANTS accèdent aux données du PC
    (agenda, devoirs…) et que les autorisations sont enregistrées.
    """
    if name == "obtenir_agenda":
        calendar = state.get_service("calendar")
        return calendar.describe_upcoming(days_ahead=1)

    if name == "obtenir_devoirs":
        automation = state.get_service("automation")
        worker = state.get_service("worker")
        return automation.get_homework(worker)[:2500]

    if name == "obtenir_notes":
        automation = state.get_service("automation")
        worker = state.get_service("worker")
        return automation.get_grades(worker)[:2500]

    if name == "dire_heure":
        return f"Il est {datetime.now().strftime('%H:%M')}."

    if name == "enregistrer_permission":
        decision = str(args.get("decision", "")).strip().lower()
        granted = decision in {"oui", "ouais", "yes", "ok", "d'accord", "daccord",
                               "accord", "j'autorise", "autorise", "vas-y", "va-y"}
        request = state.get_permission(str(args.get("permission_id", "")))
        resolved = state.resolve_permission(
            request.id, granted, note=f"téléphone : {decision}"
        ) if request else state.resolve_latest_pending(
            granted, note=f"téléphone : {decision}"
        )
        if not resolved:
            log.warning("Réponse d'autorisation reçue mais aucune demande en attente.")
        return f"Autorisation enregistrée : {'oui' if granted else 'non'}."

    return f"Outil inconnu : {name}"


def handle_end_of_call(state: SharedState, message: dict) -> None:
    """Stocke le résumé de l'appel et clôture une autorisation en attente."""
    call_id = str((message.get("call") or {}).get("id") or message.get("callId") or "")
    analysis = message.get("analysis") or {}
    artifact = message.get("artifact") or {}
    summary = str(analysis.get("summary") or "")
    transcript = str(artifact.get("transcript") or "")
    state.call_summaries.append({
        "call_id": call_id, "summary": summary, "transcript": transcript,
    })
    log.info("Fin d'appel %s : %s", call_id, summary[:200])

    # S'il reste une demande d'autorisation non résolue → on la clôt ici.
    pending = state.pending_permission()
    if pending is None:
        return
    combined = " ".join(part for part in (summary, transcript) if part)
    if not combined:
        state.resolve_permission(pending.id, False, "appel terminé sans réponse claire")
        return
    try:
        brain = state.get_service("brain")
        granted = brain.classify_yes_no(combined, "L'utilisateur a-t-il explicitement donné son accord ?")
    except Exception:  # noqa: BLE001 — fail-safe : refus
        granted = False
    state.resolve_permission(pending.id, granted, combined[:300])


def create_app(settings: Settings, state: SharedState) -> FastAPI:
    """Construit l'application FastAPI branchée sur l'état partagé."""
    app = FastAPI(title="Perseus Webhook Server", docs_url=None, redoc_url=None)

    def _check_secret(request: Request) -> None:
        """Vérifie le secret partagé Vapi si celui-ci est configuré."""
        if settings.webhook_secret and request.headers.get("x-vapi-secret") != settings.webhook_secret:
            raise HTTPException(status_code=401, detail="Secret webhook invalide")

    @app.get("/health")
    async def health():
        """Sonde de santé (utile pour vérifier que le tunnel fonctionne)."""
        return {"status": "ok", "service": "perseus-webhook"}

    @app.post("/vapi/webhook")
    async def vapi_webhook(request: Request):
        """Point d'entrée unique des événements Vapi (outils + rapports d'appel)."""
        _check_secret(request)
        payload = await request.json()
        message = payload.get("message", payload)
        event_type = str(message.get("type", ""))

        if event_type in TOOL_CALL_TYPES:
            from starlette.concurrency import run_in_threadpool

            calls: list[dict] = []
            for key in ("toolCallList", "toolWithToolCallList", "toolCalls", "toolWithToolCalls"):
                calls.extend(message.get(key) or [])

            results = []
            for call_item in calls:
                name, args = _extract_function(call_item)
                try:
                    # Exécution dans un thread : les outils sont bloquants.
                    result = await run_in_threadpool(execute_phone_tool, state, name, args)
                except Exception as exc:  # noqa: BLE001
                    log.exception("Outil téléphonique « %s » en échec", name)
                    result = f"ERREUR : {exc}"
                results.append({"toolCallId": call_item.get("id"), "result": result})

            if len(results) == 1:
                return {"result": results[0]["result"]}
            return {"results": results}

        if event_type == "end-of-call-report":
            await run_in_threadpool(handle_end_of_call, state, message)
            return {}

        log.debug("Événement Vapi ignoré : %s", event_type or "(inconnu)")
        return {}

    @app.post("/twilio/status")
    async def twilio_status(request: Request):
        """Journalise les callbacks de statut Twilio (informationnel)."""
        form = await request.form()
        log.info("Statut Twilio : %s %s", form.get("CallStatus", "?"), form.get("CallSid", ""))
        return {"ok": True}

    return app


def start_webhook_server(settings: Settings, state: SharedState) -> threading.Thread:
    """Démarre le serveur webhook dans un thread daemon et le renvoie."""
    app = create_app(settings, state)

    def _run() -> None:
        """Lance uvicorn (son propre event loop) dans ce thread."""
        import uvicorn

        uvicorn.run(app, host=settings.webhook_host, port=settings.webhook_port,
                    log_level="warning")

    thread = threading.Thread(target=_run, name="perseus-webhook", daemon=True)
    thread.start()
    log.info(
        "Serveur webhook démarré sur http://%s:%s  (URL publique : %s)",
        settings.webhook_host, settings.webhook_port,
        settings.public_webhook_url or "⚠️ NON CONFIGURÉE (voir SETUP.md §4)",
    )
    return thread
