import io
import tarfile
from pathlib import Path

from checkbook import updater


def _release_tar(path: Path, version: str, req="PySide6==6.12.0\n"):
    """A tarball shaped like GitHub's: one top folder containing the project."""
    with tarfile.open(path, "w:gz") as tf:
        def add(name, text):
            data = text.encode()
            info = tarfile.TarInfo(f"owner-checkbook-abc123/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        add("checkbook/__init__.py", f'__version__ = "{version}"\n')
        add("requirements.txt", req)


def test_parse_version():
    assert updater.parse_version("v1.10.0") > updater.parse_version("1.9.3")
    assert updater.parse_version("1.0.0") == (1, 0, 0)


def test_install_switches_current_and_prunes(tmp_path, monkeypatch):
    root = tmp_path / "checkbook"
    (root / "venv").mkdir(parents=True)
    pip_calls = []
    monkeypatch.setattr(updater.subprocess, "run", lambda *a, **k: pip_calls.append(a))

    for v in ("1.0.0", "1.0.1", "1.0.2"):
        tar = tmp_path / f"{v}.tar.gz"
        _release_tar(tar, v)
        updater.install_tarball(tar, v, root)
        assert (root / "current").resolve() == (root / "versions" / v).resolve()
        assert (root / "current" / "checkbook" / "__init__.py").read_text().count(v)

    # requirements unchanged after the first install -> pip ran only once
    assert len(pip_calls) == 1
    # only the newest KEEP_VERSIONS versions remain
    assert sorted(p.name for p in (root / "versions").iterdir()) == ["1.0.1", "1.0.2"]


def test_new_requirements_trigger_pip(tmp_path, monkeypatch):
    root = tmp_path / "checkbook"
    (root / "venv").mkdir(parents=True)
    calls = []
    monkeypatch.setattr(updater.subprocess, "run", lambda *a, **k: calls.append(a))
    t1, t2 = tmp_path / "a.tar.gz", tmp_path / "b.tar.gz"
    _release_tar(t1, "1.0.0")
    _release_tar(t2, "1.1.0", req="PySide6==6.12.0\nnewlib==1.0\n")
    updater.install_tarball(t1, "1.0.0", root)
    updater.install_tarball(t2, "1.1.0", root)
    assert len(calls) == 2


def test_developer_copy_never_self_updates():
    assert updater.installed_root() is None
    assert updater.update_available() is None
