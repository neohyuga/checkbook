"""Where settings live, and the small settings file itself."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "Checkbook"


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def app_dir() -> Path:
    """Folder the program runs from (handles the PyInstaller one-file .exe)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


DEFAULTS = {
    "spreadsheet_id": "",
    "spreadsheet_name": "",
    "font_size": 16,
    "last_account_id": "",
}


class Settings(dict):
    def __init__(self, path: Path | None = None):
        super().__init__(DEFAULTS)
        self.path = path or (config_dir() / "settings.json")
        try:
            with open(self.path, encoding="utf-8") as f:
                self.update(json.load(f))
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict(self), f, indent=2)
        os.replace(tmp, self.path)
