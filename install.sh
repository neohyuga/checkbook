#!/usr/bin/env bash
# Install (or reinstall) Checkbook for the current user on Linux.
# Nothing is installed system-wide, so this works on immutable systems such as
# Aurora / Bazzite / Silverblue.  Needs: python3 (3.11+), curl, internet.
#
#   ./install.sh [checkbook-setup.json]      install from this folder
#   curl -fsSL https://raw.githubusercontent.com/neohyuga/checkbook/main/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- ~/checkbook-setup.json
#   ./install.sh --uninstall                 remove the app (keeps settings and backups)
set -euo pipefail

REPO="neohyuga/checkbook"          # same as GITHUB_REPO in checkbook/__init__.py
ROOT="$HOME/.local/share/checkbook"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/scalable/apps"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "$ROOT" "$APPS/checkbook.desktop" "$ICONS/checkbook.svg" "$DESKTOP_DIR/checkbook.desktop"
    say "Checkbook was removed. (Settings, key and backups are still in ~/.config/Checkbook)"
    exit 0
fi
SETUP_FILE="${1:-}"
[ -z "$SETUP_FILE" ] || [ -f "$SETUP_FILE" ] || die "Setup file not found: $SETUP_FILE"

command -v python3 >/dev/null || die "python3 is not installed."
python3 - <<'PY' || die "Python 3.11 or newer is needed."
import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)
PY

# ---- get the app's files: this folder if we are in a copy of the project, else the latest release
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
HERE=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "$(dirname "${BASH_SOURCE[0]}")/checkbook/__init__.py" ]; then
    HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
if [ -n "$HERE" ]; then
    SRC="$HERE"
else
    say "Downloading the latest Checkbook..."
    URL="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" |
           python3 -c 'import json,sys; print(json.load(sys.stdin)["tarball_url"])')"
    curl -fsSL "$URL" -o "$WORK/release.tar.gz"
    mkdir "$WORK/src" && tar -xzf "$WORK/release.tar.gz" -C "$WORK/src" --strip-components=1
    SRC="$WORK/src"
fi
VERSION="$(python3 -c 'import re,sys; print(re.search(r"__version__ = \"([^\"]+)\"", open(sys.argv[1]).read()).group(1))' "$SRC/checkbook/__init__.py")"

say "Installing Checkbook $VERSION into $ROOT"
mkdir -p "$ROOT/versions" "$ROOT/bin"
DEST="$ROOT/versions/$VERSION"
rm -rf "$DEST" && mkdir -p "$DEST"
cp -r "$SRC/checkbook" "$SRC/requirements.txt" "$SRC/install.sh" "$SRC/README.md" "$DEST/"
find "$DEST" -name '__pycache__' -prune -exec rm -rf {} +

if [ ! -x "$ROOT/venv/bin/python" ]; then
    say "Setting up Python (first time only, a few minutes)..."
    python3 -m venv "$ROOT/venv"
fi
"$ROOT/venv/bin/python" -m pip install -q --upgrade pip
"$ROOT/venv/bin/python" -m pip install -q -r "$DEST/requirements.txt"
ln -sfn "versions/$VERSION" "$ROOT/current"

cat > "$ROOT/bin/checkbook" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$ROOT/current"
exec "$ROOT/venv/bin/python" -P -m checkbook "\$@"
EOF
chmod +x "$ROOT/bin/checkbook"

# ---- menu entry + desktop icon
mkdir -p "$APPS" "$ICONS"
cp "$DEST/checkbook/resources/icon.svg" "$ICONS/checkbook.svg"
cat > "$APPS/checkbook.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Checkbook
Comment=Checkbook register (shared through Google Sheets)
Exec=$ROOT/bin/checkbook
Icon=checkbook
Terminal=false
Categories=Office;Finance;
StartupWMClass=checkbook
EOF
if [ -d "$DESKTOP_DIR" ]; then
    cp "$APPS/checkbook.desktop" "$DESKTOP_DIR/checkbook.desktop"
    chmod +x "$DESKTOP_DIR/checkbook.desktop"
    command -v gio >/dev/null && gio set "$DESKTOP_DIR/checkbook.desktop" metadata::trusted true 2>/dev/null || true
fi
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true

# ---- connect to the checkbook (optional)
if [ -n "$SETUP_FILE" ]; then
    "$ROOT/bin/checkbook" --import-setup "$SETUP_FILE"
    say "For safety, delete the setup file now:  rm \"$SETUP_FILE\""
fi

say "Done! Open Checkbook from the menu or the desktop icon."
