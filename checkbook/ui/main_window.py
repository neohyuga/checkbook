"""The main checkbook window: account picker, register, big action buttons."""
from __future__ import annotations

import traceback
from datetime import datetime

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QThread,
                            QTimer, Signal)
from PySide6.QtGui import QAction, QBrush, QColor, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMainWindow, QTableView,
                               QVBoxLayout, QWidget)

from ..book import Book
from ..config import Settings
from ..model import CLEARED, RECONCILED, Account, RegisterLine, balances, format_money, register
from .common import (ask, big_button, busy, friendly_error, info, run_action, stylesheet, warn)
from .dialogs import AccountDialog, AccountsDialog, CategoriesDialog, TransactionDialog, TransferDialog
from .reconcile import balance_with_statement
from .report import CategoryReport

POLL_SECONDS = 30


class RegisterModel(QAbstractTableModel):
    HEADERS = ["Date", "Num", "Payee", "Category", "Memo", "Payment", "Deposit", "Clr", "Balance"]
    C_CLR = 7

    def __init__(self):
        super().__init__()
        self.lines: list[RegisterLine] = []

    def set_lines(self, lines):
        self.beginResetModel()
        self.lines = lines
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.lines)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        line = self.lines[index.row()]
        t, col = line.txn, index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return t.date.strftime("%m/%d/%Y")
            if col == 1:
                return t.number
            if col == 2:
                return t.payee
            if col == 3:
                return "(Split)" if t.splits else t.category
            if col == 4:
                return t.memo
            if col == 5:
                return format_money(t.payment, True).lstrip("$")
            if col == 6:
                return format_money(t.deposit, True).lstrip("$")
            if col == 7:
                return {CLEARED: "C", RECONCILED: "R"}.get(t.status, "")
            if col == 8:
                return format_money(line.balance)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (5, 6, 8):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if col == 7:
                return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.ForegroundRole:
            if col == 8 and line.balance < 0:
                return QBrush(QColor("#b3261e"))
            if col == 6 and t.amount > 0:
                return QBrush(QColor("#127a2b"))
        if role == Qt.ItemDataRole.ToolTipRole:
            if col == 7:
                return {CLEARED: "Cleared (on a statement)", RECONCILED: "Reconciled"}.get(
                    t.status, "Not cleared yet - click to mark cleared")
            if t.splits:
                return "\n".join(f"{s.category}: {format_money(s.amount)} {s.memo}" for s in t.splits)
        return None


class RevisionPoller(QThread):
    """Every few seconds, ask (cheaply) whether the other computer saved something."""
    changed = Signal()
    failed = Signal(str)
    ok = Signal()

    def __init__(self, revision_reader, get_known):
        super().__init__()
        self.reader_factory = revision_reader   # callable -> callable() -> str
        self.get_known = get_known
        self._stop = False

    def run(self):
        reader = None
        while not self._stop:
            for _ in range(POLL_SECONDS * 10):
                if self._stop:
                    return
                self.msleep(100)
            try:
                if reader is None:
                    reader = self.reader_factory()
                if reader() != self.get_known():
                    self.changed.emit()
                else:
                    self.ok.emit()
            except Exception as e:  # offline etc. - just report and keep trying
                reader = None
                self.failed.emit(friendly_error(e))

    def stop(self):
        self._stop = True
        self.wait(2000)


class _UpdateCheck(QThread):
    found = Signal(dict)
    none = Signal()
    failed = Signal(str)

    def run(self):
        from .. import updater
        try:
            rel = updater.update_available()
        except Exception as e:  # offline etc.
            self.failed.emit(friendly_error(e))
            return
        (self.found.emit(rel) if rel else self.none.emit())


class MainWindow(QMainWindow):
    def __init__(self, book: Book, settings: Settings, revision_reader, title_extra="",
                 uses_google: bool = True):
        super().__init__()
        self.uses_google = uses_google
        self.book, self.settings = book, settings
        self.setWindowTitle("Checkbook" + (f" - {title_extra}" if title_extra else ""))
        self.resize(1400, 860)

        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)

        # ---- left: big buttons
        side = QVBoxLayout()
        self.b_check = big_button("Write a Check\nor Payment", "Money going out  (F2)", primary=True)
        self.b_dep = big_button("Make a\nDeposit", "Money coming in  (F3)", primary=True)
        self.b_xfer = big_button("Transfer\nMoney", "Move money between your accounts")
        self.b_edit = big_button("Change\nSelected", "Change the highlighted line (or double-click it)")
        self.b_del = big_button("Delete\nSelected", "Delete the highlighted line")
        self.b_rec = big_button("Balance with\nStatement", "Check off items on your bank statement")
        self.b_rep = big_button("Spending\nReport", "Totals by category")
        for b, fn in ((self.b_check, self.new_payment), (self.b_dep, self.new_deposit),
                      (self.b_xfer, self.new_transfer), (self.b_edit, self.edit_selected),
                      (self.b_del, self.delete_selected), (self.b_rec, self.reconcile),
                      (self.b_rep, self.report)):
            b.clicked.connect(fn)
            side.addWidget(b)
            if b in (self.b_xfer, self.b_del):
                side.addSpacing(18)
        side.addStretch()
        outer.addLayout(side)

        # ---- right: account + register
        main = QVBoxLayout()
        top = QHBoxLayout()
        top.addWidget(QLabel("Account:"))
        self.acct_box = QComboBox()
        self.acct_box.setMinimumWidth(320)
        self.acct_box.currentIndexChanged.connect(self._account_changed)
        top.addWidget(self.acct_box)
        top.addStretch()
        self.lbl_cleared = QLabel()
        self.lbl_cleared.setObjectName("hint")
        top.addWidget(self.lbl_cleared)
        top.addSpacing(24)
        self.lbl_balance = QLabel()
        self.lbl_balance.setObjectName("balance")
        top.addWidget(self.lbl_balance)
        main.addLayout(top)

        find_row = QHBoxLayout()
        find_row.addWidget(QLabel("Find:"))
        self.find = QLineEdit()
        self.find.setPlaceholderText("type part of a name, memo, amount or check number")
        self.find.setClearButtonEnabled(True)
        find_row.addWidget(self.find, 1)
        main.addLayout(find_row)

        self.model = RegisterModel()
        self.proxy = QSortFilterProxyModel()
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.find.textChanged.connect(self.proxy.setFilterFixedString)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.doubleClicked.connect(self._double_clicked)
        self.table.clicked.connect(self._clicked)
        main.addWidget(self.table, 1)
        outer.addLayout(main, 1)

        self.status = QLabel()
        self.statusBar().addWidget(self.status, 1)

        self._build_menu()
        self.apply_font()
        self.refresh_view(select_account=settings.get("last_account_id"))
        QTimer.singleShot(0, self.table.scrollToBottom)

        self.poller = RevisionPoller(revision_reader, lambda: self.book.snap.revision)
        self.poller.changed.connect(self.remote_changed)
        self.poller.failed.connect(lambda msg: self.set_status("Offline: " + msg.split("\n")[0], bad=True))
        self.poller.ok.connect(lambda: self.set_status(
            f"Up to date  (checked {datetime.now().strftime('%I:%M %p').lstrip('0')})"))
        self.poller.start()
        self.set_status("Up to date")

    # ------------------------------------------------------------ menu
    def _build_menu(self):
        m = self.menuBar()
        f = m.addMenu("&File")
        self._act(f, "&Accounts...", self.manage_accounts)
        self._act(f, "&Categories...", self.manage_categories)
        f.addSeparator()
        self._act(f, "&Refresh now", self.reload, QKeySequence.StandardKey.Refresh)
        self._act(f, "Open the spreadsheet in a browser", self.open_sheet)
        if self.uses_google:
            self._act(f, "Connect to a spreadsheet...", self.reconnect)
            self._act(f, "Make a setup file for another computer...", self.make_setup_file)
        f.addSeparator()
        self._act(f, "E&xit", self.close, QKeySequence.StandardKey.Quit)
        v = m.addMenu("&View")
        self._act(v, "&Bigger text", lambda: self.change_font(+2), QKeySequence.StandardKey.ZoomIn)
        self._act(v, "&Smaller text", lambda: self.change_font(-2), QKeySequence.StandardKey.ZoomOut)
        t = m.addMenu("&Enter")
        self._act(t, "Write a check or payment", self.new_payment, QKeySequence("F2"))
        self._act(t, "Make a deposit", self.new_deposit, QKeySequence("F3"))
        self._act(t, "Transfer money", self.new_transfer, QKeySequence("F4"))
        h = m.addMenu("&Help")
        self._act(h, "How to use Checkbook", self.help)
        self._act(h, "Check for updates", lambda: self.check_for_updates(quiet=False))
        self._act(h, "About Checkbook", self.about)

    def _act(self, menu, text, fn, shortcut=None):
        a = QAction(text, self)
        a.triggered.connect(fn)
        if shortcut:
            a.setShortcut(shortcut)
        menu.addAction(a)
        return a

    # ------------------------------------------------------------ font
    def apply_font(self):
        pt = int(self.settings.get("font_size", 16))
        app = QApplication.instance()
        f = app.font()
        f.setPointSize(pt)
        app.setFont(f)
        app.setStyleSheet(stylesheet(pt))
        self.table.verticalHeader().setDefaultSectionSize(int(pt * 2.6))
        # Fixed widths from the font (sizing to contents lets old, long entries squeeze Payee).
        fm = self.table.fontMetrics()
        hh = self.table.horizontalHeader()
        pad = fm.horizontalAdvance("M")
        for col, sample in ((0, "00/00/0000"), (1, "00000"), (3, "Food : Groceries XX"),
                            (5, "Payment"), (6, "00,000.00"), (7, "Clr"), (8, "$00,000.00")):
            hh.resizeSection(col, fm.horizontalAdvance(sample) + pad)

    def change_font(self, delta):
        self.settings["font_size"] = max(10, min(32, int(self.settings.get("font_size", 16)) + delta))
        self.settings.save()
        self.apply_font()

    # ------------------------------------------------------------ view
    def current_account(self) -> Account | None:
        return self.book.account(self.acct_box.currentData()) if self.acct_box.count() else None

    def refresh_view(self, select_account: str | None = None):
        keep = select_account or self.acct_box.currentData()
        sel = self.selected_txn()
        self.acct_box.blockSignals(True)
        self.acct_box.clear()
        for a in self.book.open_accounts:
            total, _ = balances(a, self.book.transactions)
            self.acct_box.addItem(f"{a.name}", a.id)
        i = self.acct_box.findData(keep)
        self.acct_box.setCurrentIndex(i if i >= 0 else 0)
        self.acct_box.blockSignals(False)
        self._show_register(keep_txn=sel.id if sel else None)

    def _account_changed(self, *_):
        a = self.current_account()
        if a:
            self.settings["last_account_id"] = a.id
            self.settings.save()
        self._show_register()
        self.table.scrollToBottom()

    def _show_register(self, keep_txn: str | None = None):
        a = self.current_account()
        has = a is not None
        for b in (self.b_check, self.b_dep, self.b_xfer, self.b_edit, self.b_del, self.b_rec):
            b.setEnabled(has)
        if not has:
            self.model.set_lines([])
            self.lbl_balance.setText("")
            self.lbl_cleared.setText("")
            return
        lines = register(a, self.book.transactions)
        self.model.set_lines(lines)
        total, cleared = balances(a, self.book.transactions)
        self.lbl_balance.setText(f"Balance: {format_money(total)}")
        self.lbl_balance.setStyleSheet("color: #b3261e;" if total < 0 else "")
        self.lbl_cleared.setText(f"Cleared: {format_money(cleared)}")
        if keep_txn:
            for r, line in enumerate(lines):
                if line.txn.id == keep_txn:
                    idx = self.proxy.mapFromSource(self.model.index(r, 0))
                    self.table.selectRow(idx.row())
                    self.table.scrollTo(idx)
                    break

    def selected_txn(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        src = self.proxy.mapToSource(rows[0])
        return self.model.lines[src.row()].txn if src.row() < len(self.model.lines) else None

    def set_status(self, text, bad=False):
        self.status.setText(text)
        self.status.setStyleSheet("color: #b3261e; font-weight: bold;" if bad else "color: #333;")

    # ------------------------------------------------------------ sync
    def reload(self):
        try:
            with busy():
                self.book.reload()
            self.refresh_view()
            self.set_status("Up to date")
        except Exception as e:
            traceback.print_exc()
            warn(self, "Could not refresh", friendly_error(e))

    def remote_changed(self):
        # Don't yank the register out from under an open dialog; it reloads after saving anyway.
        if QApplication.activeModalWidget() is not None:
            return
        self.reload()
        self.set_status("Updated with changes from the other computer")

    def _after(self, ok: bool):
        self.refresh_view()
        if ok:
            self.set_status("Saved")

    # ------------------------------------------------------------ actions
    def _need_account(self):
        a = self.current_account()
        if a is None:
            info(self, "No account", "Please create an account first (File > Accounts).")
        return a

    def new_payment(self):
        if a := self._need_account():
            self._after(bool(TransactionDialog(self, self.book, a, deposit=False).exec()))
            self.table.scrollToBottom()

    def new_deposit(self):
        if a := self._need_account():
            self._after(bool(TransactionDialog(self, self.book, a, deposit=True).exec()))
            self.table.scrollToBottom()

    def new_transfer(self):
        a = self._need_account()
        if not a:
            return
        if len(self.book.open_accounts) < 2:
            info(self, "Transfer", "You need at least two accounts to transfer money.\n"
                                   "Add one with File > Accounts.")
            return
        self._after(bool(TransferDialog(self, self.book, a).exec()))

    def edit_selected(self):
        t = self.selected_txn()
        if t is None:
            info(self, "Change", "Please click on the line you want to change first.")
            return
        self._edit(t)

    def _edit(self, t):
        a = self.current_account()
        if t.status == RECONCILED and not ask(
                self, "Already balanced",
                "This item was already balanced with a bank statement.\n\n"
                "Changing it may make your next statement not balance.\nChange it anyway?",
                "Change it", "Leave it"):
            return
        if t.transfer_id:
            dlg = TransferDialog(self, self.book, a, t)
        else:
            dlg = TransactionDialog(self, self.book, a, txn=t)
        self._after(bool(dlg.exec()))

    def delete_selected(self):
        t = self.selected_txn()
        if t is None:
            info(self, "Delete", "Please click on the line you want to delete first.")
            return
        what = f"{t.date.strftime('%m/%d/%Y')}  {t.payee}  {format_money(t.amount)}"
        extra = "\n\nThis is a transfer, so it will be removed from both accounts." if t.transfer_id else ""
        if ask(self, "Delete?", f"Delete this item?\n\n{what}{extra}", "Delete", "Keep it"):
            self._after(run_action(self, self.book.delete_transaction, t))

    def _double_clicked(self, idx):
        if self.proxy.mapToSource(idx).column() == RegisterModel.C_CLR:
            return
        t = self.selected_txn()
        if t:
            self._edit(t)

    def _clicked(self, idx):
        src = self.proxy.mapToSource(idx)
        if src.column() != RegisterModel.C_CLR:
            return
        t = self.model.lines[src.row()].txn
        if t.status == RECONCILED:
            info(self, "Already balanced", "This item was already balanced with a statement.")
            return
        self._after(run_action(self, self.book.toggle_cleared, t))

    def reconcile(self):
        if a := self._need_account():
            self._after(balance_with_statement(self, self.book, a))

    def report(self):
        CategoryReport(self, self.book).exec()

    def manage_accounts(self):
        AccountsDialog(self, self.book).exec()
        self.refresh_view()

    def manage_categories(self):
        CategoriesDialog(self, self.book).exec()

    def reconnect(self):
        from .setup import SetupDialog
        before = self.settings.get("spreadsheet_id")
        if SetupDialog(self.settings).exec() and self.settings.get("spreadsheet_id") != before:
            info(self, "Spreadsheet changed",
                 "Please close Checkbook and open it again to use the new spreadsheet.")

    def open_sheet(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from ..google_sheets import spreadsheet_url
        sid = self.settings.get("spreadsheet_id")
        if sid and self.uses_google:
            QDesktopServices.openUrl(QUrl(spreadsheet_url(sid)))
        else:
            info(self, "Spreadsheet", "This checkbook is not stored in Google Sheets.")

    def help(self):
        info(self, "How to use Checkbook",
             "WRITE A CHECK OR PAYMENT - for money going out.\n"
             "MAKE A DEPOSIT - for money coming in.\n"
             "TRANSFER MONEY - to move money from one of your accounts to another.\n\n"
             "To change or delete a line, click it once, then press Change or Delete.\n"
             "You can also double-click a line to change it.\n\n"
             "Click in the 'Clr' column to mark a line as cleared by the bank.\n\n"
             "BALANCE WITH STATEMENT - when your bank statement arrives, check off\n"
             "each item on it until the difference is $0.00.\n\n"
             "Everything is saved automatically to Google as soon as you press Save,\n"
             "and the other computer will see your changes within a minute.\n\n"
             "Text too small? Use View > Bigger text  (Ctrl and +).")

    # ------------------------------------------------------------ setup file / updates
    def make_setup_file(self):
        from PySide6.QtWidgets import QFileDialog
        from .. import google_sheets as gs
        if not ask(self, "Setup file",
                   "This makes a file that connects another computer to this checkbook "
                   "in one step.\n\nIt contains the key to the checkbook, so treat it like a "
                   "password: copy it with a USB stick, don't email it, and delete it "
                   "after use.\n\nMake the file now?", "Make it", "Cancel"):
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save setup file", "checkbook-setup.json",
                                              "Checkbook setup file (*.json)")
        if not path:
            return
        try:
            gs.export_setup(path, self.settings)
        except Exception as e:
            warn(self, "Setup file", friendly_error(e))
            return
        info(self, "Setup file", f"Saved:\n{path}\n\nOn the other computer, run install.sh with "
                                 "this file (see README), or pick it in the Checkbook setup "
                                 "window under 'Choose the key file'.")

    def check_for_updates(self, quiet: bool):
        """Look for a newer version in the background; offer to install it."""
        from .. import updater
        if updater.installed_root() is None:
            if not quiet:
                info(self, "Updates", "This copy of Checkbook updates itself only when it was "
                                      "installed with install.sh.")
            return
        self._upd = _UpdateCheck()
        self._upd.found.connect(self._offer_update)
        if not quiet:
            self._upd.none.connect(lambda: info(self, "Updates", "Checkbook is up to date."))
            self._upd.failed.connect(lambda m: warn(self, "Updates", m))
        self._upd.start()

    def _offer_update(self, rel: dict):
        from .. import updater
        if QApplication.activeModalWidget() is not None:
            return          # don't interrupt; it will be offered next time
        if not ask(self, "New version",
                   f"A new version of Checkbook ({rel['version']}) is ready.\n\n"
                   "Update now? It takes about a minute and Checkbook will reopen by itself.",
                   "Update now", "Later"):
            return
        if not run_action(self, updater.apply_update, rel):
            return
        info(self, "Updated", "Checkbook was updated and will now reopen.")
        self.poller.stop()
        updater.restart()

    def about(self):
        from .. import __version__
        info(self, "About Checkbook", f"Checkbook version {__version__}")

    def closeEvent(self, e):
        self.poller.stop()
        super().closeEvent(e)


def first_account_prompt(win: MainWindow):
    if not win.book.accounts:
        info(win, "Welcome", "Welcome to Checkbook!\n\nFirst, let's create your checking account.\n\n"
             "(If you are going to import from Microsoft Money, press Cancel on the next window.)")
        if AccountDialog(win, win.book).exec():
            # starter categories only for a brand-new checkbook (not one about to be imported)
            if not win.book.categories:
                run_action(win, win.book.add_default_categories)
            win.refresh_view()
