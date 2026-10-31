"""Registre des outils de Perseus (« function calling »).

Chaque outil associe un schéma JSON (format OpenAI « tools ») à un
gestionnaire Python. Le cerveau (LLM) décide seul d'appeler les outils ;
les actions sensibles passent d'abord par la barrière d'autorisation
humaine (core/gate.py — appel téléphonique via Vapi/Twilio).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from config.settings import Settings
from core.state import SharedState
from utils.logger import get_logger

log = get_logger("tools")


@dataclass
class ToolSpec:
    """Description complète d'un outil exposé au LLM."""

    name: str
    description: str
    parameters: dict                     # Schéma JSON des arguments
    handler: Callable[..., str]          # Fonction Python exécutée
    dangerous: bool = False              # Nécessite une autorisation humaine


class ToolRegistry:
    """Collection d'outils consultable par le cerveau."""

    def __init__(self) -> None:
        """Registre vide au départ."""
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        """Ajoute un outil au registre."""
        self._tools[spec.name] = spec

    def to_openai_tools(self) -> list[dict]:
        """Renvoie les outils au format attendu par l'API OpenAI/OpenRouter."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    def execute(self, name: str, arguments: dict | str) -> str:
        """Exécute un outil par son nom et renvoie TOUJOURS une chaîne."""
        spec = self._tools.get(name)
        if spec is None:
            return f"ERREUR : outil inconnu « {name} »."
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
        except json.JSONDecodeError:
            return f"ERREUR : arguments JSON invalides pour {name}."
        log.info("Outil → %s(%s)", name,
                 ", ".join(f"{key}={str(value)[:50]}" for key, value in args.items()))
        try:
            return str(spec.handler(**args))
        except TypeError as exc:
            return f"ERREUR : arguments invalides pour {name} ({exc})."
        except Exception as exc:  # noqa: BLE001 — l'erreur est renvoyée au LLM
            log.exception("Échec de l'outil %s", name)
            return f"ERREUR pendant {name} : {exc}"


def build_default_registry(settings: Settings, state: SharedState) -> ToolRegistry:
    """Construit le registre standard de Perseus (outils câblés aux modules)."""
    registry = ToolRegistry()

    def svc(name: str):
        """Accès paresseux aux services enregistrés par main.py."""
        try:
            return state.get_service(name)
        except KeyError as exc:
            raise RuntimeError(f"Module « {name} » non initialisé (vérifie la configuration).") from exc

    # ------------------------------------------------------------------ #
    # Vision                                                             #
    # ------------------------------------------------------------------ #
    registry.register(ToolSpec(
        name="lire_ecran",
        description=(
            "Capture l'écran de l'utilisateur et le fait analyser par un modèle "
            "vision. À utiliser quand il demande « qu'est-ce qui s'affiche », "
            "de lire quelque chose à l'écran, ou de suivre une vidéo."
        ),
        parameters={
            "type": "object",
            "properties": {
                "question": {"type": "string",
                             "description": "Ce qu'il faut chercher/lire sur l'écran."},
            },
            "required": [],
        },
        handler=lambda question="": svc("screen").analyze(question),
    ))

    registry.register(ToolSpec(
        name="prendre_capture_ecran",
        description="Enregistre une capture d'écran dans data/debug et renvoie le chemin.",
        parameters={"type": "object", "properties": {}},
        handler=lambda: str(svc("screen").save_debug(svc("screen").capture(), "capture_demandee")),
    ))

    # ------------------------------------------------------------------ #
    # Contrôle du PC                                                     #
    # ------------------------------------------------------------------ #
    def pc_guard(action: Callable[[], str]) -> str:
        """Vérifie que le contrôle du PC est autorisé avant d'exécuter l'action."""
        if not settings.allow_pc_control:
            return "ERREUR : contrôle du PC désactivé (ALLOW_PC_CONTROL=false dans .env)."
        return action()

    registry.register(ToolSpec(
        name="ouvrir_application",
        description="Ouvre une application installée (chrome, calculatrice, spotify, vscode, bloc-notes…).",
        parameters={
            "type": "object",
            "properties": {"application": {"type": "string"}},
            "required": ["application"],
        },
        handler=lambda application: pc_guard(lambda: svc("pc").open_app(application)),
    ))

    registry.register(ToolSpec(
        name="ouvrir_site_web",
        description="Ouvre une URL dans le navigateur par défaut.",
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
        handler=lambda url: pc_guard(lambda: svc("pc").open_url(url)),
    ))

    registry.register(ToolSpec(
        name="taper_texte",
        description="Tape du texte dans la fenêtre active (via presse-papiers, gère les accents).",
        parameters={
            "type": "object",
            "properties": {"texte": {"type": "string"}},
            "required": ["texte"],
        },
        handler=lambda texte: pc_guard(lambda: svc("pc").type_text(texte)),
    ))

    registry.register(ToolSpec(
        name="raccourci_clavier",
        description="Envoie un raccourci clavier, ex. touches=['ctrl','s'] ou ['alt','tab'].",
        parameters={
            "type": "object",
            "properties": {"touches": {"type": "array", "items": {"type": "string"}}},
            "required": ["touches"],
        },
        handler=lambda touches: pc_guard(lambda: svc("pc").press_hotkey(touches)),
    ))

    registry.register(ToolSpec(
        name="cliquer",
        description="Clique à une position absolue de l'écran (pixels). Utilise lire_ecran d'abord pour repérer la cible.",
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "bouton": {"type": "string", "enum": ["left", "right"]},
            },
            "required": ["x", "y"],
        },
        handler=lambda x, y, bouton="left": pc_guard(lambda: svc("pc").click(x, y, bouton)),
    ))

    registry.register(ToolSpec(
        name="defiler",
        description="Fait défiler la fenêtre active (positif = vers le haut, négatif = vers le bas).",
        parameters={
            "type": "object",
            "properties": {"montant": {"type": "integer"}},
            "required": ["montant"],
        },
        handler=lambda montant: pc_guard(lambda: svc("pc").scroll(montant)),
    ))

    def guarded_shell(command: str) -> str:
        """Commande système protégée par la barrière d'autorisation humaine."""
        if not settings.allow_shell:
            return "ERREUR : ALLOW_SHELL=false — exécution de commandes désactivée."
        decision = svc("gate").request_authorization(
            f"Exécuter la commande système : {command}"
        )
        if decision != "accord":
            return "REFUSÉ : l'utilisateur n'a pas autorisé la commande."
        return svc("pc").run_command(command)

    registry.register(ToolSpec(
        name="executer_commande",
        description=(
            "Exécute une commande Windows (cmd/PowerShell). ACTION CRITIQUE : "
            "une autorisation est demandée à l'utilisateur par téléphone avant exécution."
        ),
        parameters={
            "type": "object",
            "properties": {"commande": {"type": "string"}},
            "required": ["commande"],
        },
        handler=guarded_shell,
        dangerous=True,
    ))

    # ------------------------------------------------------------------ #
    # Portails scolaires                                                 #
    # ------------------------------------------------------------------ #
    registry.register(ToolSpec(
        name="obtenir_devoirs",
        description=(
            "Se connecte au portail scolaire (Pronote/EduConnect) et renvoie le "
            "cahier de texte brut. À utiliser pour toute question sur les devoirs."
        ),
        parameters={"type": "object", "properties": {}},
        handler=lambda: svc("automation").get_homework(svc("worker")),
    ))

    registry.register(ToolSpec(
        name="obtenir_notes",
        description="Récupère les dernières notes affichées dans Pronote.",
        parameters={"type": "object", "properties": {}},
        handler=lambda: svc("automation").get_grades(svc("worker")),
    ))

    # ------------------------------------------------------------------ #
    # Google Workspace                                                   #
    # ------------------------------------------------------------------ #
    registry.register(ToolSpec(
        name="lister_evenements",
        description="Liste les prochains événements Google Calendar (7 jours par défaut).",
        parameters={
            "type": "object",
            "properties": {"jours": {"type": "integer", "description": "Nombre de jours à couvrir"}},
            "required": [],
        },
        handler=lambda jours=7: svc("calendar").describe_upcoming(days_ahead=int(jours)),
    ))

    registry.register(ToolSpec(
        name="creer_evenement",
        description=(
            "Crée un événement Google Calendar. debut_iso au format "
            "'2026-11-05T14:00' (heure locale Europe/Paris)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "titre": {"type": "string"},
                "debut_iso": {"type": "string"},
                "fin_iso": {"type": "string", "description": "Optionnel ; sinon duree_minutes"},
                "duree_minutes": {"type": "integer"},
                "description": {"type": "string"},
                "lieu": {"type": "string"},
            },
            "required": ["titre", "debut_iso"],
        },
        handler=lambda titre, debut_iso, fin_iso="", duree_minutes=60, description="", lieu="":
            svc("calendar").create_event(titre, debut_iso, fin_iso, int(duree_minutes), description, lieu),
    ))

    registry.register(ToolSpec(
        name="modifier_evenement",
        description="Modifie un événement Google Calendar trouvé par mots-clés (dans le titre).",
        parameters={
            "type": "object",
            "properties": {
                "recherche": {"type": "string"},
                "nouveau_titre": {"type": "string"},
                "nouveau_debut_iso": {"type": "string"},
                "nouvelle_fin_iso": {"type": "string"},
            },
            "required": ["recherche"],
        },
        handler=lambda recherche, nouveau_titre="", nouveau_debut_iso="", nouvelle_fin_iso="":
            svc("calendar").update_event(recherche, nouveau_titre, nouveau_debut_iso, nouvelle_fin_iso),
    ))

    def guarded_delete_event(recherche: str) -> str:
        """Suppression d'événement protégée par autorisation humaine."""
        decision = svc("gate").request_authorization(
            f"Supprimer l'événement Calendar « {recherche} »"
        )
        if decision != "accord":
            return "REFUSÉ : suppression non autorisée."
        return svc("calendar").delete_event(recherche)

    registry.register(ToolSpec(
        name="supprimer_evenement",
        description="Supprime un événement Google Calendar trouvé par mots-clés. ACTION CRITIQUE.",
        parameters={
            "type": "object",
            "properties": {"recherche": {"type": "string"}},
            "required": ["recherche"],
        },
        handler=guarded_delete_event,
        dangerous=True,
    ))

    registry.register(ToolSpec(
        name="rechercher_dans_drive",
        description="Recherche des fichiers par nom dans Google Drive.",
        parameters={
            "type": "object",
            "properties": {"requete": {"type": "string"}},
            "required": ["requete"],
        },
        handler=lambda requete: svc("workspace").search_files(requete),
    ))

    registry.register(ToolSpec(
        name="lire_document_google",
        description="Lit le contenu texte d'un Google Doc (par titre ou identifiant).",
        parameters={
            "type": "object",
            "properties": {"nom": {"type": "string"}},
            "required": ["nom"],
        },
        handler=lambda nom: svc("workspace").read_doc(nom),
    ))

    # ------------------------------------------------------------------ #
    # Téléphonie                                                         #
    # ------------------------------------------------------------------ #
    def phone_call(message: str) -> str:
        """Appel sortant simple : transmet un message vocal à l'utilisateur."""
        from telephony.vapi import TelephonyError, VapiClient

        if not settings.telephony_enabled:
            return "ERREUR : téléphonie désactivée (TELEPHONY_ENABLED=false dans .env)."
        client = VapiClient(settings)
        try:
            call = client.place_call(
                customer_number=settings.user_phone_number,
                assistant_id=settings.vapi_assistant_id,
                phone_number_id=settings.vapi_phone_number_id,
                first_message=message,
                max_duration_s=120,
            )
            ended = client.wait_for_call_end(call["id"], timeout_s=180)
        except TelephonyError as exc:
            return f"ERREUR téléphonie : {exc}"
        summary = client.call_summary(ended) or "(sans résumé)"
        return f"Appel terminé ({ended.get('endedReason', 'ok')}). Résumé : {summary[:500]}"

    registry.register(ToolSpec(
        name="telephoner_message",
        description=(
            "Appelle le téléphone de l'utilisateur via Vapi/Twilio pour un rappel "
            "urgent (message court). Bloque jusqu'à la fin de l'appel."
        ),
        parameters={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        handler=phone_call,
    ))

    registry.register(ToolSpec(
        name="demander_autorisation_telephone",
        description=(
            "Appelle l'utilisateur pour lui demander verbalement l'autorisation "
            "d'une action sensible ; renvoie 'accord' ou 'refusé'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Description claire de l'action à autoriser"},
            },
            "required": ["action"],
        },
        handler=lambda action: svc("gate").request_authorization(action),
    ))

    return registry
