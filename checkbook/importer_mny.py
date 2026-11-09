"""One-time import from a Microsoft Money (.mny) file.

    python -m checkbook.importer_mny money.mny               # into your Google checkbook
    python -m checkbook.importer_mny money.mny --local f.json  # into a local test file
    python -m checkbook.importer_mny money.mny --dry-run       # just show what would happen

The .mny file is opened read-only by a small Java helper (tools/MnyDump.java,
using the Jackcess library) which writes each table to CSV; this module then
turns the Money tables into Checkbook accounts, categories and transactions.

What is imported: bank / credit card / cash / other accounts (open and closed),
all their transactions with payee, category, memo, check number, cleared /
reconciled status, splits and transfers.  What is skipped: investment accounts
and security trades, scheduled (future) bill templates, budgets.
"""
from __future__ import annotations

import argparse
import csv
import getpass
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from .model import (CLEARED, RECONCILED, UNCLEARED, ZERO, Account, Category, Split, Transaction,
                    balances, parse_money, transfer_category)

# Money account types (ACCT.at)
AT_KIND = {"0": "Checking", "1": "Credit Card", "2": "Cash", "3": "Other", "6": "Other"}
AT_INVESTMENT = "5"
# TRN.grftt bit flags
F_TRANSFER, F_SPLIT_PARENT, F_SPLIT_CHILD, F_VOID = 0x2, 0x20, 0x40, 0x100
STATUS = {"0": UNCLEARED, "1": CLEARED, "2": RECONCILED}


@dataclass
class ImportResult:
    accounts: list[Account] = field(default_factory=list)
    categories: list[Category] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # acct id -> (Money's last statement ending balance, statement date)
    money_reconciled: dict[str, tuple[Decimal, date]] = field(default_factory=dict)


# ------------------------------------------------------------------ step 1: dump

def tools_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "tools"


def find_java() -> str:
    for p in (tools_dir() / "jdk" / "bin" / "java", tools_dir() / "jdk" / "bin" / "java.exe"):
        if p.exists():
            return str(p)
    j = shutil.which("java")
    if not j:
        raise RuntimeError("Java was not found. See README.md, 'Importing from Microsoft Money'.")
    return j


def dump_mny(mny: str, password: str, out_dir: str) -> None:
    td = tools_dir()
    cp = os.pathsep.join([str(td / "lib" / "*")])
    proc = subprocess.run([find_java(), "-cp", cp, str(td / "MnyDump.java"), mny, out_dir],
                          input=password + "\n", text=True, capture_output=True)
    if proc.returncode != 0:
        msg = proc.stderr.strip().splitlines()[-1:] or ["unknown error"]
        raise RuntimeError(f"Could not read the Money file: {msg[0]}")


# ------------------------------------------------------------------ step 2: convert

def _read(d: str, name: str) -> list[dict]:
    p = Path(d) / f"{name}.csv"
    if not p.exists():
        return []
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _date(s: str) -> date | None:
    if not s or s.startswith("+"):
        return None
    return date.fromisoformat(s[:10])


def _check_number(sz: str) -> str:
    """Money stores '0   2462' for check 2462 and '1ATM' for the text 'ATM'."""
    if not sz:
        return ""
    return sz[1:].strip() if sz[0] in "01" else sz.strip()


def convert(dump_dir: str) -> ImportResult:
    res = ImportResult()
    acct_rows, trn_rows = _read(dump_dir, "ACCT"), _read(dump_dir, "TRN")
    cat_rows, pay_rows = _read(dump_dir, "CAT"), _read(dump_dir, "PAY")
    if not acct_rows or not trn_rows:
        raise RuntimeError("The Money file has no accounts or transactions.")

    # ---- categories: build "Parent : Child" names under the INCOME / EXPENSE roots
    cats = {c["hcat"]: c for c in cat_rows}

    def cat_name(h: str) -> tuple[str, bool]:
        parts, income, cur, guard = [], False, cats.get(h), 0
        while cur is not None and guard < 10:
            if cur["nLevel"] == "0":
                income = cur["szFull"].upper() == "INCOME"
                break
            parts.append(cur["szFull"].strip())
            cur, guard = cats.get(cur["hcatParent"]), guard + 1
        return " : ".join(reversed(parts)), income

    cat_names: dict[str, tuple[str, bool]] = {h: cat_name(h) for h in cats}
    seen = set()
    for h, (name, income) in sorted(cat_names.items(), key=lambda kv: kv[1][0].lower()):
        if name and name.lower() not in seen:
            seen.add(name.lower())
            res.categories.append(Category(f"mc{h}", name, income,
                                           hidden=cats[h]["fHidden"] == "true"))

    payees = {p["hpay"]: p["szFull"].strip() for p in pay_rows}

    # ---- accounts
    accts: dict[str, Account] = {}
    used_names = set()
    for a in acct_rows:
        if a["at"] == AT_INVESTMENT:
            res.notes.append(f"Skipped investment account '{a['szFull']}'.")
            continue
        name = a["szFull"].strip() or f"Account {a['hacct']}"
        while name.lower() in used_names:
            name += " (2)"
        used_names.add(name.lower())
        kind = AT_KIND.get(a["at"], "Other")
        if kind == "Checking" and "sav" in name.lower():
            kind = "Savings"
        acc = Account(f"ma{a['hacct']}", name, kind, parse_money(a["amtOpen"] or "0"),
                      closed=a["fClosed"] == "true")
        accts[a["hacct"]] = acc
        res.accounts.append(acc)

    # ---- transactions
    by_id = {r["htrn"]: r for r in trn_rows}
    children: dict[str, list[dict]] = defaultdict(list)
    child_ids = set()
    for s in sorted(_read(dump_dir, "TRN_SPLIT"), key=lambda s: int(s["iSplit"] or 0)):
        if s["htrn"] in by_id:
            children[s["htrnParent"]].append(by_id[s["htrn"]])
            child_ids.add(s["htrn"])
    xfer_pair: dict[str, str] = {}
    for x in _read(dump_dir, "TRN_XFER"):
        xfer_pair[x["htrnFrom"]] = x["htrnLink"]
        xfer_pair[x["htrnLink"]] = x["htrnFrom"]

    skipped = defaultdict(int)
    for r in trn_rows:
        h = r["htrn"]
        if h in child_ids:
            continue                                  # folded into the parent's splits
        if r["frq"] != "-1":
            skipped["scheduled bills (not yet paid)"] += 1
            continue
        if r["hsec"]:
            skipped["investment trades"] += 1
            continue
        acc = accts.get(r["hacct"])
        if acc is None:
            skipped["in investment accounts"] += 1
            continue
        d = _date(r["dt"])
        if d is None:
            skipped["without a date"] += 1
            continue
        grftt = int(r["grftt"] or 0)
        amount = parse_money(r["amt"] or "0")
        cat, _ = cat_names.get(r["hcat"], ("", False))
        memo = r["mMemo"].strip()
        if grftt & F_VOID:
            memo = ("VOID " + memo).strip()
        t = Transaction(id=f"m{int(h):010d}", account_id=acc.id, date=d, amount=amount,
                        payee=payees.get(r["lHpay"], ""), category=cat, memo=memo,
                        number=_check_number(r["szId"]), status=STATUS.get(r["cs"], UNCLEARED))

        if h in children:
            t.splits = [Split(cat_names.get(c["hcat"], ("", False))[0], parse_money(c["amt"]),
                              c["mMemo"].strip())
                        for c in children[h] if parse_money(c["amt"] or "0") != 0]
            t.category = ""
            if sum((s.amount for s in t.splits), ZERO) != amount:
                res.notes.append(f"Split on {d} ({t.payee} {amount}) did not add up; kept as one line.")
                t.splits = []
            elif len(t.splits) == 1:
                t.category, t.splits = t.splits[0].category, []

        if r["hacctLink"]:
            other = accts.get(r["hacctLink"])
            partner = xfer_pair.get(h)
            if other is not None and partner and by_id.get(partner, {}).get("frq") == "-1":
                t.transfer_id = f"mx{min(int(h), int(partner))}"
                t.transfer_account_id = other.id
                t.category = transfer_category(other.name, amount < 0)
                t.payee = t.payee or other.name
            else:
                # the other side is an investment / skipped account: keep as a normal line
                link_name = next((a["szFull"] for a in acct_rows if a["hacct"] == r["hacctLink"]), "?")
                t.category = transfer_category(link_name, amount < 0)
                t.payee = t.payee or link_name
        res.transactions.append(t)

    # transfers must come in pairs; un-pair any orphan halves
    pairs = defaultdict(list)
    for t in res.transactions:
        if t.transfer_id:
            pairs[t.transfer_id].append(t)
    for xid, ts in pairs.items():
        if len(ts) != 2:
            for t in ts:
                t.transfer_id = t.transfer_account_id = ""

    for k, n in skipped.items():
        res.notes.append(f"Skipped {n} transactions: {k}.")

    # Money's own reconciled balance, to check our math
    for a in acct_rows:
        if a["hacct"] in accts and a["amtEndRec"] and _date(a["dtEndRec"]):
            res.money_reconciled[accts[a["hacct"]].id] = (parse_money(a["amtEndRec"]),
                                                          _date(a["dtEndRec"]))
    return res


def summary(res: ImportResult) -> str:
    lines = [f"{len(res.accounts)} accounts, {len(res.categories)} categories, "
             f"{len(res.transactions)} transactions", ""]
    for a in res.accounts:
        n = sum(1 for t in res.transactions if t.account_id == a.id)
        total, _ = balances(a, res.transactions)
        check = ""
        if a.id in res.money_reconciled:
            money, when = res.money_reconciled[a.id]
            rec = a.opening_balance + sum((t.amount for t in res.transactions
                                           if t.account_id == a.id and t.status == RECONCILED
                                           and t.date <= when), ZERO)
            check = (f"  matches Money's {when:%m/%Y} statement" if money == rec else
                     f"  (Money's {when:%m/%Y} statement: {money:,.2f}, here {rec:,.2f})")
        lines.append(f"  {a.name:<36} {a.kind:<12} {'closed' if a.closed else 'open  '} "
                     f"{n:>5} items  balance {total:>12,.2f}{check}")
    if res.notes:
        lines += ["", *res.notes]
    return "\n".join(lines)


# ------------------------------------------------------------------ step 3: save

def save_into(book, res: ImportResult) -> None:
    book.reload()
    if book.transactions:
        raise RuntimeError("This checkbook already has transactions. Import only into a new, "
                           "empty checkbook so nothing is duplicated.")
    existing = {c.name.lower() for c in book.categories}
    cats = [c for c in res.categories if c.name.lower() not in existing]
    book.store.save(new_accounts=res.accounts, new_categories=cats, new_txns=res.transactions)
    book.reload()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m checkbook.importer_mny", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mny", help="the Microsoft Money .mny file")
    ap.add_argument("--local", metavar="FILE", help="import into a local JSON file instead of Google")
    ap.add_argument("--dry-run", action="store_true", help="only show what would be imported")
    ap.add_argument("--dump-dir", help="use an existing CSV dump instead of reading the .mny")
    args = ap.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="mnydump-") as tmp:
        dump_dir = args.dump_dir
        if not dump_dir:
            pw = getpass.getpass("Microsoft Money password (just press Enter if none): ")
            print("Reading the Money file...")
            dump_mny(args.mny, pw, tmp)
            dump_dir = tmp
        res = convert(dump_dir)
    print(summary(res))
    if args.dry_run:
        return 0

    from .book import Book
    from .store import JsonBackend, Store
    if args.local:
        book = Book(Store(JsonBackend(args.local)))
    else:
        from . import google_sheets as gs
        from .config import Settings
        s = Settings()
        if not s.get("spreadsheet_id") or not gs.has_key():
            print("Start Checkbook once first and connect it to your spreadsheet, then run this again.")
            return 1
        book = Book(gs.open_store(gs.get_credentials(), s["spreadsheet_id"]))
        print(f"\nImporting into Google checkbook '{s.get('spreadsheet_name')}'")
    if input("\nGo ahead and import? (yes/no) ").strip().lower() not in ("y", "yes"):
        print("Nothing was imported.")
        return 0
    save_into(book, res)
    print("Import finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
