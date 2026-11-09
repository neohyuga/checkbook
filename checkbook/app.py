"""Program entry point.

    python -m checkbook                 # normal use (Google Sheets)
    python -m checkbook --local f.json  # use a local file instead (testing / demo)
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import date

from pathlib import Path

from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __version__
from .book import Book
from .config import Settings, config_dir
from .store import JsonBackend, Store
from .ui.common import busy, friendly_error, stylesheet

KEEP_BACKUPS = 30


def light_palette() -> QPalette:
    p = QPalette()
    for role, color in ((QPalette.ColorRole.Window, "#fafafa"), (QPalette.ColorRole.WindowText, "#111"),
                        (QPalette.ColorRole.Base, "#ffffff"), (QPalette.ColorRole.AlternateBase, "#f2f6fb"),
                        (QPalette.ColorRole.Text, "#111"), (QPalette.ColorRole.Button, "#f4f5f7"),
                        (QPalette.ColorRole.ButtonText, "#111"), (QPalette.ColorRole.Highlight, "#ffe08a"),
                        (QPalette.ColorRole.HighlightedText, "#000"), (QPalette.ColorRole.ToolTipBase, "#ffffe0"),
                        (QPalette.ColorRole.ToolTipText, "#111"), (QPalette.ColorRole.PlaceholderText, "#777")):
        p.setColor(role, QColor(color))
    return p


def write_backup(store: Store) -> None:
    """Keep a daily local copy of everything, in case of mistakes or Google trouble."""
    raw = getattr(store, "last_raw", None)
    if not raw:
        return
    d = config_dir() / "backups"
    d.mkdir(exist_ok=True)
    (d / f"checkbook-{date.today().isoformat()}.json").write_text(json.dumps(raw), encoding="utf-8")
    for old in sorted(d.glob("checkbook-*.json"))[:-KEEP_BACKUPS]:
        old.unlink()


def open_google(settings: Settings):
    from . import google_sheets as gs
    from .store import SheetsBackend
    from .ui.setup import SetupDialog

    creds = None
    if settings.get("spreadsheet_id") and gs.has_key():
        try:
            creds = gs.get_credentials()
        except Exception:
            traceback.print_exc()
    if not creds:
        dlg = SetupDialog(settings)
        if not dlg.exec():
            return None
        creds = dlg.creds
    sid = settings["spreadsheet_id"]
    store = gs.open_store(creds, sid)

    def reader_factory():
        b = SheetsBackend(gs._sheets(creds), sid)   # own connection for the background thread
        return b.get_revision

    return store, reader_factory, settings.get("spreadsheet_name", "")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="checkbook")
    ap.add_argument("--local", metavar="FILE", help="use a local JSON file instead of Google")
    ap.add_argument("--import-setup", metavar="FILE",
                    help="install a setup file (key + spreadsheet) made on another computer, then exit")
    ap.add_argument("--version", action="version", version=f"Checkbook {__version__}")
    args = ap.parse_args(argv)

    if args.import_setup:
        from . import google_sheets as gs
        try:
            name = gs.import_setup(args.import_setup, Settings())
        except (OSError, ValueError, KeyError) as e:
            print(f"Could not use the setup file: {e}", file=sys.stderr)
            return 1
        print(f"Checkbook is set up to use the spreadsheet '{name}'.")
        return 0

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Checkbook")
    app.setDesktopFileName("checkbook")
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "resources" / "icon.svg")))
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    settings = Settings()
    app.setStyleSheet(stylesheet(int(settings.get("font_size", 16))))

    if args.local:
        backend = JsonBackend(args.local)
        store, reader_factory, title = Store(backend), (lambda: backend.get_revision), args.local
    else:
        opened = open_google(settings)
        if opened is None:
            return 0
        store, reader_factory, title = opened

    book = Book(store)
    while True:
        try:
            with busy():
                book.reload()
            break
        except Exception as e:
            traceback.print_exc()
            box = QMessageBox(QMessageBox.Icon.Warning, "Checkbook",
                              "Could not open the checkbook.\n\n" + friendly_error(e))
            retry = box.addButton("Try again", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Quit", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not retry:
                return 1
    try:
        write_backup(store)
    except OSError:
        traceback.print_exc()

    from .ui.main_window import MainWindow, first_account_prompt
    win = MainWindow(book, settings, reader_factory, title, uses_google=not args.local)
    win.show()
    if book.snap.problems:
        QMessageBox.warning(win, "Checkbook", "Some lines in the spreadsheet could not be read "
                            "and are not shown:\n\n" + "\n".join(book.snap.problems[:10]))
    first_account_prompt(win)
    win.check_for_updates(quiet=True)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
