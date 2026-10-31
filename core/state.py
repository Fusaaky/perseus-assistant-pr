"""État partagé de l'assistant.

Contient :
- la mémoire de conversation (fil des derniers échanges) ;
- un annuaire de services partagés entre modules (brain, calendar, automation…) ;
- les demandes d'autorisation en attente (appels téléphoniques de confirmation).

Toutes les méthodes sont thread-safe : la voix, le serveur webhook et les
outils du LLM vivent dans des threads différents.
"""

from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PermissionRequest:
    """Une demande d'autorisation en attente de réponse humaine."""

    id: str
    description: str
    event: threading.Event = field(default_factory=threading.Event)
    granted: bool = False
    note: str = ""


class SharedState:
    """Conteneur central partagé entre tous les modules de Perseus."""

    def __init__(self, max_memory: int = 20) -> None:
        """Initialise la mémoire, l'annuaire de services et les autorisations."""
        self._lock = threading.Lock()
        self.memory: deque[dict] = deque(maxlen=max_memory)
        self._services: dict[str, Any] = {}
        self._permissions: dict[str, PermissionRequest] = {}
        self.call_summaries: deque[dict] = deque(maxlen=20)

    # ------------------------------------------------------------------ #
    # Services partagés                                                  #
    # ------------------------------------------------------------------ #
    def set_service(self, name: str, service: Any) -> None:
        """Enregistre un service partagé (ex. 'calendar', 'automation')."""
        with self._lock:
            self._services[name] = service

    def get_service(self, name: str) -> Any:
        """Renvoie un service enregistré, ou lève KeyError s'il est absent."""
        with self._lock:
            if name not in self._services:
                raise KeyError(f"Service « {name} » non initialisé")
            return self._services[name]

    def has_service(self, name: str) -> bool:
        """Indique si un service a déjà été enregistré."""
        with self._lock:
            return name in self._services

    # ------------------------------------------------------------------ #
    # Mémoire de conversation                                            #
    # ------------------------------------------------------------------ #
    def remember(self, role: str, content: str) -> None:
        """Mémorise un tour de conversation (role: user | assistant)."""
        if not content:
            return
        with self._lock:
            self.memory.append({"role": role, "content": content})

    def recent_messages(self, count: int = 10) -> list[dict]:
        """Renvoie les derniers tours de conversation (contexte du LLM)."""
        with self._lock:
            return list(self.memory)[-count:]

    # ------------------------------------------------------------------ #
    # Autorisations (actions critiques)                                  #
    # ------------------------------------------------------------------ #
    def new_permission(self, description: str) -> PermissionRequest:
        """Crée et enregistre une nouvelle demande d'autorisation."""
        request = PermissionRequest(id=uuid.uuid4().hex[:12], description=description)
        with self._lock:
            self._permissions[request.id] = request
        return request

    def get_permission(self, permission_id: str) -> PermissionRequest | None:
        """Renvoie une demande d'autorisation par son identifiant."""
        with self._lock:
            return self._permissions.get(permission_id)

    def resolve_permission(self, permission_id: str, granted: bool, note: str = "") -> bool:
        """Résout une demande d'autorisation (idempotent). Renvoie True si résolue."""
        with self._lock:
            request = self._permissions.get(permission_id)
            if request is None or request.event.is_set():
                return False
            request.granted = granted
            request.note = note
            request.event.set()
            return True

    def resolve_latest_pending(self, granted: bool, note: str = "") -> bool:
        """Résout la demande en attente la plus récente (fallback sans id)."""
        with self._lock:
            target = None
            for request in reversed(list(self._permissions.values())):
                if not request.event.is_set():
                    target = request
                    break
        if target is None:
            return False
        return self.resolve_permission(target.id, granted, note)

    def pending_permission(self) -> PermissionRequest | None:
        """Renvoie la plus ancienne demande d'autorisation non résolue."""
        with self._lock:
            for request in self._permissions.values():
                if not request.event.is_set():
                    return request
        return None

    def wait_permission(self, permission_id: str, timeout_s: float) -> tuple[bool, str]:
        """Bloque jusqu'à la résolution d'une demande (ou expiration du délai)."""
        with self._lock:
            request = self._permissions.get(permission_id)
        if request is None:
            return False, "demande introuvable"
        request.event.wait(timeout_s)
        return request.granted, request.note
