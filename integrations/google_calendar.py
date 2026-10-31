"""Module Google Calendar : lecture, création, modification d'événements."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config.settings import Settings
from integrations.google_auth import get_service
from utils.logger import get_logger

log = get_logger("calendar")

# Libellés français (indépendants des paramètres régionaux de Windows)
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]


def fmt_date(moment: datetime) -> str:
    """Formate une date/heure en français lisible (« mardi 3 novembre à 14h05 »)."""
    return f"{JOURS[moment.weekday()]} {moment.day} {MOIS[moment.month]} à {moment.hour:02d}h{moment.minute:02d}"


class GoogleCalendar:
    """Opérations sur l'agenda Google de l'utilisateur."""

    def __init__(self, settings: Settings) -> None:
        """Stocke la configuration ; le client API est créé au premier accès."""
        self.settings = settings
        self._service = None
        self.tz = ZoneInfo(settings.google_timezone)

    @property
    def service(self):
        """Client API Calendar (créé paresseusement)."""
        if self._service is None:
            self._service = get_service(self.settings, "calendar", "v3")
        return self._service

    # ------------------------------------------------------------------ #
    # Lecture                                                            #
    # ------------------------------------------------------------------ #
    def list_events(self, days_ahead: int = 7, query: str = "") -> list[dict]:
        """Liste les événements à venir (filtre facultatif par mots-clés)."""
        now = datetime.now(self.tz)
        arguments: dict = {
            "calendarId": self.settings.google_calendar_id,
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": 50,
        }
        if query:
            arguments["q"] = query
        else:
            arguments["timeMin"] = now.isoformat()
            arguments["timeMax"] = (now + timedelta(days=days_ahead)).isoformat()
        result = self.service.events().list(**arguments).execute()
        return result.get("items", [])

    def describe_upcoming(self, days_ahead: int = 7, query: str = "") -> str:
        """Renvoie une description française prête à être lue à voix haute."""
        events = self.list_events(days_ahead=days_ahead, query=query)
        if not events:
            return "Aucun événement prévu."
        lines = []
        for event in events:
            start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date", "")
            if not start:
                continue
            try:
                moment = datetime.fromisoformat(start)
            except ValueError:
                moment = datetime.fromisoformat(start + "T00:00:00")  # événement « journée »
            title = event.get("summary") or "(sans titre)"
            location = f" ({event['location']})" if event.get("location") else ""
            lines.append(f"• {fmt_date(moment)} — {title}{location}")
        return "\n".join(lines)

    def find_event(self, query: str) -> dict | None:
        """Recherche le premier événement futur correspondant à `query`."""
        events = self.list_events(days_ahead=60, query=query)
        return events[0] if events else None

    # ------------------------------------------------------------------ #
    # Écriture                                                           #
    # ------------------------------------------------------------------ #
    def create_event(self, title: str, start_iso: str, end_iso: str = "",
                     duration_min: int = 60, description: str = "",
                     location: str = "") -> str:
        """Crée un événement et renvoie une confirmation en français."""
        start = self._parse_dt(start_iso)
        end = self._parse_dt(end_iso) if end_iso else start + timedelta(minutes=int(duration_min))
        event = {
            "summary": title,
            "description": description or None,
            "location": location or None,
            "start": {"dateTime": start.isoformat(), "timeZone": self.settings.google_timezone},
            "end": {"dateTime": end.isoformat(), "timeZone": self.settings.google_timezone},
        }
        created = self.service.events().insert(
            calendarId=self.settings.google_calendar_id, body=event,
        ).execute()
        log.info("Événement créé : %s", created.get("id"))
        return f"Événement « {title} » créé le {fmt_date(start)}."

    def update_event(self, query: str, new_title: str = "", new_start_iso: str = "",
                     new_end_iso: str = "", new_description: str = "") -> str:
        """Modifie le premier événement futur correspondant à la recherche."""
        event = self.find_event(query)
        if not event:
            return f"Aucun événement trouvé pour « {query} »."
        patch: dict = {}
        if new_title:
            patch["summary"] = new_title
        if new_description:
            patch["description"] = new_description
        if new_start_iso:
            start = self._parse_dt(new_start_iso)
            end = self._parse_dt(new_end_iso) if new_end_iso else start + timedelta(minutes=60)
            patch["start"] = {"dateTime": start.isoformat(), "timeZone": self.settings.google_timezone}
            patch["end"] = {"dateTime": end.isoformat(), "timeZone": self.settings.google_timezone}
        self.service.events().patch(
            calendarId=self.settings.google_calendar_id, eventId=event["id"], body=patch,
        ).execute()
        return f"Événement « {event.get('summary')} » modifié."

    def delete_event(self, query: str) -> str:
        """Supprime le premier événement correspondant (action critique)."""
        event = self.find_event(query)
        if not event:
            return f"Aucun événement trouvé pour « {query} »."
        self.service.events().delete(
            calendarId=self.settings.google_calendar_id, eventId=event["id"],
        ).execute()
        return f"Événement « {event.get('summary')} » supprimé."

    def _parse_dt(self, value: str) -> datetime:
        """Convertit une chaîne ISO en datetime conscient du fuseau local."""
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=self.tz)
        return moment.astimezone(self.tz)
