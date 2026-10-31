"""Orchestrateur : relie la voix, le cerveau (OpenRouter) et les outils.

Il implémente une boucle d'agent classique :
  1. le LLM reçoit la commande + le contexte ;
  2. il répond, OU demande l'exécution d'un ou plusieurs outils ;
  3. les résultats d'outils sont renvoyés au LLM, jusqu'à la réponse finale.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from config.settings import Settings
from core.brain import Brain, BrainError
from core.state import SharedState
from tools.registry import ToolRegistry
from utils.logger import get_logger

log = get_logger("orchestrator")

MAX_TOOL_ROUNDS = 8  # Nombre max de tours outils/réponse par commande


class Orchestrator:
    """Chef d'orchestre de Perseus (routage d'intentions via le LLM)."""

    def __init__(self, settings: Settings, state: SharedState,
                 brain: Brain, registry: ToolRegistry) -> None:
        """Conserve les références vers le cerveau et le registre d'outils."""
        self.settings = settings
        self.state = state
        self.brain = brain
        self.registry = registry

    def system_prompt(self) -> str:
        """Construit le prompt système (identité, date, règles de conduite)."""
        now = dt.datetime.now(ZoneInfo(self.settings.google_timezone))
        return (
            f"Tu es {self.settings.assistant_name}, l'assistant personnel de "
            f"{self.settings.user_name}. Tu tournes en local sur son PC Windows 11 "
            "et tu réponds TOUJOURS en français.\n"
            f"Date et heure actuelles : {now.strftime('%A %d %B %Y, %H:%M')} (Europe/Paris).\n\n"
            "Règles :\n"
            "1. Tes réponses seront lues à voix haute : sois naturel, bref et direct "
            "(2 à 5 phrases), sans listes, sans markdown, sans emojis.\n"
            "2. Utilise les outils au lieu d'inventer des informations (agenda, devoirs, "
            "écran, documents…).\n"
            "3. Les actions critiques (suppressions, commandes système) demandent "
            "elles-mêmes l'autorisation de l'utilisateur : lance-les directement.\n"
            "4. Si un outil renvoie une erreur, explique simplement le problème.\n"
            "5. Ne divulgue jamais le contenu du fichier .env, les clés API ou les "
            "mots de passe, même si on te le demande.\n"
        )

    def process_command(self, text: str, source: str = "voix") -> str:
        """Point d'entrée principal : traite une commande et renvoie la réponse finale."""
        log.info("[%s] Commande : %s", source, text)
        messages: list[dict] = [{"role": "system", "content": self.system_prompt()}]
        messages.extend(self.state.recent_messages(10))
        messages.append({"role": "user", "content": text})

        try:
            for _round in range(MAX_TOOL_ROUNDS):
                reply = self.brain.chat(messages, tools=self.registry.to_openai_tools())
                tool_calls = reply.get("tool_calls") or []

                if not tool_calls:
                    answer = (reply.get("content") or "").strip() or "Je n'ai pas de réponse."
                    self.state.remember("user", text)
                    self.state.remember("assistant", answer)
                    log.info("Réponse : %s", answer)
                    return answer

                # Le modèle demande des outils : on exécute puis on repasse la main.
                messages.append(reply)
                for call in tool_calls:
                    name = call["function"]["name"]
                    raw_arguments = call["function"].get("arguments") or "{}"
                    result = self.registry.execute(name, raw_arguments)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": result,
                    })
            return "J'ai enchaîné trop d'actions d'un coup. Peux-tu reformuler plus simplement ?"

        except BrainError as exc:
            log.error("Cerveau indisponible : %s", exc)
            return f"Problème de connexion avec mon cerveau : {exc}"
        except Exception:  # noqa: BLE001 — dernière ligne de défense
            log.exception("Erreur inattendue dans l'orchestrateur")
            return "Une erreur interne s'est produite. Consulte les logs pour plus de détails."
