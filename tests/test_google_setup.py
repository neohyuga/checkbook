import json

import pytest

from checkbook import google_sheets as gs
from checkbook.store import TABS


def test_extract_sheet_id():
    sid = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-xy"
    assert gs.extract_sheet_id(f"https://docs.google.com/spreadsheets/d/{sid}/edit#gid=0") == sid
    assert gs.extract_sheet_id(f"  {sid} ") == sid
    with pytest.raises(ValueError):
        gs.extract_sheet_id("https://example.com/whatever")


def test_install_key_validates(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "config_dir", lambda: tmp_path / "cfg")
    (tmp_path / "cfg").mkdir()
    bad = tmp_path / "client_secret.json"
    bad.write_text(json.dumps({"installed": {"client_id": "x"}}))
    with pytest.raises(ValueError):
        gs.install_key(str(bad))
    good = tmp_path / "key.json"
    good.write_text(json.dumps({"type": "service_account", "client_email": "robot@example.test",
                                "private_key": "not-a-real-key"}))
    assert gs.install_key(str(good)) == "robot@example.test"
    assert (tmp_path / "cfg" / gs.KEY_NAME).exists()


class _R:
    def __init__(self, v):
        self.v = v

    def execute(self):
        return self.v() if callable(self.v) else self.v


class FakeApi:
    """Just enough of spreadsheets() for prepare_sheet."""

    def __init__(self):
        self.sheets = {"Sheet1": 0}
        self.cells = {}
        self.requests = []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId, fields=None, range=None):
        if range is not None:
            return _R({})
        return _R({"properties": {"title": "Checkbook"},
                   "sheets": [{"properties": {"title": t, "sheetId": i}} for t, i in self.sheets.items()]})

    def batchUpdate(self, spreadsheetId, body):
        if "data" in body:
            for d in body["data"]:
                self.cells[d["range"]] = d["values"]
            return _R({})
        replies = []
        for r in body["requests"]:
            self.requests.append(r)
            if "addSheet" in r:
                t = r["addSheet"]["properties"]["title"]
                self.sheets[t] = len(self.sheets) + 10
                replies.append({"addSheet": {"properties": {"sheetId": self.sheets[t]}}})
            if "deleteSheet" in r:
                self.sheets = {k: v for k, v in self.sheets.items() if v != r["deleteSheet"]["sheetId"]}
        return _R({"replies": replies})


def test_prepare_sheet(monkeypatch):
    api = FakeApi()
    monkeypatch.setattr(gs, "_sheets", lambda creds: api)
    assert gs.prepare_sheet(None, "sid") == "Checkbook"
    assert set(api.sheets) == set(TABS)                 # tabs added, empty Sheet1 removed
    assert api.cells["'Transactions'!A1"] == [TABS["Transactions"]]
    assert api.cells["'Meta'!A2:B3"][0] == ["revision", "new"]
    n = len(api.requests)
    gs.prepare_sheet(None, "sid")                       # second time: nothing to do
    assert len(api.requests) == n
