"""T4.2 restore test (PL1 step 5), narrow contract (Q148), on synthetic repositories only.

`REPOS` is patched to a temporary repository and a local bare remote; the real vault and
runtime are never touched by these tests. Test list from the D74 consult (session 55).
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "experiments"))

import restore_test as rt  # noqa: E402
from dbrain import config, outpath  # noqa: E402

REAL_REPOS = {k: dict(v) for k, v in rt.REPOS.items()}  # before any test patches the table


def sh(cwd, *args):
    return subprocess.run(["git"] + rt.GIT_ARGS + list(args), cwd=str(cwd), env=rt.git_env(),
                          capture_output=True, text=True, check=True).stdout.strip()


def local_config(repo):
    items = sh(repo, "config", "--local", "--list", "-z").split("\0")
    return dict(i.partition("\n")[::2] for i in items if i)


def all_bytes(root):
    out = {}
    for dirpath, _, names in os.walk(str(root)):
        for n in names:
            p = Path(dirpath) / n
            if not p.is_symlink():
                out[str(p.relative_to(root))] = p.read_bytes()
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(os.path.realpath(self._tmp.name))
        self.addCleanup(self._tmp.cleanup)
        self.remote = self.tmp / "remote.git"
        sh(self.tmp, "init", "--quiet", "--bare", "-b", "main", str(self.remote))
        self.repo = self.tmp / "repo"
        sh(self.tmp, "init", "--quiet", "-b", "main", str(self.repo))
        for name, text in (("a.md", "alpha\n"), ("b.md", "beta\n"), ("c.txt", "gamma\n")):
            (self.repo / name).write_text(text)
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "--quiet", "-m", "one")
        sh(self.repo, "remote", "add", "origin", str(self.remote))
        sh(self.repo, "push", "--quiet", "-u", "origin", "main")
        self.spec = {"path": self.repo, "branch": "main", "url": str(self.remote),
                     "config": local_config(self.repo)}
        p = mock.patch.dict(rt.REPOS, {"test": self.spec}, clear=True)
        p.start()
        self.addCleanup(p.stop)
        self.drills = self.tmp / "drills"
        self.drills.mkdir()

    def run_rt(self):
        return rt.run("test", tmp_parent=self.drills)

    def fails(self, part):
        with self.assertRaises(rt.RestoreFailed) as ctx:
            self.run_rt()
        self.assertIn(part, str(ctx.exception))

    def push(self, msg):
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "--quiet", "-m", msg)
        sh(self.repo, "push", "--quiet", "origin", "main")


class PassTest(Base):
    def test_clean_pushed_repo_passes(self):                      # 1
        r = self.run_rt()
        self.assertTrue(r["ok"])
        self.assertEqual(r["state_tested"]["commit"], sh(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(r["rollback"]["tree_after_revert"], r["state_tested"]["tree"])
        self.assertEqual(r["restore"]["restored_tree"], r["state_tested"]["tree"])
        self.assertIn("not_tested", r)

    def test_repo_git_dir_and_remote_bytes_unchanged(self):        # 2
        before = (all_bytes(self.repo), all_bytes(self.remote))
        self.run_rt()
        self.assertEqual((all_bytes(self.repo), all_bytes(self.remote)), before)

    def test_temporary_folders_removed_on_success_and_failure(self):  # 3
        self.run_rt()
        self.assertEqual(list(self.drills.iterdir()), [])
        with mock.patch.object(rt, "restore_drill", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                self.run_rt()
        self.assertEqual(list(self.drills.iterdir()), [])

    def test_untracked_file_passes(self):
        (self.repo / "scratch.md").write_text("untracked\n")
        self.assertTrue(self.run_rt()["ok"])


class IdentityTest(Base):
    def test_unknown_name_and_link_path_refused(self):            # 4
        with self.assertRaises(KeyError):
            rt.run("elsewhere", tmp_parent=self.drills)
        link = self.tmp / "link"
        os.symlink(str(self.repo), str(link))
        self.spec["path"] = link
        self.fails("link or not a directory")

    def test_gitfile_and_git_symlink_refused(self):               # 5
        sep = self.tmp / "sepdir"
        other = self.tmp / "other"
        sh(self.tmp, "clone", "--quiet", "--separate-git-dir", str(sep), str(self.remote), str(other))
        self.spec["path"] = other
        self.fails(".git is not a plain directory")

    def test_worktree_checkout_refused(self):                     # 5
        wt = self.tmp / "wt"
        sh(self.repo, "worktree", "add", "--quiet", "-b", "side", str(wt))
        self.fails("unusual git state: .git/worktrees")
        self.spec["path"], self.spec["branch"] = wt, "side"
        self.fails(".git is not a plain directory")

    def test_extra_config_keys_refused(self):                     # 6
        for key, value in (("core.fsmonitor", "true"), ("core.sparseCheckout", "true"),
                           ("extensions.worktreeConfig", "true"), ("include.path", "/tmp/x"),
                           ("url.https://evil.invalid/.insteadOf", "https://github.com/"),
                           ("filter.lfs.smudge", "git-lfs smudge"), ("credential.helper", "store"),
                           ("remote.second.url", "https://example.invalid/x.git")):
            sh(self.repo, "config", key, value)
            self.fails("unexpected local git config key")
            sh(self.repo, "config", "--unset", key)
        self.assertTrue(self.run_rt()["ok"])

    def test_changed_missing_or_repeated_config_values_refused(self):  # 6, review 3 (3)
        sh(self.repo, "config", "core.filemode", "false")
        self.fails("unexpected value for local git config key core.filemode")
        sh(self.repo, "config", "core.filemode", "true")
        sh(self.repo, "config", "--unset", "core.logallrefupdates")
        self.fails("missing local git config key: core.logallrefupdates")
        sh(self.repo, "config", "core.logallrefupdates", "true")
        sh(self.repo, "config", "--add", "remote.origin.fetch", "+refs/tags/*:refs/tags/*")
        self.fails("repeated local git config key: remote.origin.fetch")
        sh(self.repo, "config", "--unset-all", "remote.origin.fetch")
        sh(self.repo, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
        self.assertTrue(self.run_rt()["ok"])

    def test_optional_editor_key_may_be_absent_but_is_pinned(self):
        self.spec["config"]["branch.main.vscode-merge-base"] = "origin/main"
        self.assertTrue(self.run_rt()["ok"])
        sh(self.repo, "config", "branch.main.vscode-merge-base", "origin/elsewhere")
        self.fails("unexpected value")

    def test_changed_origin_refused(self):                        # 6
        sh(self.repo, "remote", "set-url", "origin", "https://example.invalid/x.git")
        self.fails("fixed GitHub URL")

    def test_denied_git_paths_refused(self):                      # 7
        gitdir = self.repo / ".git"
        for rel in ("shallow", "objects/info/alternates", "info/sparse-checkout",
                    "info/attributes", "MERGE_HEAD", "index.lock", "lfs"):
            target = gitdir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if rel == "lfs":
                target.mkdir()
            else:
                target.write_text("")
            self.fails("unusual git state")
            target.rmdir() if target.is_dir() else target.unlink()

    def test_replace_ref_in_packed_refs_refused(self):            # 7
        head = sh(self.repo, "rev-parse", "HEAD")
        with open(str(self.repo / ".git" / "packed-refs"), "a") as f:
            f.write("%s refs/replace/%s\n" % (head, head))
        self.fails("replace refs")


class TreeTest(Base):
    def test_symlink_in_tree_refused(self):                       # 8
        os.symlink("a.md", str(self.repo / "link.md"))
        self.push("link")
        self.fails("non-file entry")

    def test_gitlink_refused(self):                               # 8
        head = sh(self.repo, "rev-parse", "HEAD")
        sh(self.repo, "update-index", "--add", "--cacheinfo", "160000,%s,sub" % head)
        sh(self.repo, "commit", "--quiet", "-m", "gitlink")
        sh(self.repo, "push", "--quiet", "origin", "main")
        self.fails("non-file entry")

    def test_gitmodules_and_attributes_anywhere_refused(self):    # 8
        for rel in (".gitmodules", ".gitattributes", "sub/.gitattributes"):
            path = self.repo / rel
            path.parent.mkdir(exist_ok=True)
            path.write_text("x\n")
            self.push("add " + rel)
            self.fails("tree holds")
            sh(self.repo, "rm", "--quiet", rel)
            self.push("rm " + rel)


class StateTest(Base):
    def test_wrong_branch_and_detached_head_refused(self):        # 9
        self.spec["branch"] = "other"
        self.fails("not on the fixed branch")
        self.spec["branch"] = "main"
        sh(self.repo, "checkout", "--quiet", "--detach")
        with self.assertRaises(rt.RestoreFailed):
            self.run_rt()

    def test_uncommitted_tracked_changes_refused(self):           # 9
        (self.repo / "a.md").write_text("edited\n")
        self.fails("uncommitted changes")
        sh(self.repo, "checkout", "--quiet", "--", "a.md")
        (self.repo / "b.md").unlink()
        self.fails("uncommitted changes")
        sh(self.repo, "checkout", "--quiet", "--", "b.md")
        (self.repo / "a.md").write_text("staged\n")
        sh(self.repo, "add", "a.md")
        self.assertTrue(sh(self.repo, "diff", "--cached", "--name-only"))  # really staged
        self.fails("uncommitted changes")

    def test_executable_bit_change_refused(self):                 # review 3 (3), D74
        os.chmod(str(self.repo / "a.md"), 0o755)
        self.fails("uncommitted changes")
        os.chmod(str(self.repo / "a.md"), 0o644)
        self.assertTrue(self.run_rt()["ok"])

    def test_index_flags_refused(self):                           # 9
        for flag in ("--assume-unchanged", "--skip-worktree"):
            sh(self.repo, "update-index", flag, "a.md")
            self.fails("unusual flag")
            sh(self.repo, "update-index", flag.replace("--", "--no-"), "a.md")


class RemoteTest(Base):
    def test_unpushed_commit_fails(self):                         # 10
        (self.repo / "a.md").write_text("alpha 2\n")
        sh(self.repo, "commit", "--quiet", "-am", "not pushed")
        self.fails("not the snapshotted commit")

    def test_rewound_remote_fails_even_if_the_commit_survives(self):  # 10
        (self.repo / "a.md").write_text("alpha 2\n")
        self.push("two")
        sh(self.repo, "push", "--quiet", "origin", "main:refs/heads/keep")
        sh(self.repo, "push", "--quiet", "origin", "main:refs/tags/keep")
        first = sh(self.repo, "rev-list", "--max-parents=0", "HEAD")
        sh(self.remote, "update-ref", "refs/heads/main", first)
        self.fails("not the snapshotted commit")

    def test_remote_moved_on_fails(self):                         # 10
        other = self.tmp / "other"
        sh(self.tmp, "clone", "--quiet", str(self.remote), str(other))
        (other / "a.md").write_text("elsewhere\n")
        sh(other, "commit", "--quiet", "-am", "elsewhere")
        sh(other, "push", "--quiet", "origin", "main")
        self.fails("not the snapshotted commit")


class FailurePathTest(Base):                                      # 11
    def test_git_timeout_is_a_failure(self):
        real = subprocess.run
        def slow(argv, *a, **kw):
            if "fsck" in argv:
                raise subprocess.TimeoutExpired(argv, 1)
            return real(argv, *a, **kw)
        with mock.patch.object(rt.subprocess, "run", side_effect=slow):
            self.fails("timed out")
        self.assertEqual(list(self.drills.iterdir()), [])

    def test_remote_clone_error_is_a_failure(self):
        self.spec["url"] = str(self.tmp / "missing.git")
        sh(self.repo, "remote", "set-url", "origin", self.spec["url"])
        self.fails("clone")

    def test_wrong_restored_tree_is_a_failure(self):
        real = rt.tree
        def lying(cwd, rev="HEAD"):
            return "0" * 40 if Path(cwd).name == "restored" else real(cwd, rev)
        with mock.patch.object(rt, "tree", side_effect=lying):
            self.fails("restored tree differs")

    def test_fsck_failure_and_dirty_restore_are_failures(self):
        real = rt.git
        def broken(cwd, *args, **kw):
            if args[:1] == ("fsck",):
                raise rt.RestoreFailed("git fsck failed (1): bad object")
            return real(cwd, *args, **kw)
        with mock.patch.object(rt, "git", side_effect=broken):
            self.fails("fsck")
        def dirty(cwd, *args, **kw):
            out = real(cwd, *args, **kw)
            if Path(cwd).name == "restored" and args[:2] == ("status", "--porcelain"):
                return " M a.md\n"
            return out
        with mock.patch.object(rt, "git", side_effect=dirty):
            self.fails("restored working copy is not clean")

    def test_main_reports_any_failure(self):
        for exc in (OSError("disk gone"), ValueError("disk gone"), RuntimeError("disk gone"),
                    KeyboardInterrupt("disk gone")):
            reports = self.tmp / ("reports-" + type(exc).__name__)
            with mock.patch.object(rt, "REPORTS", reports), \
                    mock.patch.object(rt, "run", side_effect=exc):
                self.assertEqual(rt.main(["test"]), 1)
            (report,) = reports.iterdir()
            data = json.loads(report.read_text())
            self.assertFalse(data["ok"])
            self.assertIn("disk gone", data["error"])
            self.assertIn("not_tested", data)

    def test_decode_failure_through_main_is_a_report(self):       # D74
        reports = self.tmp / "reports"
        real = rt.subprocess.run
        def latin(argv, *a, **kw):
            res = real(argv, *a, **kw)
            if "ls-tree" in argv:
                res.stdout = b"\xff" + res.stdout
            return res
        with mock.patch.object(rt, "REPORTS", reports), \
                mock.patch.dict(os.environ, {"TMPDIR": str(self.drills)}), \
                mock.patch.object(rt.subprocess, "run", side_effect=latin):
            self.assertEqual(rt.main(["test"]), 1)
        (report,) = reports.iterdir()
        self.assertIn("not UTF-8", json.loads(report.read_text())["error"])

    def test_report_and_stdout_both_failing_is_exit_3(self):      # D74
        class Broken(io.StringIO):
            def write(self, _):
                raise OSError("stdout gone")
        with mock.patch.object(rt, "run", return_value={"ok": True}), \
                mock.patch.object(rt, "write_report", side_effect=OSError("disk")), \
                mock.patch.object(rt.sys, "stdout", Broken()):
            self.assertEqual(rt.main(["test"]), 3)

    def test_non_utf8_git_output_is_a_failure(self):              # review 3 (5)
        real = rt.subprocess.run
        def latin(argv, *a, **kw):
            res = real(argv, *a, **kw)
            if "ls-tree" in argv:
                res.stdout = b"\xff" + res.stdout
            return res
        with mock.patch.object(rt.subprocess, "run", side_effect=latin):
            self.fails("not UTF-8")

    def test_non_utf8_packed_refs_is_a_refusal_not_a_crash(self):  # review 3 (5)
        with open(str(self.repo / ".git" / "packed-refs"), "ab") as f:
            f.write(b"# \xff\n")
        with self.assertRaises(rt.RestoreFailed):
            self.run_rt()

    def test_report_write_failure_prints_the_whole_report(self):  # review 3 (6)
        out = io.StringIO()
        result = {"test": "T4.2 restore test", "repo": "test", "ok": True, "seconds": 1.0}
        with mock.patch.object(rt, "run", return_value=result), \
                mock.patch.object(rt, "write_report", side_effect=OSError("read-only disk")), \
                contextlib.redirect_stdout(out):
            self.assertEqual(rt.main(["test"]), 2)
        data = json.loads(out.getvalue())
        self.assertFalse(data["ok"])
        self.assertEqual(data["seconds"], 1.0)
        self.assertIn("read-only disk", data["report_write_error"])


class EnvironmentTest(Base):
    def test_inherited_git_variables_dropped_and_isolation_set(self):  # 12
        with mock.patch.dict(os.environ, {"GIT_DIR": "/x", "GIT_CONFIG_PARAMETERS": "'a.b=c'",
                                          "GIT_CONFIG_COUNT": "1"}):
            env = rt.git_env()
        for k in ("GIT_DIR", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"):
            self.assertNotIn(k, env)
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], "/dev/null")
        self.assertEqual(env["GIT_NO_REPLACE_OBJECTS"], "1")
        for arg in ("core.fsmonitor=false", "core.attributesFile=/dev/null"):
            self.assertIn(arg, rt.GIT_ARGS)

    def test_credential_helper_answers_get_only(self):           # Q150
        self.assertEqual(rt.CREDENTIAL_ARGS[1], "credential.helper=")
        helper = rt.CREDENTIAL_ARGS[3].split("=", 1)[1]
        self.assertIn('[ "$1" = get ]', helper)
        self.assertNotIn("store", helper)

    def test_temp_folder_inside_a_repo_or_the_vault_refused(self):
        for parent in (self.repo, Path(config.VAULT)):
            with self.assertRaises((rt.RestoreFailed, outpath.OutsideVaultError)):
                rt.run("test", tmp_parent=parent)

    def test_temp_folder_found_without_probing_and_checked_first(self):  # review 3 (2)
        before = all_bytes(self.repo)
        with mock.patch.object(rt.tempfile, "gettempdir", side_effect=AssertionError("probe")), \
                mock.patch.dict(os.environ, {"TMPDIR": str(self.repo / "sub")}):
            (self.repo / "sub").mkdir()
            with self.assertRaises((rt.RestoreFailed, outpath.OutsideVaultError)):
                rt.run("test")
            self.assertEqual(list((self.repo / "sub").iterdir()), [])
            (self.repo / "sub").rmdir()
            self.assertEqual(all_bytes(self.repo), before)
            os.environ["TMPDIR"] = str(self.drills)
            self.assertTrue(rt.run("test")["ok"])

    def test_vault_guard_ignores_dbrain_vault(self):
        self.assertEqual(rt.REPOS_VAULT, Path.home() / "Work" / "Dbrain2")
        with mock.patch.object(config, "VAULT", self.tmp / "elsewhere"):
            with self.assertRaises((rt.RestoreFailed, outpath.OutsideVaultError)):
                rt.temp_parent(rt.REPOS_VAULT / "90 System")

    def script(self, *args, flags=("-I", "-B"), env=None):
        return subprocess.run([sys.executable] + list(flags) +
                              [str(HERE / "experiments" / "restore_test.py")] + list(args),
                              env=env, capture_output=True, text=True)

    def test_script_writes_no_bytecode_from_interpreter_start(self):  # review 3 (1), D74
        prefix = self.tmp / "protected" / "pycache"  # stands in for a folder in a repository
        prefix.mkdir(parents=True)
        env = dict(os.environ, PYTHONPYCACHEPREFIX=str(prefix))
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        res = self.script("--help", env=env)
        self.assertEqual(res.returncode, 2)
        self.assertFalse(json.loads(res.stdout)["ok"])
        self.assertEqual(list(prefix.rglob("*")), [])

    def test_script_without_isolation_flags_is_refused_with_a_report(self):  # D74
        # Only cases that fail the check here: on Apple's Python 3.9, -I alone also sets -B.
        for flags in ((), ("-B",)):
            res = self.script("vault", flags=flags)
            self.assertEqual(res.returncode, 2, flags)
            data = json.loads(res.stdout)
            self.assertFalse(data["ok"])
            self.assertIn("-I -B", data["error"])

    def test_bad_argument_ends_in_a_report(self):                  # D74
        for args in ((), ("elsewhere",), ("vault", "runtime")):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(rt.main(list(args)), 2)
            data = json.loads(out.getvalue())
            self.assertFalse(data["ok"])
            self.assertIn("usage", data["error"])
            self.assertIn("not_tested", data)

    def test_import_failure_ends_in_a_report(self):                # D74
        out = io.StringIO()
        with mock.patch.object(rt, "IMPORT_ERROR", "ImportError: no dbrain"), \
                contextlib.redirect_stdout(out):
            self.assertEqual(rt.main(["test"]), 2)
        self.assertIn("no dbrain", json.loads(out.getvalue())["error"])


class ReportTest(Base):
    def test_report_goes_to_the_fixed_folder_only(self):         # 13
        reports = self.tmp / "reports"
        with mock.patch.object(rt, "REPORTS", reports), \
                mock.patch.dict(os.environ, {"TMPDIR": str(self.drills)}):
            self.assertEqual(rt.main(["test"]), 0)
        (report,) = reports.iterdir()
        self.assertTrue(json.loads(report.read_text())["ok"])

    def test_report_folder_inside_git_or_the_vault_refused(self):  # 13
        for folder in (self.repo / "reports", self.repo / ".git", Path(config.VAULT) / "t42"):
            with self.assertRaises(outpath.OutsideVaultError, msg=str(folder)):
                rt.write_report("test", {"ok": False}, folder=folder)

    def test_fixed_table_matches_the_real_repositories(self):    # 14
        real = {"vault": (Path.home() / "Work" / "Dbrain2", "master",
                          "https://github.com/David-T13Consulting/Dbrain2.git"),
                "runtime": (Path.home() / "Work" / "dbrain-runtime", "main",
                            "https://github.com/David-T13Consulting/dbrain-runtime.git")}
        self.assertEqual({k: (v["path"], v["branch"], v["url"]) for k, v in REAL_REPOS.items()}, real)
        self.assertEqual(REAL_REPOS["vault"]["path"], Path(config.VAULT))

    def test_pinned_config_matches_the_real_repositories(self):  # 14, read-only
        for name, spec in REAL_REPOS.items():
            rt._check_config(spec["path"], spec)


if __name__ == "__main__":
    unittest.main()
