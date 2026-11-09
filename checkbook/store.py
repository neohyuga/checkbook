"""Storage: rows in a spreadsheet-like table, with safe concurrent editing.

Layout (one tab per table, first row is the header):

  Accounts      id, name, kind, opening_balance, closed, modified
  Categories    id, name, income, hidden
  Transactions  id, account_id, date, number, payee, category, memo, amount,
                status, transfer_id, transfer_account_id, splits, modified, deleted
  Meta          key, value          (holds 'revision', changed on every write)

Rows are never physically removed or re-ordered (deletes are a 'deleted'
flag), so a row number stays valid.  Every update re-reads the target rows and
compares the 'modified' stamp first; if another computer changed the row in the
meantime a ``ConflictError`` is raised instead of overwriting their work.

Two backends implement the tiny ``Backend`` protocol: ``SheetsBackend``
(Google Sheets) and ``JsonBackend`` (a local file, for testing/offline demo).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from .model import (Account, Category, Split, Transaction, new_id, now_stamp,
                    parse_money)

ACCT_HEADERS = ["id", "name", "kind", "opening_balance", "closed", "modified"]
CAT_HEADERS = ["id", "name", "income", "hidden"]
TXN_HEADERS = ["id", "account_id", "date", "number", "payee", "category", "memo", "amount",
               "status", "transfer_id", "transfer_account_id", "splits", "modified", "deleted"]
META_HEADERS = ["key", "value"]

TABS = {"Accounts": ACCT_HEADERS, "Categories": CAT_HEADERS,
        "Transactions": TXN_HEADERS, "Meta": META_HEADERS}


class ConflictError(Exception):
    """Somebody else changed the same item since we loaded it."""


class StoreError(Exception):
    pass


# ---------------------------------------------------------------- row <-> object

def _b(v: str) -> bool:
    return str(v).strip().upper() in ("TRUE", "1", "YES", "Y", "X")


def _bs(v: bool) -> str:
    return "TRUE" if v else ""


def parse_date(s: str) -> date:
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Bad date {s!r}")


def acct_to_row(a: Account) -> list[str]:
    return [a.id, a.name, a.kind, str(a.opening_balance), _bs(a.closed), a.modified]


def row_to_acct(r: list[str]) -> Account:
    return Account(r[0], r[1], r[2] or "Checking", parse_money(r[3]), _b(r[4]), r[5])


def cat_to_row(c: Category) -> list[str]:
    return [c.id, c.name, _bs(c.income), _bs(c.hidden)]


def row_to_cat(r: list[str]) -> Category:
    return Category(r[0], r[1], _b(r[2]), _b(r[3]))


def txn_to_row(t: Transaction) -> list[str]:
    return [t.id, t.account_id, t.date.isoformat(), t.number, t.payee, t.category, t.memo,
            str(t.amount), t.status, t.transfer_id, t.transfer_account_id, t.splits_json(),
            t.modified, _bs(t.deleted)]


def row_to_txn(r: list[str]) -> Transaction:
    splits = [Split.from_json(d) for d in json.loads(r[11])] if r[11].strip() else []
    return Transaction(id=r[0], account_id=r[1], date=parse_date(r[2]), number=r[3], payee=r[4],
                       category=r[5], memo=r[6], amount=parse_money(r[7]),
                       status=r[8].strip().upper(), transfer_id=r[9], transfer_account_id=r[10],
                       splits=splits, modified=r[12], deleted=_b(r[13]))


# ---------------------------------------------------------------- backends

class Backend(Protocol):
    def read_all(self) -> dict[str, list[list[str]]]:
        """All tabs, *including* header row, keyed by tab name."""

    def read_rows(self, tab: str, rownums: list[int]) -> dict[int, list[str]]:
        """Specific 1-based sheet rows."""

    def write_rows(self, writes: dict[str, dict[int, list[str]]]) -> None:
        """Overwrite specific rows: {tab: {rownum: row}} - in a single request."""

    def append(self, tab: str, rows: list[list[str]]) -> int:
        """Append rows; return the 1-based row number of the first appended row."""

    def get_revision(self) -> str: ...

    def set_revision(self, rev: str) -> None: ...


def _pad(r: list, n: int) -> list[str]:
    r = ["" if v is None else str(v) for v in r]
    return (r + [""] * n)[:n]


class JsonBackend:
    """A local JSON file laid out exactly like the spreadsheet (for testing/offline)."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        if not os.path.exists(path):
            self._save({tab: [list(h)] for tab, h in TABS.items()} | {"_revision": ""})

    def _load(self) -> dict:
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: dict) -> None:
        d = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, self.path)

    def read_all(self):
        d = self._load()
        return {tab: d.get(tab, [list(TABS[tab])]) for tab in TABS}

    def read_rows(self, tab, rownums):
        rows = self._load()[tab]
        return {n: rows[n - 1] if 0 < n <= len(rows) else [] for n in rownums}

    def write_rows(self, writes):
        with self._lock:
            d = self._load()
            for tab, rows in writes.items():
                for n, row in rows.items():
                    d[tab][n - 1] = row
            self._save(d)

    def append(self, tab, rows):
        with self._lock:
            d = self._load()
            first = len(d[tab]) + 1
            d[tab].extend(rows)
            self._save(d)
            return first

    def get_revision(self):
        return self._load().get("_revision", "")

    def set_revision(self, rev):
        with self._lock:
            d = self._load()
            d["_revision"] = rev
            self._save(d)


_RANGE_ROW = re.compile(r"![A-Z]+(\d+)")


class SheetsBackend:
    """Google Sheets backend.  ``service`` is a googleapiclient sheets v4 service."""

    def __init__(self, service, spreadsheet_id: str):
        self.svc = service.spreadsheets()
        self.sid = spreadsheet_id

    @staticmethod
    def _last_col(n: int) -> str:
        return chr(ord("A") + n - 1)

    def _range(self, tab: str, row: int | None = None) -> str:
        last = self._last_col(len(TABS[tab]))
        if row is None:
            return f"'{tab}'!A1:{last}"
        return f"'{tab}'!A{row}:{last}{row}"

    def read_all(self):
        tabs = list(TABS)
        resp = self.svc.values().batchGet(spreadsheetId=self.sid,
                                          ranges=[self._range(t) for t in tabs],
                                          valueRenderOption="FORMATTED_VALUE").execute()
        out = {}
        for tab, vr in zip(tabs, resp.get("valueRanges", [])):
            out[tab] = vr.get("values", [])
        return out

    def read_rows(self, tab, rownums):
        if not rownums:
            return {}
        resp = self.svc.values().batchGet(spreadsheetId=self.sid,
                                          ranges=[self._range(tab, n) for n in rownums],
                                          valueRenderOption="FORMATTED_VALUE").execute()
        out = {}
        for n, vr in zip(rownums, resp.get("valueRanges", [])):
            vals = vr.get("values", [])
            out[n] = vals[0] if vals else []
        return out

    def write_rows(self, writes):
        data = [{"range": self._range(tab, n), "values": [row]}
                for tab, rows in writes.items() for n, row in rows.items()]
        if data:
            self.svc.values().batchUpdate(spreadsheetId=self.sid, body={
                "valueInputOption": "RAW", "data": data}).execute()

    def append(self, tab, rows):
        resp = self.svc.values().append(
            spreadsheetId=self.sid, range=f"'{tab}'!A1", valueInputOption="RAW",
            insertDataOption="INSERT_ROWS", body={"values": rows}).execute()
        rng = resp.get("updates", {}).get("updatedRange", "")
        m = _RANGE_ROW.search(rng)
        if not m:
            raise StoreError(f"Unexpected append response: {rng!r}")
        return int(m.group(1))

    def get_revision(self):
        resp = self.svc.values().get(spreadsheetId=self.sid, range="'Meta'!B2").execute()
        vals = resp.get("values", [])
        return vals[0][0] if vals and vals[0] else ""

    def set_revision(self, rev):
        self.svc.values().update(spreadsheetId=self.sid, range="'Meta'!A2:B2",
                                 valueInputOption="RAW",
                                 body={"values": [["revision", rev]]}).execute()


# ---------------------------------------------------------------- the store

@dataclass
class Snapshot:
    accounts: list[Account] = field(default_factory=list)
    categories: list[Category] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
    revision: str = ""
    problems: list[str] = field(default_factory=list)   # rows that could not be read


class Store:
    """Reads and writes accounts/categories/transactions through a Backend."""

    def __init__(self, backend: Backend):
        self.backend = backend
        self._rows: dict[str, dict[str, int]] = {tab: {} for tab in TABS}   # id -> rownum
        self._lock = threading.RLock()
        self.last_raw: dict | None = None

    # -- reading
    def load(self) -> Snapshot:
        with self._lock:
            data = self.backend.read_all()
            self.last_raw = data
            snap = Snapshot(revision=self.backend.get_revision())
            for tab, headers in TABS.items():
                rows = data.get(tab, [])
                if tab == "Meta":
                    continue
                if rows and [h.strip().lower() for h in rows[0][:len(headers)]] != headers:
                    raise StoreError(f"The '{tab}' tab has unexpected column headings. "
                                     "Please don't rearrange the columns in the spreadsheet.")
                index = {}
                for i, raw in enumerate(rows[1:], start=2):
                    r = _pad(raw, len(headers))
                    if not r[0].strip():
                        continue
                    try:
                        if tab == "Accounts":
                            snap.accounts.append(row_to_acct(r))
                        elif tab == "Categories":
                            snap.categories.append(row_to_cat(r))
                        else:
                            snap.transactions.append(row_to_txn(r))
                        index[r[0]] = i
                    except (ValueError, json.JSONDecodeError) as e:
                        snap.problems.append(f"{tab} row {i}: {e}")
                self._rows[tab] = index
            return snap

    def revision(self) -> str:
        return self.backend.get_revision()

    # -- writing
    def save(self, *, new_accounts=(), changed_accounts=(), new_categories=(),
             changed_categories=(), new_txns=(), changed_txns=()) -> None:
        """Write a group of changes.

        ``changed_*`` items must carry the ``modified`` stamp they had when they
        were loaded; it is checked against the sheet before writing and then
        replaced with a fresh stamp.  Raises ConflictError if any check fails
        (in which case nothing in ``changed_*`` is written).
        """
        with self._lock:
            stamp = now_stamp()
            writes: dict[str, dict[int, list[str]]] = {}

            checks = [("Accounts", a, acct_to_row) for a in changed_accounts] + \
                     [("Categories", c, cat_to_row) for c in changed_categories] + \
                     [("Transactions", t, txn_to_row) for t in changed_txns]
            by_tab: dict[str, list] = {}
            for tab, obj, conv in checks:
                by_tab.setdefault(tab, []).append((obj, conv))

            for tab, items in by_tab.items():
                width = len(TABS[tab])
                rownums = []
                for obj, _ in items:
                    n = self._rows[tab].get(obj.id)
                    if n is None:
                        raise ConflictError(f"Could not find '{obj.id}' in the spreadsheet.")
                    rownums.append(n)
                current = self.backend.read_rows(tab, rownums)
                for (obj, conv), n in zip(items, rownums):
                    cur = _pad(current.get(n, []), width)
                    if cur[0] != obj.id:
                        raise ConflictError("The spreadsheet rows were rearranged by someone. "
                                            "Please reload.")
                    if tab != "Categories" and cur[-2 if tab == "Transactions" else -1] != obj.modified:
                        raise ConflictError("This was changed on another computer.")
                    if tab != "Categories":
                        obj.modified = stamp
                    writes.setdefault(tab, {})[n] = conv(obj)

            if writes:
                self.backend.write_rows(writes)

            for tab, objs, conv in (("Accounts", new_accounts, acct_to_row),
                                    ("Categories", new_categories, cat_to_row),
                                    ("Transactions", new_txns, txn_to_row)):
                objs = list(objs)
                if not objs:
                    continue
                for o in objs:
                    if tab != "Categories":
                        o.modified = stamp
                first = self.backend.append(tab, [conv(o) for o in objs])
                for i, o in enumerate(objs):
                    self._rows[tab][o.id] = first + i

            self.backend.set_revision(new_id("r"))
