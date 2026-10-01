"""Read-only Google Drive access through a service account."""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
_FOLDER_MIME = "application/vnd.google-apps.folder"
_RETRIES = 5


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    mime_type: str
    modified_time: datetime  # timezone-aware (UTC)

    @property
    def is_folder(self) -> bool:
        return self.mime_type == _FOLDER_MIME


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class DriveClient:
    def __init__(self, service_account_file: str) -> None:
        credentials = service_account.Credentials.from_service_account_file(
            service_account_file, scopes=_SCOPES
        )
        self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)

    def list_children(self, folder_id: str) -> list[DriveFile]:
        files: list[DriveFile] = []
        token: Optional[str] = None
        while True:
            response = (
                self._service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed=false",
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
                    pageSize=1000,
                    pageToken=token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute(num_retries=_RETRIES)
            )
            files.extend(
                DriveFile(f["id"], f["name"], f["mimeType"], _parse_time(f["modifiedTime"]))
                for f in response.get("files", [])
            )
            token = response.get("nextPageToken")
            if not token:
                return files

    def find_child(self, folder_id: str, name: str) -> Optional[DriveFile]:
        """Match by stripped name — some folder names carry a trailing space."""
        for child in self.list_children(folder_id):
            if child.name.strip() == name:
                return child
        return None

    def download(self, file_id: str) -> bytes:
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(
            buffer, self._service.files().get_media(fileId=file_id, supportsAllDrives=True)
        )
        done = False
        while not done:
            _, done = downloader.next_chunk(num_retries=_RETRIES)
        return buffer.getvalue()
