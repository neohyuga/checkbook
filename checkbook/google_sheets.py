"""Connecting to the checkbook Google Sheet with a service account ("robot" Google identity).

How it works: you create an ordinary Google Sheet in your own Google account and
share it (as Editor) with the service account's email address.  The app reads
and writes *only* spreadsheets that have been shared with that address, using
its key file (service_account.json).  There is no browser sign-in, no consent
screen, and nothing expires.

Keep the key file private: anyone who has it can edit the shared spreadsheet.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from .config import app_dir, config_dir
from .store import TABS, SheetsBackend, Store

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
KEY_NAME = "service_account.json"


class NoKeyFile(Exception):
    pass


def key_path() -> Path:
    for p in (config_dir() / KEY_NAME, app_dir() / KEY_NAME):
        if p.exists():
            return p
    raise NoKeyFile(f"The key file '{KEY_NAME}' was not found.")


def has_key() -> bool:
    try:
        key_path()
        return True
    except NoKeyFile:
        return False


def install_key(src: str) -> str:
    """Check that ``src`` is a service-account key file and copy it into the settings folder.

    Returns the robot's email address.
    """
    try:
        with open(src, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        raise ValueError("That file could not be read as a Google key file.") from None
    if data.get("type") != "service_account" or not data.get("client_email") \
            or not data.get("private_key"):
        raise ValueError("That is not a service-account key file.\n\n"
                         "In Google Cloud, open the service account, choose Keys > Add key > "
                         "Create new key > JSON, and pick the file that downloads.")
    dest = config_dir() / KEY_NAME
    shutil.copyfile(src, dest)
    try:
        dest.chmod(0o600)
    except OSError:
        pass
    return data["client_email"]


def robot_email() -> str:
    with open(key_path(), encoding="utf-8") as f:
        return json.load(f).get("client_email", "")


def get_credentials():
    from google.oauth2 import service_account
    return service_account.Credentials.from_service_account_file(str(key_path()), scopes=SCOPES)


def _sheets(creds):
    from googleapiclient.discovery import build
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]{20,})")


def extract_sheet_id(text: str) -> str:
    """Accept a full Google Sheets link or a bare spreadsheet id."""
    text = text.strip()
    m = _ID_RE.search(text)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{20,}", text):
        return text
    raise ValueError("That doesn't look like a Google Sheets link.\n\n"
                     "Open the spreadsheet in your browser and copy the whole address, "
                     "which starts with https://docs.google.com/spreadsheets/d/")


def prepare_sheet(creds, sid: str) -> str:
    """Make sure the shared spreadsheet has Checkbook's tabs and headings.

    Missing tabs are added; an empty default 'Sheet1' is removed.  Existing
    Checkbook tabs are left untouched.  Returns the spreadsheet's title.
    """
    api = _sheets(creds).spreadsheets()
    meta = api.get(spreadsheetId=sid, fields="properties.title,sheets.properties").execute()
    title = meta["properties"]["title"]
    existing = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

    missing = [t for t in TABS if t not in existing]
    if missing:
        reqs = [{"addSheet": {"properties": {"title": t, "gridProperties": {"frozenRowCount": 1}}}}
                for t in missing]
        reply = api.batchUpdate(spreadsheetId=sid, body={"requests": reqs}).execute()
        new_ids = [r["addSheet"]["properties"]["sheetId"] for r in reply["replies"]]
        data = [{"range": f"'{t}'!A1", "values": [TABS[t]]} for t in missing]
        if "Meta" in missing:
            data.append({"range": "'Meta'!A2:B3", "values": [["revision", "new"],
                                                            ["schema_version", "1"]]})
        api.values().batchUpdate(spreadsheetId=sid, body={"valueInputOption": "RAW",
                                                          "data": data}).execute()
        cleanup = [{"repeatCell": {"range": {"sheetId": i, "startRowIndex": 0, "endRowIndex": 1},
                                   "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                                   "fields": "userEnteredFormat.textFormat.bold"}}
                   for i in new_ids]
        for name, sheet_id in existing.items():
            if name in TABS:
                continue
            vals = api.values().get(spreadsheetId=sid, range=f"'{name}'!A1:Z5").execute()
            if name in ("Sheet1", "Hoja 1", "Hoja1") and not vals.get("values"):
                cleanup.append({"deleteSheet": {"sheetId": sheet_id}})
        api.batchUpdate(spreadsheetId=sid, body={"requests": cleanup}).execute()
    return title


def spreadsheet_url(sid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sid}/edit"


def open_store(creds, sid: str) -> Store:
    return Store(SheetsBackend(_sheets(creds), sid))


# ---------------------------------------------------------------- setup files
# A "setup file" bundles the key and the spreadsheet link, so another computer
# can be connected in one step.  It contains the key: keep it private.

SETUP_MARK = "checkbook_setup"


def export_setup(path: str, settings) -> None:
    with open(key_path(), encoding="utf-8") as f:
        key = json.load(f)
    data = {SETUP_MARK: 1, "spreadsheet_id": settings.get("spreadsheet_id", ""),
            "spreadsheet_name": settings.get("spreadsheet_name", ""), "service_account": key}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


def is_setup_file(path: str) -> bool:
    try:
        with open(path, encoding="utf-8") as f:
            return SETUP_MARK in json.load(f)
    except (OSError, ValueError, TypeError):
        return False


def import_setup(path: str, settings) -> str:
    """Install the key and spreadsheet from a setup file.  Returns the spreadsheet name."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if SETUP_MARK not in data:
        raise ValueError("That is not a Checkbook setup file.")
    fd, tmp = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data["service_account"], f)
        install_key(tmp)
    finally:
        os.unlink(tmp)
    settings["spreadsheet_id"] = extract_sheet_id(data["spreadsheet_id"])
    settings["spreadsheet_name"] = data.get("spreadsheet_name", "")
    settings.save()
    return settings["spreadsheet_name"]
