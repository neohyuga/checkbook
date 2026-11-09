# Checkbook

A simple, large-print checkbook register to replace Microsoft Money Plus.
Runs on Linux and Windows. The data lives in a Google Sheet in your Google
account, so two computers can share it (even at the same time).

Features: multiple accounts, categories (with sub-categories and splits),
transfers between accounts, cleared / reconciled marks, "Balance with
Statement" (reconcile), spending-by-category report, one-time import from a
Microsoft Money `.mny` file, and a daily local backup.

---

## 1. Google setup (one time, about 10 minutes)

Checkbook uses a Google **service account**: a "robot" Google identity that
belongs to your Cloud project. You share one Google Sheet with the robot, and
Checkbook (holding the robot's key file) can read and write that sheet and
nothing else. There is no browser sign-in and nothing expires.

1. Go to <https://console.cloud.google.com/>, select the project **Checkbook App**
   in the bar at the top.
2. Enable the **Google Sheets API**:
   <https://console.cloud.google.com/apis/library/sheets.googleapis.com> → **Enable**.
   (The Drive API is not needed.)
3. Open **IAM & Admin → Service Accounts**
   (<https://console.cloud.google.com/iam-admin/serviceaccounts>) → **Create service account**.
   Name: `checkbook`. Click **Create and continue**, then skip the optional
   "roles" and "users" steps (press **Continue** / **Done**). No roles are needed.
4. Click the new service account → **Keys** tab → **Add key → Create new key → JSON → Create**.
   A `.json` file downloads. This is the key: keep it private, like a password.
5. In Google Sheets (signed in as the account that will own the checkbook),
   create a **blank spreadsheet**, name it e.g. `Checkbook`.

## 2. Running it

**Linux:** `./run.sh` (sets itself up the first time; needs Python 3.11+).

The first time, Checkbook shows a setup window:

1. **Choose the key file** you downloaded. Checkbook copies it to its settings
   folder (`~/.config/Checkbook/` on Linux, `%APPDATA%\Checkbook\` on Windows);
   you can then delete the downloaded copy.
2. Press **Copy** next to the robot's email address. In the spreadsheet press
   **Share**, paste the address, choose **Editor**, untick "Notify people", **Send**.
3. Copy the spreadsheet's address from the browser bar, paste it into
   Checkbook, press **Connect**. Checkbook adds its tabs to the sheet.

File → *Connect to a spreadsheet...* reopens this window later.

**Testing without Google:** `./run.sh --local ~/checkbook-test.json` uses a
local file instead (one computer only).

## Installing on other Linux computers (e.g. Aurora, Bazzite, Silverblue)

Everything installs inside the user's home folder; nothing needs admin
rights or changes the system, so it works on immutable systems.

1. On a computer where Checkbook already works: **File → Make a setup file for
   another computer...** and save `checkbook-setup.json` to a USB stick.
   (It contains the key: don't email it, delete it when done.)
2. On the new computer, signed in as the person who will use it, open a terminal and run:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/neohyuga/checkbook/main/install.sh | bash -s -- /path/to/checkbook-setup.json
   ```
   That downloads the latest release, sets it up (a few minutes the first
   time), connects it to the checkbook, and puts **Checkbook** in the
   application menu and on the desktop.
3. Delete the setup file from the USB stick and the computer.

Remove it again with `~/.local/share/checkbook/versions/*/install.sh --uninstall`
(settings, key and backups in `~/.config/Checkbook` are kept).

## Updates

Installed copies check for a new version each time they start. If there is
one, they ask **"Update now?"**, update themselves in about a minute and
reopen. (Help → *Check for updates* does it on demand.) The previous version
is kept on disk, so a failed update never breaks the working copy.

To publish a new version (on the development computer, after committing):

```bash
./release.sh 1.0.1 "Bigger buttons on the reconcile screen"
```

It runs the tests, refuses to publish if a key or data file is tracked, bumps
the version, tags, pushes, and creates the GitHub release.

## Windows (later)

`build_windows.bat` builds `dist\Checkbook.exe` on a Windows PC with Python
installed. Self-updating is Linux-only for now.

## 3. Importing from Microsoft Money (one time)

Do this on the Linux computer, right after connecting Checkbook to the new, empty spreadsheet:

```bash
.venv/bin/python -m checkbook.importer_mny money.mny --dry-run   # look first
.venv/bin/python -m checkbook.importer_mny money.mny             # then import
```

It asks for the Money password (not shown on screen), prints a summary per
account (and checks it against Money's last reconciled statement), then asks
before saving. It refuses to import into a checkbook that already has
transactions, so nothing can be doubled.

The importer uses a small Java helper and the Jackcess library in `tools/`
(a portable JDK is in `tools/jdk`; to re-download, see the `curl` commands in
the project history or use any Java 17+ on the PATH with the three jars in
`tools/lib`: jackcess 5.0.4, jackcess-encrypt 5.0.2, bcprov-jdk18on 1.86).

Imported: bank, credit card, cash and other accounts (closed ones are kept but
hidden), with payee, category, memo, check number, cleared/reconciled status,
splits and transfers. Skipped: investment accounts and trades, scheduled bill
reminders, budgets.

## How two computers stay in step

- Each computer checks Google every 30 seconds and refreshes if the other one
  saved something.
- New entries never collide.
- If both computers change *the same* entry, the second save is refused with
  a friendly message and the newest version is shown, so nothing is silently
  overwritten.
- Please don't sort, insert or delete rows directly in the Google Sheet; open
  it to look (File → Open the spreadsheet in a browser) but make changes in
  Checkbook.

## Backups

Every time Checkbook opens, it saves a copy of everything to
`~/.config/Checkbook/backups/` (Windows: `%APPDATA%\Checkbook\backups\`),
keeping the last 30 days. Google Sheets also keeps its own version history
(File → Version history in the sheet).

## For developers

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/python -m checkbook --local demo.json   # run without Google
```

Code map: `checkbook/model.py` (money math), `store.py` (sheet layout, conflict
detection, Google/JSON backends), `book.py` (operations), `google_sheets.py`
(service-account key, preparing the shared sheet), `importer_mny.py`, `ui/` (PySide6 windows).
