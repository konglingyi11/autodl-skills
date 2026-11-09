"""Updater tests use synthetic packages or local Git repositories, never GitHub."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import autodl_update as update


REVISION = "1" * 40
NEXT_REVISION = "2" * 40


def package(version="new"):
    files = {"SKILL.md": version.encode(), "README.md": b"instructions",
             "scripts/autodl_instance.py": b"VALUE = 1\n",
             "scripts/autodl_update.py": b"VALUE = 2\n"}
    files[update.MANIFEST_FILE] = json.dumps({
        "schema": 1, "files": {name: update.file_hash(data) for name, data in files.items()},
    }).encode()
    return files


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def install(self, files, revision=REVISION):
        for name, data in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.root / update.STATE_FILE).write_text(json.dumps({
            "revision": revision, "files": {name: update.file_hash(data) for name, data in files.items()},
        }), encoding="utf-8")

    def test_windows_line_endings_do_not_look_like_local_edits(self):
        self.assertEqual(update.file_hash(b"one\r\ntwo\r\n"), update.file_hash(b"one\ntwo\n"))

    def test_network_failure_is_silent_and_preserves_local_files(self):
        (self.root / "SKILL.md").write_text("local")
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(update, "latest_revision", side_effect=TimeoutError()), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertFalse(update.maybe_update(self.root))
        self.assertEqual(stdout.getvalue() + stderr.getvalue(), "")
        self.assertEqual((self.root / "SKILL.md").read_text(), "local")
        self.assertFalse((self.root / update.LOCK_FILE).exists())

    def test_each_use_checks_revision_even_when_already_current(self):
        self.install(package())
        with patch.object(update, "latest_revision", return_value=REVISION) as latest, \
                patch.object(update, "_package") as download:
            self.assertFalse(update.maybe_update(self.root))
            self.assertFalse(update.maybe_update(self.root))
        self.assertEqual(latest.call_count, 2)
        download.assert_not_called()

    def test_matching_release_records_revision_without_restarting(self):
        files = package()
        self.install(files)
        (self.root / update.STATE_FILE).unlink()
        with patch.object(update, "_package", return_value=files):
            self.assertFalse(update._installed_update(self.root, REVISION))
        self.assertEqual(json.loads((self.root / update.STATE_FILE).read_text())["revision"], REVISION)

    def test_installation_update_preserves_private_and_unmanaged_files(self):
        old = {**package("old"), "references/removed.md": b"old reference"}
        self.install(old)
        (self.root / ".env").write_text("private")
        (self.root / "receipt.json").write_text("receipt")
        with patch.object(update, "latest_revision", return_value=NEXT_REVISION), \
                patch.object(update, "_package", return_value=package()):
            self.assertTrue(update.maybe_update(self.root))
        self.assertEqual((self.root / "SKILL.md").read_bytes(), b"new")
        self.assertFalse((self.root / "references/removed.md").exists())
        self.assertEqual((self.root / ".env").read_text(), "private")
        self.assertEqual((self.root / "receipt.json").read_text(), "receipt")
        self.assertEqual(json.loads((self.root / update.STATE_FILE).read_text())["revision"], NEXT_REVISION)

    def test_local_edits_prevent_update(self):
        self.install(package("old"))
        (self.root / "SKILL.md").write_text("user edit")
        with patch.object(update, "latest_revision", return_value=NEXT_REVISION), \
                patch.object(update, "_package") as download:
            self.assertFalse(update.maybe_update(self.root))
        download.assert_not_called()
        self.assertEqual((self.root / "SKILL.md").read_text(), "user edit")

    def test_new_upstream_file_does_not_overwrite_untracked_local_file(self):
        self.install(package("old"))
        (self.root / "references").mkdir()
        (self.root / "references/new.md").write_text("user file")
        with patch.object(update, "_package", return_value={**package(), "references/new.md": b"upstream"}):
            self.assertFalse(update._installed_update(self.root, NEXT_REVISION))
        self.assertEqual((self.root / "references/new.md").read_text(), "user file")

    def test_first_update_can_bootstrap_a_non_git_copy(self):
        for name, data in package("old").items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        with patch.object(update, "_package", return_value=package()):
            self.assertTrue(update._installed_update(self.root, REVISION))
        self.assertTrue((self.root / update.STATE_FILE).exists())

    def test_unknown_installation_without_a_baseline_is_preserved(self):
        (self.root / "SKILL.md").write_text("custom local copy")
        with patch.object(update, "_package") as download:
            self.assertFalse(update._installed_update(self.root, REVISION))
        download.assert_not_called()
        self.assertEqual((self.root / "SKILL.md").read_text(), "custom local copy")

    def test_manifest_preserves_edits_before_the_first_update(self):
        for name, data in package("old").items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.root / "SKILL.md").write_text("user edit")
        with patch.object(update, "_package") as download:
            self.assertFalse(update._installed_update(self.root, REVISION))
        download.assert_not_called()

    def test_write_failure_rolls_back_changes(self):
        old = package("old")
        self.install(old)
        replace = update._replace
        calls = []

        def fail_once(path, data):
            calls.append(path)
            if len(calls) == 2:
                raise PermissionError("locked file")
            replace(path, data)

        with patch.object(update, "latest_revision", return_value=NEXT_REVISION), \
                patch.object(update, "_package", return_value=package()), \
                patch.object(update, "_replace", side_effect=fail_once):
            self.assertFalse(update.maybe_update(self.root))
        for name, data in old.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.assertEqual(json.loads((self.root / update.STATE_FILE).read_text())["revision"], REVISION)

    def test_concurrent_update_and_opt_out_do_not_start_requests(self):
        (self.root / update.LOCK_FILE).touch()
        with patch.object(update, "latest_revision") as latest:
            self.assertFalse(update.maybe_update(self.root))
            (self.root / update.LOCK_FILE).unlink()
            with patch.dict(os.environ, {"AUTODL_AUTO_UPDATE": "0"}):
                self.assertFalse(update.maybe_update(self.root))
        latest.assert_not_called()

    def test_stale_lock_is_recovered(self):
        path = self.root / update.LOCK_FILE
        path.touch()
        os.utime(path, (time.time() - 200, time.time() - 200))
        with patch.object(update, "latest_revision", side_effect=TimeoutError()) as latest:
            self.assertFalse(update.maybe_update(self.root))
        latest.assert_called_once_with()
        self.assertFalse(path.exists())

    def test_archive_is_pinned_filters_files_and_rejects_broken_scripts(self):
        def archive(files):
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w") as zipped:
                for name, contents in files.items():
                    zipped.writestr("release/" + name, contents)
            return data.getvalue()

        files = package()
        with patch.object(update, "_read_url", return_value=archive({
                **files, "../escape.py": b"bad", ".env": b"private"})) as download:
            self.assertEqual(update._package(REVISION), files)
        self.assertIn(REVISION, download.call_args.args[0])
        broken = {**files, "scripts/broken.py": b"not valid python !!!"}
        broken[update.MANIFEST_FILE] = json.dumps({"files": {
            name: update.file_hash(data) for name, data in broken.items() if name != update.MANIFEST_FILE
        }}).encode()
        with patch.object(update, "_read_url", return_value=archive(broken)):
            with self.assertRaises(SyntaxError):
                update._package(REVISION)
        with patch.object(update, "_read_url", return_value=archive({"SKILL.md": b"incomplete"})):
            with self.assertRaises(ValueError):
                update._package(REVISION)

    def test_network_helper_has_a_hard_deadline(self):
        with patch.object(update.subprocess, "run", side_effect=subprocess.TimeoutExpired("fetch", 2)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                update._read_url("https://api.github.com/", 2)
        self.assertEqual(run.call_args.kwargs["timeout"], 2.5)

    def test_successful_bootstrap_restarts_once_with_original_arguments(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(update, "maybe_update", return_value=True) as check, \
                patch.object(update.os, "execv") as restart:
            update.bootstrap()
            restart.assert_called_once_with(sys.executable, [sys.executable, *sys.argv])
            update.bootstrap()
            check.assert_called_once_with()

    def test_updated_command_executes_new_code_on_the_same_invocation(self):
        entry = self.root / "command.py"
        new_code = (
            "import os, autodl_update as update\n"
            "assert os.environ.get(update._RESTART_KEY) == '1'\n"
            "update.bootstrap()\n"
            "assert update._RESTART_KEY not in os.environ\n"
            "print('new command')\n")
        entry.write_text(
            "from pathlib import Path\nimport autodl_update as update\n"
            "def install():\n"
            f"    Path(__file__).write_text({new_code!r})\n"
            "    return True\n"
            "update.maybe_update = install\n"
            "update.bootstrap()\nprint('old command')\n", encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(Path(update.__file__).parent))
        env.pop(update._RESTART_KEY, None)
        result = subprocess.run([sys.executable, str(entry)], env=env, capture_output=True,
                                text=True, timeout=10, check=True)
        self.assertEqual(result.stdout.strip(), "new command")
        self.assertEqual(result.stderr, "")


class GitUpdateTests(unittest.TestCase):
    def test_git_updates_are_fast_forward_only_and_preserve_local_edits(self):
        # Use local repos and override only the public origin identity check.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            upstream, checkout = root / "upstream", root / "checkout"

            def git(path, *args):
                return subprocess.run(["git", "-C", str(path), *args], check=True,
                                      capture_output=True).stdout.decode().strip()

            upstream.mkdir()
            git(upstream, "init", "-b", "main")
            git(upstream, "config", "user.email", "fixture@example.invalid")
            git(upstream, "config", "user.name", "Fixture")
            (upstream / ".gitignore").write_text(update.LOCK_FILE + "\n")
            (upstream / "SKILL.md").write_text("old")
            git(upstream, "add", ".")
            git(upstream, "commit", "-m", "initial")
            subprocess.run(["git", "clone", str(upstream), str(checkout)], check=True, capture_output=True)
            (upstream / "SKILL.md").write_text("new")
            git(upstream, "commit", "-am", "update")
            revision = git(upstream, "rev-parse", "HEAD")
            original_git = update._git

            def local_git(path, *args, **kwargs):
                if args == ("remote", "get-url", "origin"):
                    return f"https://github.com/{update.REPOSITORY}.git"
                return original_git(path, *args, **kwargs)

            with patch.object(update, "_git", side_effect=local_git), \
                    patch.object(update, "latest_revision", return_value=revision):
                self.assertTrue(update.maybe_update(checkout))
                self.assertEqual((checkout / "SKILL.md").read_text(), "new")
                (checkout / "SKILL.md").write_text("local edit")
                (upstream / "SKILL.md").write_text("newer")
                git(upstream, "commit", "-am", "another update")
                with patch.object(update, "latest_revision", return_value=git(upstream, "rev-parse", "HEAD")):
                    self.assertFalse(update.maybe_update(checkout))
                self.assertEqual((checkout / "SKILL.md").read_text(), "local edit")


if __name__ == "__main__":
    unittest.main()
