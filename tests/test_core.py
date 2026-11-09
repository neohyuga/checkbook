from datetime import date
from decimal import Decimal as D

import pytest

from checkbook.book import Book
from checkbook.model import (CLEARED, RECONCILED, Split, Transaction, balances, category_totals,
                             new_id, next_check_number, parse_money, register, format_money)
from checkbook.store import ConflictError, JsonBackend, Store


def make_book(tmp_path, name="book.json"):
    return Book(Store(JsonBackend(str(tmp_path / name))))


def test_parse_money():
    assert parse_money("1,234.5") == D("1234.50")
    assert parse_money("$12") == D("12.00")
    assert parse_money("(5.00)") == D("-5.00")
    assert parse_money("") == D("0")
    with pytest.raises(ValueError):
        parse_money("abc")
    with pytest.raises(ValueError):
        parse_money("NaN")
    assert format_money(D("-1234.5")) == "-$1,234.50"


def test_add_and_balance(tmp_path):
    b = make_book(tmp_path)
    b.reload()
    a = b.add_account("Checking", "Checking", D("100.00"))
    b.add_transaction(Transaction(new_id(), a.id, date(2026, 1, 2), D("-30.00"), "Grocer",
                                  "Food : Groceries", number="101"))
    b.add_transaction(Transaction(new_id(), a.id, date(2026, 1, 1), D("50.00"), "SSA",
                                  "Income : Social Security"))
    lines = register(b.account(a.id), b.transactions)
    assert [l.balance for l in lines] == [D("150.00"), D("120.00")]
    assert next_check_number(a.id, b.transactions) == "102"
    assert "Food : Groceries" in b.category_names()


def test_reload_from_disk_roundtrip(tmp_path):
    b = make_book(tmp_path)
    b.reload()
    a = b.add_account("Checking", "Checking", D("0"))
    t = Transaction(new_id(), a.id, date(2026, 3, 4), D("-12.34"), 'Joe "the" Plumber',
                    "Split", memo="a,b", splits=[Split("Household", D("-10")), Split("Gifts", D("-2.34"))])
    b.add_transaction(t)
    b2 = make_book(tmp_path)
    b2.reload()
    t2 = b2.txn(t.id)
    assert t2.payee == 'Joe "the" Plumber' and t2.splits[1].amount == D("-2.34")
    totals = category_totals(b2.transactions, date(2026, 1, 1), date(2026, 12, 31))
    assert totals == {"Household": D("-10.00"), "Gifts": D("-2.34")}


def test_conflict_between_two_computers(tmp_path):
    pc1, pc2 = make_book(tmp_path), make_book(tmp_path)
    pc1.reload()
    a = pc1.add_account("Checking", "Checking", D("0"))
    t = Transaction(new_id(), a.id, date(2026, 1, 1), D("-5.00"), "A")
    pc1.add_transaction(t)
    pc2.reload()

    pc1.update_transaction(pc1.txn(t.id).copy(payee="Changed on PC1"))
    with pytest.raises(ConflictError):
        pc2.update_transaction(pc2.txn(t.id).copy(payee="Changed on PC2"))
    # after the conflict pc2 has reloaded and sees pc1's change; retry works
    assert pc2.txn(t.id).payee == "Changed on PC1"
    pc2.update_transaction(pc2.txn(t.id).copy(payee="Changed on PC2"))
    pc1.reload()
    assert pc1.txn(t.id).payee == "Changed on PC2"


def test_concurrent_adds_do_not_clash(tmp_path):
    pc1, pc2 = make_book(tmp_path), make_book(tmp_path)
    pc1.reload()
    a = pc1.add_account("Checking", "Checking", D("0"))
    pc2.reload()
    pc1.add_transaction(Transaction(new_id(), a.id, date(2026, 1, 1), D("-1"), "one"))
    pc2.add_transaction(Transaction(new_id(), a.id, date(2026, 1, 1), D("-2"), "two"))
    # pc2 had stale row map but appends never collide; editing pc1's row still works
    pc2.reload()
    one = next(t for t in pc2.transactions if t.payee == "one")
    pc2.update_transaction(one.copy(memo="ok"))
    pc1.reload()
    assert {t.payee for t in pc1.transactions} == {"one", "two"}
    assert pc1.has_remote_changes() is False


def test_transfer_kept_in_step(tmp_path):
    b = make_book(tmp_path)
    b.reload()
    chk = b.add_account("Checking", "Checking", D("500"))
    sav = b.add_account("Savings", "Savings", D("0"))
    b.add_transfer(chk, sav, date(2026, 2, 1), D("100"))
    out = next(t for t in b.transactions if t.account_id == chk.id)
    assert out.amount == D("-100.00") and b.partner(out).amount == D("100.00")
    b.update_transfer(out, sav, date(2026, 2, 2), D("-150"), "more", "")
    out = b.txn(out.id)
    assert b.partner(out).amount == D("150") and b.partner(out).date == date(2026, 2, 2)
    assert balances(b.account(chk.id), b.transactions)[0] == D("350.00")
    b.delete_transaction(out)
    assert all(t.deleted for t in b.transactions)
    assert balances(b.account(sav.id), b.transactions)[0] == D("0")
    # transfers are not counted as spending
    assert category_totals(b.transactions, date(2026, 1, 1), date(2026, 12, 31)) == {}


def test_reconcile(tmp_path):
    b = make_book(tmp_path)
    b.reload()
    a = b.add_account("Checking", "Checking", D("100"))
    for amt in ("-10", "-20", "30"):
        b.add_transaction(Transaction(new_id(), a.id, date(2026, 1, 1), D(amt), "x"))
    first_two = b.transactions[:2]
    b.set_status(first_two, CLEARED)
    total, cleared = balances(b.account(a.id), b.transactions)
    assert (total, cleared) == (D("100"), D("70"))
    b.finish_reconcile(b.account(a.id), [t for t in b.transactions if t.status == CLEARED])
    assert sorted(t.status for t in b.transactions) == ["", RECONCILED, RECONCILED]
