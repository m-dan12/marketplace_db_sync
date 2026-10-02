"""Read-only Google Sheets access through the same service account as Drive.
The sheets are shared with the account; the Sheets API has to be enabled in
its project."""
from __future__ import annotations

from typing import Any, Optional, Protocol

from google.oauth2 import service_account
from googleapiclient.discovery import build

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
_RETRIES = 5


class SheetReader(Protocol):
    def read(self, spreadsheet_id: str, sheet_title: str, cell_range: Optional[str] = None) -> list[list[Any]]: ...


class SheetsClient:
    def __init__(self, service_account_file: str) -> None:
        credentials = service_account.Credentials.from_service_account_file(
            service_account_file, scopes=_SCOPES
        )
        self._service = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    def read(self, spreadsheet_id: str, sheet_title: str, cell_range: Optional[str] = None) -> list[list[Any]]:
        """Raw cell values (numbers stay numbers, dates come as typed text).
        Trailing empty cells of a row are not returned by the API."""
        target = f"'{sheet_title}'" + (f"!{cell_range}" if cell_range else "")
        response = (
            self._service.spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=target,
                valueRenderOption="UNFORMATTED_VALUE",
                dateTimeRenderOption="FORMATTED_STRING",
            )
            .execute(num_retries=_RETRIES)
        )
        return response.get("values", [])
