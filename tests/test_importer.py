"""Converter test using a tiny synthetic Money dump (no real financial data)."""
import csv
from decimal import Decimal as D

from checkbook.importer_mny import convert
from checkbook.model import RECONCILED, balances

NULL = "+10000-02-28T00:00"


def _w(d, name, rows):
    with open(d / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def acct(h, name, at="0", opening="0", closed="false"):
    return {"hacct": h, "szFull": name, "at": at, "amtOpen": opening, "fClosed": closed,
            "amtEndRec": "", "dtEndRec": NULL}


def trn(h, a, dt, amt, *, szId="", cs="0", hcat="", pay="", link="", frq="-1", grftt="0", memo="", hsec=""):
    return {"htrn": h, "hacct": a, "hacctLink": link, "dt": dt + "T00:00", "cs": cs, "hsec": hsec,
            "amt": amt, "szId": szId, "hcat": hcat, "frq": frq, "mMemo": memo, "grftt": grftt,
            "lHpay": pay}


def test_convert(tmp_path):
    _w(tmp_path, "ACCT", [acct("1", "Checking", opening="100"), acct("2", "Savings"),
                          acct("3", "Stocks", at="5")])
    _w(tmp_path, "CAT", [
        {"hcat": "130", "nLevel": "0", "hcatParent": "", "szFull": "INCOME", "fHidden": "false"},
        {"hcat": "131", "nLevel": "0", "hcatParent": "", "szFull": "EXPENSE", "fHidden": "false"},
        {"hcat": "140", "nLevel": "1", "hcatParent": "131", "szFull": "Bills", "fHidden": "false"},
        {"hcat": "141", "nLevel": "2", "hcatParent": "140", "szFull": "Water", "fHidden": "false"},
        {"hcat": "142", "nLevel": "1", "hcatParent": "130", "szFull": "Pension", "fHidden": "false"},
        {"hcat": "143", "nLevel": "1", "hcatParent": "131", "szFull": "Food", "fHidden": "false"}])
    _w(tmp_path, "PAY", [{"hpay": "1", "szFull": "Water Co"}, {"hpay": "2", "szFull": "Store"}])
    _w(tmp_path, "TRN", [
        trn("10", "1", "2026-01-02", "-20.0000", szId="0        1001", cs="2", hcat="141", pay="1"),
        trn("11", "1", "2026-01-03", "500.0000", szId="1DEP", cs="1", hcat="142"),
        trn("12", "1", "2026-01-04", "-30.0000", pay="2", grftt="32", memo="split"),
        trn("13", "1", "2026-01-04", "-25.0000", hcat="143", grftt="64"),
        trn("14", "1", "2026-01-04", "-5.0000", hcat="141", grftt="64"),
        trn("15", "1", "2026-01-05", "-50.0000", link="2", grftt="2"),
        trn("16", "2", "2026-01-05", "50.0000", link="1", grftt="6"),
        trn("17", "1", "2026-02-01", "-99.0000", frq="3"),              # scheduled bill template
        trn("18", "3", "2026-01-01", "-10.0000", hsec="5"),             # investment
        trn("19", "1", "2026-01-06", "0.0000", szId="0        1002", grftt="256", pay="2"),
    ])
    _w(tmp_path, "TRN_SPLIT", [{"htrn": "13", "htrnParent": "12", "iSplit": "0"},
                               {"htrn": "14", "htrnParent": "12", "iSplit": "1"}])
    _w(tmp_path, "TRN_XFER", [{"htrnFrom": "15", "htrnLink": "16"}])

    res = convert(str(tmp_path))
    assert [a.name for a in res.accounts] == ["Checking", "Savings"]
    names = {c.name: c.income for c in res.categories}
    assert names["Bills : Water"] is False and names["Pension"] is True
    t = {x.id: x for x in res.transactions}
    assert set(t) == {f"m{n:010d}" for n in (10, 11, 12, 15, 16, 19)}
    first = t["m0000000010"]
    assert (first.number, first.category, first.payee, first.status) == ("1001", "Bills : Water",
                                                                         "Water Co", RECONCILED)
    assert t["m0000000011"].number == "DEP"
    split = t["m0000000012"]
    assert [(s.category, s.amount) for s in split.splits] == [("Food", D("-25.00")),
                                                              ("Bills : Water", D("-5.00"))]
    out, inc = t["m0000000015"], t["m0000000016"]
    assert out.transfer_id and out.transfer_id == inc.transfer_id
    assert out.category == "Transfer to: Savings" and inc.payee == "Checking"
    assert t["m0000000019"].memo == "VOID"
    chk = res.accounts[0]
    assert balances(chk, res.transactions)[0] == D("100") - 20 + 500 - 30 - 50
