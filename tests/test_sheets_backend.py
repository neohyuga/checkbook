"""Exercise SheetsBackend against an in-memory imitation of the Sheets v4 API."""
import re
from datetime import date
from decimal import Decimal as D

import pytest

from checkbook.book import Book
from checkbook.model import Transaction, new_id
from checkbook.store import TABS, ConflictError, SheetsBackend, Store

A1 = re.compile(r"^'([^']+)'!([A-Z]+)(\d+)(?::([A-Z]+)(\d+)?)?$")


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeSheets:
    """Tabs are lists of rows; trailing empty cells are trimmed like the real API."""

    def __init__(self):
        self.tabs = {t: [list(h)] for t, h in TABS.items()}
        self.tabs["Meta"].append(["revision", "new"])

    # chaining: service.spreadsheets().values().get(...)
    def spreadsheets(self):
        return self

    def values(self):
        return self

    def _parse(self, rng):
        m = A1.match(rng)
        assert m, rng
        tab, c1, r1, c2, r2 = m.groups()
        r1 = int(r1)
        r2 = int(r2) if r2 else (r1 if c2 is None else None)
        ncols = (ord(c2) - ord(c1) + 1) if c2 else 1
        return tab, ord(c1) - ord("A"), ncols, r1, r2

    def _read(self, rng):
        tab, c0, nc, r1, r2 = self._parse(rng)
        rows = self.tabs[tab]
        end = len(rows) if r2 is None else min(r2, len(rows))
        out = []
        for r in rows[r1 - 1:end]:
            cells = [str(v) for v in r[c0:c0 + nc]]
            while cells and cells[-1] == "":
                cells.pop()
            out.append(cells)
        while out and not out[-1]:
            out.pop()
        return {"range": rng, "values": out} if out else {"range": rng}

    def _write(self, rng, values):
        tab, c0, _, r1, _ = self._parse(rng)
        rows = self.tabs[tab]
        for i, v in enumerate(values):
            n = r1 - 1 + i
            while len(rows) <= n:
                rows.append([])
            row = rows[n] + [""] * max(0, c0 + len(v) - len(rows[n]))
            row[c0:c0 + len(v)] = v
            rows[n] = row

    def get(self, spreadsheetId, range, **kw):
        return _Req(lambda: self._read(range))

    def batchGet(self, spreadsheetId, ranges, **kw):
        return _Req(lambda: {"valueRanges": [self._read(r) for r in ranges]})

    def update(self, spreadsheetId, range, valueInputOption, body):
        assert valueInputOption == "RAW"
        return _Req(lambda: self._write(range, body["values"]) or {})

    def batchUpdate(self, spreadsheetId, body):
        assert body["valueInputOption"] == "RAW"
        return _Req(lambda: [self._write(d["range"], d["values"]) for d in body["data"]] and {})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        tab = self._parse(range)[0]

        def go():
            first = len(self.tabs[tab]) + 1
            self.tabs[tab].extend([list(r) for r in body["values"]])
            last = first + len(body["values"]) - 1
            return {"updates": {"updatedRange": f"'{tab}'!A{first}:N{last}"}}
        return _Req(go)


def test_sheets_backend_end_to_end():
    fake = FakeSheets()
    pc1 = Book(Store(SheetsBackend(fake, "sid")))
    pc2 = Book(Store(SheetsBackend(fake, "sid")))
    pc1.reload()
    a = pc1.add_account("Checking", "Checking", D("100"))
    t = Transaction(new_id(), a.id, date(2026, 5, 1), D("-9.99"), "Store", "Household")
    pc1.add_transaction(t)
    pc2.reload()
    assert pc2.txn(t.id).amount == D("-9.99")
    assert pc2.txn(t.id).memo == ""          # trimmed trailing cells handled
    assert not pc2.has_remote_changes()

    pc1.update_transaction(pc1.txn(t.id).copy(memo="first"))
    assert pc2.has_remote_changes()
    with pytest.raises(ConflictError):
        pc2.update_transaction(pc2.txn(t.id).copy(memo="second"))
    assert pc2.txn(t.id).memo == "first"

    pc2.delete_transaction(pc2.txn(t.id))
    pc1.reload()
    assert pc1.txn(t.id).deleted
