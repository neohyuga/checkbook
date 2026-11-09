"""Dialogs: write a check / deposit, split, transfer, accounts, categories."""
from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QRadioButton,
                               QTableWidget, QVBoxLayout)

from ..book import Book
from ..model import (ZERO, Account, Split, Transaction, format_money, last_for_payee, new_id,
                     next_check_number, parse_money)
from .common import (big_button, completer_combo, date_edit, info, money_edit, read_money,
                     run_action, to_date, warn)

ACCOUNT_KINDS = ["Checking", "Savings", "Credit Card", "Cash", "Other"]


def _buttons(dlg: QDialog, ok_text="Save") -> QDialogButtonBox:
    bb = QDialogButtonBox()
    ok = bb.addButton(ok_text, QDialogButtonBox.ButtonRole.AcceptRole)
    ok.setProperty("primary", True)
    bb.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    return bb


class TransactionDialog(QDialog):
    """Write a check / payment, or record a deposit.  Also used to change one."""

    def __init__(self, parent, book: Book, account: Account, deposit: bool = False,
                 txn: Transaction | None = None):
        super().__init__(parent)
        self.book, self.account, self.orig = book, account, txn
        self.splits: list[Split] = [Split(s.category, s.amount, s.memo) for s in txn.splits] if txn else []
        deposit = txn.amount > 0 if txn else deposit
        self.setWindowTitle(("Change " if txn else "") + ("Deposit" if deposit else "Check or Payment")
                            + f" - {account.name}")
        self.setMinimumWidth(640)

        lay = QVBoxLayout(self)
        kind_row = QHBoxLayout()
        self.rb_pay = QRadioButton("Payment / Check (money out)")
        self.rb_dep = QRadioButton("Deposit (money in)")
        grp = QButtonGroup(self)
        grp.addButton(self.rb_pay)
        grp.addButton(self.rb_dep)
        (self.rb_dep if deposit else self.rb_pay).setChecked(True)
        kind_row.addWidget(self.rb_pay)
        kind_row.addWidget(self.rb_dep)
        kind_row.addStretch()
        lay.addLayout(kind_row)

        form = QFormLayout()
        form.setVerticalSpacing(12)
        self.date = date_edit(txn.date if txn else None)
        form.addRow("Date:", self.date)
        self.number = QLineEdit(txn.number if txn else
                                ("" if deposit else next_check_number(account.id, book.transactions)))
        self.number.setPlaceholderText("check number (leave blank if none)")
        self.number_label = QLabel("Check number:")
        form.addRow(self.number_label, self.number)
        self.payee = completer_combo(book.payees(), txn.payee if txn else "",
                                     "start typing a name...")
        self.payee_label = QLabel()
        form.addRow(self.payee_label, self.payee)
        self.amount = money_edit()
        if txn:
            self.amount.setText(f"{abs(txn.amount):.2f}")
        form.addRow("Amount  $", self.amount)
        cat_row = QHBoxLayout()
        self.category = completer_combo(book.category_names(), txn.category if txn else "",
                                        "for example: Food : Groceries")
        self.split_btn = big_button("Split...", "Divide this amount among several categories")
        self.split_btn.clicked.connect(self.edit_splits)
        cat_row.addWidget(self.category, 1)
        cat_row.addWidget(self.split_btn)
        form.addRow("Category:", cat_row)
        self.split_label = QLabel()
        self.split_label.setObjectName("hint")
        form.addRow("", self.split_label)
        self.memo = QLineEdit(txn.memo if txn else "")
        self.memo.setPlaceholderText("optional note")
        form.addRow("Memo:", self.memo)
        lay.addLayout(form)
        lay.addWidget(_buttons(self))

        self.rb_pay.toggled.connect(self._kind_changed)
        self.payee.activated.connect(self._payee_chosen)
        self.payee.lineEdit().editingFinished.connect(self._payee_chosen)
        self._kind_changed()
        self._show_splits()
        (self.payee if not txn else self.amount).setFocus()

    def _kind_changed(self):
        dep = self.rb_dep.isChecked()
        self.payee_label.setText("Received from:" if dep else "Pay to:")
        self.number_label.setText("Number:" if dep else "Check number:")

    def _payee_chosen(self, *_):
        """Like Money: fill in the category (and amount) used last time for this payee."""
        if self.orig:
            return
        last = last_for_payee(self.payee.currentText(), self.book.transactions)
        if not last or last.transfer_id:
            return
        if not self.category.currentText().strip() and not self.splits:
            self.category.setEditText(last.category)
            if last.splits:
                self.splits = [Split(s.category, s.amount, s.memo) for s in last.splits]
                self._show_splits()
        if not self.amount.text().strip():
            self.amount.setText(f"{abs(last.amount):.2f}")
            self.amount.selectAll()
        if (last.amount > 0) != self.rb_dep.isChecked():
            (self.rb_dep if last.amount > 0 else self.rb_pay).setChecked(True)

    def _show_splits(self):
        if self.splits:
            self.category.setEditText("(Split)")
            self.category.setEnabled(False)
            self.split_label.setText("Split: " + ", ".join(
                f"{s.category or '?'} {format_money(abs(s.amount))}" for s in self.splits))
            self.split_label.show()
        else:
            if self.category.currentText() == "(Split)":
                self.category.setEditText("")
            self.category.setEnabled(True)
            self.split_label.hide()

    def edit_splits(self):
        sign = 1 if self.rb_dep.isChecked() else -1
        try:
            total = abs(parse_money(self.amount.text()))
        except ValueError:
            total = ZERO
        start = self.splits or ([Split(self.category.currentText(), sign * total)]
                                if self.category.currentText().strip() else [])
        dlg = SplitDialog(self, self.book, start, total, sign)
        if dlg.exec():
            self.splits = dlg.result_splits
            if self.splits:
                self.amount.setText(f"{abs(sum((s.amount for s in self.splits), ZERO)):.2f}")
            self._show_splits()

    def result_txn(self) -> Transaction | None:
        amt = read_money(self, self.amount)
        if amt is None:
            return None
        if amt < 0:
            warn(self, "Please check", "Please type the amount without a minus sign.\n"
                                       "Choose 'Payment' or 'Deposit' at the top instead.")
            return None
        sign = 1 if self.rb_dep.isChecked() else -1
        amount = sign * amt
        splits = [Split(s.category, sign * abs(s.amount), s.memo) for s in self.splits]
        if splits and sum((s.amount for s in splits), ZERO) != amount:
            warn(self, "Please check", "The split amounts don't add up to the total amount.\n"
                                       "Please press 'Split...' and fix them.")
            return None
        payee = self.payee.currentText().strip()
        if not payee:
            warn(self, "Please check", "Please type who the money was paid to or received from.")
            self.payee.setFocus()
            return None
        fields = dict(date=to_date(self.date), amount=amount, payee=payee,
                      category="" if splits else self.category.currentText().strip(),
                      memo=self.memo.text().strip(), number=self.number.text().strip(),
                      splits=splits)
        if self.orig:
            return self.orig.copy(**fields)
        return Transaction(new_id(), self.account.id, **fields)

    def accept(self):
        t = self.result_txn()
        if t is None:
            return
        fn = self.book.update_transaction if self.orig else self.book.add_transaction
        if run_action(self, fn, t):
            super().accept()


class SplitDialog(QDialog):
    def __init__(self, parent, book: Book, splits: list[Split], total: Decimal, sign: int):
        super().__init__(parent)
        self.setWindowTitle("Split among categories")
        self.setMinimumSize(760, 460)
        self.book, self.total, self.sign = book, total, sign
        self.result_splits: list[Split] = []
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Put each part of the amount on its own line:"))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Category", "Amount", "Memo"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(48)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        add = big_button("Add a line")
        add.clicked.connect(lambda: self._add_row())
        rem = big_button("Remove line")
        rem.clicked.connect(self._remove_row)
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch()
        self.left = QLabel()
        self.left.setObjectName("heading")
        row.addWidget(self.left)
        lay.addLayout(row)
        lay.addWidget(_buttons(self, "Done"))
        for s in splits:
            self._add_row(s)
        while self.table.rowCount() < 2:
            self._add_row()
        self._update_left()

    def _add_row(self, s: Split | None = None):
        r = self.table.rowCount()
        self.table.insertRow(r)
        cat = completer_combo(self.book.category_names(), s.category if s else "")
        amt = money_edit()
        if s and s.amount:
            amt.setText(f"{abs(s.amount):.2f}")
        amt.textChanged.connect(self._update_left)
        memo = QLineEdit(s.memo if s else "")
        self.table.setCellWidget(r, 0, cat)
        self.table.setCellWidget(r, 1, amt)
        self.table.setCellWidget(r, 2, memo)

    def _remove_row(self):
        r = self.table.currentRow()
        if r < 0:
            r = self.table.rowCount() - 1
        if r >= 0:
            self.table.removeRow(r)
        self._update_left()

    def _rows(self):
        for r in range(self.table.rowCount()):
            yield (self.table.cellWidget(r, 0).currentText().strip(),
                   self.table.cellWidget(r, 1), self.table.cellWidget(r, 2).text().strip())

    def _update_left(self, *_):
        used = ZERO
        for _, amt, _ in self._rows():
            try:
                used += abs(parse_money(amt.text()))
            except ValueError:
                pass
        if self.total:
            self.left.setText(f"Total {format_money(used)} of {format_money(self.total)}  -  "
                              f"left: {format_money(self.total - used)}")
        else:
            self.left.setText(f"Total: {format_money(used)}")

    def accept(self):
        out = []
        for cat, amt, memo in self._rows():
            if not cat and not amt.text().strip():
                continue
            v = read_money(self, amt)
            if v is None:
                return
            if not cat:
                warn(self, "Please check", "Every line needs a category.")
                return
            out.append(Split(cat, self.sign * abs(v), memo))
        if len(out) == 1:
            warn(self, "Please check", "A split needs at least two lines.\n"
                                       "(Or remove all lines to stop splitting.)")
            return
        self.result_splits = out
        super().accept()


class TransferDialog(QDialog):
    def __init__(self, parent, book: Book, account: Account, txn: Transaction | None = None):
        super().__init__(parent)
        self.book, self.orig = book, txn
        self.setWindowTitle("Change Transfer" if txn else "Transfer Money Between Accounts")
        self.setMinimumWidth(600)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setVerticalSpacing(12)
        accts = book.open_accounts
        self.from_acct = QComboBox()
        self.to_acct = QComboBox()
        for a in accts:
            self.from_acct.addItem(a.name, a.id)
            self.to_acct.addItem(a.name, a.id)
        if txn:
            out_id, in_id = ((txn.account_id, txn.transfer_account_id) if txn.amount < 0
                             else (txn.transfer_account_id, txn.account_id))
            self.from_acct.setCurrentIndex(max(0, self.from_acct.findData(out_id)))
            self.to_acct.setCurrentIndex(max(0, self.to_acct.findData(in_id)))
            # the side we are viewing from can't be moved to another account
            (self.from_acct if txn.amount < 0 else self.to_acct).setEnabled(False)
        else:
            self.from_acct.setCurrentIndex(max(0, self.from_acct.findData(account.id)))
            self.to_acct.setCurrentIndex(1 if self.from_acct.currentIndex() == 0 and len(accts) > 1 else 0)
        form.addRow("From account:", self.from_acct)
        form.addRow("To account:", self.to_acct)
        self.date = date_edit(txn.date if txn else None)
        form.addRow("Date:", self.date)
        self.amount = money_edit()
        if txn:
            self.amount.setText(f"{abs(txn.amount):.2f}")
        form.addRow("Amount  $", self.amount)
        self.memo = QLineEdit(txn.memo if txn else "")
        form.addRow("Memo:", self.memo)
        lay.addLayout(form)
        lay.addWidget(_buttons(self))

    def accept(self):
        amt = read_money(self, self.amount)
        if amt is None:
            return
        if amt < 0:
            warn(self, "Please check", "Please type the amount without a minus sign.")
            return
        frm = self.book.account(self.from_acct.currentData())
        to = self.book.account(self.to_acct.currentData())
        if frm is None or to is None or frm.id == to.id:
            warn(self, "Please check", "Please pick two different accounts.")
            return
        if self.orig:
            t = self.orig
            other = to if t.amount < 0 else frm
            signed = -amt if t.amount < 0 else amt
            ok = run_action(self, self.book.update_transfer, t, other, to_date(self.date),
                            signed, self.memo.text().strip(), t.number)
        else:
            ok = run_action(self, self.book.add_transfer, frm, to, to_date(self.date), amt,
                            self.memo.text().strip())
        if ok:
            super().accept()


class AccountDialog(QDialog):
    def __init__(self, parent, book: Book, account: Account | None = None):
        super().__init__(parent)
        self.book, self.orig = book, account
        self.setWindowTitle("Change Account" if account else "New Account")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(account.name if account else "")
        self.name.setPlaceholderText("for example: Checking at First Bank")
        form.addRow("Account name:", self.name)
        self.kind = QComboBox()
        self.kind.addItems(ACCOUNT_KINDS)
        if account:
            self.kind.setCurrentText(account.kind)
        form.addRow("Type:", self.kind)
        self.opening = money_edit()
        self.opening.setText(f"{account.opening_balance:.2f}" if account else "")
        form.addRow("Starting balance  $", self.opening)
        self.closed = QCheckBox("This account is closed (hide it)")
        self.closed.setChecked(bool(account and account.closed))
        if account:
            form.addRow("", self.closed)
        lay.addLayout(form)
        lay.addWidget(_buttons(self))

    def accept(self):
        bal = read_money(self, self.opening, "starting balance", allow_zero=True)
        if bal is None:
            return
        if self.orig:
            a = self.orig
            changed = Account(a.id, self.name.text().strip() or a.name, self.kind.currentText(),
                              bal, self.closed.isChecked(), a.modified)
            ok = run_action(self, self.book.update_account, changed)
        else:
            ok = run_action(self, self.book.add_account, self.name.text(), self.kind.currentText(), bal)
        if ok:
            super().accept()


class AccountsDialog(QDialog):
    def __init__(self, parent, book: Book):
        super().__init__(parent)
        self.book = book
        self.setWindowTitle("Accounts")
        self.setMinimumSize(600, 420)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self.change)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        for text, fn in (("New account", self.new), ("Change", self.change)):
            b = big_button(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        close = big_button("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self.fill()

    def fill(self):
        self.list.clear()
        for a in self.book.accounts:
            it = QListWidgetItem(f"{a.name}   ({a.kind}{', closed' if a.closed else ''})")
            it.setData(Qt.ItemDataRole.UserRole, a.id)
            self.list.addItem(it)

    def new(self):
        if AccountDialog(self, self.book).exec():
            self.fill()

    def change(self, *_):
        it = self.list.currentItem()
        if not it:
            info(self, "Accounts", "Please click an account first.")
            return
        a = self.book.account(it.data(Qt.ItemDataRole.UserRole))
        if a and AccountDialog(self, self.book, a).exec():
            self.fill()


class CategoriesDialog(QDialog):
    def __init__(self, parent, book: Book):
        super().__init__(parent)
        self.book = book
        self.setWindowTitle("Categories")
        self.setMinimumSize(620, 560)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Tip: use ' : ' to make a sub-category, like  Food : Groceries"))
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText("new category name")
        add = big_button("Add")
        add.clicked.connect(self.add)
        hide = big_button("Hide / Show")
        hide.setToolTip("Hidden categories are not offered when typing, but old entries keep them")
        hide.clicked.connect(self.toggle_hidden)
        row.addWidget(self.new_name, 1)
        row.addWidget(add)
        row.addWidget(hide)
        lay.addLayout(row)
        close = big_button("Close")
        close.clicked.connect(self.accept)
        lay.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        self.fill()

    def fill(self):
        self.list.clear()
        for c in sorted(self.book.categories, key=lambda c: c.name.lower()):
            label = c.name + ("   (income)" if c.income else "") + ("   [hidden]" if c.hidden else "")
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, c.id)
            self.list.addItem(it)

    def add(self):
        name = self.new_name.text().strip()
        if not name:
            return
        income = name.lower().startswith("income")
        if run_action(self, lambda: (self.book.ensure_category(name, income), self.book.reload())):
            self.new_name.clear()
            self.fill()

    def toggle_hidden(self):
        it = self.list.currentItem()
        if not it:
            return
        c = next(c for c in self.book.categories if c.id == it.data(Qt.ItemDataRole.UserRole))
        c2 = type(c)(c.id, c.name, c.income, not c.hidden)
        if run_action(self, self.book.update_category, c2):
            self.fill()
