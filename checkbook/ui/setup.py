"""First-run setup: install the key file, then connect to the shared Google Sheet."""
from __future__ import annotations

import traceback

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QVBoxLayout)

from .. import google_sheets as gs
from .common import api_disabled, big_button, busy, friendly_error, info, warn


class SetupDialog(QDialog):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.creds = None
        self.setWindowTitle("Checkbook - Setup")
        self.setMinimumWidth(820)
        lay = QVBoxLayout(self)
        lay.setSpacing(14)
        h = QLabel("Welcome to Checkbook")
        h.setObjectName("heading")
        lay.addWidget(h)
        lay.addWidget(QLabel("Your checkbook is kept in a Google Sheet, so both of your "
                             "computers can use it."))

        # ---- step 1: key file
        self.step1 = QLabel()
        self.step1.setWordWrap(True)
        lay.addWidget(self.step1)
        row1 = QHBoxLayout()
        self.key_btn = big_button("Choose the key file...")
        self.key_btn.clicked.connect(self.choose_key)
        row1.addWidget(self.key_btn)
        row1.addStretch()
        lay.addLayout(row1)

        # ---- step 2: share
        self.step2 = QLabel()
        self.step2.setWordWrap(True)
        lay.addWidget(self.step2)
        row2 = QHBoxLayout()
        self.email = QLineEdit()
        self.email.setReadOnly(True)
        self.copy_btn = big_button("Copy")
        self.copy_btn.clicked.connect(self.copy_email)
        row2.addWidget(self.email, 1)
        row2.addWidget(self.copy_btn)
        lay.addLayout(row2)

        # ---- step 3: link
        self.step3 = QLabel("Step 3: Copy the spreadsheet's address from the browser "
                            "and paste it here:")
        lay.addWidget(self.step3)
        self.link = QLineEdit()
        self.link.setPlaceholderText("https://docs.google.com/spreadsheets/d/...")
        lay.addWidget(self.link)
        row3 = QHBoxLayout()
        row3.addStretch()
        cancel = big_button("Cancel")
        cancel.clicked.connect(self.reject)
        self.connect_btn = big_button("Connect", primary=True)
        self.connect_btn.clicked.connect(self.connect_sheet)
        row3.addWidget(cancel)
        row3.addWidget(self.connect_btn)
        lay.addLayout(row3)
        self._refresh()

    def _refresh(self):
        have = gs.has_key()
        self.step1.setText(
            "Step 1: The key file is installed." if have else
            "Step 1: Choose the key file (service_account.json) that you downloaded "
            "from Google Cloud.")
        self.key_btn.setText("Choose a different key file..." if have else "Choose the key file...")
        email = gs.robot_email() if have else ""
        self.email.setText(email)
        self.step2.setText(
            "Step 2: In Google Sheets, create a blank spreadsheet (for example 'Checkbook'). "
            "Press Share, paste this address, choose Editor, and press Send:")
        for w in (self.step2, self.email, self.copy_btn, self.step3, self.link, self.connect_btn):
            w.setEnabled(have)
        if self.settings.get("spreadsheet_id"):
            self.link.setText(gs.spreadsheet_url(self.settings["spreadsheet_id"]))

    def choose_key(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose the key file", "",
                                              "Key or setup file (*.json)")
        if not path:
            return
        if gs.is_setup_file(path):
            try:
                gs.import_setup(path, self.settings)
            except (OSError, ValueError, KeyError) as e:
                warn(self, "Setup file", f"That setup file could not be used.\n\n{e}")
                return
            self._refresh()
            self.connect_sheet()          # everything is known: connect right away
            return
        try:
            gs.install_key(path)
        except ValueError as e:
            warn(self, "Key file", str(e))
            return
        info(self, "Key file", "The key file is installed.\n\nFor safety you can now delete "
                               "the downloaded copy (for example from your Downloads folder).")
        self._refresh()

    def copy_email(self):
        QGuiApplication.clipboard().setText(self.email.text())
        self.copy_btn.setText("Copied!")

    def connect_sheet(self):
        try:
            sid = gs.extract_sheet_id(self.link.text())
        except ValueError as e:
            warn(self, "Spreadsheet link", str(e))
            return
        try:
            with busy():
                creds = gs.get_credentials()
                title = gs.prepare_sheet(creds, sid)
        except Exception as e:
            traceback.print_exc()
            status = str(getattr(getattr(e, "resp", None), "status", ""))
            if api_disabled(e):
                warn(self, "Google Sheets API is off", friendly_error(e))
            elif status in ("403", "404"):
                warn(self, "Not shared yet",
                     "Checkbook can't open that spreadsheet yet.\n\n"
                     "Please make sure you shared it with this address as Editor:\n\n"
                     f"{self.email.text()}")
            else:
                warn(self, "Google", friendly_error(e))
            return
        self.creds = creds
        self.settings["spreadsheet_id"] = sid
        self.settings["spreadsheet_name"] = title
        self.settings.save()
        info(self, "Connected", f"Checkbook is connected to the spreadsheet '{title}'.")
        self.accept()
