"""Tests de diagnostic module par module : `python main.py --test <module>`.

Chaque test est indépendant : il construit uniquement ce dont il a besoin,
afin de pouvoir valider un module sans que le reste soit configuré.
"""

from __future__ import annotations

from config.settings import Settings
from utils.logger import get_logger

log = get_logger("diagnostics")


def test_brain(settings: Settings) -> None:
    """Vérifie la clé OpenRouter avec une question simple."""
    from core.brain import Brain

    print("→ Modèle :", settings.llm_model)
    answer = Brain(settings).chat(
        [{"role": "user", "content": "Dis bonjour en une seule phrase courte."}]
    )
    print("✅ Réponse du cerveau :", answer.get("content"))


def test_tts(settings: Settings) -> None:
    """Synthétise et joue une phrase française avec le moteur configuré."""
    from voice.tts import create_tts

    tts = create_tts(settings)
    print("→ Moteur TTS :", type(tts).__name__, f"(réglage : {settings.tts_provider})")
    tts.speak(
        f"Bonjour {settings.user_name}, je suis {settings.assistant_name}. "
        "Le système vocal fonctionne parfaitement."
    )
    print("✅ TTS OK")


def test_stt(settings: Settings) -> None:
    """Enregistre une phrase au micro puis affiche la transcription Whisper."""
    from voice.audio import AudioInput
    from voice.stt import Transcriber

    microphone = AudioInput(settings)
    microphone.start()
    try:
        print("🎙️  Parle maintenant (6 secondes max, puis silence)…")
        audio = microphone.record_utterance(max_s=6, silence_s=2, idle_timeout_s=6)
    finally:
        microphone.stop()
    if audio.size == 0:
        print("⚠️  Aucune parole détectée — vérifie le micro ou baisse WAKE_ENERGY_THRESHOLD.")
        return
    text = Transcriber(settings).transcribe(audio)
    print("✅ Transcription Whisper :", text)


def test_vision(settings: Settings) -> None:
    """Capture l'écran et le fait décrire par le modèle vision."""
    from core.brain import Brain
    from vision.screen import ScreenVision

    print("→ Capture de l'écran en cours…")
    print("✅ Analyse :", ScreenVision(settings, Brain(settings)).analyze(
        "Décris brièvement ce qui est affiché à l'écran."))


def test_calendar(settings: Settings) -> None:
    """Liste les prochains événements Google Calendar (7 jours)."""
    from integrations.google_calendar import GoogleCalendar

    print("✅ Agenda à venir :\n" + GoogleCalendar(settings).describe_upcoming(7))


def test_playwright(settings: Settings) -> None:
    """Ouvre le portail Pronote, détecte la page de connexion, capture l'écran."""
    if not settings.pronote_url:
        print("⚠️  PRONOTE_URL vide dans .env — test annulé (SETUP.md §3.7).")
        return
    from automation.browser import PlaywrightWorker, debug_dump, find_first_visible

    worker = PlaywrightWorker(settings)
    try:
        def job(context):
            """Joint le portail, détecte formulaire/ENT et produit un dump."""
            page = context.new_page()
            try:
                page.goto(settings.pronote_url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2500)
                login_field = find_first_visible(page, ['input[type="password"]'], timeout_ms=8000)
                edu_button = find_first_visible(page, ['text=EduConnect'], timeout_ms=3000)
                debug_dump(page, "test_playwright", settings)
                return {
                    "titre": page.title(),
                    "url": page.url,
                    "formulaire_login": bool(login_field),
                    "bouton_educonnect": bool(edu_button),
                }
            finally:
                page.close()

        info = worker.submit(job)
        print("✅ Portail joint :", info)
        print("   Captures + textes de débogage dans data/debug/")
    finally:
        worker.shutdown()


def test_phone(settings: Settings) -> None:
    """Place un appel test Vapi vers USER_PHONE_NUMBER (décroche !)."""
    from telephony.vapi import VapiClient

    if not settings.telephony_enabled:
        print("⚠️  TELEPHONY_ENABLED=false — configure la téléphonie d'abord (SETUP.md §3.4).")
        return
    client = VapiClient(settings)
    print("→ Appel en cours vers ton numéro…")
    call = client.place_call(
        customer_number=settings.user_phone_number,
        assistant_id=settings.vapi_assistant_id,
        phone_number_id=settings.vapi_phone_number_id,
        first_message=(
            "Ceci est un test de Perseus. Si tu entends ce message, "
            "la téléphonie fonctionne parfaitement. Au revoir !"
        ),
        max_duration_s=45,
    )
    print("✅ Appel lancé, id =", call.get("id"))
    ended = client.wait_for_call_end(call["id"], timeout_s=90)
    print("→ Fin d'appel :", ended.get("status"), ended.get("endedReason", ""))


def run_test(name: str, settings: Settings) -> None:
    """Aiguille vers le test demandé (ou tous, l'un après l'autre)."""
    tests = {
        "brain": test_brain,
        "tts": test_tts,
        "stt": test_stt,
        "vision": test_vision,
        "calendar": test_calendar,
        "playwright": test_playwright,
        "phone": test_phone,
    }
    if name == "all":
        for test_name, test_function in tests.items():
            print(f"\n===== TEST {test_name.upper()} =====")
            try:
                test_function(settings)
            except Exception as exc:  # noqa: BLE001 — un échec ne bloque pas les autres
                print(f"❌ {test_name} : {exc}")
        return
    if name not in tests:
        raise SystemExit(f"Test inconnu : {name} (choix : {', '.join(tests)} | all)")
    tests[name](settings)
