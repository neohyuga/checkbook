"""Self-update from GitHub Releases (Linux, for copies installed with install.sh).

Installed layout (per user, nothing system-wide - fine on immutable Fedora/Aurora):

    ~/.local/share/checkbook/
        venv/                   Python environment with PySide6 etc.
        versions/1.0.0/         the app's source for each installed version
        current -> versions/1.0.0
        bin/checkbook           launcher (used by the menu / desktop icon)

Updating = download the newer release, unpack it into versions/<new>, install
any new requirements, then switch the 'current' link in one step.  The old
version stays on disk (the last two are kept), so a failed update never breaks
the working copy.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from . import GITHUB_REPO, __version__

KEEP_VERSIONS = 2
_UA = {"User-Agent": "checkbook-updater", "Accept": "application/vnd.github+json"}


def parse_version(s: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", s or "")
    return tuple(int(n) for n in nums[:3]) or (0,)


def installed_root() -> Path | None:
    """The install folder if this copy was installed by install.sh, else None (developer copy)."""
    here = Path(__file__).resolve().parent.parent          # .../versions/<ver>
    root = here.parent.parent
    if here.parent.name == "versions" and (root / "venv").is_dir():
        return root
    return None


def latest_release(timeout: float = 8) -> dict | None:
    """{'version', 'tarball', 'notes'} of the newest published release, or None."""
    if GITHUB_REPO.startswith("OWNER/"):
        return None
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    return {"version": data["tag_name"].lstrip("vV"), "tarball": data["tarball_url"],
            "notes": data.get("body") or ""}


def update_available() -> dict | None:
    """Newer release info if one exists *and* this copy can update itself."""
    if installed_root() is None:
        return None
    rel = latest_release()
    if rel and parse_version(rel["version"]) > parse_version(__version__):
        return rel
    return None


def _download(url: str, dest: Path, timeout: float = 60) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "checkbook-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def install_tarball(tar_path: Path, version: str, root: Path) -> Path:
    """Unpack a release tarball into root/versions/<version> and make it current."""
    versions = root / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=versions) as tmp:
        with tarfile.open(tar_path) as tf:
            tf.extractall(tmp, filter="data")
        tops = [p for p in Path(tmp).iterdir() if p.is_dir()]
        src = tops[0] if len(tops) == 1 else Path(tmp)
        if not (src / "checkbook" / "__init__.py").exists():
            raise RuntimeError("The download does not look like a Checkbook release.")
        target = versions / version
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(src), target)

    current = root / "current"
    old_req = (current / "requirements.txt").read_text() if (current / "requirements.txt").exists() else ""
    new_req = (target / "requirements.txt").read_text()
    if new_req != old_req:
        subprocess.run([str(root / "venv" / "bin" / "python"), "-m", "pip", "install", "-q",
                        "-r", str(target / "requirements.txt")], check=True)

    # switch 'current' atomically
    tmp_link = root / "current.new"
    if tmp_link.is_symlink() or tmp_link.exists():
        tmp_link.unlink()
    tmp_link.symlink_to(Path("versions") / version)
    os.replace(tmp_link, current)

    # keep only the newest few versions (never the one now current)
    others = sorted((p for p in versions.iterdir() if p.is_dir() and p != target),
                    key=lambda p: parse_version(p.name), reverse=True)
    for old in others[KEEP_VERSIONS - 1:]:
        shutil.rmtree(old, ignore_errors=True)
    return target


def apply_update(rel: dict) -> None:
    root = installed_root()
    if root is None:
        raise RuntimeError("This copy of Checkbook was not installed with install.sh.")
    with tempfile.TemporaryDirectory() as tmp:
        tar_path = Path(tmp) / "release.tar.gz"
        _download(rel["tarball"], tar_path)
        install_tarball(tar_path, rel["version"], root)


def restart() -> None:
    root = installed_root()
    launcher = root / "bin" / "checkbook" if root else None
    if launcher and launcher.exists():
        os.execv(str(launcher), [str(launcher)])
