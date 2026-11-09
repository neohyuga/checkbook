"""The checkbook as the app sees it: in-memory data + operations that save to the Store."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from .model import (CLEARED, RECONCILED, UNCLEARED, Account, Category, Transaction, new_id,
                    transfer_category, make_transfer, DEFAULT_CATEGORIES)
from .store import ConflictError, Snapshot, Store


class Book:
    def __init__(self, store: Store):
        self.store = store
        self.snap = Snapshot()

    # ------------------------------------------------------------ loading
    def reload(self) -> Snapshot:
        self.snap = self.store.load()
        return self.snap

    def has_remote_changes(self) -> bool:
        return self.store.revision() != self.snap.revision

    def _after_write(self) -> None:
        # Our own write bumped the revision; reload so we see others' changes too.
        self.reload()

    # ------------------------------------------------------------ lookups
    @property
    def accounts(self) -> list[Account]:
        return self.snap.accounts

    @property
    def open_accounts(self) -> list[Account]:
        return [a for a in self.snap.accounts if not a.closed]

    @property
    def categories(self) -> list[Category]:
        return self.snap.categories

    @property
    def transactions(self) -> list[Transaction]:
        return self.snap.transactions

    def account(self, account_id: str) -> Account | None:
        return next((a for a in self.snap.accounts if a.id == account_id), None)

    def txn(self, txn_id: str) -> Transaction | None:
        return next((t for t in self.snap.transactions if t.id == txn_id), None)

    def partner(self, t: Transaction) -> Transaction | None:
        if not t.transfer_id:
            return None
        return next((o for o in self.snap.transactions
                     if o.transfer_id == t.transfer_id and o.id != t.id and not o.deleted), None)

    def payees(self) -> list[str]:
        seen = {}
        for t in self.snap.transactions:
            if t.payee and not t.deleted and not t.transfer_id:
                seen[t.payee.strip().lower()] = t.payee.strip()
        return sorted(seen.values(), key=str.lower)

    def category_names(self) -> list[str]:
        names = {c.name for c in self.snap.categories if not c.hidden}
        return sorted(names, key=str.lower)

    # ------------------------------------------------------------ accounts
    def add_account(self, name: str, kind: str, opening_balance: Decimal) -> Account:
        name = name.strip()
        if not name:
            raise ValueError("Please type a name for the account.")
        if any(a.name.lower() == name.lower() for a in self.snap.accounts):
            raise ValueError(f"There is already an account called '{name}'.")
        a = Account(new_id("a"), name, kind, opening_balance)
        self.store.save(new_accounts=[a])
        self._after_write()
        return a

    def update_account(self, a: Account) -> None:
        self.store.save(changed_accounts=[a])
        self._after_write()

    # ------------------------------------------------------------ categories
    def ensure_category(self, name: str, income: bool = False) -> None:
        name = name.strip()
        if not name or name.lower().startswith("transfer "):
            return
        if any(c.name.lower() == name.lower() for c in self.snap.categories):
            return
        self.store.save(new_categories=[Category(new_id("c"), name, income)])

    def add_default_categories(self) -> None:
        existing = {c.name.lower() for c in self.snap.categories}
        new = [Category(new_id("c"), n, inc) for n, inc in DEFAULT_CATEGORIES
               if n.lower() not in existing]
        if new:
            self.store.save(new_categories=new)
            self._after_write()

    def update_category(self, c: Category) -> None:
        self.store.save(changed_categories=[c])
        self._after_write()

    # ------------------------------------------------------------ transactions
    def add_transaction(self, t: Transaction) -> None:
        self.ensure_category(t.category, income=t.amount > 0)
        for s in t.splits:
            self.ensure_category(s.category, income=s.amount > 0)
        self.store.save(new_txns=[t])
        self._after_write()

    def update_transaction(self, t: Transaction) -> None:
        """Save an edited copy of a transaction (``t.modified`` = stamp when loaded)."""
        if t.transfer_id:
            raise ValueError("Use update_transfer for transfers")
        self.ensure_category(t.category, income=t.amount > 0)
        for s in t.splits:
            self.ensure_category(s.category, income=s.amount > 0)
        try:
            self.store.save(changed_txns=[t])
        finally:
            self._after_write()

    def add_transfer(self, from_acct: Account, to_acct: Account, when: date, amount: Decimal,
                     memo: str = "", number: str = "") -> None:
        if from_acct.id == to_acct.id:
            raise ValueError("Please pick two different accounts.")
        out, inc = make_transfer(from_acct, to_acct, when, amount, memo, number)
        self.store.save(new_txns=[out, inc])
        self._after_write()

    def update_transfer(self, t: Transaction, other_account: Account, when: date,
                        amount_out: Decimal, memo: str, number: str) -> None:
        """Edit one half of a transfer; the other half is kept in step.

        ``t`` is the (unmodified) half being edited.  ``amount_out`` is the
        signed amount for ``t``'s own account.  The other half may move to a
        different account.
        """
        partner = self.partner(t)
        mine = t.copy(date=when, amount=amount_out, memo=memo, number=number,
                      transfer_account_id=other_account.id, payee=other_account.name,
                      category=transfer_category(other_account.name, amount_out < 0))
        changed = [mine]
        my_acct = self.account(t.account_id)
        if partner is not None:
            changed.append(partner.copy(
                date=when, amount=-amount_out, memo=memo, account_id=other_account.id,
                status=partner.status if partner.account_id == other_account.id else UNCLEARED,
                payee=my_acct.name if my_acct else partner.payee,
                category=transfer_category(my_acct.name if my_acct else "?", amount_out > 0)))
        try:
            self.store.save(changed_txns=changed)
        finally:
            self._after_write()

    def delete_transaction(self, t: Transaction) -> None:
        changed = [t.copy(deleted=True)]
        p = self.partner(t)
        if p is not None:
            changed.append(p.copy(deleted=True))
        try:
            self.store.save(changed_txns=changed)
        finally:
            self._after_write()

    def set_status(self, txns: list[Transaction], status: str) -> None:
        changed = [t.copy(status=status) for t in txns if t.status != status]
        if not changed:
            return
        try:
            self.store.save(changed_txns=changed)
        finally:
            self._after_write()

    def toggle_cleared(self, t: Transaction) -> None:
        self.set_status([t], UNCLEARED if t.status == CLEARED else CLEARED)

    def finish_reconcile(self, account: Account, cleared: list[Transaction]) -> None:
        """Mark all checked-off transactions as Reconciled."""
        self.set_status(cleared, RECONCILED)


__all__ = ["Book", "ConflictError"]
