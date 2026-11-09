"""Shared UI helpers: friendly error handling, busy cursor, input widgets."""
from __future__ import annotations

import contextlib
import traceback
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QComboBox, QCompleter, QDateEdit, QLineEdit, QMessageBox,
                               QPushButton, QWidget)

from ..model import parse_money
from ..store import ConflictError

DATE_FMT = "MM/dd/yyyy"


@contextlib.contextmanager
def busy():
    QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        yield
    finally:
        QGuiApplication.restoreOverrideCursor()


def info(parent, title, text):
    QMessageBox.information(parent, title, text)


def warn(parent, title, text):
    QMessageBox.warning(parent, title, text)


def ask(parent, title, text, yes="Yes", no="No") -> bool:
    box = QMessageBox(QMessageBox.Icon.Question, title, text, parent=parent)
    y = box.addButton(yes, QMessageBox.ButtonRole.YesRole)
    box.addButton(no, QMessageBox.ButtonRole.NoRole)
    box.setDefaultButton(y)
    box.exec()
    return box.clickedButton() is y


def api_disabled(e: BaseException) -> bool:
    """True if Google refused because the Sheets API isn't enabled in the Cloud project."""
    content = getattr(e, "content", b"") or b""
    if isinstance(content, bytes):
        content = content.decode("utf-8", "replace")
    return "SERVICE_DISABLED" in content or "has not been used in project" in content


def friendly_error(e: BaseException) -> str:
    name = type(e).__name__
    text = str(e)
    if name in ("HttpError",):
        status = getattr(getattr(e, "resp", None), "status", "")
        if api_disabled(e):
            return ("The Google Sheets API is turned off for this Checkbook's Google Cloud "
                    "project.\n\nIn Google Cloud, open APIs & Services > Library > Google "
                    "Sheets API and press Enable. Wait a minute, then try again.")
        if str(status) in ("401", "403"):
            return ("Google did not allow access to the checkbook.\n\n"
                    "Make sure the spreadsheet is still shared (as Editor) with the "
                    "Checkbook key's email address. File > Connect to a spreadsheet shows it.")
        if str(status) == "429":
            return "Google is busy right now. Please wait a minute and try again."
        return f"Google reported a problem ({status}). Please try again in a minute."
    if isinstance(e, (OSError, TimeoutError)) or name in ("ServerNotFoundError", "TransportError"):
        return ("Could not reach Google. Please check that the computer is connected "
                "to the internet, then try again.")
    return f"Something went wrong:\n\n{text or name}"


def run_action(parent, fn, *args, **kwargs) -> bool:
    """Run a save operation with a busy cursor and friendly messages.  True on success."""
    try:
        with busy():
            fn(*args, **kwargs)
        return True
    except ConflictError:
        info(parent, "Changed on the other computer",
             "This item was just changed on the other computer.\n\n"
             "The newest version is now showing. Please look it over and make your "
             "change again if it is still needed.")
    except ValueError as e:
        warn(parent, "Please check", str(e))
    except Exception as e:  # network, Google, etc.
        traceback.print_exc()
        warn(parent, "Could not save", friendly_error(e) + "\n\nYour change was NOT saved.")
    return False


def big_button(text: str, tooltip: str = "", primary: bool = False) -> QPushButton:
    b = QPushButton(text)
    b.setToolTip(tooltip)
    b.setProperty("primary", primary)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def date_edit(d: date | None = None) -> QDateEdit:
    w = QDateEdit()
    w.setCalendarPopup(True)
    w.setDisplayFormat(DATE_FMT)
    w.setDate(QDate(d) if d else QDate.currentDate())
    return w


def to_date(w: QDateEdit) -> date:
    return w.date().toPython()


def completer_combo(items: list[str], text: str = "", placeholder: str = "") -> QComboBox:
    """Editable combo box that suggests matches as you type (anywhere in the word)."""
    c = QComboBox()
    c.setEditable(True)
    c.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    c.addItems(items)
    c.setMaxVisibleItems(15)
    comp = QCompleter(items, c)
    comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    comp.setFilterMode(Qt.MatchFlag.MatchContains)
    c.setCompleter(comp)
    c.setCurrentIndex(-1)
    c.setEditText(text)
    if placeholder:
        c.lineEdit().setPlaceholderText(placeholder)
    return c


def money_edit(placeholder: str = "0.00") -> QLineEdit:
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setAlignment(Qt.AlignmentFlag.AlignRight)
    return e


def read_money(parent: QWidget, edit: QLineEdit, what: str = "amount", allow_zero=False):
    """Parse a money field; show a message and return None if invalid."""
    try:
        v = parse_money(edit.text())
    except ValueError:
        warn(parent, "Please check", f"The {what} '{edit.text()}' is not a number.\n\n"
                                     "Type it like this: 25.00")
        edit.setFocus()
        return None
    if v == 0 and not allow_zero:
        warn(parent, "Please check", f"Please type the {what}.")
        edit.setFocus()
        return None
    return v


def stylesheet(pt: int) -> str:
    pad = max(6, pt // 2)
    return f"""
    * {{ font-size: {pt}pt; }}
    QPushButton {{ padding: {pad}px {pad * 2}px; min-height: {pt * 2}px; border-radius: 6px;
                  border: 1px solid #8a8f98; background: #f4f5f7; color: #111; }}
    QPushButton:hover {{ background: #e6ecf5; }}
    QPushButton:disabled {{ color: #999; background: #eee; }}
    QPushButton[primary="true"] {{ background: #1f5fbf; color: white; border-color: #174a96;
                                   font-weight: bold; }}
    QPushButton[primary="true"]:hover {{ background: #2a6fd6; }}
    QLineEdit, QComboBox, QDateEdit {{ padding: 4px 6px; min-height: {int(pt * 1.8)}px; }}
    QTableView {{ gridline-color: #d0d4da; selection-background-color: #ffe08a;
                  selection-color: black; alternate-background-color: #f2f6fb; }}
    QHeaderView::section {{ padding: 4px; font-weight: bold; background: #e4e8ee; color: #111;
                            border: 0; border-right: 1px solid #c8ccd2; }}
    QTableView::indicator, QCheckBox::indicator, QRadioButton::indicator {{
        width: {int(pt * 1.4)}px; height: {int(pt * 1.4)}px; }}
    QLabel#balance {{ font-size: {int(pt * 1.5)}pt; font-weight: bold; }}
    QLabel#heading {{ font-size: {int(pt * 1.25)}pt; font-weight: bold; }}
    QLabel#hint {{ color: #555; }}
    """
