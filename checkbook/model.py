"""Core data model: accounts, categories, transactions and balance math.

Money is always handled as ``decimal.Decimal`` (never float).  A negative
amount is money leaving the account (check, payment, withdrawal); a positive
amount is money coming in (deposit).
"""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

# Transaction status values (same meaning as Microsoft Money)
UNCLEARED = ""
CLEARED = "C"      # seen on the bank statement / checked off
RECONCILED = "R"   # part of a finished reconcile; locked-ish

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def new_id(prefix: str = "t") -> str:
    """Time-ordered unique id, e.g. 't01730000000000a1b2c3'."""
    return f"{prefix}{int(time.time() * 1000):014d}{secrets.token_hex(3)}"


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_money(text: str | Decimal | int | float | None) -> Decimal:
    """Parse '1,234.50', '$12', '(5.00)', '-3' -> Decimal.  Raises ValueError."""
    if text is None:
        return ZERO
    if isinstance(text, Decimal):
        return text.quantize(CENT)
    if isinstance(text, (int, float)):
        return Decimal(str(text)).quantize(CENT)
    s = str(text).strip().replace(",", "").replace("$", "").replace(" ", "")
    if not s:
        return ZERO
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"Not a valid amount: {text!r}") from None
    if not d.is_finite():
        raise ValueError(f"Not a valid amount: {text!r}")
    d = d.quantize(CENT)
    return -d if neg else d


def format_money(d: Decimal, blank_zero: bool = False) -> str:
    if blank_zero and d == 0:
        return ""
    s = f"{abs(d):,.2f}"
    return f"-${s}" if d < 0 else f"${s}"


@dataclass
class Account:
    id: str
    name: str
    kind: str = "Checking"            # Checking, Savings, Credit Card, Cash, Other
    opening_balance: Decimal = ZERO
    closed: bool = False
    modified: str = ""


@dataclass
class Category:
    id: str
    name: str                          # "Food" or "Food : Groceries"
    income: bool = False
    hidden: bool = False


@dataclass
class Split:
    category: str
    amount: Decimal
    memo: str = ""

    def to_json(self) -> dict:
        return {"category": self.category, "amount": str(self.amount), "memo": self.memo}

    @staticmethod
    def from_json(d: dict) -> "Split":
        return Split(d.get("category", ""), parse_money(d.get("amount", "0")), d.get("memo", ""))


@dataclass
class Transaction:
    id: str
    account_id: str
    date: date
    amount: Decimal                    # negative = payment, positive = deposit
    payee: str = ""
    category: str = ""
    memo: str = ""
    number: str = ""                   # check number, or "ATM", "DEP", ...
    status: str = UNCLEARED
    transfer_id: str = ""              # shared by both halves of a transfer
    transfer_account_id: str = ""      # the *other* account of a transfer
    splits: list[Split] = field(default_factory=list)
    modified: str = ""
    deleted: bool = False

    @property
    def payment(self) -> Decimal:
        return -self.amount if self.amount < 0 else ZERO

    @property
    def deposit(self) -> Decimal:
        return self.amount if self.amount > 0 else ZERO

    @property
    def is_transfer(self) -> bool:
        return bool(self.transfer_id)

    def copy(self, **changes) -> "Transaction":
        t = replace(self, **changes)
        if "splits" not in changes:
            t.splits = [replace(s) for s in self.splits]
        return t

    def splits_json(self) -> str:
        return json.dumps([s.to_json() for s in self.splits]) if self.splits else ""


def sort_key(t: Transaction):
    return (t.date, t.id)


@dataclass
class RegisterLine:
    txn: Transaction
    balance: Decimal


def register(account: Account, txns: list[Transaction]) -> list[RegisterLine]:
    """Transactions for one account in date order with a running balance."""
    bal = account.opening_balance
    out = []
    for t in sorted((t for t in txns if t.account_id == account.id and not t.deleted), key=sort_key):
        bal += t.amount
        out.append(RegisterLine(t, bal))
    return out


def balances(account: Account, txns: list[Transaction]) -> tuple[Decimal, Decimal]:
    """(current balance, cleared balance) for an account."""
    total = cleared = account.opening_balance
    for t in txns:
        if t.account_id != account.id or t.deleted:
            continue
        total += t.amount
        if t.status in (CLEARED, RECONCILED):
            cleared += t.amount
    return total, cleared


def reconciled_balance(account: Account, txns: list[Transaction]) -> Decimal:
    """Balance of everything already reconciled ('R') - the statement starting point."""
    bal = account.opening_balance
    for t in txns:
        if t.account_id == account.id and not t.deleted and t.status == RECONCILED:
            bal += t.amount
    return bal


def category_totals(txns: list[Transaction], start: date, end: date,
                    account_ids: set[str] | None = None) -> dict[str, Decimal]:
    """Sum of amounts per category between start and end (inclusive).

    Transfers between accounts are excluded (they are not income or spending).
    Split transactions contribute each split to its own category.
    """
    totals: dict[str, Decimal] = {}
    for t in txns:
        if t.deleted or t.is_transfer or not (start <= t.date <= end):
            continue
        if account_ids is not None and t.account_id not in account_ids:
            continue
        parts = t.splits or [Split(t.category, t.amount)]
        for s in parts:
            name = s.category or "(No category)"
            totals[name] = totals.get(name, ZERO) + s.amount
    return totals


def next_check_number(account_id: str, txns: list[Transaction]) -> str:
    best = 0
    for t in txns:
        if t.account_id == account_id and not t.deleted and t.number.isdigit():
            best = max(best, int(t.number))
    return str(best + 1) if best else ""


def last_for_payee(payee: str, txns: list[Transaction]) -> Transaction | None:
    """Most recent transaction with this payee (used to auto-fill like Money did)."""
    p = payee.strip().lower()
    if not p:
        return None
    hits = [t for t in txns if not t.deleted and t.payee.strip().lower() == p]
    return max(hits, key=sort_key) if hits else None


def transfer_category(other_account_name: str, outgoing: bool) -> str:
    return f"Transfer {'to' if outgoing else 'from'}: {other_account_name}"


def make_transfer(from_acct: Account, to_acct: Account, when: date, amount: Decimal,
                  memo: str = "", number: str = "") -> tuple[Transaction, Transaction]:
    """Create the two linked halves of a transfer.  ``amount`` must be positive."""
    if amount <= 0:
        raise ValueError("Transfer amount must be more than zero")
    xid = new_id("x")
    stamp = now_stamp()
    out = Transaction(new_id(), from_acct.id, when, -amount, payee=to_acct.name,
                      category=transfer_category(to_acct.name, True), memo=memo, number=number,
                      transfer_id=xid, transfer_account_id=to_acct.id, modified=stamp)
    inc = Transaction(new_id(), to_acct.id, when, amount, payee=from_acct.name,
                      category=transfer_category(from_acct.name, False), memo=memo,
                      transfer_id=xid, transfer_account_id=from_acct.id, modified=stamp)
    return out, inc


DEFAULT_CATEGORIES = [
    ("Auto : Fuel", False), ("Auto : Insurance", False), ("Auto : Repairs", False),
    ("Bills : Cable/Internet", False), ("Bills : Electricity", False), ("Bills : Gas", False),
    ("Bills : Telephone", False), ("Bills : Water", False),
    ("Charitable Donations", False), ("Clothing", False),
    ("Food : Dining Out", False), ("Food : Groceries", False),
    ("Gifts", False), ("Healthcare : Doctor", False), ("Healthcare : Prescriptions", False),
    ("Healthcare : Insurance", False), ("Household", False), ("Housing : Mortgage/Rent", False),
    ("Housing : Repairs", False), ("Insurance", False), ("Taxes", False), ("Miscellaneous", False),
    ("Cash Withdrawal", False), ("Bank Charges", False),
    ("Income : Social Security", True), ("Income : Pension", True), ("Income : Interest", True),
    ("Income : Dividends", True), ("Income : Other", True), ("Income : Salary", True),
]
