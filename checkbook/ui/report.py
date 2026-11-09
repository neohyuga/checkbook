"""Spending / income by category for a period."""
from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..book import Book
from ..model import ZERO, category_totals, format_money
from .common import big_button, date_edit, to_date


def _periods(today: date):
    first = today.replace(day=1)
    last_month_end = first - timedelta(days=1)
    return [
        ("This month", first, today),
        ("Last month", last_month_end.replace(day=1), last_month_end),
        ("This year", today.replace(month=1, day=1), today),
        ("Last year", date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)),
        ("Last 12 months", today.replace(year=today.year - 1) + timedelta(days=1), today),
        ("Choose dates...", None, None),
    ]


class CategoryReport(QDialog):
    def __init__(self, parent, book: Book):
        super().__init__(parent)
        self.book = book
        self.setWindowTitle("Spending by Category")
        self.resize(820, 760)
        lay = QVBoxLayout(self)

        row = QHBoxLayout()
        self.period = QComboBox()
        self.periods = _periods(date.today())
        for name, *_ in self.periods:
            self.period.addItem(name)
        self.period.setCurrentIndex(2)
        self.d1, self.d2 = date_edit(), date_edit()
        self.accts = QComboBox()
        self.accts.addItem("All accounts", None)
        for a in book.accounts:
            self.accts.addItem(a.name, a.id)
        row.addWidget(self.period)
        row.addWidget(self.d1)
        row.addWidget(QLabel("to"))
        row.addWidget(self.d2)
        row.addWidget(self.accts, 1)
        lay.addLayout(row)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Category", "Amount"])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table)

        close = big_button("Close")
        close.clicked.connect(self.accept)
        lay.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

        self.period.currentIndexChanged.connect(self._period_changed)
        for w in (self.d1, self.d2):
            w.dateChanged.connect(lambda *_: self.fill())
        self.accts.currentIndexChanged.connect(lambda *_: self.fill())
        self._period_changed()

    def _period_changed(self, *_):
        _, a, b = self.periods[self.period.currentIndex()]
        custom = a is None
        self.d1.setEnabled(custom)
        self.d2.setEnabled(custom)
        if not custom:
            for w, d in ((self.d1, a), (self.d2, b)):
                w.blockSignals(True)
                w.setDate(d)
                w.blockSignals(False)
        self.fill()

    def fill(self):
        acct = self.accts.currentData()
        totals = category_totals(self.book.transactions, to_date(self.d1), to_date(self.d2),
                                 {acct} if acct else None)
        income = sorted((k, v) for k, v in totals.items() if v > 0)
        spend = sorted((k, -v) for k, v in totals.items() if v < 0)
        rows: list[tuple[str, str, bool]] = []
        if income:
            rows.append(("INCOME", "", True))
            rows += [(f"    {k}", format_money(v), False) for k, v in income]
            rows.append(("Total income", format_money(sum((v for _, v in income), ZERO)), True))
            rows.append(("", "", False))
        if spend:
            rows.append(("SPENDING", "", True))
            rows += [(f"    {k}", format_money(v), False) for k, v in spend]
            rows.append(("Total spending", format_money(sum((v for _, v in spend), ZERO)), True))
        if not rows:
            rows.append(("Nothing in this period.", "", False))
        else:
            rows.append(("", "", False))
            net = sum(totals.values(), ZERO)
            rows.append(("Income minus spending", format_money(net), True))
        self.table.setRowCount(len(rows))
        bold = QFont(self.font())
        bold.setBold(True)
        for i, (a, b, is_bold) in enumerate(rows):
            ia, ib = QTableWidgetItem(a), QTableWidgetItem(b)
            ib.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if is_bold:
                ia.setFont(bold)
                ib.setFont(bold)
            self.table.setItem(i, 0, ia)
            self.table.setItem(i, 1, ib)
