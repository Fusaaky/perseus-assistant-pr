"""Module Google Workspace : lecture de Google Docs et recherche Drive."""

from __future__ import annotations

from config.settings import Settings
from integrations.google_auth import get_service
from utils.logger import get_logger

log = get_logger("workspace")


class GoogleWorkspace:
    """Opérations sur Google Docs et Google Drive."""

    def __init__(self, settings: Settings) -> None:
        """Stocke la configuration ; les clients API sont créés au premier accès."""
        self.settings = settings
        self._drive = None
        self._docs = None

    @property
    def drive(self):
        """Client API Drive (créé paresseusement)."""
        if self._drive is None:
            self._drive = get_service(self.settings, "drive", "v3")
        return self._drive

    @property
    def docs(self):
        """Client API Docs (créé paresseusement)."""
        if self._docs is None:
            self._docs = get_service(self.settings, "docs", "v1")
        return self._docs

    # ------------------------------------------------------------------ #
    # Drive                                                              #
    # ------------------------------------------------------------------ #
    def search_files(self, query: str, limit: int = 8) -> str:
        """Recherche des fichiers par nom dans Drive (résultats en texte)."""
        safe = query.replace("'", " ")
        q = f"name contains '{safe}' and trashed = false" if safe else "trashed = false"
        result = self.drive.files().list(
            q=q, pageSize=limit, orderBy="modifiedTime desc",
            fields="files(id,name,mimeType,modifiedTime)",
        ).execute()
        files = result.get("files", [])
        if not files:
            return f"Aucun fichier trouvé pour « {query} »."
        return "\n".join(
            f"• {f['name']} (id: {f['id']}, type: {f['mimeType']}, modifié: {f['modifiedTime']})"
            for f in files
        )

    def find_doc_id(self, name: str) -> str | None:
        """Retrouve l'identifiant d'un Google Doc à partir de son titre."""
        safe = name.replace("'", " ")
        q = (
            f"name contains '{safe}' and "
            "mimeType = 'application/vnd.google-apps.document' and trashed = false"
        )
        result = self.drive.files().list(q=q, pageSize=1, fields="files(id,name)").execute()
        files = result.get("files", [])
        return files[0]["id"] if files else None

    def download_file(self, query: str) -> str:
        """Télécharge un fichier Drive dans data/downloads et renvoie le chemin."""
        import io

        from googleapiclient.http import MediaIoBaseDownload

        safe = query.replace("'", " ")
        result = self.drive.files().list(
            q=f"name contains '{safe}' and trashed = false",
            pageSize=1, fields="files(id,name,mimeType)",
        ).execute()
        files = result.get("files", [])
        if not files:
            return f"Fichier « {query} » introuvable."
        file = files[0]
        mime = file["mimeType"]
        destination = self.settings.downloads_dir / file["name"].replace("/", "-")

        if mime.startswith("application/vnd.google-apps"):
            # Fichier Google natif → export (PDF pour les docs, texte sinon)
            is_document = "document" in mime
            export_mime = "application/pdf" if is_document else "text/plain"
            request = self.drive.files().export_media(fileId=file["id"], mimeType=export_mime)
            destination = destination.with_suffix(".pdf" if is_document else ".txt")
        else:
            request = self.drive.files().get_media(fileId=file["id"])

        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _status, done = downloader.next_chunk()
        destination.write_bytes(buffer.getvalue())
        return f"Fichier téléchargé : {destination}"

    # ------------------------------------------------------------------ #
    # Google Docs                                                        #
    # ------------------------------------------------------------------ #
    def read_doc(self, name: str) -> str:
        """Lit le contenu texte d'un Google Doc (par titre ou identifiant)."""
        # Un identifiant Docs fait > 20 caractères et contient des tirets.
        doc_id = name if (len(name) > 20 and "-" in name) else self.find_doc_id(name)
        if not doc_id:
            return f"Document « {name} » introuvable dans Drive."
        document = self.docs.documents().get(documentId=doc_id).execute()
        parts: list[str] = []
        for element in document.get("body", {}).get("content", []):
            paragraph = element.get("paragraph")
            if not paragraph:
                continue
            for item in paragraph.get("elements", []):
                text = item.get("textRun", {}).get("content", "")
                if text:
                    parts.append(text)
        content = "".join(parts).strip()
        return content[:6000] if content else "(document vide)"
