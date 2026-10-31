# Perseus 🛡️

Assistant personnel local façon J.A.R.V.I.S pour Windows 11 :
voix locale (Whisper + Piper/Edge-TTS/SAPI), cerveau **OpenRouter**, vision d'écran,
contrôle du PC, portails scolaires (EduConnect/Pronote), Google Workspace et
téléphonie bidirectionnelle (Twilio + Vapi.ai) avec autorisation vocale des
actions critiques.

## Démarrage rapide

```powershell
cd Perseus
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env      # puis remplis tes clés (voir SETUP.md)
python main.py --test all   # diagnostics module par module
python main.py              # 🚀 « Perseus ! »
```

📖 **Guide complet** (clés API, Twilio, Vapi, tunnel, dépannage) : [`SETUP.md`](SETUP.md)

## Structure du projet

```
Perseus/
├── main.py                     # Point d'entrée : boucle vocale, CLI, tests
├── diagnostics.py              # Tests unitaires par module (--test)
├── config/settings.py          # Configuration (.env)
├── core/
│   ├── brain.py                # Cerveau LLM (OpenRouter, function-calling, vision)
│   ├── orchestrator.py         # Boucle d'agent : LLM ⇄ outils
│   ├── gate.py                 # Barrière d'autorisation humaine (appel téléphonique)
│   └── state.py                # État partagé thread-safe
├── voice/
│   ├── audio.py                # Micro (VAD énergie), lecture WAV, bips
│   ├── stt.py                  # Whisper local (faster-whisper)
│   ├── tts.py                  # Piper / ElevenLabs / Kokoro (français)
│   └── wake_word.py            # Détection « Perseus » + capture de commande
├── vision/
│   ├── screen.py               # Capture d'écran + analyse par modèle vision
│   └── pc_control.py           # PyAutoGUI : clavier, souris, apps, commandes
├── automation/
│   ├── browser.py              # Playwright persistant (thread dédié)
│   ├── educonnect.py           # Connexion EduConnect (SSO)
│   └── pronote.py              # Devoirs & notes Pronote
├── integrations/
│   ├── google_auth.py          # OAuth2 Google
│   ├── google_calendar.py      # Agenda : lire/créer/modifier/supprimer
│   └── google_workspace.py     # Docs & Drive
├── telephony/
│   ├── vapi.py                 # Appels sortants (API REST Vapi)
│   └── webhook_server.py       # FastAPI : outils serveur + autorisations
├── tools/registry.py           # Registre des outils du LLM
└── SETUP.md                    # Guide d'installation pas à pas
```

## Sécurité

- Toutes les actions critiques (commandes système, suppressions) passent par
  `core/gate.py` : **Perseus t'appelle** et attend ton « oui » verbal.
- Les secrets restent dans `.env` (jamais versionné) ; journal d'audit dans
  `data/audit.log` ; tout le traitement vocal est local par défaut.
