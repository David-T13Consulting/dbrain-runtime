import hashlib
import os
import subprocess
import sys
import unittest
from pathlib import Path

from dbrain import adapter, answer, config, scope

REPO = Path(__file__).resolve().parent.parent


def snapshot_vault(root):
    """Hash and stat every file in the vault, not only the allowlisted ones.

    Checking only allowed files would miss a write to `90 System/` or to a note the
    profile excludes - exactly the kind of escape the read-only claim is about. `.git`
    is skipped: git's own housekeeping touches it independently of this service.
    """
    out = {}
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            p = Path(dirpath) / name
            try:
                st = p.stat()
                out[str(p)] = (st.st_mtime_ns, st.st_size,
                               hashlib.sha256(p.read_bytes()).hexdigest())
            except (OSError, ValueError):
                out[str(p)] = "unreadable"
    return out


class ReadOnlyTest(unittest.TestCase):
    def test_an_in_process_query_changes_nothing_in_the_whole_vault(self):
        root = Path(config.VAULT)
        before = snapshot_vault(root)
        self.assertGreater(len(before), 50, "vault snapshot too small to prove anything")
        result = answer.ask("What counts as a tempo session?", profile=config.DEFAULT,
                            model=adapter.StubAdapter(reply="[E1] tempo marathon pace"))
        self.assertTrue(result.status)
        self.assertEqual(before, snapshot_vault(root), "the vault changed during a query")

    def test_the_cli_including_tracing_changes_nothing_in_the_whole_vault(self):
        """The in-process test skips the trace writer. This runs the real entry point
        with tracing enabled, which is the path that actually writes something."""
        root = Path(config.VAULT)
        before = snapshot_vault(root)
        proc = subprocess.run(
            [sys.executable, "query", "What counts as a tempo session?", "--stub-model"],
            cwd=str(REPO), capture_output=True, text=True,
        )
        self.assertIn(proc.returncode, (0, 2), proc.stderr)
        self.assertEqual(before, snapshot_vault(root),
                         "the vault changed during a traced CLI query")

    def test_scope_module_opens_read_only_and_offers_no_writer(self):
        # Strip comments and docstring lines: prose naming a flag is not a use of it.
        raw = Path(scope.__file__).read_text()
        src = "\n".join(
            line.split("#", 1)[0] for line in raw.splitlines()
            if not line.strip().startswith(('"""', "'''", "#"))
        )
        for forbidden in ("O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND", "O_TRUNC"):
            self.assertNotIn(forbidden, src, "scope.py references %s" % forbidden)
        self.assertIn("os.O_RDONLY", src)
        for name in dir(scope):
            self.assertNotIn(name, ("write_text", "write", "unlink", "mkdir", "rmtree"))

    def test_traces_default_outside_the_vault(self):
        self.assertFalse(
            str(Path(config.LOGS).resolve()).startswith(
                str(Path(config.VAULT).resolve()) + os.sep),
            "the default trace directory is inside the vault")


if __name__ == "__main__":
    unittest.main()
