"""Google Sheets client for data_connection app."""

import base64
import json
import re
from dataclasses import dataclass

import gspread
import logfire
from constance import config
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from gotta_bike_platform.config import settings


class GSClientError(Exception):
    """Base exception for Google Sheets client errors."""


class GSAuthError(GSClientError):
    """Authentication error."""


class GSSheetError(GSClientError):
    """Sheet operation error."""


class GSSpreadsheetNotFoundError(GSClientError):
    """Spreadsheet not found or inaccessible error."""


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _get_credentials() -> Credentials:
    """Get Google service account credentials.

    Returns:
        Google service account credentials.

    Raises:
        GSAuthError: If credentials are not configured or invalid.

    """
    if not settings.google_credentials_base64:
        raise GSAuthError("Google credentials not configured (GOOGLE_CREDENTIALS_BASE64)")

    try:
        credentials_json = base64.b64decode(settings.google_credentials_base64).decode("utf-8")
        credentials_info = json.loads(credentials_json)
        return Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    except (ValueError, json.JSONDecodeError) as e:
        raise GSAuthError(f"Invalid Google credentials: {e}") from e


def _get_client() -> gspread.Client:
    """Get authenticated gspread client.

    Returns:
        Authenticated gspread client.

    """
    credentials = _get_credentials()
    return gspread.authorize(credentials)


def _extract_spreadsheet_id(url: str) -> str:
    """Extract spreadsheet ID from Google Sheets URL.

    Args:
        url: Google Sheets URL.

    Returns:
        Spreadsheet ID.

    Raises:
        GSSheetError: If URL is invalid.

    """
    # Match patterns like /d/SPREADSHEET_ID/ or /d/SPREADSHEET_ID
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", url)
    if not match:
        raise GSSheetError(f"Invalid Google Sheets URL: {url}")
    return match.group(1)


def create_spreadsheet(title: str, sheet_name: str = "DATA_CONN") -> str:
    """Create a new Google Spreadsheet.

    Args:
        title: Title for the new spreadsheet.
        sheet_name: Name for the first sheet (default: DATA_CONN).

    Returns:
        URL of the created spreadsheet.

    Raises:
        GSSheetError: If creation fails or folder ID is not configured.

    """
    folder_id = config.GOOGLE_DRIVE_FOLDER_ID
    if not folder_id:
        raise GSSheetError("GOOGLE_DRIVE_FOLDER_ID is not configured in settings")

    with logfire.span("gs_create_spreadsheet", title=title, sheet_name=sheet_name, folder_id=folder_id):
        try:
            client = _get_client()
            # Create spreadsheet in the shared folder so it uses the folder owner's quota
            spreadsheet = client.create(title, folder_id=folder_id)

            # Rename the default sheet (Google creates "Sheet1" by default)
            worksheet = spreadsheet.sheet1
            worksheet.update_title(sheet_name)

            logfire.info(f"Created spreadsheet: {title}", spreadsheet_id=spreadsheet.id, folder_id=folder_id)
            return spreadsheet.url

        except gspread.exceptions.APIError as e:
            logfire.error(f"Failed to create spreadsheet: {e}")
            raise GSSheetError(f"Failed to create spreadsheet: {e}") from e


def share_spreadsheet(spreadsheet_url: str, email: str, role: str = "writer", transfer_ownership: bool = False) -> None:
    """Share a spreadsheet with an email address.

    Args:
        spreadsheet_url: URL of the spreadsheet.
        email: Email address to share with.
        role: Permission role (reader, writer, owner). Default: writer.
        transfer_ownership: If True and role is 'owner', transfer ownership to the email.

    Raises:
        GSSheetError: If sharing fails.

    """
    with logfire.span("gs_share_spreadsheet", email=email, role=role, transfer_ownership=transfer_ownership):
        try:
            client = _get_client()
            spreadsheet_id = _extract_spreadsheet_id(spreadsheet_url)
            spreadsheet = client.open_by_key(spreadsheet_id)

            if transfer_ownership and role == "owner":
                # Transfer ownership requires the transferOwnership parameter
                spreadsheet.share(email, perm_type="user", role="owner", notify=True, transferOwnership=True)
                logfire.info(f"Transferred ownership to {email}")
            else:
                spreadsheet.share(email, perm_type="user", role=role)
                logfire.info(f"Shared spreadsheet with {email} as {role}")

        except gspread.exceptions.APIError as e:
            logfire.error(f"Failed to share spreadsheet: {e}")
            raise GSSheetError(f"Failed to share spreadsheet: {e}") from e


def set_headers(spreadsheet_url: str, sheet_name: str, headers: list[str]) -> None:
    """Set header row in a spreadsheet.

    Args:
        spreadsheet_url: URL of the spreadsheet.
        sheet_name: Name of the sheet.
        headers: List of header values.

    Raises:
        GSSheetError: If setting headers fails.

    """
    with logfire.span("gs_set_headers", sheet_name=sheet_name, header_count=len(headers)):
        try:
            client = _get_client()
            spreadsheet_id = _extract_spreadsheet_id(spreadsheet_url)
            spreadsheet = client.open_by_key(spreadsheet_id)

            try:
                worksheet = spreadsheet.worksheet(sheet_name)
            except gspread.exceptions.WorksheetNotFound:
                # Create the worksheet if it doesn't exist
                worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=100, cols=len(headers))

            # Set headers in the first row
            worksheet.update("A1", [headers])
            logfire.info(f"Set {len(headers)} headers in {sheet_name}")

        except gspread.exceptions.APIError as e:
            logfire.error(f"Failed to set headers: {e}")
            raise GSSheetError(f"Failed to set headers: {e}") from e


def clear_and_write_data(
    spreadsheet_url: str,
    sheet_name: str,
    headers: list[str],
    rows: list[list[str]],
) -> int:
    """Clear a sheet and write new data with headers.

    Args:
        spreadsheet_url: URL of the spreadsheet.
        sheet_name: Name of the sheet tab.
        headers: List of header values for the first row.
        rows: List of rows, where each row is a list of cell values.

    Returns:
        Number of rows written (excluding header).

    Raises:
        GSSpreadsheetNotFoundError: If spreadsheet doesn't exist or is inaccessible.
        GSSheetError: If operation fails.

    """
    with logfire.span("gs_clear_and_write", sheet_name=sheet_name, row_count=len(rows)):
        try:
            client = _get_client()
            spreadsheet_id = _extract_spreadsheet_id(spreadsheet_url)
            spreadsheet = client.open_by_key(spreadsheet_id)

            try:
                worksheet = spreadsheet.worksheet(sheet_name)
            except gspread.exceptions.WorksheetNotFound:
                # Create the worksheet if it doesn't exist
                worksheet = spreadsheet.add_worksheet(
                    title=sheet_name,
                    rows=max(len(rows) + 1, 100),
                    cols=len(headers),
                )

            # Clear all existing data
            worksheet.clear()

            # Prepare all data (headers + rows)
            all_data = [headers, *rows]

            # Write all data at once (more efficient than row-by-row)
            if all_data:
                worksheet.update(f"A1:{_col_letter(len(headers))}{len(all_data)}", all_data)

            logfire.info(
                f"Wrote {len(rows)} rows to {sheet_name}",
                header_count=len(headers),
            )
            return len(rows)

        except gspread.exceptions.SpreadsheetNotFound as e:
            logfire.error(f"Spreadsheet not found: {spreadsheet_url}")
            raise GSSpreadsheetNotFoundError(f"Spreadsheet not found or deleted: {e}") from e
        except gspread.exceptions.APIError as e:
            logfire.error(f"Failed to write data: {e}")
            raise GSSheetError(f"Failed to write data: {e}") from e


def _col_letter(col_num: int) -> str:
    """Convert column number to letter (1=A, 26=Z, 27=AA, etc.).

    Args:
        col_num: Column number (1-indexed).

    Returns:
        Column letter(s).

    """
    result = ""
    while col_num > 0:
        col_num, remainder = divmod(col_num - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _get_drive_service():
    """Get authenticated Google Drive API service.

    Returns:
        Google Drive API service object.

    """
    credentials = _get_credentials()
    return build("drive", "v3", credentials=credentials)


def get_spreadsheet_owner(spreadsheet_url: str) -> str | None:
    """Get the owner email of a spreadsheet.

    Args:
        spreadsheet_url: URL of the spreadsheet.

    Returns:
        Owner's email address, or None if unable to determine.

    """
    try:
        drive_service = _get_drive_service()
        spreadsheet_id = _extract_spreadsheet_id(spreadsheet_url)

        # Get file metadata including owners
        file_info = drive_service.files().get(
            fileId=spreadsheet_id,
            fields="owners",
            supportsAllDrives=True,
        ).execute()

        owners = file_info.get("owners", [])
        if owners:
            return owners[0].get("emailAddress")
        return None

    except Exception as e:
        logfire.warning(f"Failed to get spreadsheet owner: {e}")
        return None


@dataclass
class DriveFile:
    """Represents a file in Google Drive."""

    id: str
    name: str
    size: int
    trashed: bool
    mime_type: str


@dataclass
class DriveQuotaInfo:
    """Storage quota information for the service account."""

    limit: int | None  # None if unlimited
    usage: int
    usage_in_drive: int
    usage_in_drive_trash: int
    files: list[DriveFile]

    @property
    def limit_display(self) -> str:
        """Human-readable storage limit."""
        if self.limit is None:
            return "Unlimited"
        return f"{self.limit / (1024**3):.2f} GB"

    @property
    def usage_display(self) -> str:
        """Human-readable storage usage."""
        return f"{self.usage / (1024**3):.2f} GB"

    @property
    def trash_display(self) -> str:
        """Human-readable trash usage."""
        return f"{self.usage_in_drive_trash / (1024**3):.2f} GB"

    @property
    def total_file_count(self) -> int:
        """Total number of files."""
        return len(self.files)

    @property
    def trashed_file_count(self) -> int:
        """Number of trashed files."""
        return sum(1 for f in self.files if f.trashed)


def get_drive_quota_info() -> DriveQuotaInfo:
    """Get storage quota and file list for the service account.

    Returns:
        DriveQuotaInfo with quota details and file list.

    Raises:
        GSClientError: If the API call fails.

    """
    with logfire.span("gs_get_drive_quota"):
        try:
            drive_service = _get_drive_service()

            # Get quota information
            about = drive_service.about().get(fields="storageQuota").execute()
            quota = about.get("storageQuota", {})

            # List all files owned by service account (including trashed)
            files = []
            page_token = None

            while True:
                results = drive_service.files().list(
                    q="'me' in owners",
                    fields="nextPageToken, files(id, name, size, trashed, mimeType)",
                    pageSize=1000,
                    pageToken=page_token,
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                ).execute()

                files.extend(
                    DriveFile(
                        id=f.get("id", ""),
                        name=f.get("name", ""),
                        size=int(f.get("size", 0)),
                        trashed=f.get("trashed", False),
                        mime_type=f.get("mimeType", ""),
                    )
                    for f in results.get("files", [])
                )

                page_token = results.get("nextPageToken")
                if not page_token:
                    break

            # Parse quota - limit may be missing for unlimited accounts
            limit_str = quota.get("limit")
            limit = int(limit_str) if limit_str else None

            info = DriveQuotaInfo(
                limit=limit,
                usage=int(quota.get("usage", 0)),
                usage_in_drive=int(quota.get("usageInDrive", 0)),
                usage_in_drive_trash=int(quota.get("usageInDriveTrash", 0)),
                files=files,
            )

            logfire.info(
                f"Drive quota: {info.usage_display} used, {info.total_file_count} files",
                limit=info.limit_display,
                trashed_files=info.trashed_file_count,
            )

            return info

        except Exception as e:
            logfire.error(f"Failed to get drive quota: {e}")
            raise GSClientError(f"Failed to get drive quota: {e}") from e


def empty_trash() -> int:
    """Empty the service account's Drive trash.

    Returns:
        Number of files that were in trash.

    Raises:
        GSClientError: If the API call fails.

    """
    with logfire.span("gs_empty_trash"):
        try:
            drive_service = _get_drive_service()

            # First count trashed files
            info = get_drive_quota_info()
            trashed_count = info.trashed_file_count

            if trashed_count > 0:
                drive_service.files().emptyTrash().execute()
                logfire.info(f"Emptied trash: {trashed_count} files deleted")

            return trashed_count

        except Exception as e:
            logfire.error(f"Failed to empty trash: {e}")
            raise GSClientError(f"Failed to empty trash: {e}") from e


def delete_file(file_id: str) -> None:
    """Permanently delete a file from Drive.

    Args:
        file_id: The ID of the file to delete.

    Raises:
        GSClientError: If the API call fails.

    """
    with logfire.span("gs_delete_file", file_id=file_id):
        try:
            drive_service = _get_drive_service()
            drive_service.files().delete(fileId=file_id).execute()
            logfire.info(f"Deleted file: {file_id}")

        except Exception as e:
            logfire.error(f"Failed to delete file: {e}")
            raise GSClientError(f"Failed to delete file: {e}") from e
