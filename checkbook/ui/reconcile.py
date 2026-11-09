"""Balance the account against a bank statement (reconcile), Money-style."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView,
                               QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..book import Book
from ..model import (CLEARED, RECONCILED, UNCLEARED, ZERO, Account, Transaction, format_money,
                     reconciled_balance, sort_key)
from .common import (ask, big_button, date_edit, money_edit, read_money, run_action, to_date)


class StatementDialog(QDialog):
    """Step 1: what does the bank statement say?"""

    def __init__(self, parent, book: Book, account: Account):
        super().__init__(parent)
        self.setWindowTitle(f"Balance with Statement - {account.name}")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        h = QLabel("Step 1 of 2:  Look at your bank statement")
        h.setObjectName("heading")
        lay.addWidget(h)
        form = QFormLayout()
        form.setVerticalSpacing(12)
        self.start = reconciled_balance(account, book.transactions)
        form.addRow("Starting balance (from last time):", QLabel(format_money(self.start)))
        self.date = date_edit()
        form.addRow("Statement date:", self.date)
        self.ending = money_edit()
        form.addRow("Ending balance on statement  $", self.ending)
        lay.addLayout(form)
        hint = QLabel("Next you will check off each item that appears on the statement.")
        hint.setObjectName("hint")
        lay.addWidget(hint)
        bb = QDialogButtonBox()
        nxt = bb.addButton("Next  >", QDialogButtonBox.ButtonRole.AcceptRole)
        nxt.setProperty("primary", True)
        bb.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.ending_value = ZERO
        self.ending.setFocus()

    def accept(self):
        v = read_money(self, self.ending, "ending balance", allow_zero=True)
        if v is None:
            return
        self.ending_value = v
        super().accept()


class ReconcileDialog(QDialog):
    """Step 2: tick off the items on the statement until the difference is zero."""

    COLS = ["On statement?", "Date", "Num", "Payee", "Payment", "Deposit"]

    def __init__(self, parent, book: Book, account: Account, statement_date, ending):
        super().__init__(parent)
        self.book, self.account, self.ending = book, account, ending
        self.setWindowTitle(f"Balance with Statement - {account.name}")
        self.resize(1100, 720)
        self.start = reconciled_balance(account, book.transactions)
        self.txns: list[Transaction] = sorted(
            (t for t in book.transactions if t.account_id == account.id and not t.deleted
             and t.status != RECONCILED), key=sort_key)

        lay = QVBoxLayout(self)
        h = QLabel("Step 2 of 2:  Click each item that is on your statement")
        h.setObjectName("heading")
        lay.addWidget(h)

        self.table = QTableWidget(len(self.txns), len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for i, t in enumerate(self.txns):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Checked if t.status == CLEARED
                                else Qt.CheckState.Unchecked)
            self.table.setItem(i, 0, check)
            vals = [t.date.strftime("%m/%d/%Y"), t.number, t.payee,
                    format_money(t.payment, True), format_money(t.deposit, True)]
            for j, v in enumerate(vals, start=1):
                it = QTableWidgetItem(v)
                if j >= 4:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if t.date > statement_date:
                    it.setForeground(QColor("#888"))
                self.table.setItem(i, j, it)
        self.table.resizeColumnsToContents()
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.cellClicked.connect(self._clicked)
        self.table.itemChanged.connect(lambda *_: self._update())
        lay.addWidget(self.table)

        summary = QFormLayout()
        self.lbl_statement = QLabel(format_money(ending))
        self.lbl_cleared = QLabel()
        self.lbl_diff = QLabel()
        self.lbl_diff.setObjectName("balance")
        summary.addRow("Statement ending balance:", self.lbl_statement)
        summary.addRow("Checked-off balance:", self.lbl_cleared)
        summary.addRow("Difference:", self.lbl_diff)
        lay.addLayout(summary)

        row = QHBoxLayout()
        later = big_button("Finish Later", "Save the check marks and come back another time")
        later.clicked.connect(self.finish_later)
        cancel = big_button("Cancel")
        cancel.clicked.connect(self.reject)
        self.done_btn = big_button("Finished!", primary=True)
        self.done_btn.clicked.connect(self.finish)
        row.addWidget(later)
        row.addStretch()
        row.addWidget(cancel)
        row.addWidget(self.done_btn)
        lay.addLayout(row)
        self._update()

    def _clicked(self, row, col):
        if col != 0:   # clicking anywhere on the line toggles it
            it = self.table.item(row, 0)
            it.setCheckState(Qt.CheckState.Unchecked if it.checkState() == Qt.CheckState.Checked
                             else Qt.CheckState.Checked)

    def _checked(self) -> list[Transaction]:
        return [t for i, t in enumerate(self.txns)
                if self.table.item(i, 0).checkState() == Qt.CheckState.Checked]

    def _update(self):
        cleared = self.start + sum((t.amount for t in self._checked()), ZERO)
        diff = self.ending - cleared
        self.lbl_cleared.setText(format_money(cleared))
        if diff == 0:
            self.lbl_diff.setText("$0.00   - it balances!")
            self.lbl_diff.setStyleSheet("color: #127a2b;")
        else:
            self.lbl_diff.setText(format_money(diff))
            self.lbl_diff.setStyleSheet("color: #b3261e;")
        self.done_btn.setEnabled(diff == 0)
        self.done_btn.setToolTip("" if diff == 0 else
                                 "The difference must be $0.00. Check for a missed or mistyped item.")

    def _save_marks(self, final: bool) -> bool:
        checked = {t.id for t in self._checked()}
        to_set = [t for t in self.txns if (t.id in checked) != (t.status == CLEARED)]
        new_c = [t for t in to_set if t.id in checked]
        new_u = [t for t in to_set if t.id not in checked]

        def go():
            if new_u:
                self.book.set_status(new_u, UNCLEARED)
            if final:
                fresh = [self.book.txn(i) for i in checked]
                self.book.set_status([t for t in fresh if t], RECONCILED)
            elif new_c:
                self.book.set_status(new_c, CLEARED)
        return run_action(self, go)

    def finish_later(self):
        if self._save_marks(final=False):
            self.accept()

    def finish(self):
        if self._save_marks(final=True):
            self.accept()

    def reject(self):
        if self._checked() != [t for t in self.txns if t.status == CLEARED]:
            if not ask(self, "Leave?", "Leave without saving your check marks?",
                       "Leave", "Stay"):
                return
        super().reject()


def balance_with_statement(parent, book: Book, account: Account) -> bool:
    step1 = StatementDialog(parent, book, account)
    if not step1.exec():
        return False
    return bool(ReconcileDialog(parent, book, account, to_date(step1.date), step1.ending_value).exec())
