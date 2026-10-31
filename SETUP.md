# Perseus — Guide d'installation complet (Windows 11)

> Assistant personnel local façon J.A.R.V.I.S : voix locale (Whisper + Piper),
> cerveau OpenRouter, vision d'écran, contrôle du PC, portails scolaires,
> Google Workspace et téléphonie bidirectionnelle (Twilio + Vapi.ai).

---

## 0. Architecture en un coup d'œil

```
                          ┌─────────────────────── TON PC WINDOWS 11 ────────────────────────┐
                          │                                                                  │
   🎙️ Micro ──► Détection « Perseus » ──► ORCHESTRATEUR ◄────► 🧠 Cerveau (OpenRouter LLM)
                          │   (Whisper local)                     │                ⇄ 👁️ Vision écran (mss + modèle vision)
                          │                                       │                ⇄ 🖱️ Contrôle PC (PyAutoGUI)
                          │                                       │                ⇄ 🏫 Playwright (Pronote/EduConnect)
                          │                                       │                ⇄ 📅 Google Calendar / Docs / Drive
                          │   ⚠️ Action critique ? ──► Barrière d'autorisation (core/gate.py)
                          │                                       │
   🔊 Haut-parleurs ◄──── TTS (Piper/ElevenLabs) ◄────────────────┘
                          │
                          │   Serveur webhook local (FastAPI, port 8629)
                          └──────────────┬───────────────────────────────────
                                         │ tunnel public (cloudflared)
                                         ▼
                              Vapi.ai (agent vocal cloud) ◄──► numéro Twilio ◄──► 📱 Ton portable
```

**Flux « autorisation critique »** : Perseus veut exécuter une commande
sensible → il t'appelle → l'agent Vapi te demande « oui ou non ? » → ta
réponse verbale transite par le webhook local (`enregistrer_permission`) →
l'action est exécutée ou refusée. Chaque décision est tracée dans `data/audit.log`.

**Flux « appel entrant »** : tu appelles le numéro Twilio → Vapi répond avec
la voix de Perseus → l'agent interroge ton PC via le webhook (`obtenir_agenda`,
`obtenir_devoirs`…) → dialogue en temps réel.

---

## 1. Prérequis

| Élément | Détail |
|---|---|
| Python **3.11** (recommandé) | Télécharge-le sur [python.org/downloads](https://www.python.org/downloads/) (⚠️ **pas** la version du Microsoft Store). À l'installation, coche **« Add python.exe to PATH »**. **3.11, 3.12, 3.13 et 3.14 fonctionnent** : tous les moteurs vocaux (Piper ≥ 1.3, Edge-TTS, SAPI) sont compatibles. |
| RAM | 8 Go minimum (16 Go recommandé pour Whisper `small`) |
| GPU | Optionnel : NVIDIA + CUDA accélère fortement Whisper (sinon CPU int8 fonctionne) |
| Micro + enceintes/casque | Le micro par défaut de Windows est utilisé |
| Chrome ou Edge | Pour le OAuth Google et les tests |
| Espace disque | ~5 Go (navigateur Playwright + modèles Whisper/voix) |

Vérifie dans PowerShell :

```powershell
python --version    # doit afficher Python 3.11.x
```

---

## 2. Installation du projet

```powershell
# 1. Va dans le dossier du projet
cd "C:\Users\eliot\Documents\Default Project\Perseus"

# 2. Crée et active l'environnement virtuel (`venv` ; `.venv` marche aussi).
#    Python 3.11 à 3.14 : tout est compatible.
#    ⚠️ Lance TOUJOURS pip DEPUIS le venv activé, sinon le Python système
#       est utilisé et les paquets vont au mauvais endroit.
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
# → si PowerShell refuse : Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 3. Installe les dépendances
python -m pip install --upgrade pip
pip install -r requirements.txt

# 4. Installe le navigateur Chromium de Playwright
python -m playwright install chromium

# 5. Crée ton fichier de configuration
copy .env.example .env
```

---

## 3. Génération de toutes les clés API

### 3.1 OpenRouter (le cerveau)

1. Va sur **https://openrouter.ai** → crée un compte (Google/Email).
2. Va dans **https://openrouter.ai/settings/keys** → **Create Key**.
3. Nomme-la `perseus`, copie la clé (`sk-or-v1-…`) → colle-la dans `.env`
   sous `OPENROUTER_API_KEY`.
4. (Conseillé) Ajoute **5 $ de crédit** dans *Credits* : les modèles gratuits
   (`*:free`) sont limités en débit et n'autorisent parfois pas le
   function-calling. `openai/gpt-4o-mini` coûte ~0,15 $/million de tokens
   d'entrée : un mois d'usage normal coûte quelques centimes.

Modèles recommandés dans `.env` :
- `LLM_MODEL=openai/gpt-4o-mini` (fiable pour le function-calling)
- `VISION_MODEL=openai/gpt-4o-mini` (accepte les images)

### 3.2 Google Cloud (Calendar, Docs, Drive)

1. **https://console.cloud.google.com** → crée un projet nommé `perseus`.
2. Menu **APIs & Services → Library** → active ces trois API :
   - *Google Calendar API*
   - *Google Drive API*
   - *Google Docs API*
3. **APIs & Services → OAuth consent screen** :
   - Type **External** → nom de l'app `Perseus` → ton email de support.
   - Section **Test users** : **ajoute ton adresse Gmail** (indispensable,
     sinon Google refusera la connexion avec `access_denied`).
   - Ajoute les scopes des trois API si le formulaire le demande.
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID** :
   - Type d'application : **Desktop app**.
   - **Download JSON** → renomme le fichier en `credentials.json`
     → place-le **à la racine du projet** (à côté de `main.py`).

Au premier test Calendar, un navigateur s'ouvre pour consentir ; le jeton est
ensuite mis en cache dans `token.json` (rafraîchissement automatique).

### 3.3 Twilio (le numéro de téléphone)

1. **https://www.twilio.com** → crée un compte (essai gratuit avec crédit).
2. Console → copie **Account SID** et **Auth Token** → dans `.env`
   (`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`).
3. **Buy a number** → coche **Voice** (SMS optionnel) → achète.
   ⚠️ Les numéros **français (+33)** exigent un dossier réglementaire
   (pièce d'identité, adresse) validé par Twilio — compte 1 à 5 jours.
   Pour tester **rapidement**, achète un numéro **US** (~1 $/mois) : il
   appelle parfaitement un mobile français.
4. Note le numéro au format E.164 (ex. `+15551234567`) → `TWILIO_PHONE_NUMBER`.

### 3.4 Vapi.ai (l'agent vocal du téléphone)

1. **https://dashboard.vapi.ai** → crée un compte → icône engrenage →
   **Private Key** → copie-la → `VAPI_API_KEY`.
2. **Importe ton numéro Twilio** : menu *Phone Numbers* → **Create Phone
   Number** → provider **Twilio** → colle SID/AuthToken → sélectionne ton
   numéro. Copie l'**ID du numéro** (colonne *Id*) → `VAPI_PHONE_NUMBER_ID`.
3. **Crée l'assistant** : menu *Assistants* → **Create** → nom `Perseus-Phone` :
   - **Model** : provider **OpenRouter** → colle ta clé OpenRouter →
     modèle = celui de `LLM_MODEL` → temperature 0.6.
   - **System Prompt** (copie-colle, en remplaçant `{USER_NAME}`) :

     ```text
     Tu es Perseus, l'assistant personnel de {USER_NAME}. Tu l'appelles
     depuis son PC. Deux cas possibles :
     1) Si on te donne une variable « action » : tu demandes poliment et
        brièvement l'autorisation pour cette action précise. Tu exiges une
        réponse claire (oui ou non), puis tu appelles l'outil
        enregistrer_permission avec permission_id={{permission_id}} et
        decision (oui ou non). Ensuite tu racroches.
     2) Sinon (appel entrant ou rappel) : tu annonces le premier message,
        puis tu réponds aux questions en utilisant les outils disponibles
        (obtenir_agenda, obtenir_devoirs, obtenir_notes, dire_heure).
     Réponds toujours en français. Sois très bref, naturel et vocal.
     ```
   - **First Message Mode** : *assistant-speaks-first*.
   - **Voice** : une voix française (ex. 11labs `Nicl`/`Rachel` ou Azure
     `fr-FR-DeniseNeural`). **Transcriber** : *Deepgram nova-2* language `fr`
     (ou *multilingual*).
   - **Outil serveur** (onglet *Tools* du modèle → *Add Tool → Function*) :
     - Name : `enregistrer_permission`
     - Description : `Enregistre la réponse de l'utilisateur (oui/non) à une demande d'autorisation.`
     - Parameters : `permission_id` (string, required), `decision` (string, required — valeurs oui/non)
     - **Server URL** : `https://TON-TUNNEL.cloudflared.workers.dev/vapi/webhook`
       (voir §4 — à remplir après avoir lancé le tunnel)
     - *Server Secret* : une chaîne aléatoire → reporte-la dans `.env`
       (`WEBHOOK_SECRET`). Vapi l'enverra dans l'en-tête `x-vapi-secret`.
   - Copie l'**Assistant ID** → `VAPI_ASSISTANT_ID`.
4. **Appels entrants** : dans *Phone Numbers*, associe le numéro à
   l'assistant `Perseus-Phone` (*Assistant* → *Inbound* = `Perseus-Phone`).
5. Dans `.env` : `TELEPHONY_ENABLED=true`, `USER_PHONE_NUMBER=+336…`.

### 3.5 ElevenLabs (TTS premium — optionnel)

**https://elevenlabs.io** → Profile → **API Keys** → copie → `ELEVENLABS_API_KEY`.
Choisis une voix dans *Voices* et copie son ID → `ELEVENLABS_VOICE_ID`.
Puis `TTS_PROVIDER=elevenlabs`. (Par défaut, Piper 100 % local suffit.)

### 3.6 Cloudflare tunnel (pour joindre ton PC depuis Vapi)

```powershell
# Installation
winget install Cloudflare.cloudflared

# Lancement du tunnel (laisse la fenêtre ouverte pendant la session)
cloudflared tunnel --url http://localhost:8629
```

Copie l'URL affichée (`https://xxxx.trycloudflare.com`) → dans `.env` :

```
PUBLIC_WEBHOOK_URL=https://xxxx.trycloudflare.com
```

⚠️ L'URL change à chaque redémarrage du tunnel « quick » : pense à la
reporter aussi dans l'**Server URL de l'outil** Vapi (§3.4) et dans le
*Server URL* global de l'assistant si tu en configures un. Pour une URL
fixe : `cloudflared tunnel create perseus` (compte gratuit Cloudflare) ou ngrok.

### 3.7 Portails scolaires (EduConnect / Pronote)

Dans `.env` :

```
PRONOTE_URL=https://XXXXXXX.index-education.net/pronote/eleve.html
EDUCONNECT_URL=https://educonnect.education.gouv.fr/...
PORTAL_USERNAME=ton.identifiant
PORTAL_PASSWORD=ton.mot.de.passe
```

- **PRONOTE_URL** : l'URL complète de ton établissement (fournie par le lycée).
- Si ton école passe par le SSO ENT → laisse Pronote gérer la redirection
  (Perseus clique automatiquement sur « EduConnect »).
- `AUTOMATION_HEADLESS=false` (recommandé) : de nombreux portails détectent
  et bloquent les navigateurs headless, et tu peux voir la première connexion.
- Le **profil navigateur est persistant** (`data/browser_profile`) : après la
  première connexion, la session reste active d'un jour à l'autre.

---

## 4. Remplir le `.env` — récapitulatif

```ini
OPENROUTER_API_KEY=sk-or-v1-...        # §3.1  OBLIGATOIRE
LLM_MODEL=openai/gpt-4o-mini
VISION_MODEL=openai/gpt-4o-mini
STT_MODEL_SIZE=small                   # base si PC modeste
TTS_PROVIDER=piper
PRONOTE_URL=...                        # §3.7
PORTAL_USERNAME=... / PORTAL_PASSWORD=...
GOOGLE_CREDENTIALS_FILE=credentials.json   # §3.2
TELEPHONY_ENABLED=true                 # quand §3.3/§3.4/§3.6 sont faits
TWILIO_ACCOUNT_SID=... / TWILIO_AUTH_TOKEN=... / TWILIO_PHONE_NUMBER=+1...
VAPI_API_KEY=... / VAPI_ASSISTANT_ID=... / VAPI_PHONE_NUMBER_ID=...
USER_PHONE_NUMBER=+336...
PUBLIC_WEBHOOK_URL=https://xxxx.trycloudflare.com
WEBHOOK_SECRET=...
```

🔒 **Sécurité** : `.env`, `credentials.json`, `token.json` sont dans
`.gitignore`. Ne les partage jamais. Les identifiants du portail restent en
local sur ta machine uniquement.

---

## 5. Tester chaque module, un par un

Dans le venv activé :

```powershell
python main.py --test brain        # 1. Le cerveau répond-il ?
python main.py --test tts          # 2. Perseus parle-t-il français ? (auto : Piper → Edge-TTS → voix Windows)
python main.py --test stt          # 3. Perseus comprend-il ta voix ?
python main.py --test vision       # 4. Perseus "voit"-il ton écran ?
python main.py --test calendar     # 5. Agenda Google (ouvre le navigateur au 1er lancement)
python main.py --test playwright   # 6. Accès au portail scolaire (dumps dans data/debug)
python main.py --test phone        # 7. Perseus appelle-t-il ton portable ? (décroche !)
python main.py --test all          # tout enchaîner
```

Ensuite :

```powershell
python main.py --cli       # mode texte : tape tes demandes
python main.py             # 🚀 assistant vocal complet (mot d'éveil)
```

Exemples de commandes vocales :

- « **Perseus**, quels sont mes devoirs pour demain ? »
- « **Perseus**, qu'est-ce qui s'affiche sur mon écran ? »
- « **Perseus**, qu'est-ce que j'ai dans mon agenda cette semaine ? »
- « **Perseus**, ajoute un contrôle de maths vendredi à 14 heures »
- « **Perseus**, ouvre Chrome et va sur YouTube »
- « **Perseus**, rappelle-moi au téléphone de sortir le lave-vaisselle »

---

## 6. Calibrer la connexion au portail (si besoin)

1. Lance `python main.py --test playwright`.
2. Ouvre les fichiers `data/debug/test_playwright_*.png/.txt` : tu vois ce
   que Perseus voit.
3. Si le formulaire n'est pas détecté, ouvre la page dans Chrome → clic droit
   sur le champ identifiant → *Inspecter* → copie son sélecteur → mets-le
   dans `.env` (`PORTAL_USER_SELECTOR="#mon-champ"`, idem mot de passe/bouton).
4. Relance. Les dumps `pronote_homework_*.txt` montrent le texte brut que le
   LLM recevra — s'il est vide, c'est que le menu n'a pas été trouvé.

---

## 7. Dépannage

| Symptôme | Cause / solution |
|---|---|
| `No matching distribution found` pour `piper-tts==1.2.0` / `piper-phonemize` | Deux causes : (1) le `pip` utilisé n'était pas celui du venv → active-le d'abord (`.\venv\Scripts\Activate.ps1`) ; (2) `requirements.txt` trop ancien — Piper 1.2.0 dépendait de `piper-phonemize`, sans wheel Windows. Le projet utilise désormais **`piper-tts>=1.3`** : `pip install -r requirements.txt` suffit. |
| Edge-TTS échoue (pas d'Internet / proxy) | Force la voix locale de Windows : `TTS_PROVIDER=sapi` dans `.env` (fonctionne hors ligne, zéro dépendance). |
| `PortAudioError` au démarrage | Le micro est occupé (Discord/Zoom) ou périphérique par défaut erroné → mets `AUDIO_INPUT_DEVICE` avec le nom exact du micro (`python -c "import sounddevice; print(sounddevice.query_devices())"`). |
| Whisper très lent | Passe `STT_MODEL_SIZE=base` (ou `tiny`). GPU : installe CUDA/cuDNN (voir README) ou laisse `int8` CPU. |
| Aucune parole détectée | Baisse `WAKE_ENERGY_THRESHOLD` (ex. 400). Augmente-le si Perseus s'auto-déclenche avec les haut-parleurs. |
| OpenRouter 401 | Clé invalide. 402 : crédits épuisés → passe sur un modèle `:free` ou recharge. |
| Google `access_denied` | Ton compte n'est pas dans les **Test users** de l'écran de consentement (§3.2.3). |
| Google `redirect_uri_mismatch` | Le client OAuth doit être de type **Desktop app** (pas Web). |
| Playwright timeout | Le portail est lent ou détecte l'automatisation → garde `AUTOMATION_HEADLESS=false`, relance (le profil persistant finit par être accepté). |
| Vapi « server URL unreachable » | Tunnel coupé → relance `cloudflared`, mets à jour `PUBLIC_WEBHOOK_URL` et l'URL serveur de l'outil dans Vapi. |
| L'appel ne donne aucune autorisation | Vérifie le secret (`WEBHOOK_SECRET` == *Server Secret* Vapi) et que l'outil Vapi pointe vers `/vapi/webhook`. |
| Kokoro refuses | `pip install kokoro torch` + installe **espeak-ng** (installateur Windows sur GitHub espeak-ng). Sinon reste sur Piper. |
| PyAutoGUI lève `FailSafeException` | Ta souris est dans un coin de l'écran (arrêt d'urgence) → éloigne-la. |
| Pare-feu Windows | Autorise Python sur le réseau privé (le webhook écoute sur localhost). |

---

## 8. Bonnes pratiques & limites

- **Tunnel public** : n'importe qui connaissant l'URL pourrait parler à ton
  webhook → **défins toujours `WEBHOOK_SECRET`** et change l'URL régulièrement.
- **Actions critiques** : la barrière téléphonique est un filet, pas une
  garantie absolue. Garde `ALLOW_SHELL=false` tant que tu n'en as pas besoin.
- **Portail scolaire** : l'automatisation doit rester un usage personnel ;
  vérifie les conditions d'utilisation de ton ENT. Les sélecteurs peuvent
  changer à chaque rentrée — les dumps de `data/debug` te permettent de
  recalibrer en 2 minutes.
- **Confidentialité** : tout ce qui tourne en local (Whisper, Piper, historique)
  ne quitte ta machine ; seules les demandes LLM (OpenRouter), les appels
  (Twilio/Vapi) et les captures d'écran analysées transitent par le cloud.

## 9. Aller plus loin

- Remplacer le réveil Whisper par openWakeWord (modèle custom « Perseus »
  entraînable sur Google Colab) pour diviser la consommation CPU au repos.
- Streaming STT temps réel (faster-whisper en mode chunked).
- SMS via Twilio (rappels écrits) — le SDK `twilio` est déjà en commentaire
  dans `requirements.txt`.
- Mémoire long-terme (base SQLite des préférences de l'utilisateur).
