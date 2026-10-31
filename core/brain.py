"""Cerveau de Perseus : interface avec les LLM d'OpenRouter.

OpenRouter expose une API 100 % compatible avec le SDK `openai` : il suffit
de changer la `base_url` et d'utiliser une clé OpenRouter. Le cerveau gère :
- les conversations avec « function calling » (outils) ;
- l'analyse d'images (modèle vision, pour lire l'écran) ;
- la classification oui/non (utilisée par la barrière d'autorisation).
"""

from __future__ import annotations

import base64
import io
import time

from openai import APIConnectionError, APIStatusError, BadRequestError, OpenAI, RateLimitError
from PIL import Image

from config.settings import Settings
from utils.logger import get_logger

log = get_logger("brain")


class BrainError(RuntimeError):
    """Erreur levée quand le cerveau (LLM) ne peut pas répondre."""


class Brain:
    """Client LLM unique de Perseus (chat, vision, classification)."""

    def __init__(self, settings: Settings) -> None:
        """Initialise le client OpenRouter avec la clé configurée."""
        self.settings = settings
        self.model = settings.llm_model
        self.vision_model = settings.vision_model
        self.client = OpenAI(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key or "MISSING_KEY",
            default_headers={
                "HTTP-Referer": "https://localhost/perseus",
                "X-Title": "Perseus",
            },
        )

    # ------------------------------------------------------------------ #
    # Conversation                                                       #
    # ------------------------------------------------------------------ #
    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             temperature: float | None = None, max_tokens: int | None = None) -> dict:
        """Envoie la conversation au LLM et renvoie le message sous forme de dict.

        Le dict renvoyé contient `role`, `content` et éventuellement
        `tool_calls` (demandes d'exécution d'outils formulées par le modèle).
        """
        last_error: Exception | None = None
        for attempt in range(2):
            # Certains modèles gratuits refusent le "function calling" :
            # on retente une fois sans outils avant d'abandonner.
            use_tools = tools if attempt == 0 else None
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.settings.llm_temperature if temperature is None else temperature,
                    max_tokens=max_tokens or self.settings.llm_max_tokens,
                    **({"tools": use_tools, "tool_choice": "auto"} if use_tools else {}),
                )
                return self._message_to_dict(response.choices[0].message)
            except BadRequestError as exc:
                if attempt == 0 and tools:
                    log.warning("Modèle incompatible avec les outils (%s) → nouvel essai sans outils.", exc)
                    continue
                raise BrainError(f"Requête refusée par OpenRouter : {exc}") from exc
            except RateLimitError as exc:
                if attempt == 0:
                    time.sleep(4)
                    continue
                raise BrainError("Quota OpenRouter épuisé ou limite de débit atteinte (402/429).") from exc
            except (APIConnectionError, APIStatusError) as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(2)
                    continue
        raise BrainError(f"OpenRouter injoignable : {last_error}")

    @staticmethod
    def _message_to_dict(message) -> dict:
        """Convertit proprement un message du SDK en dict JSON-sérialisable."""
        result: dict = {"role": message.role, "content": message.content or ""}
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            result["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in tool_calls
            ]
        return result

    # ------------------------------------------------------------------ #
    # Vision (lecture d'écran)                                           #
    # ------------------------------------------------------------------ #
    def describe_image(self, image: Image.Image, question: str) -> str:
        """Envoie une image au modèle vision d'OpenRouter et renvoie sa réponse."""
        data_url = self._image_to_data_url(image)
        response = self.client.chat.completions.create(
            model=self.vision_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
            max_tokens=700,
            temperature=0.2,
        )
        return (response.choices[0].message.content or "").strip()

    @staticmethod
    def _image_to_data_url(image: Image.Image, max_width: int = 1280, quality: int = 85) -> str:
        """Redimensionne puis encode une image PIL en data-URL JPEG base64."""
        if image.width > max_width:
            ratio = max_width / image.width
            image = image.resize((max_width, int(image.height * ratio)), Image.LANCZOS)
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, "JPEG", quality=quality)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"

    # ------------------------------------------------------------------ #
    # Classification oui / non                                           #
    # ------------------------------------------------------------------ #
    def classify_yes_no(self, text: str, question: str = "Cela signifie-t-il OUI ?") -> bool:
        """Décide si un texte (ex. transcription d'appel) vaut oui ou non."""
        try:
            answer = self.chat(
                messages=[{
                    "role": "user",
                    "content": (
                        f"Question : {question}\n"
                        f"Texte : « {text[:2000]} »\n"
                        "Réponds uniquement par OUI ou par NON."
                    ),
                }],
                temperature=0.0,
                max_tokens=8,
            )
            content = str(answer.get("content", "")).upper()
            return "OUI" in content and "NON" not in content
        except BrainError as exc:
            log.warning("Classification impossible : %s", exc)
            return False
