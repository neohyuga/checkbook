#!/usr/bin/env bash
# Publish a new version:   ./release.sh 1.0.1  "What changed (optional)"
# Runs the tests, checks no secrets are about to be published, bumps the
# version, commits, tags, pushes and creates the GitHub release.  Installed
# copies offer the update the next time they start.
set -euo pipefail
cd "$(dirname "$0")"

NEW="${1:-}"; NOTES="${2:-}"
[[ "$NEW" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "usage: ./release.sh X.Y.Z [notes]"; exit 1; }
command -v gh >/dev/null || { echo "The GitHub 'gh' command is needed (brew install gh; gh auth login)."; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "Commit or discard your changes first:"; git status --short; exit 1; }

OLD="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' checkbook/__init__.py)"
python3 - "$OLD" "$NEW" <<'PY' || { echo "New version must be higher than $OLD"; exit 1; }
import sys
t = lambda s: tuple(map(int, s.split(".")))
sys.exit(0 if t(sys.argv[2]) > t(sys.argv[1]) else 1)
PY

echo "== tests"
.venv/bin/python -m pytest -q

echo "== secret check"
if git ls-files | grep -Ei '\.(json|mny|ckb|pem|key)$'; then
    echo "STOP: the files above must never be published."; exit 1
fi
if git grep -nI -e 'BEGIN PRIVATE KEY' -e '"private_key_id"' -e 'iam.gserviceaccount.com"' -- . ':!release.sh'; then
    echo "STOP: key material found in tracked files."; exit 1
fi

echo "== $OLD -> $NEW"
sed -i "s/^__version__ = \".*\"/__version__ = \"$NEW\"/" checkbook/__init__.py
git commit -qam "Release $NEW"
git tag -a "v$NEW" -m "Checkbook $NEW"
git push -q origin HEAD "v$NEW"
if [ -n "$NOTES" ]; then
    gh release create "v$NEW" --title "Checkbook $NEW" --notes "$NOTES"
else
    gh release create "v$NEW" --title "Checkbook $NEW" --generate-notes
fi
echo "Published Checkbook $NEW."
