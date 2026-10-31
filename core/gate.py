"""Barrière de sécurité : autorisation humaine avant toute action critique.

Deux modes (automatiquement choisis selon la configuration) :
1. Téléphonie configurée  → Perseus appelle réellement l'utilisateur via
   Vapi.ai/Twilio et attend un « oui » ou un « non » prononcé au téléphone.
2. Sinon (mode test)      → confirmation dans la console.

Chaque décision est journalisée dans `data/audit.log` pour garder une trace.
"""

from __future__ import annotations

from datetime import datetime

from config.settings import Settings
from core.state import SharedState
from utils.logger import get_logger

log = get_logger("gate")


class PermissionGate:
    """Porte de contrôle demandant une validation humaine avant une action."""

    def __init__(self, settings: Settings, state: SharedState) -> None:
        """Conserve la configuration et l'état partagé (chemin d'audit inclus)."""
        self.settings = settings
        self.state = state
        self.audit_path = settings.data_dir / "audit.log"

    def request_authorization(self, description: str, timeout_s: int | None = None) -> str:
        """Bloque jusqu'à une décision humaine. Renvoie 'accord' ou 'refusé'."""
        description = description.strip() or "action non décrite"
        timeout_s = timeout_s or self.settings.call_timeout_s
        log.warning("ACTION CRITIQUE demandée : %s", description)
        self._audit(f"DEMANDE | {description}")

        if self.settings.telephony_enabled and self.settings.vapi_api_key:
            granted, note = self._authorize_by_phone(description, timeout_s)
        else:
            granted, note = self._authorize_by_console(description)

        self._audit(f"{'ACCORD' if granted else 'REFUS'} | {description} | {note}")
        return "accord" if granted else "refusé"

    # ------------------------------------------------------------------ #
    # Mode 1 : appel téléphonique (production)                           #
    # ------------------------------------------------------------------ #
    def _authorize_by_phone(self, description: str, timeout_s: int) -> tuple[bool, str]:
        """Appelle l'utilisateur (Vapi) et attend sa réponse verbale."""
        from telephony.vapi import TelephonyError, VapiClient  # import tardif

        request = self.state.new_permission(description)
        client = VapiClient(self.settings)
        first_message = (
            f"Bonjour {self.settings.user_name}, c'est {self.settings.assistant_name}. "
            f"Je demande ton autorisation pour : {description}. "
            "Réponds clairement oui ou non, puis raccroche."
        )
        try:
            call = client.place_call(
                customer_number=self.settings.user_phone_number,
                assistant_id=self.settings.vapi_assistant_id,
                phone_number_id=self.settings.vapi_phone_number_id,
                first_message=first_message,
                variable_values={"permission_id": request.id, "action": description},
                max_duration_s=max(45, timeout_s),
            )
        except TelephonyError as exc:
            log.error("Appel impossible (%s) → refus par défaut (fail-safe).", exc)
            self.state.resolve_permission(request.id, granted=False, note=f"appel échoué : {exc}")
            return False, f"appel échoué : {exc}"

        log.info("Appel d'autorisation lancé (id=%s). En attente de la réponse…", call.get("id"))
        granted, note = self.state.wait_permission(request.id, timeout_s)

        # Fallback si aucun webhook n'a répondu (tunnel down, etc.) : on
        # interroge directement Vapi pour récupérer le résumé de l'appel.
        if not request.event.is_set():
            try:
                ended = client.get_call(str(call.get("id")))
                summary = client.call_summary(ended)
                if summary:
                    brain = self.state.get_service("brain")
                    granted = brain.classify_yes_no(summary, "L'utilisateur a-t-il donné son accord ?")
                    note = f"sondage : {summary[:200]}"
                    self.state.resolve_permission(request.id, granted, note)
                else:
                    self.state.resolve_permission(request.id, False, "aucune réponse (délai dépassé)")
                    granted, note = False, "aucune réponse (délai dépassé)"
            except Exception as exc:  # noqa: BLE001 — fail-safe : refus
                self.state.resolve_permission(request.id, False, f"vérification impossible : {exc}")
                granted, note = False, f"vérification impossible : {exc}"

        return granted, note

    # ------------------------------------------------------------------ #
    # Mode 2 : console (test local sans téléphonie)                      #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _authorize_by_console(description: str) -> tuple[bool, str]:
        """Demande la confirmation au clavier (utile en phase de test)."""
        print("\n" + "=" * 64)
        print(f"⚠️  AUTORISATION REQUISE : {description}")
        answer = input("    Tape 'oui' pour autoriser, autre chose pour refuser > ").strip().lower()
        return answer in {"oui", "o", "yes", "y", "ok"}, f"console : {answer}"

    # ------------------------------------------------------------------ #
    # Journal d'audit                                                    #
    # ------------------------------------------------------------------ #
    def _audit(self, line: str) -> None:
        """Ajoute une ligne horodatée au journal d'audit (échec non fatal)."""
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.audit_path, "a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} | {line}\n")
        except OSError as exc:
            log.debug("Écriture du journal d'audit impossible : %s", exc)
