"""Quiet, best-effort updates from the project's GitHub main branch.

Only the Python standard library is needed so this can run before other imports.
"""

import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath


REPOSITORY = "konglingyi11/autodl-skills"
BRANCH = "main"
STATE_FILE = ".autodl-update.json"
LOCK_FILE = ".autodl-update.lock"
MANIFEST_FILE = "update-manifest.json"
CHECK_TIMEOUT = 2
DOWNLOAD_TIMEOUT = 4
GIT_TIMEOUT = 5
MAX_BYTES = 8 * 1024 * 1024
REQUIRED_FILES = {"SKILL.md", "README.md", MANIFEST_FILE,
                  "scripts/autodl_instance.py", "scripts/autodl_update.py"}
_RESTART_KEY = "_AUTODL_UPDATE_RESTARTED"


def managed(name):
    """Update the skill distribution, never credentials or operational files."""
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in name.split("/")) or "\\" in name:
        return False
    return (name in ("SKILL.md", "README.md", MANIFEST_FILE) or len(path.parts) == 2 and (
        path.parts[0] == "scripts" and path.suffix == ".py"
        or path.parts[0] == "references" and path.suffix == ".md"
        or path.parts[0] == "agents" and path.suffix in (".yaml", ".yml")))


def file_hash(data):
    # Git may convert text to CRLF on Windows; that is not a local content edit.
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _download(url, timeout):
    request = urllib.request.Request(url, headers={
        "User-Agent": "autodl-skills-updater", "Accept": "application/vnd.github+json",
    })
    deadline = time.monotonic() + timeout
    with urllib.request.urlopen(request, timeout=timeout) as response:
        chunks, size = [], 0
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("Update download timed out")
            chunk = response.read(65536)
            if not chunk:
                return b"".join(chunks)
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError("Update is too large")
            chunks.append(chunk)


def _read_url(url, timeout):
    # Isolate DNS/TLS stalls in a read-only helper; never kill a file update.
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--fetch", url, str(timeout)],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout + 0.5, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return result.stdout


def latest_revision():
    data = json.loads(_read_url(
        f"https://api.github.com/repos/{REPOSITORY}/commits/{BRANCH}", CHECK_TIMEOUT))
    revision = data["sha"]
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid GitHub revision")
    return revision


def _git(root, *args, timeout=GIT_TIMEOUT):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    result = subprocess.run(["git", "-C", str(root), *args], env=env,
                            stdin=subprocess.DEVNULL, capture_output=True,
                            timeout=timeout, check=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return result.stdout.decode("utf-8", errors="replace").strip()


@contextmanager
def _lock(root):
    path = root / LOCK_FILE
    try:
        if path.exists() and time.time() - path.stat().st_mtime > 120:
            path.unlink()
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        yield False
        return
    try:
        os.close(descriptor)
        yield True
    finally:
        path.unlink(missing_ok=True)


def _git_update(root, revision):
    if Path(_git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        return False
    origin = _git(root, "remote", "get-url", "origin").removesuffix(".git").rstrip("/")
    if origin not in (f"https://github.com/{REPOSITORY}", f"git@github.com:{REPOSITORY}"):
        return False
    before = _git(root, "rev-parse", "HEAD")
    if before == revision or _git(root, "branch", "--show-current") != BRANCH:
        return False
    # Never stash/reset local work, switch branches, or resolve conflicts here.
    if _git(root, "status", "--porcelain", "--untracked-files=normal"):
        return False
    _git(root, "fetch", "--quiet", "origin", revision)
    _git(root, "merge", "--ff-only", "--quiet", revision)
    return _git(root, "rev-parse", "HEAD") != before


def _package(revision):
    payload = _read_url(f"https://codeload.github.com/{REPOSITORY}/zip/{revision}", DOWNLOAD_TIMEOUT)
    files, total = {}, 0
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for item in archive.infolist():
            # GitHub archives have one top-level directory.
            _, separator, name = item.filename.partition("/")
            if not separator or item.is_dir() or not managed(name):
                continue
            if stat.S_ISLNK(item.external_attr >> 16) or name in files:
                raise ValueError("Invalid update entry")
            total += item.file_size
            if total > MAX_BYTES or len(files) >= 100:
                raise ValueError("Update is too large")
            files[name] = archive.read(item)
    if not REQUIRED_FILES.issubset(files):
        raise ValueError("Incomplete update")
    manifest = json.loads(files[MANIFEST_FILE])
    if manifest.get("files") != {name: file_hash(data) for name, data in files.items()
                                if name != MANIFEST_FILE}:
        raise ValueError("Update manifest does not match package")
    # Reject truncated/broken scripts before changing the installed copy.
    for name, data in files.items():
        if name.endswith(".py"):
            compile(data, name, "exec")
    return files


def _target(root, name):
    path = root / name
    if path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("Update target is outside the installation")
    return path


def _replace(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".autodl-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _installed_update(root, revision):
    state_path = _target(root, STATE_FILE)
    baseline = state_path if state_path.exists() else _target(root, MANIFEST_FILE)
    if not baseline.is_file():
        return False  # No trusted baseline: do not overwrite an unknown local copy.
    state = json.loads(baseline.read_text(encoding="utf-8"))
    if state.get("revision") == revision:
        return False
    previous = dict(state.get("files", {}))
    if baseline.name == MANIFEST_FILE:
        previous[MANIFEST_FILE] = file_hash(baseline.read_bytes())
    for name, digest in previous.items():
        if not managed(name):
            raise ValueError("Invalid managed file")
        path = _target(root, name)
        if not path.is_file() or file_hash(path.read_bytes()) != digest:
            return False  # Preserve local edits and deliberate deletions.
    files = _package(revision)
    # A newly introduced upstream file must not overwrite an untracked local file.
    if previous:
        for name, data in files.items():
            path = _target(root, name)
            if name not in previous and path.exists() and path.read_bytes() != data:
                return False
    changes = {name: files.get(name) for name in set(previous) | set(files)}
    changes[STATE_FILE] = json.dumps({
        "revision": revision, "files": {name: file_hash(data) for name, data in files.items()},
    }, sort_keys=True).encode("utf-8")
    originals, written = {}, []
    for name in changes:
        path = _target(root, name)
        originals[name] = path.read_bytes() if path.exists() else None
    try:
        for name, data in changes.items():
            path = _target(root, name)
            if data is None:
                path.unlink(missing_ok=True)
            else:
                _replace(path, data)
            written.append(name)
    except Exception:
        for name in reversed(written):
            path = _target(root, name)
            if originals[name] is None:
                path.unlink(missing_ok=True)
            else:
                _replace(path, originals[name])
        raise
    return any(originals[name] != data for name, data in changes.items() if name != STATE_FILE)


def build_manifest(root):
    """Explicit release preparation; this is never called by normal usage."""
    root = Path(root).resolve()
    files = {}
    for directory in (root, root / "scripts", root / "references", root / "agents"):
        if directory.is_dir():
            for path in directory.iterdir():
                name = path.relative_to(root).as_posix()
                if path.is_file() and name != MANIFEST_FILE and managed(name):
                    files[name] = file_hash(_target(root, name).read_bytes())
    _replace(root / MANIFEST_FILE, json.dumps({"schema": 1, "files": files},
                                            sort_keys=True, indent=2).encode("utf-8") + b"\n")


def maybe_update(root=None):
    """Return whether an update was applied; all failures are intentionally quiet."""
    if os.environ.get("AUTODL_AUTO_UPDATE") == "0":
        return False
    try:
        root = Path(root or Path(__file__).resolve().parent.parent).resolve()
        with _lock(root) as acquired:
            if not acquired:
                return False
            revision = latest_revision()
            return (_git_update(root, revision) if (root / ".git").exists()
                    else _installed_update(root, revision))
    except Exception:
        return False


def bootstrap():
    """Apply updates before importing application modules, then restart once."""
    if os.environ.pop(_RESTART_KEY, None) == "1":
        return
    if maybe_update():
        os.environ[_RESTART_KEY] = "1"
        try:
            os.execv(sys.executable, [sys.executable, *sys.argv])
        except OSError:
            os.environ.pop(_RESTART_KEY, None)


if __name__ == "__main__":
    if sys.argv[1:] == ["--build-manifest"]:
        build_manifest(Path(__file__).resolve().parent.parent)
    elif len(sys.argv) == 4 and sys.argv[1] == "--fetch":
        try:
            sys.stdout.buffer.write(_download(sys.argv[2], float(sys.argv[3])))
        except Exception:
            raise SystemExit(1) from None
    else:
        maybe_update()
